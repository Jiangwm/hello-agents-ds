from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import (
    ApprovalRequest,
    PlanEditRequest,
    PlanningRequest,
    TaskScheduleEdit,
)
from app.service import EnergyPlanningAssistant


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def run_demo(
    approval: ApprovalRequest | None = None,
    destination: Path | None = None,
) -> tuple[dict, int]:
    assistant = EnergyPlanningAssistant()
    plan = assistant.create_plan(
        PlanningRequest(
            line_id="LINE-A",
            planning_date=date(2026, 8, 1),
            output_target_units=1000,
            shifts=["day", "night"],
            optimization_objective="balanced",
            tariff_profile_id="CN-DEMO-SUMMER",
            locked_task_ids=["T-HEAT"],
        )
    )
    recommended = next(
        option
        for option in plan.candidates
        if option.option_id == plan.recommended_option_id
    )
    movable = next(task for task in recommended.tasks if task.task_id == "T-MIX")
    plan = assistant.edit_plan(
        plan.plan_id,
        PlanEditRequest(
            option_id=recommended.option_id,
            changes=[
                TaskScheduleEdit(
                    task_id=movable.task_id,
                    start_minute=movable.start_minute - 30,
                )
            ],
            actor="demo-planner",
            reason="演示安全的可调任务移动",
        ),
    )
    selected = next(
        option
        for option in plan.candidates
        if option.option_id == plan.recommended_option_id
    )
    summary = {
        "plan_id": plan.plan_id,
        "approval_status": plan.approval_status,
        "recommended_option_id": plan.recommended_option_id,
        "recomputed_task_ids": plan.recomputed_task_ids,
        "read_only": plan.read_only,
        "production_control": plan.production_control,
        "data_summary": plan.data_summary.model_dump(mode="json"),
        "estimation_error_percent": plan.estimation_error_percent,
        "recommended_metrics": {
            "total_cost": selected.cost.total_cost,
            "peak_kw": selected.peak_kw,
            "unit_energy_kwh": selected.unit_energy_kwh,
        },
    }
    if approval is None:
        summary["pipeline_state"] = "awaiting_human_approval"
        return summary, 2
    plan = assistant.approve_plan(plan.plan_id, approval)
    summary["approval_status"] = plan.approval_status
    summary["pipeline_state"] = "approved_for_local_export"
    summary["evidence_package"] = assistant.export_plan(
        plan.plan_id,
        destination,
    )
    return summary, 0


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description="离线工厂能源排产建议助手")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="运行脱敏离线演示")
    demo.add_argument("--approve", action="store_true", help="记录人工审批")
    demo.add_argument("--approver", help="审批人")
    demo.add_argument("--reason", help="审批原因")
    demo.add_argument("--output", type=Path, help="导出已审批 JSON 证据包")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(
            list(sys.argv[1:] if argv is None else argv)
        )
        if arguments.approve and (
            not arguments.approver or not arguments.reason
        ):
            raise ValueError("--approve requires --approver and --reason")
        if arguments.output is not None and not arguments.approve:
            raise ValueError("--output requires --approve")
        approval = (
            ApprovalRequest(
                approver=arguments.approver,
                reason=arguments.reason,
            )
            if arguments.approve
            else None
        )
        payload, exit_code = run_demo(approval, arguments.output)
        print(json.dumps(payload, ensure_ascii=False))
        return exit_code
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "error": "input_error",
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 4
    except Exception as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
