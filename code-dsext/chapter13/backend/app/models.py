from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OperationConstraint(StrictModel):
    constraint_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    kind: Literal[
        "fixed",
        "window",
        "precedence",
        "duration",
        "resource",
        "peak_limit",
    ]
    value: str | float | int | list[int]
    related_task_id: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "OperationConstraint":
        if self.kind == "precedence":
            if not self.related_task_id:
                raise ValueError("precedence constraint requires related_task_id")
            if self.related_task_id == self.task_id:
                raise ValueError("precedence constraint cannot reference itself")
            if self.value != "after":
                raise ValueError("precedence constraint value must be 'after'")
        if self.kind in {"fixed", "duration"}:
            if not isinstance(self.value, (int, float)) or isinstance(
                self.value,
                bool,
            ):
                raise ValueError(f"{self.kind} constraint requires a number")
            if not float(self.value).is_integer():
                raise ValueError(f"{self.kind} constraint requires an integer")
        if self.kind == "peak_limit":
            if not isinstance(self.value, (int, float)) or isinstance(
                self.value,
                bool,
            ):
                raise ValueError("peak_limit constraint requires a number")
            if not isfinite(float(self.value)) or self.value <= 0:
                raise ValueError(
                    "peak_limit constraint requires a finite positive number"
                )
        if self.kind == "resource" and (
            not isinstance(self.value, str) or not self.value.strip()
        ):
            raise ValueError("resource constraint requires a resource id")
        if self.kind == "window" and self.value != "task-window":
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError(
                    "window constraint requires [start_minute, end_minute]"
                )
            start_minute, end_minute = self.value
            if (
                start_minute < 0
                or end_minute > 1440
                or end_minute <= start_minute
                or start_minute % 30
                or end_minute % 30
            ):
                raise ValueError(
                    "window constraint must be a valid 30-minute aligned interval"
                )
        return self


class PlanningRequest(StrictModel):
    line_id: str = Field(min_length=1)
    planning_date: date
    output_target_units: int = Field(gt=0)
    shifts: list[Literal["day", "night"]] = Field(min_length=1)
    optimization_objective: Literal["cost", "peak", "balanced"]
    tariff_profile_id: str = Field(min_length=1)
    locked_task_ids: list[str] = Field(default_factory=list)
    constraints: list[OperationConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_inputs(self) -> "PlanningRequest":
        if len(set(self.shifts)) != len(self.shifts):
            raise ValueError("shifts must be unique")
        if len(set(self.locked_task_ids)) != len(self.locked_task_ids):
            raise ValueError("locked_task_ids must be unique")
        return self


class TariffPeriod(StrictModel):
    name: Literal["peak", "flat", "valley"]
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(gt=0, le=1440)
    price_per_kwh: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "TariffPeriod":
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be greater than start_minute")
        return self


class EnergyBaseline(StrictModel):
    task_id: str
    expected_power_kw: float = Field(gt=0)
    lower_power_kw: float = Field(ge=0)
    upper_power_kw: float = Field(gt=0)
    model_version: str

    @model_validator(mode="after")
    def validate_interval(self) -> "EnergyBaseline":
        if not (
            self.lower_power_kw
            <= self.expected_power_kw
            <= self.upper_power_kw
        ):
            raise ValueError("expected power must be inside confidence interval")
        return self


class EnergyDataMetadata(StrictModel):
    data_version: str = Field(min_length=1)
    source_window: str = Field(min_length=1)
    description: str = Field(min_length=1)
    interval_minutes: Literal[30]
    timezone: str = Field(min_length=1)
    expected_meter_points: int = Field(gt=0)
    observed_meter_points: int = Field(ge=0)
    anomaly_rate: float = Field(ge=0, le=1)


class ProductionTask(StrictModel):
    task_id: str
    product: str
    batch_size: int = Field(gt=0)
    device_candidates: list[str] = Field(min_length=1)
    selected_device_id: str = Field(min_length=1)
    due_minute: int = Field(gt=0, le=1440)
    duration_minutes: int = Field(gt=0, multiple_of=30)
    power_kw: float = Field(gt=0)
    start_minute: int = Field(ge=0, lt=1440)
    movable: bool
    earliest_start_minute: int = Field(ge=0, lt=1440)
    latest_end_minute: int = Field(gt=0, le=1440)
    locked: bool = False

    @model_validator(mode="after")
    def validate_window(self) -> "ProductionTask":
        if self.selected_device_id not in self.device_candidates:
            raise ValueError("selected device must be one of device_candidates")
        if len(set(self.device_candidates)) != len(self.device_candidates):
            raise ValueError("device_candidates must be unique")
        if self.start_minute + self.duration_minutes > 1440:
            raise ValueError("task exceeds planning day")
        if self.earliest_start_minute + self.duration_minutes > min(
            self.latest_end_minute,
            self.due_minute,
        ):
            raise ValueError("movable window before delivery is shorter than task duration")
        return self


class EnergyPoint(StrictModel):
    start_minute: int
    power_kw: float
    energy_kwh: float
    tariff_period: str
    price_per_kwh: float


class EnergyCurve(StrictModel):
    interval_minutes: Literal[30] = 30
    points: list[EnergyPoint]


class CostBreakdown(StrictModel):
    by_period: dict[str, float]
    total_cost: float


class ConstraintViolation(StrictModel):
    constraint_id: str
    task_id: str | None = None
    message: str
    severity: Literal["error", "warning"] = "error"


class RecommendationEvidence(StrictModel):
    tool_call_ids: list[str]
    content_hashes: list[str]
    source_window: str
    data_version: str
    model_version: str
    assumptions: list[str]
    estimation_error: str
    reviewed: bool
    read_only: Literal[True] = True
    production_control: Literal["prohibited"] = "prohibited"


class ScheduleOption(StrictModel):
    option_id: str
    name: str
    strategy: Literal["baseline", "cost", "peak", "balanced"]
    tasks: list[ProductionTask]
    curve: EnergyCurve
    cost: CostBreakdown
    total_energy_kwh: float
    peak_kw: float
    unit_energy_kwh: float
    confidence_interval_kwh: tuple[float, float]
    violations: list[ConstraintViolation]
    feasible: bool
    evidence: RecommendationEvidence | None = None


class EnergyMetrics(StrictModel):
    curve: EnergyCurve
    cost: CostBreakdown
    total_energy_kwh: float
    peak_kw: float
    unit_energy_kwh: float
    confidence_interval_kwh: tuple[float, float]


class ChangeRecord(StrictModel):
    change_id: str
    content_hash: str
    changed_at: str
    option_id: str
    task_id: str
    before: dict[str, int | bool]
    after: dict[str, int | bool]
    actor: str
    reason: str


class ApprovalRecord(StrictModel):
    approval_id: str
    input_hash: str
    approver: str
    reason: str
    approved_at: str


class DataSummary(StrictModel):
    data_version: str
    model_version: str
    source_window: str
    status: Literal["ready", "degraded"]
    issues: list[str] = Field(default_factory=list)
    task_count: int = Field(ge=1)
    baseline_count: int = Field(ge=1)


class EnergyPlan(StrictModel):
    plan_id: str
    request: PlanningRequest
    effective_constraints: list[OperationConstraint]
    baseline: ScheduleOption
    candidates: list[ScheduleOption]
    recommended_option_id: str
    approval_status: Literal["pending_approval", "approved"] = "pending_approval"
    approval: ApprovalRecord | None = None
    change_history: list[ChangeRecord] = Field(default_factory=list)
    recomputed_task_ids: list[str] = Field(default_factory=list)
    progress: list[str]
    data_summary: DataSummary
    assumptions: list[str]
    estimation_error_percent: float = Field(ge=0)
    evidence: list[RecommendationEvidence]
    read_only: Literal[True] = True
    production_control: Literal["prohibited"] = "prohibited"


class TaskScheduleEdit(StrictModel):
    task_id: str = Field(min_length=1)
    start_minute: int | None = Field(
        default=None,
        ge=0,
        lt=1440,
        multiple_of=30,
    )
    locked: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "TaskScheduleEdit":
        if self.start_minute is None and self.locked is None:
            raise ValueError("task edit must change start_minute or locked")
        return self


class PlanEditRequest(StrictModel):
    option_id: str = Field(min_length=1)
    changes: list[TaskScheduleEdit] = Field(min_length=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ApprovalRequest(StrictModel):
    approver: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MCPReadResult(StrictModel):
    payload: dict | list
    tool_call_id: str
    content_hash: str
    source_window: str
    data_version: str
