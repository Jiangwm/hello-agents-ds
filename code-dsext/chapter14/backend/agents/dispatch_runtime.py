from __future__ import annotations

from typing import assert_never

from backend.agents.dispatch_contracts import (
    DispatchInput,
    DispatchInputError,
    DispatchResult,
    access_request,
)
from backend.models import EvidenceStatus, NoteType, TodoOutcome
from backend.services.authorization import (
    ActorContext,
    AwaitingCrossDomainApproval,
    DeniedAccess,
)
from backend.tools.documents import DocumentSearchResult, DocumentSearchTool
from backend.tools.operations import DomainFinding, OperationsTools
from backend.tools.quality import (
    ControlComparison,
    MissingDataFinding,
    NegativeResultFinding,
    OnsetFinding,
    ProcessDriftFinding,
    QualityAnalysisTool,
    QualityQuery,
    QualityToolResult,
)


class ToolDispatcher:
    def __init__(
        self,
        actor: ActorContext,
        quality: QualityAnalysisTool,
        operations: OperationsTools,
        documents: DocumentSearchTool,
    ) -> None:
        self._actor = actor
        self._quality = quality
        self._operations = operations
        self._documents = documents

    def execute(self, item: DispatchInput) -> DispatchResult:
        if item.todo_key in {"anomaly_scope", "normal_control", "process_drift"}:
            return self._quality_result(item)
        if item.todo_key in {"equipment_maintenance", "material_change"}:
            return self._operations_result(item)
        if item.todo_key == "historical_cases":
            return self._document_result(item)
        if item.todo_key == "root_cause_validation":
            raise DispatchInputError(
                "dispatch.synthesis_owned", "synthesis is orchestrator-owned"
            )
        raise DispatchInputError("dispatch.unknown_todo", item.todo_key)

    def _quality_result(self, item: DispatchInput) -> DispatchResult:
        query = QualityQuery(
            item.tenant_id,
            item.line_id,
            item.product,
            item.start,
            item.end,
            item.retrieved_at,
            item.tool_call_id,
            f"{item.tool_call_id}:evidence",
        )
        match item.todo_key:
            case "anomaly_scope":
                result = self._quality.detect_onset(query)
            case "normal_control":
                result = self._quality.compare_control(query)
            case "process_drift":
                result = self._quality.analyze_process_drift(query)
            case unreachable:
                assert_never(unreachable)
        return quality_result(result)

    def _operations_result(self, item: DispatchInput) -> DispatchResult:
        result = self._operations.search(
            self._actor,
            access_request(item, "equipment"),
            access_request(item, "material"),
            batch_id=item.batch_id,
            start_at=item.start.isoformat(),
            end_at=item.end.isoformat(),
            tool_call_id=item.tool_call_id,
        )
        match result:
            case AwaitingCrossDomainApproval():
                return DispatchResult(None, (), None, None, awaiting=result)
            case DeniedAccess():
                return DispatchResult(
                    None, (), None, None, "permission denied", denied=True
                )
            case DomainFinding():
                return operations_result(item.todo_key, result)
            case unreachable:
                assert_never(unreachable)

    def _document_result(self, item: DispatchInput) -> DispatchResult:
        result = self._documents.search(
            self._actor,
            access_request(item, "internal_docs"),
            query=item.product,
            tool_call_id=item.tool_call_id,
        )
        match result:
            case AwaitingCrossDomainApproval():
                return DispatchResult(None, (), None, None, awaiting=result)
            case DeniedAccess():
                return DispatchResult(
                    None, (), None, None, "permission denied", denied=True
                )
            case DocumentSearchResult(no_result=True):
                return DispatchResult(
                    TodoOutcome.NEGATIVE_RESULT,
                    result.evidence,
                    NoteType.NEGATIVE_RESULT,
                    "历史案例检索无结果",
                )
            case DocumentSearchResult():
                body = "; ".join(hit.canonical_reference for hit in result.hits)
                return DispatchResult(
                    TodoOutcome.EVIDENCE, result.evidence, NoteType.FACT, body
                )
            case unreachable:
                assert_never(unreachable)


def quality_result(result: QualityToolResult) -> DispatchResult:
    match result.finding:
        case MissingDataFinding(resource=resource, missing_columns=columns, reason=reason):
            evidence = tuple(
                item.model_copy(update={"status": EvidenceStatus.MISSING_DATA})
                for item in result.evidence
            )
            detail = f"missing data: {resource}; columns={','.join(columns) or 'unknown'}; {reason}"
            return DispatchResult(None, evidence, None, None, detail)
        case NegativeResultFinding(resource=resource, reason=reason):
            return DispatchResult(
                TodoOutcome.NEGATIVE_RESULT,
                result.evidence,
                NoteType.NEGATIVE_RESULT,
                f"{resource}: {reason}",
            )
        case OnsetFinding() | ControlComparison() | ProcessDriftFinding() as finding:
            return DispatchResult(
                TodoOutcome.EVIDENCE,
                result.evidence,
                NoteType.FACT,
                f"确定性工具结果：{finding!r}",
            )
        case unreachable:
            assert_never(unreachable)


def operations_result(todo_key: str, finding: DomainFinding) -> DispatchResult:
    if todo_key == "equipment_maintenance":
        evidence = finding.evidence[:2]
        present = bool(finding.equipment_events or finding.maintenance_events)
    else:
        evidence = finding.evidence[2:]
        present = bool(finding.material_lots)
    if finding.conflict and todo_key == "equipment_maintenance":
        conflict = tuple(
            item.model_copy(update={"status": EvidenceStatus.CONFLICT}) for item in evidence
        )
        return DispatchResult(
            TodoOutcome.CONFLICT,
            conflict,
            NoteType.CONFLICT,
            "设备事件与维护记录存在关联冲突",
        )
    if not present:
        return DispatchResult(
            TodoOutcome.NEGATIVE_RESULT,
            evidence,
            NoteType.NEGATIVE_RESULT,
            f"{todo_key} 范围内无结果",
        )
    return DispatchResult(
        TodoOutcome.EVIDENCE,
        evidence,
        NoteType.FACT,
        f"只记录关联因素：{todo_key}; correlation_only=true",
    )
