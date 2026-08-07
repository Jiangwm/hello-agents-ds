from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from datasets import load_cases
from evaluation import evaluate_candidates, write_evaluation_report
from training import prepare_training_artifacts, validate_reward_review


CHAPTER_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = CHAPTER_DIR / "datasets" / "industrial_cases.jsonl"
DEFAULT_CONFIG = CHAPTER_DIR / "configs" / "training_config.json"
DEFAULT_CANDIDATES = CHAPTER_DIR / "evaluation" / "model_candidates.jsonl"


class InputArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = InputArgumentParser(
        description="离线生成并验证工业诊断 SFT/GRPO 教学产物。"
    )
    parser.add_argument("stage", choices=("prepare", "evaluate", "demo"))
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 JSON：{path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"无法读取 JSONL：{path}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"JSONL 第 {line_number} 行无效：{path}") from error
        if not isinstance(record, dict):
            raise ValueError(f"JSONL 第 {line_number} 行必须是对象：{path}")
        records.append(record)
    if not records:
        raise ValueError(f"JSONL 不得为空：{path}")
    return records


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError(f"无法读取输入文件：{path}") from error


def _reward_config(config: Mapping[str, Any]) -> SimpleNamespace:
    validate_reward_review(config)
    weights = config.get("reward_weights")
    evaluation = config.get("evaluation")
    if not isinstance(weights, Mapping) or not isinstance(evaluation, Mapping):
        raise ValueError("训练配置缺少 reward_weights 或 evaluation")
    component_names = (
        "tool_selection",
        "parameter_accuracy",
        "evidence_grounding",
        "diagnosis_quality",
        "calibration",
        "format_compliance",
        "efficiency",
        "safety_penalty",
    )
    missing = [name for name in component_names if name not in weights]
    if missing:
        raise ValueError("reward_weights 缺少：" + ", ".join(missing))
    return SimpleNamespace(
        **{
            f"{name}_weight": float(weights[name])
            for name in component_names
        },
        failure_threshold=float(evaluation["failure_threshold"]),
        high_risk_threshold=float(evaluation["high_risk_threshold"]),
    )


def _validate_candidate_source(
    cases_path: Path,
    cases: Iterable[Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]],
) -> None:
    expected_hash = _sha256(cases_path)
    schema_versions = {str(case["schema_version"]) for case in cases}
    case_splits = {str(case["case_id"]): str(case["split"]) for case in cases}
    for record in candidates:
        case_id = str(record.get("case_id", ""))
        if record.get("source_hash") != expected_hash:
            raise ValueError(f"候选轨迹来源哈希不匹配：{case_id}")
        if str(record.get("schema_version", "")) not in schema_versions:
            raise ValueError(f"候选轨迹 schema_version 不匹配：{case_id}")
        if case_id not in case_splits:
            raise ValueError(f"候选轨迹引用未知案例：{case_id}")
        if str(record.get("split", "")) != case_splits[case_id]:
            raise ValueError(f"候选轨迹 split 不匹配：{case_id}")


def _audit_record(event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        {"event": event, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "event_id": "AUD-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "status": "completed",
        "payload": dict(payload),
    }


def _write_audit(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    rows = [
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        for record in records
    ]
    path.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    cases_path = args.cases.resolve()
    config_path = args.config.resolve()
    candidates_path = args.candidates.resolve()
    output_dir = args.output_dir.resolve()
    cases = load_cases(cases_path)
    config = _load_json(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_records: list[dict[str, Any]] = []
    manifest: dict[str, Any] | None = None
    report: dict[str, Any] | None = None

    if args.stage in {"prepare", "demo"}:
        manifest = prepare_training_artifacts(
            cases,
            config_path,
            output_dir,
            cases_source=cases_path,
            candidates_source=candidates_path,
        )
        audit_records.append(
            _audit_record(
                "training_artifacts_prepared",
                {
                    "manifest_status": manifest["status"],
                    "cases_sha256": manifest["inputs"]["cases"]["sha256"],
                    "training_executed": False,
                },
            )
        )

    if args.stage in {"evaluate", "demo"}:
        candidates = _load_jsonl(candidates_path)
        _validate_candidate_source(cases_path, cases, candidates)
        report = evaluate_candidates(cases, candidates, _reward_config(config))
        write_evaluation_report(report, output_dir / "evaluation_report.json")
        audit_records.append(
            _audit_record(
                "demo_candidates_evaluated",
                {
                    "candidate_fixture_sha256": _sha256(candidates_path),
                    "demo_only": report["demo_only"],
                    "high_risk_gate_passed": report["high_risk_gate"]["passed"],
                    "release_status": report["release_status"],
                },
            )
        )

    _write_audit(output_dir / "audit.jsonl", audit_records)
    high_risk_gate = report["high_risk_gate"] if report is not None else None
    response = {
        "status": report["status"] if report is not None else manifest["status"],
        "pipeline_state": "pending_human_approval",
        "stage": args.stage,
        "execution_mode": "plan_only",
        "training_executed": False,
        "production_control": "prohibited",
        "demo_only": bool(report and report["demo_only"]),
        "output_dir": str(output_dir),
        "high_risk_gate": high_risk_gate,
    }
    if high_risk_gate is not None and not high_risk_gate["passed"]:
        return int(high_risk_gate["gate_exit_code"]), response
    return 0, response


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        exit_code, payload = run(args)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {"status": "input_error", "message": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
