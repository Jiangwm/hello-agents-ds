#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.10,<3"]
# ///

# ─── How to run ───
# 1. Install the chapter dependencies from backend/requirements.txt.
# 2. Run: python -X utf8 run_quality_deep_research.py demo --state-dir <STATE_DIR>
# 3. Or: uv run --script run_quality_deep_research.py demo --state-dir <STATE_DIR>
# ──────────────────

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence, assert_never

from pydantic import ValidationError

from backend.cli_contracts import CliError, Command, JsonObject, build_parser
from backend.cli_runtime import (
    OutputExtras, Runtime, approve, exit_for, human, open_runtime, report_bundle,
    save_context, summary,
)
from backend.models import (
    Budget, GateName, GateStatus, NoteStatus, ResearchRun, ResearchRunStatus,
    ResearchScope,
)
from backend.services.evidence import EvidenceLedger
from backend.services.export import ExportDeniedError, ExportService
from backend.services.gates import GateService
from backend.services.repository import canonical_json


ROOT = Path(__file__).resolve().parent


def start_run(current: Runtime, args: argparse.Namespace) -> ResearchRun:
    run = current.orchestrator.start(
        args.run_id,
        ResearchScope(
            scope_id="scope-normal", objective=args.objective,
            asset_ids=("EQ-COAT-01",),
            data_domains=("quality", "process", "equipment", "material", "documents"),
        ),
        Budget(
            max_tool_calls=args.max_tool_calls, max_cost=args.max_cost,
            max_rows_scanned=args.max_rows_scanned,
            max_elapsed_ms=args.max_elapsed_ms,
        ),
    )
    save_context(current, run.run_id)
    return run


def export_report(
    current: Runtime, args: argparse.Namespace, run: ResearchRun
) -> tuple[JsonObject, int]:
    if not args.output:
        raise CliError("invalid_input", "--output is required", 4)
    bundle = report_bundle(current, run)
    try:
        receipt = ExportService(
            current.repository,
            EvidenceLedger(current.repository, current.workspace, run.run_id),
            GateService(current.repository),
        ).export(bundle.run, bundle.report, bundle.request, args.output)
    except ExportDeniedError:
        return summary(current, bundle.run, OutputExtras(bundle.report)), 2
    return summary(
        current, bundle.run, OutputExtras(bundle.report, receipt)
    ), 0


def demo(args: argparse.Namespace) -> tuple[JsonObject, int]:
    current = open_runtime(args, create=True)
    run = start_run(current, args)
    plan = next(item for item in run.gates if item.name is GateName.RESEARCH_PLAN)
    run = current.orchestrator.approve_gate(
        run.run_id, plan.gate_id, human(current, args.plan_approver),
        "demo research policy",
    )
    for _ in range(24):
        run = current.orchestrator.run_until_stop(run.run_id)
        if run.status is ResearchRunStatus.BLOCKED:
            return summary(current, run), 3
        waiting = next((item for item in run.gates
                        if item.status is GateStatus.AWAITING_HUMAN), None)
        if waiting is not None and waiting.name in {
            GateName.CROSS_DOMAIN_ACCESS, GateName.VALIDATION_EXPERIMENT,
        }:
            run = current.orchestrator.approve_gate(
                run.run_id, waiting.gate_id, human(current, args.plan_approver),
                "demo checkpoint policy",
            )
            continue
        if any(item.status is NoteStatus.DRAFT for item in run.notes):
            run = current.orchestrator.review_notes(
                run.run_id, human(current, args.plan_approver), "demo note review"
            )
            continue
        if waiting is not None and waiting.name is GateName.FINAL_CONCLUSION:
            bundle = report_bundle(current, run)
            if not args.approve_conclusion:
                return summary(current, bundle.run, OutputExtras(bundle.report)), 2
            final_gate = next(
                item for item in bundle.run.gates
                if item.name is GateName.FINAL_CONCLUSION
            )
            run = current.orchestrator.approve_gate(
                run.run_id, final_gate.gate_id,
                human(current, args.conclusion_approver), "demo final review",
            )
            if not args.output:
                return summary(current, run, OutputExtras(bundle.report)), 0
            return export_report(current, args, run)
    raise CliError("execution_limit", "demo did not reach a stable gate", 1)


def execute(args: argparse.Namespace) -> tuple[JsonObject, int]:
    command = Command(args.command)
    if command is Command.DEMO:
        return demo(args)
    current = open_runtime(args, create=command is Command.START)
    match command:
        case Command.START:
            run = start_run(current, args)
            return summary(current, run), 2
        case Command.STATUS:
            return summary(current, current.orchestrator.get(args.run_id)), 0
        case Command.APPROVE:
            run = approve(current, args)
            return summary(current, run), exit_for(run)
        case Command.RUN:
            run = current.orchestrator.run_until_stop(args.run_id, args.max_steps)
            return summary(current, run), exit_for(run)
        case Command.PAUSE:
            run = current.orchestrator.pause(args.run_id, current.actor)
            return summary(current, run), 0
        case Command.REVISE:
            run = current.orchestrator.get(args.run_id)
            revised = current.orchestrator.revise_scope(
                run.run_id, run.scope.model_copy(update={"objective": args.objective}),
                current.actor,
            )
            return summary(current, revised), 0
        case Command.RESUME:
            run = current.orchestrator.resume(args.run_id, current.actor)
            return summary(current, run), exit_for(run)
        case Command.REVIEW_NOTES:
            run = current.orchestrator.review_notes(
                args.run_id, human(current, args.reviewer), args.reason
            )
            return summary(current, run), exit_for(run)
        case Command.REPORT:
            bundle = report_bundle(current, current.orchestrator.get(args.run_id))
            return summary(
                current, bundle.run, OutputExtras(bundle.report)
            ), exit_for(bundle.run)
        case Command.EXPORT:
            return export_report(current, args, current.orchestrator.get(args.run_id))
        case Command.DEMO:
            raise CliError("invalid_command", "demo command routing failed", 1)
        case unreachable:
            assert_never(unreachable)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser(ROOT).parse_args(argv)
    except SystemExit as error:
        if error.code == 0:
            return 0
        print(canonical_json(
            {"error": {"code": "invalid_input", "detail": "invalid arguments"}}
        ))
        return 4
    try:
        payload, exit_code = execute(args)
    except ValidationError as error:
        print(str(error), file=sys.stderr)
        print(canonical_json(
            {"error": {"code": "invalid_input", "detail": "input validation failed"}}
        ))
        return 4
    except CliError as error:
        payload = {"error": {"code": error.code, "detail": error.detail}}
        if error.code == "permission_denied":
            payload.update(
                {"status": "blocked", "outcome": "permission_denied",
                 "evidence": [], "notes": [], "export": None}
            )
        print(error.detail, file=sys.stderr)
        print(canonical_json(payload))
        return error.exit_code
    except Exception as error:  # noqa: BROAD_EXCEPT_OK
        print(str(error), file=sys.stderr)
        print(canonical_json(
            {"error": {"code": "runtime_error", "detail": type(error).__name__}}
        ))
        return 1
    print(canonical_json(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
