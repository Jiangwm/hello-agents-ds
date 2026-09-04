from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from backend.agents.dispatch import InvestigationContext
from backend.agents.orchestrator_execution import ExecutionMixin
from backend.agents.orchestrator_review import ReviewMixin
from backend.agents.orchestrator_state import OrchestratorError, OrchestratorState
from backend.models import Gate, GateName, ResearchRun, TodoItem
from backend.services.authorization import ActorContext, AuthorizationService
from backend.services.budget import BudgetService
from backend.services.events import EventService, EventType
from backend.services.gates import GateService
from backend.services.planner import PlanningService
from backend.services.repository import JsonValue, Repository
from backend.services.synthesis import SynthesisService
from backend.tools.workspace import ReadOnlyWorkspace


class ResearchOrchestrator(ExecutionMixin, ReviewMixin):
    def __init__(
        self,
        repository: Repository,
        workspace: ReadOnlyWorkspace,
        actor: ActorContext,
        context: InvestigationContext,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if actor.tenant_id != context.tenant_id or actor.line_id != context.line_id:
            raise OrchestratorError(
                "orchestrator.actor_scope", "actor is outside investigation scope"
            )
        self._repository = repository
        self._workspace = workspace
        self._actor = actor
        self._context = context
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._planner = PlanningService()
        self._gates = GateService(repository)
        self._events = EventService(repository)
        self._auth = AuthorizationService(repository, self._clock)
        self._budget = BudgetService()
        self._synthesis = SynthesisService()
        self._state = OrchestratorState(repository, workspace, actor, context)

    def _emit(
        self, run_id: str, event_type: EventType, payload: dict[str, JsonValue]
    ) -> None:
        self._events.emit(run_id, event_type, payload)

    @staticmethod
    def _replace_todo(run: ResearchRun, updated: TodoItem) -> ResearchRun:
        return run.model_copy(
            update={
                "todos": tuple(
                    updated if item.todo_id == updated.todo_id else item
                    for item in run.todos
                )
            }
        )

    @staticmethod
    def _with_gate(run: ResearchRun, updated: Gate) -> ResearchRun:
        return run.model_copy(
            update={
                "gates": tuple(
                    updated if item.name is updated.name else item for item in run.gates
                )
            }
        )

    @staticmethod
    def _gate(run: ResearchRun, name: GateName) -> Gate:
        return next(item for item in run.gates if item.name is name)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise OrchestratorError(
                "orchestrator.naive_clock", "orchestrator clock must be timezone aware"
            )
        return now


__all__ = ["InvestigationContext", "OrchestratorError", "ResearchOrchestrator"]
