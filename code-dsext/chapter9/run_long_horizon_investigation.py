from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from workflows import InvestigationTarget, LongHorizonYieldInvestigation


EXIT_SUCCESS = 0
EXIT_AWAITING_APPROVAL = 2
EXIT_BLOCKED = 3
EXIT_ERROR = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线跨班次良率波动长程调查")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--investigation-id", default="yield-investigation-demo")
    parser.add_argument("--objective", default="调查跨班次良率波动")
    parser.add_argument("--product", default="P-001")
    parser.add_argument("--line", default="L-01")
    parser.add_argument("--start-date", default="2026-07-20")
    parser.add_argument("--end-date", default="2026-07-29")
    parser.add_argument(
        "--stage",
        choices=("day1", "day2", "day3", "week", "demo", "report"),
        default="demo",
    )
    parser.add_argument(
        "--approve",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="人工批准/拒绝第三天离线验证计划",
    )
    parser.add_argument("--approver", default=None)
    parser.add_argument("--comment", default="")
    return parser


def _exit_code(result: dict[str, Any]) -> int:
    nested_statuses = [
        str(value.get("status", ""))
        for value in result.values()
        if isinstance(value, dict)
    ]
    if "awaiting_human_approval" in nested_statuses:
        return EXIT_AWAITING_APPROVAL
    if any(
        status == "blocked"
        or status == "validation_plan_rejected"
        or status.endswith("_blocked")
        for status in nested_statuses
    ):
        return EXIT_BLOCKED
    status = str(result.get("status", ""))
    if status == "awaiting_human_approval":
        return EXIT_AWAITING_APPROVAL
    if status in {"blocked", "validation_plan_rejected"} or status.endswith("_blocked"):
        return EXIT_BLOCKED
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        target = InvestigationTarget(
            investigation_id=args.investigation_id,
            objective=args.objective,
            product=args.product,
            line=args.line,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        if args.stage == "demo":
            result = LongHorizonYieldInvestigation.run_three_day_demo(
                args.data_root,
                args.state_dir,
                target,
                approver=args.approver or "",
                approved=args.approve,
                comment=args.comment,
            )
        else:
            investigation = LongHorizonYieldInvestigation(
                args.data_root, args.state_dir, target
            )
            if args.stage == "day1":
                result = investigation.day_1_scope()
            elif args.stage == "day2":
                result = investigation.day_2_hypotheses()
            elif args.stage == "day3":
                result = investigation.day_3_validation_plan(
                    approver=args.approver,
                    approved=args.approve,
                    comment=args.comment,
                )
            elif args.stage == "week":
                result = investigation.week_later_review()
            else:
                result = investigation.get_report()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return _exit_code(result)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"status": "error", "message": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
