from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from typing import Literal, TypeAlias

from backend.models import EvidenceRef


@unique
class QualityOperation(StrEnum):
    DETECT_ONSET = "detect_onset"
    COMPARE_CONTROL = "compare_control"
    ANALYZE_PROCESS_DRIFT = "analyze_process_drift"


@dataclass(frozen=True, slots=True)
class QualityQuery:
    tenant_id: str
    line_id: str
    product: str
    start: datetime
    end: datetime
    retrieved_at: datetime
    tool_call_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class RateWindow:
    start: datetime
    end: datetime
    numerator: int
    denominator: int
    defect_rate: float


@dataclass(frozen=True, slots=True)
class OnsetFinding:
    observed_at: datetime
    baseline: RateWindow
    affected: RateWindow


@dataclass(frozen=True, slots=True)
class ControlComparison:
    control: RateWindow
    affected: RateWindow
    rate_ratio: float


@dataclass(frozen=True, slots=True)
class ParameterDrift:
    parameter: str
    control_mean: float
    affected_mean: float
    drift: float


@dataclass(frozen=True, slots=True)
class ProcessDriftFinding:
    parameters: tuple[ParameterDrift, ...]


@dataclass(frozen=True, slots=True)
class MissingDataFinding:
    resource: str
    missing_columns: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class NegativeResultFinding:
    resource: str
    reason: str


QualityFinding: TypeAlias = (
    OnsetFinding
    | ControlComparison
    | ProcessDriftFinding
    | MissingDataFinding
    | NegativeResultFinding
)


@dataclass(frozen=True, slots=True)
class QualityToolResult:
    finding: QualityFinding
    evidence_ids: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    data_quality: tuple[str, ...]
    assumptions: tuple[str, ...]
    correlation_only: Literal[True] = True
    todo_status: Literal["completed", "blocked"] = "completed"


@dataclass(frozen=True, slots=True)
class QualityTodo:
    operation: QualityOperation
    query: QualityQuery


@dataclass(frozen=True, slots=True)
class QualityRow:
    observed_at: datetime
    defects: int
    inspected: int
    process_window_id: str
