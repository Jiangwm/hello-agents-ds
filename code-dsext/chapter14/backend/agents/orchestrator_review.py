from __future__ import annotations

from datetime import datetime, timedelta
from typing import assert_never

from backend.agents.orchestrator_state import OrchestratorError
from backend.models import (
    GateName,
    GateStatus,
    NoteStatus,
    ResearchRun,
    ResearchRunStatus,
    ResearchScope,
    TodoItem,
    TodoOutcome,
    TodoStatus,
)
from backend.services.authorization import ActorContext
from backend.services.synthesis import SynthesisOutcome


class ReviewMixin:
    def revise_scope(
        self,
        run_id: str,
        new_scope: ResearchScope,
        actor: ActorContext | str,
    ) -> ResearchRun:
        loaded = self._state.load(run_id)
        actor_id = self._state.actor_id(actor)
        old = loaded.model
        next_number = self._state.version_number(old.scope_version) + 1
        scope_version, plan_version = f"scope-v{next_number}", f"plan-v{next_number}"
        for gate in old.gates:
            self._gates.invalidate(
                run_id, gate.gate_id, actor_id, "scope_version_changed"
            )
        self._state.supersede_notes(run_id, scope_version, actor_id)
        if old.budget is None:
            raise OrchestratorError(
                "orchestrator.missing_budget", "run budget is required"
            )
        todos = self._planner.plan(
            f"{run_id}:{scope_version}", new_scope, old.budget
        )
        run = old.model_copy(
            update={
                "scope": new_scope,
                "scope_version": scope_version,
                "plan_version": plan_version,
                "todos": todos,
                "notes": (),
                "status": ResearchRunStatus.PLANNED,
                "paused": False,
                "blocked_reason": None,
                "current_tool": None,
                "gates": self._state.placeholders(plan_version, scope_version),
                "report_markdown": None,
            }
        )
        self._state.persist_records(run)
        run = self._with_gate(
            run, self._gates.request(self._state.gate_request(run, GateName.RESEARCH_PLAN))
        )
        self._emit(
            run_id,
            "status",
            {"status": "scope_revised", "scope_version": scope_version},
        )
        return self._state.save(loaded.version, run)

    def approve_gate(
        self,
        run_id: str,
        gate_id: str,
        approver: ActorContext,
        reason: str,
        expires_at: datetime | None = None,
    ) -> ResearchRun:
        loaded = self._state.load(run_id)
        run = loaded.model
        gate = next((item for item in run.gates if item.gate_id == gate_id), None)
        if gate is None or gate.status is not GateStatus.AWAITING_HUMAN:
            raise OrchestratorError(
                "orchestrator.gate_unavailable", "gate is not currently approvable"
            )
        if gate.name is GateName.CROSS_DOMAIN_ACCESS:
            request = self._state.cross_request(run_id, gate_id)
            expiry = expires_at or self._now() + timedelta(hours=1)
            self._auth.approve_cross_domain(
                self._actor, request, gate_id, approver, reason, expiry
            )
        else:
            self._gates.approve(run_id, gate_id, approver.actor_id, reason)
        run = self._with_gate(run, gate.model_copy(update={"status": GateStatus.APPROVED}))
        match gate.name:
            case GateName.RESEARCH_PLAN:
                run = run.model_copy(update={"status": ResearchRunStatus.RUNNING})
            case GateName.CROSS_DOMAIN_ACCESS:
                run = run.model_copy(
                    update={
                        "todos": tuple(
                            item.model_copy(update={"status": TodoStatus.PENDING})
                            if item.status is TodoStatus.AWAITING_HUMAN
                            else item
                            for item in run.todos
                        )
                    }
                )
            case GateName.FINAL_CONCLUSION:
                run = run.model_copy(update={"status": ResearchRunStatus.COMPLETED})
            case GateName.BUDGET_EXTENSION | GateName.VALIDATION_EXPERIMENT:
                pass
            case unreachable:
                assert_never(unreachable)
        self._emit(run_id, "gate", {"gate_id": gate_id, "status": "approved"})
        return self._state.save(loaded.version, run)

    def review_notes(
        self, run_id: str, reviewer: ActorContext | str, reason: str
    ) -> ResearchRun:
        loaded = self._state.load(run_id)
        reviewer_id = self._state.actor_id(reviewer)
        service = self._state.notes(run_id)
        for note in service.list_current():
            if note.status is NoteStatus.DRAFT:
                service.approve(
                    note.note_id,
                    reviewer=reviewer_id,
                    reason=reason,
                    scope_version=loaded.model.scope_version,
                )
                self._emit(
                    run_id, "note", {"note_id": note.note_id, "status": "approved"}
                )
        run = loaded.model.model_copy(update={"notes": service.list_current()})
        return self._state.save(loaded.version, run)

    def review_note(
        self, run_id: str, note_id: str, approved: bool,
        reviewer: ActorContext | str, reason: str,
    ) -> ResearchRun:
        loaded = self._state.load(run_id)
        reviewer_id = self._state.actor_id(reviewer)
        service = self._state.notes(run_id)
        if approved:
            service.approve(
                note_id, reviewer_id, reason, loaded.model.scope_version
            )
            status = NoteStatus.APPROVED
        else:
            service.reject(note_id, reviewer_id, reason)
            status = NoteStatus.REJECTED
        self._emit(
            run_id, "note", {"note_id": note_id, "status": status.value}
        )
        run = loaded.model.model_copy(update={"notes": service.list_current()})
        return self._state.save(loaded.version, run)

    def _synthesize(self, version: int, run: ResearchRun, todo: TodoItem) -> ResearchRun:
        gate = self._gate(run, GateName.VALIDATION_EXPERIMENT)
        if gate.status is GateStatus.PENDING:
            run = self._with_gate(
                run,
                self._gates.request(
                    self._state.gate_request(run, GateName.VALIDATION_EXPERIMENT)
                ),
            )
            self._emit(
                run.run_id,
                "gate",
                {
                    "name": GateName.VALIDATION_EXPERIMENT.value,
                    "status": "awaiting_human",
                },
            )
            return self._state.save(version, run)
        if gate.status is not GateStatus.APPROVED:
            return run
        notes = self._state.notes(run.run_id)
        if any(item.status is NoteStatus.DRAFT for item in notes.list_current()):
            return run
        result = self._synthesis.synthesize(
            notes.list_approved_valid(run.scope_version),
            run.evidence,
            scope_version=run.scope_version,
        )
        if result.outcome is not SynthesisOutcome.CANDIDATE:
            reason = result.blocked_reason or "approved evidence is insufficient"
            blocked = todo.model_copy(
                update={"status": TodoStatus.BLOCKED, "failure_reason": reason}
            )
            run = self._replace_todo(run, blocked).model_copy(
                update={"status": ResearchRunStatus.BLOCKED, "blocked_reason": reason}
            )
            self._emit(
                run.run_id, "blocked", {"todo_id": todo.todo_id, "reason": reason}
            )
            return self._state.save(version, run)
        candidate = result.candidates[0]
        now = self._now()
        completed = todo.model_copy(
            update={
                "status": TodoStatus.COMPLETED,
                "outcome": TodoOutcome.EVIDENCE,
                "started_at": now,
                "completed_at": now,
                "candidate_conclusion": candidate.claim,
                "confidence": candidate.confidence,
            }
        )
        run = self._replace_todo(run, completed)
        run = self._with_gate(
            run,
            self._gates.request(
                self._state.gate_request(run, GateName.FINAL_CONCLUSION)
            ),
        )
        self._emit(
            run.run_id,
            "gate",
            {"name": GateName.FINAL_CONCLUSION.value, "status": "awaiting_human"},
        )
        return self._state.save(version, run)
