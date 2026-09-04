from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from backend.agents.orchestrator import InvestigationContext, ResearchOrchestrator
from backend.cli_contracts import CliError, CliState, JsonObject, WorkspaceIdentity
from backend.models import (
    EvidenceStatus, GateName, GateStatus, NoteStatus, ResearchRun,
    ResearchRunStatus, TodoStatus,
)
from backend.services.authorization import ActorContext, Domain
from backend.services.evidence import EvidenceLedger
from backend.services.export import ExportReceipt
from backend.services.gates import GateRequest, GateService
from backend.services.reporting import ResearchReport, ReportingService, report_payload
from backend.services.repository import Repository
from backend.services.synthesis import SynthesisService
from backend.tools.workspace import ReadOnlyWorkspace


ANALYST_DOMAINS: frozenset[Domain] = frozenset(
    ("quality", "process", "internal_docs")
)
REVIEW_DOMAINS: frozenset[Domain] = frozenset(
    ("quality", "process", "equipment", "material",
     "internal_docs", "external_knowledge")
)


@dataclass(frozen=True, slots=True)
class Runtime:
    state_dir: Path
    state: CliState
    repository: Repository
    workspace: ReadOnlyWorkspace
    actor: ActorContext
    orchestrator: ResearchOrchestrator


@dataclass(frozen=True, slots=True)
class ReportBundle:
    run: ResearchRun
    report: ResearchReport
    request: GateRequest


@dataclass(frozen=True, slots=True)
class OutputExtras:
    report: ResearchReport | None = None
    receipt: ExportReceipt | None = None


def new_state(args: argparse.Namespace) -> CliState:
    return CliState(
        workspace=Path(args.workspace).resolve(), actor_id=args.actor,
        tenant_id=args.tenant, line_id=args.line, product=args.product,
        batch_id=args.batch_id, start_at=args.start_at, end_at=args.end_at,
    )


def open_runtime(args: argparse.Namespace, *, create: bool) -> Runtime:
    state_dir = Path(args.state_dir).resolve()
    state_path = state_dir / f"{args.run_id}.context.json"
    state = new_state(args) if create else CliState.model_validate_json(
        state_path.read_text(encoding="utf-8")
    )
    identity = WorkspaceIdentity.model_validate_json(
        (state.workspace / "manifest.json").read_text(encoding="utf-8")
    )
    if identity.tenant_id != state.tenant_id or identity.line_id != state.line_id:
        raise CliError("permission_denied", "actor is outside workspace scope", 3)
    workspace = ReadOnlyWorkspace(state.workspace)
    repository = Repository(state_dir / "research.sqlite3", workspace.root)
    repository.initialize()
    actor = ActorContext(
        state.actor_id, state.tenant_id, state.line_id, "analyst", ANALYST_DOMAINS
    )
    context = InvestigationContext(
        state.tenant_id, state.line_id, state.product, state.batch_id,
        state.start_at.isoformat(), state.end_at.isoformat(),
    )
    orchestrator = ResearchOrchestrator(repository, workspace, actor, context)
    return Runtime(state_dir, state, repository, workspace, actor, orchestrator)


def save_context(current: Runtime, run_id: str) -> None:
    path = current.state_dir / f"{run_id}.context.json"
    path.write_text(current.state.model_dump_json(indent=2), encoding="utf-8")


def save_run(current: Runtime, run: ResearchRun) -> ResearchRun:
    record = current.repository.get_run(run.run_id)
    if record is None:
        raise CliError("run_missing", "run does not exist", 4)
    current.repository.update_run(
        run.run_id, run.model_dump(mode="json"), expected_version=record.version
    )
    return run


def report_bundle(current: Runtime, run: ResearchRun) -> ReportBundle:
    approved = tuple(note for note in run.notes if note.status is NoteStatus.APPROVED)
    synthesis = SynthesisService().synthesize(
        approved, run.evidence, scope_version=run.scope_version
    )
    ledger = EvidenceLedger(current.repository, current.workspace, run.run_id)
    reporting = ReportingService(current.repository, ledger)
    report = reporting.generate(run, approved, synthesis)
    request = reporting.final_gate_request(run, report, current.actor.actor_id)
    gates = GateService(current.repository)
    replacement = gates.request(request)
    previous = next(item for item in run.gates if item.name is GateName.FINAL_CONCLUSION)
    if previous.gate_id != replacement.gate_id:
        gates.invalidate(
            run.run_id, previous.gate_id, current.actor.actor_id, "report_hash_bound"
        )
    updated = run.model_copy(
        update={
            "gates": tuple(
                replacement if item.name is GateName.FINAL_CONCLUSION else item
                for item in run.gates
            ),
            "report_markdown": report.markdown,
        }
    )
    return ReportBundle(save_run(current, updated), report, request)


def human(current: Runtime, requested: str) -> ActorContext:
    actor_id = requested if requested != current.actor.actor_id else f"{requested}-independent"
    return ActorContext(
        actor_id, current.state.tenant_id, current.state.line_id, "reviewer", REVIEW_DOMAINS
    )


def approve(current: Runtime, args: argparse.Namespace) -> ResearchRun:
    if args.gate is None:
        raise CliError("invalid_input", "--gate is required", 4)
    name = GateName(args.gate)
    run = current.orchestrator.get(args.run_id)
    if name is GateName.FINAL_CONCLUSION:
        run = report_bundle(current, run).run
    gate = next(
        (item for item in run.gates
         if item.name is name and item.status is GateStatus.AWAITING_HUMAN),
        None,
    )
    if gate is None:
        raise CliError("gate_unavailable", "gate is not awaiting human review", 2)
    decided = current.orchestrator.approve_gate(
        run.run_id, gate.gate_id, human(current, args.approver), args.reason
    )
    if name is not GateName.BUDGET_EXTENSION or decided.budget is None:
        return decided
    budget = decided.budget.model_copy(
        update={
            "max_tool_calls": max(decided.budget.max_tool_calls, args.max_tool_calls),
            "max_cost": max(decided.budget.max_cost, args.max_cost),
            "max_rows_scanned": max(decided.budget.max_rows_scanned, args.max_rows_scanned),
            "max_elapsed_ms": max(decided.budget.max_elapsed_ms, args.max_elapsed_ms),
        }
    )
    reopened = decided.model_copy(
        update={
            "status": ResearchRunStatus.RUNNING, "blocked_reason": None, "budget": budget,
            "todos": tuple(
                item.model_copy(update={"status": TodoStatus.PENDING, "failure_reason": None})
                if item.status is TodoStatus.BLOCKED else item for item in decided.todos
            ),
        }
    )
    return save_run(current, reopened)


def outcome(run: ResearchRun) -> str:
    statuses = {item.status for item in run.evidence}
    if EvidenceStatus.MISSING_DATA in statuses:
        return "missing_data"
    if EvidenceStatus.CONFLICT in statuses:
        return "conflict"
    if run.blocked_reason and "budget" in run.blocked_reason:
        return "budget_exhausted"
    if run.status is ResearchRunStatus.COMPLETED:
        return "completed"
    return "blocked" if run.status is ResearchRunStatus.BLOCKED else "awaiting_human"


def summary(
    current: Runtime, run: ResearchRun, extras: OutputExtras = OutputExtras()
) -> JsonObject:
    gate = next((item.name.value for item in run.gates
                 if item.status is GateStatus.AWAITING_HUMAN), None)
    payload: JsonObject = {
        "run_id": run.run_id, "status": run.status.value, "outcome": outcome(run),
        "scope_version": run.scope_version, "plan_version": run.plan_version,
        "gate": gate, "todos": [item.model_dump(mode="json") for item in run.todos],
        "evidence": [item.model_dump(mode="json") for item in run.evidence],
        "notes": [item.model_dump(mode="json") for item in run.notes],
        "approved_notes": [item.note_id for item in run.notes
                           if item.status is NoteStatus.APPROVED],
        "conflicts": [item.evidence_id for item in run.evidence
                      if item.status is EvidenceStatus.CONFLICT],
        "tool_calls": sum(item.event_type == "tool_call"
                          for item in current.repository.list_events(run.run_id)),
        "export": None,
    }
    if extras.report is not None:
        payload["report"] = report_payload(extras.report)
    if extras.receipt is not None:
        payload["export"] = {
            "destination": str(extras.receipt.destination),
            "manifest_sha256": extras.receipt.manifest_sha256,
            "bytes_written": extras.receipt.bytes_written,
        }
    return payload


def exit_for(run: ResearchRun) -> int:
    if run.status is ResearchRunStatus.BLOCKED:
        return 3
    if any(item.status is GateStatus.AWAITING_HUMAN for item in run.gates):
        return 2
    return 0


__all__ = (
    "OutputExtras", "Runtime", "approve", "exit_for", "human", "open_runtime",
    "report_bundle", "save_context", "summary",
)
