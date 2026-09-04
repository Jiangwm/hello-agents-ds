from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field

from .common import (
    DomainValidationError,
    GateName,
    GateStatus,
    StrictModel,
    allowed_gate_statuses,
)


NonEmpty = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]


class Gate(StrictModel):
    gate_id: NonEmpty
    name: GateName
    input_hash: Sha256
    plan_version: NonEmpty
    scope_version: NonEmpty
    status: GateStatus = GateStatus.PENDING
    rationale: str | None = None


class Approval(StrictModel):
    approval_id: NonEmpty
    gate_id: NonEmpty
    gate_name: GateName
    input_hash: Sha256
    plan_version: NonEmpty
    scope_version: NonEmpty
    approved: bool
    approver: NonEmpty
    reason: NonEmpty
    decided_at: datetime


def transition_gate(gate: Gate, next_status: GateStatus) -> Gate:
    if next_status not in allowed_gate_statuses(gate.status):
        raise DomainValidationError(
            "gate.invalid_transition",
            f"不允许 Gate 从 {gate.status} 转换到 {next_status}",
        )
    return Gate(
        gate_id=gate.gate_id,
        name=gate.name,
        input_hash=gate.input_hash,
        plan_version=gate.plan_version,
        scope_version=gate.scope_version,
        status=next_status,
        rationale=gate.rationale,
    )
