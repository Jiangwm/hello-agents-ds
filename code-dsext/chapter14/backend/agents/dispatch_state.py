from __future__ import annotations

from datetime import datetime
from typing import Callable

from backend.agents.dispatch_contracts import DispatchInput, DispatchInputError, DispatchResult
from backend.agents.dispatch_runtime import ToolDispatcher
from backend.models import ResearchRun, ResearchRunStatus, TodoItem, TodoOutcome, TodoStatus
from backend.services.authorization import ActorContext, AuthorizationService
from backend.services.evidence import EvidenceLedger
from backend.services.events import EventService
from backend.services.notes import NoteService
from backend.services.repository import Repository
from backend.tools.documents import DocumentSearchTool
from backend.tools.operations import OperationsTools
from backend.tools.quality import QualityAnalysisTool
from backend.tools.workspace import ReadOnlyWorkspace


class DispatchCoordinator:
    def __init__(
        self,
        repository: Repository,
        workspace: ReadOnlyWorkspace,
        events: EventService,
        actor: ActorContext,
        clock: Callable[[], datetime],
        run_id: str,
    ) -> None:
        ledger = EvidenceLedger(repository, workspace, run_id)
        authorization = AuthorizationService(repository, clock=clock)
        self._repository = repository
        self._workspace = workspace
        self._events = events
        self._run_id = run_id
        self._dispatcher = ToolDispatcher(
            actor,
            QualityAnalysisTool(workspace, ledger),
            OperationsTools(workspace, authorization, ledger, clock=clock),
            DocumentSearchTool(workspace, authorization, ledger, clock=clock),
        )

    def execute(self, item: DispatchInput) -> DispatchResult:
        return self._dispatcher.execute(item)

    def apply(
        self,
        run: ResearchRun,
        todo: TodoItem,
        result: DispatchResult,
        now: datetime,
    ) -> ResearchRun:
        if result.awaiting is not None:
            return self._apply_waiting(run, todo, result, now)
        for evidence in result.evidence:
            self._repository.replace_or_upsert(
                "evidence", run.run_id, evidence.evidence_id, evidence
            )
            self._events.emit(
                run.run_id,
                "evidence",
                {"evidence_id": evidence.evidence_id, "status": evidence.status.value},
            )
        if result.denied or result.failure_reason is not None:
            return self._apply_blocked(run, todo, result, now)
        return self._apply_completed(run, todo, result, now)

    def _apply_waiting(
        self,
        run: ResearchRun,
        todo: TodoItem,
        result: DispatchResult,
        now: datetime,
    ) -> ResearchRun:
        if result.awaiting is None:
            raise DispatchInputError("dispatch.waiting", "approval result is absent")
        waiting = todo.model_copy(
            update={"status": TodoStatus.AWAITING_HUMAN, "started_at": now}
        )
        run = replace_todo(run, waiting).model_copy(update={"current_tool": None})
        run = run.model_copy(
            update={
                "gates": tuple(
                    result.awaiting.gate if gate.name is result.awaiting.gate.name else gate
                    for gate in run.gates
                )
            }
        )
        self._events.emit(
            run.run_id,
            "gate",
            {"gate_id": result.awaiting.gate.gate_id, "status": "awaiting_human"},
        )
        self._events.emit(
            run.run_id,
            "task_status",
            {"todo_id": todo.todo_id, "status": "awaiting_human"},
        )
        return run

    def _apply_blocked(
        self,
        run: ResearchRun,
        todo: TodoItem,
        result: DispatchResult,
        now: datetime,
    ) -> ResearchRun:
        reason = result.failure_reason or "permission denied"
        blocked = todo.model_copy(
            update={
                "status": TodoStatus.BLOCKED,
                "outcome": None,
                "failure_reason": reason,
                "started_at": now,
                "evidence_ids": tuple(item.evidence_id for item in result.evidence),
            }
        )
        run = replace_todo(run, blocked).model_copy(
            update={
                "status": ResearchRunStatus.BLOCKED,
                "blocked_reason": reason,
                "current_tool": None,
                "evidence": run.evidence + result.evidence,
            }
        )
        self._events.emit(run.run_id, "blocked", {"todo_id": todo.todo_id, "reason": reason})
        self._events.emit(
            run.run_id, "task_status", {"todo_id": todo.todo_id, "status": "blocked"}
        )
        return run

    def _apply_completed(
        self,
        run: ResearchRun,
        todo: TodoItem,
        result: DispatchResult,
        now: datetime,
    ) -> ResearchRun:
        if result.outcome is None or result.note_type is None or result.body is None:
            raise DispatchInputError(
                "dispatch.incomplete_result", "completed result lacks outcome"
            )
        evidence_ids = tuple(item.evidence_id for item in result.evidence)
        completed = todo.model_copy(
            update={
                "status": TodoStatus.COMPLETED,
                "outcome": result.outcome,
                "started_at": now,
                "completed_at": now,
                "evidence_ids": evidence_ids,
                "negative_results": evidence_ids
                if result.outcome is TodoOutcome.NEGATIVE_RESULT
                else (),
            }
        )
        notes = NoteService(
            self._repository,
            EvidenceLedger(self._repository, self._workspace, self._run_id),
            self._run_id,
        )
        note = notes.create(
            note_id=f"{todo.todo_id}:note",
            note_type=result.note_type,
            todo_id=todo.todo_id,
            body=result.body,
            scope_version=run.scope_version,
            evidence_ids=evidence_ids,
        )
        run = replace_todo(run, completed).model_copy(
            update={
                "current_tool": None,
                "evidence": run.evidence + result.evidence,
                "notes": notes.list_current(),
            }
        )
        self._events.emit(run.run_id, "note", {"note_id": note.note_id, "status": "draft"})
        self._events.emit(
            run.run_id,
            "task_status",
            {"todo_id": todo.todo_id, "outcome": result.outcome.value},
        )
        if result.outcome is TodoOutcome.CONFLICT:
            self._events.emit(
                run.run_id, "conflict", {"todo_id": todo.todo_id, "status": "unresolved"}
            )
        return run


def replace_todo(run: ResearchRun, updated: TodoItem) -> ResearchRun:
    return run.model_copy(
        update={
            "todos": tuple(
                updated if item.todo_id == updated.todo_id else item for item in run.todos
            )
        }
    )
