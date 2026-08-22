from __future__ import annotations

from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def reject_nested_non_finite(self) -> "StrictModel":
        def validate(value: Any) -> None:
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("non-finite numbers are prohibited")
            if isinstance(value, dict):
                for item in value.values():
                    validate(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    validate(item)

        validate(self.__dict__)
        return self


class Equipment(StrictModel):
    equipment_id: str = Field(min_length=1)
    status: Literal["running", "idle", "failed", "maintenance", "slowed"]
    speed: float = Field(ge=0, le=1.5)
    health: float = Field(ge=0, le=1)
    power_kw: float = Field(ge=0)
    downtime_minutes: int = Field(default=0, ge=0)


class Buffer(StrictModel):
    buffer_id: str = Field(min_length=1)
    wip: int = Field(ge=0)
    capacity: int = Field(gt=0)
    wait_minutes: int = Field(default=0, ge=0)
    blocked_reason: str | None = None


class WipItem(StrictModel):
    item_id: str
    batch_id: str
    location_id: str
    status: Literal["queued", "processing", "isolated", "rework", "complete"]


class Batch(StrictModel):
    batch_id: str
    quantity: int = Field(gt=0)
    urgent: bool = False
    due_tick: int = Field(gt=0)
    isolated: bool = False


class ShiftTeam(StrictModel):
    team_id: str
    available_operators: int = Field(ge=0)
    maintenance_capacity: int = Field(ge=0)


class QualityGate(StrictModel):
    gate_id: str
    status: Literal["open", "inspection", "blocked"]
    defects: int = Field(default=0, ge=0)
    isolated_batch_ids: list[str] = Field(default_factory=list)


class EnergyState(StrictModel):
    tariff: Literal["valley", "flat", "peak"]
    price_per_kwh: float = Field(ge=0)
    current_kw: float = Field(ge=0)
    cumulative_kwh: float = Field(default=0, ge=0)
    cumulative_cost: float = Field(default=0, ge=0)


class Relationship(StrictModel):
    source_id: str
    target_id: str
    relation: Literal["upstream", "shared_resource", "maintenance_priority", "trust"]
    strength: float = Field(ge=0, le=1)


class WorldState(StrictModel):
    simulation_time: int = Field(default=0, ge=0)
    equipment: list[Equipment]
    buffers: list[Buffer]
    wip_items: list[WipItem]
    batches: list[Batch]
    shift_teams: list[ShiftTeam]
    quality_gates: list[QualityGate]
    energy: EnergyState
    relationships: list[Relationship]
    throughput: int = Field(default=0, ge=0)


class Scenario(StrictModel):
    scenario_id: Literal[
        "upstream_slowdown", "quality_isolation", "peak_tariff_urgent_order"
    ]
    name: str
    description: str
    data_version: str
    deidentified: Literal[True]
    world: WorldState


Role = Literal["equipment", "scheduler", "maintenance", "quality", "energy", "observer"]
ActionType = Literal[
    "observe",
    "adjust_speed",
    "schedule_maintenance",
    "isolate_batch",
    "inspect_quality",
    "reschedule_batch",
    "shift_energy_load",
    "production_write",
    "bypass_quality",
]


class AgentMessage(StrictModel):
    message_id: str
    simulation_time: int
    role: Role
    kind: Literal["observation", "proposal", "decision"]
    content: dict[str, Any]


class ActionProposal(StrictModel):
    proposal_id: str
    role: Role
    action_type: ActionType
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(min_length=1)
    evidence_event_ids: list[str] = Field(default_factory=list)
    proposal_hash: str


class GateDecision(StrictModel):
    decision_id: str
    proposal_id: str
    verdict: Literal["allow", "deny", "hold"]
    reason_codes: list[str]
    hard_rejection: bool = False
    proposal_hash: str
    state_hash: str
    policy_hash: str
    reviewed: bool = False
    reviewer: str | None = None
    review_reason: str | None = None
    review_hash: str | None = None


class StateEvent(StrictModel):
    event_id: str
    simulation_id: str
    seed: int
    strategy_version: str
    simulation_time: int
    source: str
    reason: str
    event: str
    state_diff: dict[str, dict[str, Any]]
    decision_evidence: list[str]
    caused_by: list[str]
    prev_event_hash: str
    event_hash: str
    state_hash: str


class Metrics(StrictModel):
    throughput: int
    wip: int
    downtime: int
    defects: int
    energy_kwh: float
    energy_cost: float
    tool_calls: int
    explanation_quality: float
    explanation_cost: float
    pending_decisions: int


class SimulationCreateRequest(StrictModel):
    scenario_id: str
    seed: int = Field(default=15, ge=0)
    strategy: Literal["fixed_rule", "single_agent", "multi_agent"] = "multi_agent"


class StepRequest(StrictModel):
    steps: int = Field(default=1, ge=1, le=1000)


class SuggestionRequest(StrictModel):
    role: Role
    action_type: ActionType
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(min_length=1)
    narrative: str | None = None


class ReviewRequest(StrictModel):
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approve: bool
    proposal_hash: str
    state_hash: str
    policy_hash: str


class ExportApprovalRequest(StrictModel):
    approver: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ReplayRequest(StrictModel):
    snapshot_id: str | None = None


class ComparisonRequest(StrictModel):
    scenario_id: str
    seed: int = Field(default=15, ge=0)
    steps: int = Field(default=6, ge=1, le=1000)


class SimulationView(StrictModel):
    simulation_id: str
    scenario_id: str
    scenario_sha256: str
    scenario_tool_call_id: str
    data_version: str
    seed: int
    strategy: str
    strategy_version: str
    status: Literal["running", "paused"]
    state: WorldState
    state_hash: str
    trajectory_hash: str
    agents: dict[Role, list[AgentMessage]]
    pending_decisions: list[GateDecision]
    export_approval: dict[str, str] | None
    read_only: Literal[True] = True
    production_control: Literal["prohibited"] = "prohibited"
