from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from factory_sandbox.engine import DigitalFactorySandbox
from factory_sandbox.models import ComparisonRequest, ExportApprovalRequest
from factory_sandbox.service import SandboxRegistry


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def run_demo(arguments: argparse.Namespace) -> tuple[dict, int]:
    sandbox = DigitalFactorySandbox.create(arguments.scenario, arguments.seed, arguments.strategy)
    sandbox.execute("step", arguments.steps)
    payload = {
        "simulation": sandbox.inspect().model_dump(mode="json"),
        "metrics": sandbox.metrics().model_dump(mode="json"),
        "pipeline_state": "awaiting_human_approval",
    }
    if not arguments.approve:
        return payload, 2
    if not arguments.approver or not arguments.reason:
        raise ValueError("--approve requires --approver and --reason")
    sandbox.approve_export(ExportApprovalRequest(approver=arguments.approver, reason=arguments.reason))
    package = sandbox.export()
    payload["pipeline_state"] = "approved_for_local_export"
    payload["evidence_package"] = package
    if arguments.output:
        if arguments.output.suffix.lower() != ".json":
            raise ValueError("--output must be a JSON file")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["output"] = str(arguments.output)
    return payload, 0


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description="离线多智能体数字工厂分析沙箱")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo")
    demo.add_argument("--scenario", default="upstream_slowdown", choices=["upstream_slowdown", "quality_isolation", "peak_tariff_urgent_order"])
    demo.add_argument("--strategy", default="multi_agent", choices=["fixed_rule", "single_agent", "multi_agent"])
    demo.add_argument("--seed", type=int, default=15)
    demo.add_argument("--steps", type=int, default=6)
    demo.add_argument("--approve", action="store_true")
    demo.add_argument("--approver")
    demo.add_argument("--reason")
    demo.add_argument("--output", type=Path)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--scenario", default="upstream_slowdown", choices=["upstream_slowdown", "quality_isolation", "peak_tariff_urgent_order"])
    compare.add_argument("--seed", type=int, default=15)
    compare.add_argument("--steps", type=int, default=6)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
        if arguments.command == "demo":
            if arguments.seed < 0 or arguments.steps < 1:
                raise ValueError("seed must be non-negative and steps must be positive")
            if arguments.output and not arguments.approve:
                raise ValueError("--output requires --approve")
            payload, exit_code = run_demo(arguments)
        else:
            request = ComparisonRequest(scenario_id=arguments.scenario, seed=arguments.seed, steps=arguments.steps)
            payload = SandboxRegistry().compare(request)
            exit_code = 0
        print(json.dumps(payload, ensure_ascii=False))
        return exit_code
    except (ValueError, argparse.ArgumentError) as exc:
        print(json.dumps({"error": "input_error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 4
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
