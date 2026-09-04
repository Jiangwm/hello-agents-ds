from __future__ import annotations

from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, Self, assert_never

from pydantic import Field, model_validator

from .approval import Gate
from .common import (
    DomainValidationError,
    GateName,
    StrictModel,
    TodoOutcome,
    TodoStatus,
    allowed_todo_statuses,
)
from .evidence import EvidenceRef, ResearchNote


NonEmpty = Annotated[str, Field(min_length=1)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0.0)]


@unique
class ResearchRunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchScope(StrictModel):
    scope_id: NonEmpty
    objective: NonEmpty
    asset_ids: tuple[NonEmpty, ...] = ()
    data_domains: tuple[NonEmpty, ...] = ()


class TodoItem(StrictModel):
    todo_id: NonEmpty
    title: NonEmpty
    question: NonEmpty
    data_scope: NonEmpty
    required_tools: tuple[NonEmpty, ...]
    status: TodoStatus = TodoStatus.PENDING
    outcome: TodoOutcome | None = None
    dependencies: tuple[NonEmpty, ...] = ()
    evidence_ids: tuple[NonEmpty, ...] = ()
    negative_results: tuple[NonEmpty, ...] = ()
    source_quality: NonEmpty
    candidate_conclusion: str | None = None
    confidence: Confidence | None = None
    falsification_conditions: tuple[NonEmpty, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cost_units: NonNegativeInt = 0
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_dependencies_and_outcome(self) -> Self:
        if self.todo_id in self.dependencies:
            raise DomainValidationError("todo.self_dependency", "TODO 不能依赖自身")
        duplicate_contracts = (
            (self.dependencies, "todo.duplicate_dependency", "TODO 依赖不能重复"),
            (self.required_tools, "todo.duplicate_required_tool", "TODO 所需工具不能重复"),
            (self.evidence_ids, "todo.duplicate_evidence_id", "TODO 证据 ID 不能重复"),
            (self.negative_results, "todo.duplicate_negative_result", "TODO 负面结果不能重复"),
            (
                self.falsification_conditions,
                "todo.duplicate_falsification",
                "TODO 反证条件不能重复",
            ),
        )
        for values, code, detail in duplicate_contracts:
            if len(values) != len(set(values)):
                raise DomainValidationError(code, detail)
        if self.status is TodoStatus.COMPLETED and self.outcome is None:
            raise DomainValidationError(
                "todo.completed_without_outcome", "完成的 TODO 必须记录 outcome"
            )
        if self.status is not TodoStatus.COMPLETED and self.outcome is not None:
            raise DomainValidationError(
                "todo.outcome_before_completion", "未完成的 TODO 不能记录 outcome"
            )
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise DomainValidationError("todo.naive_started_at", "started_at 必须包含时区")
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            raise DomainValidationError("todo.naive_completed_at", "completed_at 必须包含时区")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise DomainValidationError(
                "todo.invalid_time_range", "completed_at 不能早于 started_at"
            )
        return self


class Budget(StrictModel):
    max_tool_calls: NonNegativeInt
    max_cost: NonNegativeFloat
    max_rows_scanned: NonNegativeInt
    max_elapsed_ms: NonNegativeInt
    consumed_tool_calls: NonNegativeInt = 0
    consumed_cost: NonNegativeFloat = 0.0
    consumed_rows_scanned: NonNegativeInt = 0
    consumed_elapsed_ms: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_consumption(self) -> Self:
        limits = (
            (
                self.consumed_tool_calls,
                self.max_tool_calls,
                "budget.tool_calls_exceeded",
                "工具调用预算不能超限",
            ),
            (self.consumed_cost, self.max_cost, "budget.cost_exceeded", "成本预算不能超限"),
            (
                self.consumed_rows_scanned,
                self.max_rows_scanned,
                "budget.rows_scanned_exceeded",
                "扫描行数预算不能超限",
            ),
            (
                self.consumed_elapsed_ms,
                self.max_elapsed_ms,
                "budget.elapsed_ms_exceeded",
                "耗时预算不能超限",
            ),
        )
        for consumed, maximum, code, detail in limits:
            if consumed > maximum:
                raise DomainValidationError(code, detail)
        return self


class ResearchRun(StrictModel):
    run_id: NonEmpty
    scope: ResearchScope
    status: ResearchRunStatus
    plan_version: NonEmpty
    scope_version: NonEmpty
    todos: tuple[TodoItem, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    notes: tuple[ResearchNote, ...] = ()
    budget: Budget | None = None
    gates: tuple[Gate, ...]
    paused: bool = False
    current_tool: str | None = None
    blocked_reason: str | None = None
    report_markdown: str | None = None

    @model_validator(mode="after")
    def validate_todos(self) -> Self:
        todo_ids = tuple(item.todo_id for item in self.todos)
        if len(todo_ids) != len(set(todo_ids)):
            raise DomainValidationError(
                "research.duplicate_todo_id", "ResearchRun 中 TODO ID 不能重复"
            )
        known_ids = set(todo_ids)
        if any(dependency not in known_ids for item in self.todos for dependency in item.dependencies):
            raise DomainValidationError(
                "research.unknown_dependency", "TODO 依赖必须存在于同一 ResearchRun"
            )
        note_ids = tuple(note.note_id for note in self.notes)
        if len(note_ids) != len(set(note_ids)):
            raise DomainValidationError(
                "research.duplicate_note_id", "ResearchRun 中笔记 ID 不能重复"
            )
        if any(note.todo_id not in known_ids for note in self.notes):
            raise DomainValidationError(
                "research.unknown_note_todo", "笔记必须绑定同一 ResearchRun 的 TODO"
            )
        evidence_ids = tuple(evidence.evidence_id for evidence in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise DomainValidationError(
                "research.duplicate_evidence_id", "ResearchRun 中证据 ID 不能重复"
            )
        if {gate.name for gate in self.gates} != set(GateName):
            raise DomainValidationError(
                "research.invalid_gate_set", "ResearchRun 必须包含且仅包含五个 Gate"
            )
        gate_ids = tuple(gate.gate_id for gate in self.gates)
        if len(gate_ids) != len(set(gate_ids)):
            raise DomainValidationError(
                "research.duplicate_gate_id", "ResearchRun 中 Gate ID 不能重复"
            )
        if any(
            gate.plan_version != self.plan_version
            or gate.scope_version != self.scope_version
            for gate in self.gates
        ):
            raise DomainValidationError(
                "research.gate_version_mismatch", "Gate 必须绑定当前计划和范围版本"
            )
        match self.status:
            case ResearchRunStatus.BLOCKED:
                if self.blocked_reason is None:
                    raise DomainValidationError(
                        "research.missing_blocked_reason", "阻塞研究必须记录原因"
                    )
            case ResearchRunStatus.PAUSED:
                if not self.paused:
                    raise DomainValidationError(
                        "research.paused_flag_required", "暂停研究必须设置 paused"
                    )
            case (
                ResearchRunStatus.PLANNED
                | ResearchRunStatus.RUNNING
                | ResearchRunStatus.COMPLETED
                | ResearchRunStatus.FAILED
            ):
                if self.paused:
                    raise DomainValidationError(
                        "research.unexpected_paused_flag", "仅暂停研究可设置 paused"
                    )
            case unreachable:
                assert_never(unreachable)
        return self


def transition_todo(
    todo: TodoItem,
    next_status: TodoStatus,
    outcome: TodoOutcome | None = None,
) -> TodoItem:
    if next_status not in allowed_todo_statuses(todo.status):
        raise DomainValidationError(
            "todo.invalid_transition",
            f"不允许 TODO 从 {todo.status} 转换到 {next_status}",
        )
    return TodoItem.model_validate(
        todo.model_dump() | {"status": next_status, "outcome": outcome}
    )
