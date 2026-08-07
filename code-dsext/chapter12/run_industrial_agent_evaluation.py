from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from benchmarks import (
    load_agent_traces,
    load_benchmark_cases,
    validate_benchmark_suite,
)
from evaluators import IndustrialAgentEvaluator
from reports import write_evaluation_artifacts


CHAPTER_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = CHAPTER_DIR / "benchmarks" / "industrial_cases.jsonl"
DEFAULT_TRACES = CHAPTER_DIR / "benchmarks" / "agent_traces.jsonl"


class InputArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = InputArgumentParser(
        description="离线评测工业数据分析智能体的静态轨迹。",
    )
    parser.add_argument("stage", choices=("evaluate", "demo"))
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--agent-a", default="agent-safe-v2")
    parser.add_argument("--agent-b", default="agent-baseline-v1")
    parser.add_argument("--quality-threshold", type=float, default=0.75)
    parser.add_argument(
        "--comparison-seed",
        default="chapter12-industrial-evaluation-v1",
    )
    return parser


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    cases_path = args.cases.resolve()
    traces_path = args.traces.resolve()
    cases = load_benchmark_cases(cases_path)
    validate_benchmark_suite(cases)
    all_traces = load_agent_traces(traces_path)
    agents = (str(args.agent_a), str(args.agent_b))
    if agents[0] == agents[1]:
        raise ValueError("--agent-a 与 --agent-b 必须不同")
    traces = [
        trace
        for trace in all_traces
        if trace["agent_version"] in agents
    ]
    report = IndustrialAgentEvaluator(
        quality_threshold=float(args.quality_threshold),
    ).evaluate(
        cases,
        traces,
        comparison=agents,
        comparison_seed=str(args.comparison_seed),
    )
    artifacts = write_evaluation_artifacts(
        report,
        args.output_dir.resolve(),
        cases_path=cases_path,
        traces_path=traces_path,
    )
    response = {
        "status": report["status"],
        "stage": args.stage,
        "evaluation_mode": report["evaluation_mode"],
        "production_control": report["production_control"],
        "release_status": report["release_status"],
        "safety_gate_passed": report["safety_gate"]["passed"],
        "report_fingerprint": report["report_fingerprint"],
        "output_dir": artifacts["output_dir"],
        "report_path": artifacts["report_path"],
    }
    return (0 if report["safety_gate"]["passed"] else 3), response


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
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
