from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.models import EvidenceRef, NoteType, ResearchRun, TodoOutcome
from backend.services.authorization import (
    AccessRequest,
    AwaitingCrossDomainApproval,
    Domain,
)


TOOL_ADAPTER = {
    "query_quality_window": "detect_onset",
    "summarize_anomaly_scope": "detect_onset",
    "select_normal_control": "compare_control",
    "compare_windows": "compare_control",
    "query_process_timeseries": "analyze_process_drift",
    "detect_process_drift": "analyze_process_drift",
    "query_equipment_events": "operations.search",
    "query_maintenance_events": "operations.search",
    "query_material_lots": "operations.search",
    "compare_material_cohorts": "operations.search",
    "search_local_documents": "documents.search",
    "extract_case_evidence": "documents.search",
    "validate_candidate_causes": "synthesis.synthesize",
    "assemble_evidence_chain": "synthesis.synthesize",
}


@dataclass(frozen=True, slots=True)
class InvestigationContext:
    tenant_id: str
    line_id: str
    product: str
    batch_id: str
    start_at: str
    end_at: str


@dataclass(frozen=True, slots=True)
class DispatchInput:
    run_id: str
    todo_key: str
    tenant_id: str
    line_id: str
    product: str
    batch_id: str
    start: datetime
    end: datetime
    plan_version: str
    scope_version: str
    scope_hash: str
    tool_call_id: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class DispatchResult:
    outcome: TodoOutcome | None
    evidence: tuple[EvidenceRef, ...]
    note_type: NoteType | None
    body: str | None
    failure_reason: str | None = None
    awaiting: AwaitingCrossDomainApproval | None = None
    denied: bool = False


@dataclass(frozen=True, slots=True)
class DispatchInputError(ValueError):
    code: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)


def build_dispatch_input(
    run: ResearchRun,
    context: InvestigationContext,
    key: str,
    scope_hash: str,
    call_id: str,
    now: datetime,
) -> DispatchInput:
    start = datetime.fromisoformat(context.start_at)
    end = datetime.fromisoformat(context.end_at)
    if start.tzinfo is None or end.tzinfo is None:
        raise DispatchInputError("dispatch.naive_time", "context times need timezone")
    return DispatchInput(
        run.run_id,
        key,
        context.tenant_id,
        context.line_id,
        context.product,
        context.batch_id,
        start,
        end,
        run.plan_version,
        run.scope_version,
        scope_hash,
        call_id,
        now,
    )


def access_request(item: DispatchInput, domain: Domain) -> AccessRequest:
    return AccessRequest(
        item.run_id,
        item.tenant_id,
        item.line_id,
        domain,
        item.scope_hash,
        item.plan_version,
        item.scope_version,
    )
