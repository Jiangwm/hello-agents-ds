from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import assert_never

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class DomainValidationError(ValueError):
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        frozen=True,
    )


@unique
class TodoStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"


@unique
class TodoOutcome(StrEnum):
    EVIDENCE = "evidence"
    NEGATIVE_RESULT = "negative_result"
    MISSING_DATA = "missing_data"
    CONFLICT = "conflict"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


@unique
class GateName(StrEnum):
    RESEARCH_PLAN = "research_plan"
    CROSS_DOMAIN_ACCESS = "cross_domain_access"
    BUDGET_EXTENSION = "budget_extension"
    VALIDATION_EXPERIMENT = "validation_experiment"
    FINAL_CONCLUSION = "final_conclusion"


@unique
class GateStatus(StrEnum):
    PENDING = "pending"
    AWAITING_HUMAN = "awaiting_human"
    APPROVED = "approved"
    REJECTED = "rejected"


def allowed_todo_statuses(status: TodoStatus) -> frozenset[TodoStatus]:
    match status:
        case TodoStatus.PENDING:
            return frozenset(
                {TodoStatus.RUNNING, TodoStatus.BLOCKED, TodoStatus.AWAITING_HUMAN}
            )
        case TodoStatus.RUNNING:
            return frozenset(
                {TodoStatus.BLOCKED, TodoStatus.AWAITING_HUMAN, TodoStatus.COMPLETED}
            )
        case TodoStatus.BLOCKED:
            return frozenset({TodoStatus.RUNNING, TodoStatus.AWAITING_HUMAN})
        case TodoStatus.AWAITING_HUMAN:
            return frozenset({TodoStatus.RUNNING, TodoStatus.BLOCKED})
        case TodoStatus.COMPLETED:
            return frozenset()
        case unreachable:
            assert_never(unreachable)


def allowed_gate_statuses(status: GateStatus) -> frozenset[GateStatus]:
    match status:
        case GateStatus.PENDING:
            return frozenset({GateStatus.AWAITING_HUMAN})
        case GateStatus.AWAITING_HUMAN:
            return frozenset({GateStatus.APPROVED, GateStatus.REJECTED})
        case GateStatus.APPROVED | GateStatus.REJECTED:
            return frozenset()
        case unreachable:
            assert_never(unreachable)
