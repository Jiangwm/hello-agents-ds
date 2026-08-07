from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


_STAGE_IDS = (
    "baseline",
    "sft_lora",
    "sft_evaluation",
    "grpo",
    "blind_test",
    "high_risk_gate",
    "human_review",
    "offline_canary",
)
_TRAINING_SPLITS = {"train"}
_HIGH_RISKS = {"high", "critical"}
_CHAPTER_DIR = Path(__file__).resolve().parents[1]
_REWARD_REVIEW_FIELDS = {
    "status",
    "role",
    "reviewer_id",
    "reviewed_at",
    "rubric_version",
    "input_hash",
}


def _as_mapping(case: Any) -> dict[str, Any]:
    if isinstance(case, Mapping):
        return dict(case)
    if hasattr(case, "to_dict"):
        return dict(case.to_dict())
    if hasattr(case, "__dict__"):
        return dict(vars(case))
    raise TypeError("case must be a mapping or expose to_dict")


def _read_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid training config: {path}") from exc
    governance = config.get("governance", {})
    model = config.get("model", {})
    if governance.get("plan_only") is not True:
        raise ValueError("governance.plan_only must be true")
    if governance.get("allow_training_execution") is not False:
        raise ValueError("training execution must be disabled")
    if governance.get("allow_model_download") is not False:
        raise ValueError("model download must be disabled")
    if not str(model.get("identifier", "")).startswith("local:"):
        raise ValueError("model.identifier must use a local: identifier")
    validate_reward_review(config)
    return config


def validate_reward_review(config: Mapping[str, Any]) -> dict[str, Any]:
    review = config.get("reward_review")
    if not isinstance(review, Mapping) or _REWARD_REVIEW_FIELDS.difference(review):
        raise ValueError("reward_review metadata is incomplete")
    if any(not isinstance(review[field], str) or not review[field] for field in _REWARD_REVIEW_FIELDS):
        raise ValueError("reward_review metadata must be set")
    if review["status"] != "approved":
        raise ValueError("reward_review.status must be approved")
    if review["input_hash"] != _reward_review_hash(config):
        raise ValueError("reward_review input_hash mismatch")
    return dict(review)


def _reward_review_hash(config: Mapping[str, Any]) -> str:
    payload = {
        "reward_weights": config.get("reward_weights"),
        "evaluation": config.get("evaluation"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _materialize_cases(cases: Iterable[Any]) -> list[dict[str, Any]]:
    materialized = [_as_mapping(case) for case in cases]
    seen: set[str] = set()
    for case in materialized:
        case_id = str(case.get("case_id", "")).strip()
        if not case_id:
            raise ValueError("case_id is required")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
    return materialized


def _rejected_training_samples(
    cases: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    rejected = []
    for case in cases:
        if case.get("split") not in _TRAINING_SPLITS:
            continue
        reasons = []
        if not _is_expert_approved(case):
            reasons.append("not_expert_approved")
        if str(case.get("risk", "")).lower() in _HIGH_RISKS:
            reasons.append("high_risk_training_prohibited")
        if reasons:
            rejected.append(
                {"case_id": str(case["case_id"]), "reason": ",".join(reasons)}
            )
    return rejected


def _is_expert_approved(case: Mapping[str, Any]) -> bool:
    if "expert_approved" in case:
        return case.get("expert_approved") is True
    review = case.get("expert_review")
    return (
        isinstance(review, Mapping)
        and str(review.get("status", "")).lower() == "approved"
    )


def _split_counts(cases: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        split = str(case.get("split", "unknown"))
        counts[split] = counts.get(split, 0) + 1
    return dict(sorted(counts.items()))


def build_training_plan(
    config_path: str | Path, cases: Iterable[Any]
) -> dict[str, Any]:
    config = _read_config(config_path)
    materialized = _materialize_cases(cases)
    governance = config["governance"]
    stages = []
    for index, stage_id in enumerate(_STAGE_IDS, start=1):
        stage = {
            "order": index,
            "id": stage_id,
            "status": "pending_approval",
            "execution": "prohibited_in_plan_only_mode",
            "depends_on": [] if index == 1 else [_STAGE_IDS[index - 2]],
        }
        if stage_id == "grpo":
            stage["requirements"] = {
                "initial_checkpoint": config["grpo"]["initial_checkpoint"],
                "group_size": config["grpo"]["group_size"],
                "beta": config["grpo"]["beta"],
                "kl_coefficient": config["grpo"]["kl_coefficient"],
                "rollout_max_steps": config["grpo"]["rollout_max_steps"],
                "log_reward_components": config["monitoring"][
                    "log_reward_components"
                ],
                "kl_monitoring": config["monitoring"]["kl"],
                "output_length_monitoring": config["monitoring"][
                    "output_length"
                ],
            }
        stages.append(stage)
    return {
        "schema_version": "1.0",
        "status": "pending_approval",
        "plan_only": True,
        "execution_permitted": False,
        "model": config["model"],
        "lora": config["lora"],
        "sft": config["sft"],
        "grpo": config["grpo"],
        "reward_weights": config["reward_weights"],
        "reward_review": config["reward_review"],
        "monitoring": config["monitoring"],
        "evaluation": config["evaluation"],
        "sample_counts": _split_counts(materialized),
        "rejected_training_samples": _rejected_training_samples(materialized),
        "stages": stages,
        "governance": {
            **governance,
            "training_data_policy": "expert_approved_non_high_risk_only",
            "high_risk_usage": "evaluation_gate_only",
            "fixed_seed": config["monitoring"]["fixed_seed"],
        },
    }


def _build_records(
    cases: list[dict[str, Any]], builder_name: str
) -> list[dict[str, Any]]:
    try:
        module = importlib.import_module("datasets")
        builder = getattr(module, builder_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "local datasets package with training builders is required"
        ) from exc
    return list(builder(cases, split="train"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> int:
    rows = [
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        for record in records
    ]
    path.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")
    return len(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_manifest(
    config_path: str | Path,
    cases: list[dict[str, Any]],
    cases_source: str | Path,
    candidates_source: str | Path,
) -> dict[str, dict[str, Any]]:
    canonical_cases = Path(cases_source)
    candidates = Path(candidates_source)
    config = Path(config_path)
    for source in (canonical_cases, config, candidates):
        if not source.is_file():
            raise ValueError(f"manifest input does not exist: {source}")
    inputs = {
        "cases": {
            "source": str(canonical_cases.resolve()),
            "sha256": _sha256(canonical_cases),
            "sample_count": len(cases),
        },
        "config": {
            "source": str(config.resolve()),
            "sha256": _sha256(config),
            "sample_count": 1,
        },
        "candidate_fixtures": {
            "source": str(candidates.resolve()),
            "sha256": _sha256(candidates),
            "sample_count": len(
                candidates.read_text(encoding="utf-8").splitlines()
            ),
        },
    }
    return inputs


def prepare_training_artifacts(
    cases: Iterable[Any],
    config_path: str | Path,
    output_dir: str | Path,
    *,
    cases_source: str | Path | None = None,
    candidates_source: str | Path | None = None,
) -> dict[str, Any]:
    materialized = _materialize_cases(cases)
    plan = build_training_plan(config_path, materialized)
    cases_source = (
        Path(cases_source)
        if cases_source is not None
        else _CHAPTER_DIR / "datasets" / "industrial_cases.jsonl"
    )
    candidates_source = (
        Path(candidates_source)
        if candidates_source is not None
        else _CHAPTER_DIR / "evaluation" / "model_candidates.jsonl"
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    sft_path = output / "sft_train.jsonl"
    grpo_path = output / "grpo_train.jsonl"
    plan_path = output / "training_plan.json"
    sft_count = _write_jsonl(
        sft_path, _build_records(materialized, "build_sft_records")
    )
    grpo_count = _write_jsonl(
        grpo_path, _build_records(materialized, "build_grpo_records")
    )
    _write_json(plan_path, plan)
    manifest = {
        "schema_version": "1.0",
        "status": "pending_approval",
        "inputs": _input_manifest(
            config_path, materialized, cases_source, candidates_source
        ),
        "artifacts": {
            sft_path.name: {
                "sha256": _sha256(sft_path),
                "sample_count": sft_count,
            },
            grpo_path.name: {
                "sha256": _sha256(grpo_path),
                "sample_count": grpo_count,
            },
            plan_path.name: {
                "sha256": _sha256(plan_path),
                "sample_count": 1,
            },
        },
        "split_counts": _split_counts(materialized),
        "governance": {
            "expert_review_required": True,
            "training_samples": "expert_approved_non_high_risk_only",
            "high_risk_samples": "evaluation_gate_only",
            "reward_review_status": plan["reward_review"]["status"],
            "reward_rubric_version": plan["reward_review"]["rubric_version"],
            "reward_input_hash": plan["reward_review"]["input_hash"],
            "production_boundary": "read_only_offline",
            "execution_permitted": False,
            "fixed_seed": plan["governance"]["fixed_seed"],
            "rejected_training_samples": plan["rejected_training_samples"],
        },
    }
    _write_json(output / "manifest.json", manifest)
    return manifest
