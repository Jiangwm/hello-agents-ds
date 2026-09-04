from __future__ import annotations

from backend.agents.dispatch import DispatchCoordinator, build_dispatch_input
from backend.agents.orchestrator_state import OrchestratorError
from backend.models import (
    Budget,
    GateName,
    ResearchRun,
    ResearchRunStatus,
    ResearchScope,
    TodoItem,
    TodoStatus,
)
from backend.services.authorization import ActorContext, AllowedAccess
from backend.services.budget import BudgetCharge, BudgetExhausted, UpdatedBudget
from backend.services.evidence import EvidenceLedger


class ExecutionMixin:
    def start(self, run_id: str, scope: ResearchScope, budget: Budget) -> ResearchRun:
        if not run_id.strip():
            raise OrchestratorError("orchestrator.run_id", "run_id must not be blank")
        plan_version, scope_version = "plan-v1", "scope-v1"
        todos = self._planner.plan(f"{run_id}:{scope_version}", scope, budget)
        run = ResearchRun(
            run_id=run_id,
            scope=scope,
            status=ResearchRunStatus.PLANNED,
            plan_version=plan_version,
            scope_version=scope_version,
            todos=todos,
            budget=budget,
            gates=self._state.placeholders(plan_version, scope_version),
        )
        record = self._repository.create_run(run_id, run.model_dump(mode="json"))
        self._state.persist_records(run)
        run = self._with_gate(
            run, self._gates.request(self._state.gate_request(run, GateName.RESEARCH_PLAN))
        )
        run = self._state.save(record.version, run)
        self._emit(run_id, "todo_list", {"count": len(todos)})
        self._emit(
            run_id,
            "gate",
            {
                "name": GateName.RESEARCH_PLAN.value,
                "gate_id": self._gate(run, GateName.RESEARCH_PLAN).gate_id,
            },
        )
        return run

    def get(self, run_id: str) -> ResearchRun:
        return self._state.load(run_id).model

    def step(self, run_id: str) -> ResearchRun:
        loaded = self._state.load(run_id)
        run = loaded.model
        if run.status is not ResearchRunStatus.RUNNING or run.paused:
            return run
        ready = self._planner.ready_todos(run.todos)
        if not ready:
            return run
        todo = ready[0]
        key = todo.todo_id.rsplit(":", 1)[-1]
        if key == "root_cause_validation":
            return self._synthesize(loaded.version, run, todo)
        if run.budget is None:
            raise OrchestratorError(
                "orchestrator.missing_budget", "run budget is required"
            )
        reserved = self._budget.reserve(run.budget, BudgetCharge(1, 1, 1.0, 1))
        if isinstance(reserved, BudgetExhausted):
            return self._block_for_budget(loaded.version, run, todo)
        if not isinstance(reserved, UpdatedBudget):
            raise OrchestratorError(
                "orchestrator.budget_result", "unknown budget result"
            )
        now = self._now()
        tool_call_id = f"{todo.todo_id}:call:{reserved.budget.consumed_tool_calls}"
        run = run.model_copy(update={"budget": reserved.budget, "current_tool": key})
        self._emit(
            run_id, "budget", {"status": "reserved", "tool_call_id": tool_call_id}
        )
        self._emit(
            run_id, "tool_call", {"tool_call_id": tool_call_id, "todo_id": todo.todo_id}
        )
        coordinator = DispatchCoordinator(
            self._repository,
            self._workspace,
            self._events,
            self._actor,
            self._clock,
            run_id,
        )
        item = build_dispatch_input(
            run, self._context, key, self._state.scope_hash(run), tool_call_id, now
        )
        return self._state.save(
            loaded.version, coordinator.apply(run, todo, coordinator.execute(item), now)
        )

    def run_until_stop(self, run_id: str, max_steps: int = 100) -> ResearchRun:
        if max_steps < 1:
            raise OrchestratorError(
                "orchestrator.max_steps", "max_steps must be positive"
            )
        run = self.get(run_id)
        for _ in range(max_steps):
            updated = self.step(run_id)
            if updated.model_dump(mode="json") == run.model_dump(mode="json"):
                return updated
            run = updated
            if run.status is not ResearchRunStatus.RUNNING or any(
                item.status is TodoStatus.AWAITING_HUMAN for item in run.todos
            ):
                return run
        return run

    def pause(self, run_id: str, actor: ActorContext | str) -> ResearchRun:
        loaded = self._state.load(run_id)
        self._state.actor_id(actor)
        if loaded.model.status is not ResearchRunStatus.RUNNING:
            return loaded.model
        run = loaded.model.model_copy(
            update={"status": ResearchRunStatus.PAUSED, "paused": True}
        )
        self._emit(run_id, "status", {"status": "paused"})
        return self._state.save(loaded.version, run)

    def resume(self, run_id: str, actor: ActorContext | str) -> ResearchRun:
        loaded = self._state.load(run_id)
        self._state.actor_id(actor)
        if loaded.model.status is not ResearchRunStatus.PAUSED:
            return loaded.model
        self._workspace = type(self._workspace)(self._workspace.root)
        self._state.workspace = self._workspace
        self._state.notes(run_id).list_approved_valid(loaded.model.scope_version)
        ledger = EvidenceLedger(self._repository, self._workspace, run_id)
        if any(ledger.revalidate(item).status != "valid" for item in loaded.model.evidence):
            raise OrchestratorError(
                "orchestrator.stale_evidence", "evidence hash is stale"
            )
        for gate in (item for item in loaded.model.gates if item.status.value == "approved"):
            if gate.name is GateName.CROSS_DOMAIN_ACCESS:
                decision = self._auth.authorize(
                    self._actor, self._state.cross_request(run_id, gate.gate_id)
                )
                if not isinstance(decision, AllowedAccess):
                    raise OrchestratorError(
                        "orchestrator.stale_gate", "cross-domain approval is stale"
                    )
            elif not self._state.approval_current(run_id, gate):
                raise OrchestratorError(
                    "orchestrator.stale_gate", "gate approval is stale"
                )
        run = loaded.model.model_copy(
            update={"status": ResearchRunStatus.RUNNING, "paused": False}
        )
        self._emit(run_id, "status", {"status": "running"})
        return self._state.save(loaded.version, run)

    def _block_for_budget(
        self, version: int, run: ResearchRun, todo: TodoItem
    ) -> ResearchRun:
        blocked = todo.model_copy(
            update={"status": TodoStatus.BLOCKED, "failure_reason": "budget exhausted"}
        )
        run = self._replace_todo(run, blocked).model_copy(
            update={
                "status": ResearchRunStatus.BLOCKED,
                "blocked_reason": "budget exhausted",
            }
        )
        run = self._with_gate(
            run,
            self._gates.request(
                self._state.gate_request(run, GateName.BUDGET_EXTENSION)
            ),
        )
        self._emit(run.run_id, "budget", {"status": "exhausted"})
        self._emit(
            run.run_id,
            "task_status",
            {"todo_id": todo.todo_id, "status": "blocked"},
        )
        return self._state.save(version, run)
