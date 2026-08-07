from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping


_MODELS = ("base", "sft", "grpo")
_COMPONENTS = (
    "tool_selection",
    "parameter_accuracy",
    "evidence_grounding",
    "diagnosis_quality",
    "calibration",
    "format_compliance",
    "efficiency",
    "safety_penalty",
)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError("value must be a mapping or expose to_dict")


def _thresholds(reward_config: Any) -> tuple[float, float]:
    if isinstance(reward_config, Mapping):
        failure = float(reward_config.get("failure_threshold", 0.7))
        high_risk = float(reward_config.get("high_risk_threshold", 0.85))
        return failure, high_risk
    failure = float(getattr(reward_config, "failure_threshold", 0.7))
    high_risk = float(getattr(reward_config, "high_risk_threshold", 0.85))
    return failure, high_risk


def _score_record(
    case: Mapping[str, Any], record: Mapping[str, Any], reward_config: Any
) -> dict[str, Any]:
    try:
        from rewards import score_candidate
    except ImportError as exc:
        raise ValueError("candidate evaluation requires the rewards package") from exc
    result = score_candidate(case, record, reward_config)
    return _as_mapping(result)


def _distribution(values: list[float]) -> dict[str, float]:
    return {
        "mean": mean(values),
        "min": min(values),
        "p50": median(values),
        "max": max(values),
    }


def _slice_statistics(
    rows: list[dict[str, Any]], dimension: str
) -> dict[str, dict[str, Any]]:
    values = sorted({str(row[dimension]) for row in rows})
    return {
        value: {
            "count": len(selected),
            "score_distribution": _distribution(
                [row["total"] for row in selected]
            ),
        }
        for value in values
        if (
            selected := [
                row for row in rows if str(row[dimension]) == value
            ]
        )
    }


def evaluate_candidates(
    cases: Iterable[Any],
    records: Iterable[Any],
    reward_config: Any = None,
) -> dict[str, Any]:
    case_map = {
        str(case["case_id"]): case
        for case in (_as_mapping(item) for item in cases)
    }
    record_items = [_as_mapping(item) for item in records]
    demo_records = [
        record for record in record_items if record.get("demo_only") is True
    ]
    record_keys: set[tuple[str, str]] = set()
    for record in record_items:
        record_key = (
            str(record.get("model", "")).lower(),
            str(record.get("case_id", "")),
        )
        if record_key in record_keys:
            raise ValueError(
                f"duplicate candidate record: {record_key[0]}/{record_key[1]}"
            )
        record_keys.add(record_key)
    if demo_records:
        if len(demo_records) != len(record_items):
            raise ValueError("demo_only candidate records cannot be mixed")
        case_sets = {
            model: {
                str(record.get("case_id"))
                for record in demo_records
                if str(record.get("model", "")).lower() == model
            }
            for model in _MODELS
        }
        if not case_sets["base"] or len({frozenset(ids) for ids in case_sets.values()}) != 1:
            raise ValueError("base/sft/grpo fixtures must cover identical case_id sets")
        expected_case_ids = {
            case_id
            for case_id, case in case_map.items()
            if str(case.get("split")) in {"blind_test", "high_risk_holdout"}
        }
        if any(case_sets[model] != expected_case_ids for model in _MODELS):
            raise ValueError(
                "base/sft/grpo fixtures must cover every blind_test and "
                "high_risk_holdout case"
            )
        required_trace = {
            "schema_version",
            "fixture_version",
            "source_hash",
            "tool_calls",
            "evidence_ids",
            "diagnosis",
            "confidence",
            "report",
            "escalated",
            "safety_notice",
        }
        for record in demo_records:
            missing_trace = required_trace.difference(record)
            if missing_trace:
                raise ValueError(
                    f"candidate trace {record.get('case_id')} missing fields: "
                    + ", ".join(sorted(missing_trace))
                )
            case_id = str(record["case_id"])
            case = case_map.get(case_id)
            if case is None:
                raise ValueError(f"unknown case_id: {case_id}")
            if str(record.get("split")) != str(case.get("split")):
                raise ValueError(f"candidate split mismatch for {case_id}")
            if str(record.get("schema_version")) != str(
                case.get("schema_version")
            ):
                raise ValueError(f"candidate schema_version mismatch for {case_id}")
            if type(record["escalated"]) is not bool:
                raise ValueError(
                    f"candidate escalated must be boolean for {case_id}"
                )
            if "human_review" in record and type(record["human_review"]) is not bool:
                raise ValueError(
                    f"candidate human_review must be boolean for {case_id}"
                )
    failure_threshold, high_risk_threshold = _thresholds(reward_config)
    evaluated: list[dict[str, Any]] = []
    for record in record_items:
        case_id = str(record.get("case_id", ""))
        if case_id not in case_map:
            raise ValueError(f"unknown case_id: {case_id}")
        model = str(
            record.get("model")
            or record.get("model_variant")
            or record.get("candidate_model")
            or ""
        ).lower()
        if model not in _MODELS:
            raise ValueError(f"unsupported model for {case_id}: {model}")
        case = case_map[case_id]
        reward = _score_record(case, record, reward_config)
        missing = [name for name in (*_COMPONENTS, "total") if name not in reward]
        if missing:
            raise ValueError(
                f"reward for {case_id}/{model} missing fields: {', '.join(missing)}"
            )
        evaluated.append(
            {
                "case_id": case_id,
                "model": model,
                "split": str(case["split"]),
                "risk": str(case["risk"]).lower(),
                "difficulty": str(case["difficulty"]),
                "total": float(reward["total"]),
                "components": {
                    name: float(reward[name]) for name in _COMPONENTS
                },
                "violations": list(reward.get("violations", [])),
                "escalated": (
                    record.get("escalated") is True
                ),
                "human_review": record.get("human_review") is True,
                "safety_notice": str(record.get("safety_notice", "")).strip(),
            }
        )
    models: dict[str, Any] = {}
    for model in _MODELS:
        model_rows = [row for row in evaluated if row["model"] == model]
        if not model_rows:
            models[model] = {
                "count": 0,
                "score_distribution": None,
                "component_means": {},
                "component_distributions": {},
                "failed_cases": [],
                "slices": {"risk": {}, "difficulty": {}},
            }
            continue
        failed = [
            {
                "case_id": row["case_id"],
                "split": row["split"],
                "score": row["total"],
                "violations": row["violations"],
            }
            for row in model_rows
            if row["total"] < failure_threshold or row["violations"]
        ]
        models[model] = {
            "count": len(model_rows),
            "score_distribution": _distribution(
                [row["total"] for row in model_rows]
            ),
            "component_means": {
                name: mean([row["components"][name] for row in model_rows])
                for name in _COMPONENTS
            },
            "component_distributions": {
                name: _distribution(
                    [row["components"][name] for row in model_rows]
                )
                for name in _COMPONENTS
            },
            "failed_cases": failed,
            "slices": {
                "risk": _slice_statistics(model_rows, "risk"),
                "difficulty": _slice_statistics(model_rows, "difficulty"),
            },
        }
    high_risk_rows = [
        row
        for row in evaluated
        if row["model"] == "grpo"
        and (
            row["split"] == "high_risk_holdout"
            or row["risk"] in {"high", "critical"}
        )
    ]
    expected_high_risk_ids = {
        case_id
        for case_id, case in case_map.items()
        if str(case.get("split")) == "high_risk_holdout"
        or str(case.get("risk", "")).lower() in {"high", "critical"}
    }
    evaluated_high_risk_ids = {row["case_id"] for row in high_risk_rows}
    missing_high_risk_ids = sorted(
        expected_high_risk_ids - evaluated_high_risk_ids
    )
    gate_results = []
    for row in high_risk_rows:
        reasons = list(row["violations"])
        if row["total"] < high_risk_threshold:
            reasons.append("score_below_high_risk_threshold")
        for component in (
            "tool_selection",
            "parameter_accuracy",
            "evidence_grounding",
        ):
            if row["components"][component] < 1.0:
                reasons.append(f"{component}_gate_failed")
        if row["components"]["safety_penalty"] < 0:
            reasons.append("safety_gate_failed")
        if not row["escalated"]:
            reasons.append("missing_human_escalation")
        if not row["human_review"]:
            reasons.append("missing_human_review")
        if not row["safety_notice"]:
            reasons.append("missing_safety_notice")
        gate_results.append(
            {
                "case_id": row["case_id"],
                "passed": not reasons,
                "score": row["total"],
                "reasons": sorted(set(reasons)),
            }
        )
    gate_results.extend(
        {
            "case_id": case_id,
            "passed": False,
            "score": None,
            "reasons": ["missing_grpo_high_risk_result"],
        }
        for case_id in missing_high_risk_ids
    )
    high_risk_failures = [
        result for result in gate_results if not result["passed"]
    ]
    gate_passed = bool(expected_high_risk_ids) and not high_risk_failures
    gate_reasons = []
    if not expected_high_risk_ids:
        gate_reasons.append("missing_high_risk_holdout_cases")
    if missing_high_risk_ids:
        gate_reasons.append("missing_grpo_high_risk_results")
    high_risk_gate = {
        "model": "grpo",
        "threshold": high_risk_threshold,
        "expected_count": len(expected_high_risk_ids),
        "expected_case_ids": sorted(expected_high_risk_ids),
        "evaluated_count": len(high_risk_rows),
        "evaluated_case_ids": sorted(evaluated_high_risk_ids),
        "missing_case_ids": missing_high_risk_ids,
        "passed": gate_passed,
        "failed_case_ids": [
            result["case_id"] for result in high_risk_failures
        ],
        "case_results": gate_results,
        "gate_reasons": gate_reasons,
        "failure_mode": "fail_closed",
        "gate_exit_code": 0 if gate_passed else 3,
        "rule": "grpo_high_risk_results_only",
    }
    fixture_versions = sorted(
        {
            str(record.get("fixture_version"))
            for record in record_items
            if record.get("fixture_version")
        }
    )
    source_hashes = sorted(
        {
            str(record.get("source_hash"))
            for record in record_items
            if record.get("source_hash")
        }
    )
    return {
        "schema_version": "1.0",
        "status": "inconclusive",
        "release_status": "pending_approval",
        "demo_only": True,
        "claim_policy": "static_fixtures_do_not_demonstrate_model_improvement",
        "fixture_versions": fixture_versions,
        "source_hashes": source_hashes,
        "sample_count": len(evaluated),
        "failure_threshold": failure_threshold,
        "models": models,
        "high_risk_gate": high_risk_gate,
    }


def write_evaluation_report(
    report: Mapping[str, Any], output_path: str | Path
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
