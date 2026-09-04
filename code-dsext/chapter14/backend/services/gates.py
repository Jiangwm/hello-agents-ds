from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from ..models.approval import Approval, Gate, transition_gate
from ..models.common import GateName, GateStatus
from .repository import JsonValue, Repository, StoredRecord, canonical_json


@dataclass(frozen=True, slots=True)
class GateDecisionError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class GateRequest:
    run_id: str
    name: GateName
    requester_actor: str
    plan_version: str
    scope_version: str
    evidence_version: str
    budget_version: str


class GateService:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def request(self, request: GateRequest) -> Gate:
        self._validate_request(request)
        input_hash = self._input_hash(request)
        gate_id = self._gate_id(request.name, input_hash)
        stored = self._find_gate(request.run_id, gate_id)
        if stored is not None:
            return self._gate_from_record(stored)
        gate = Gate(
            gate_id=gate_id,
            name=request.name,
            input_hash=input_hash,
            plan_version=request.plan_version,
            scope_version=request.scope_version,
            status=GateStatus.AWAITING_HUMAN,
            rationale=None,
        )
        self._repository.replace_or_upsert(
            "gates", request.run_id, gate_id, self._gate_payload(gate, request)
        )
        return gate

    def approve(
        self, run_id: str, gate_id: str, approver: str, reason: str
    ) -> Approval:
        return self._decide(run_id, gate_id, approver, reason, approved=True)

    def reject(
        self, run_id: str, gate_id: str, approver: str, reason: str
    ) -> Approval:
        return self._decide(run_id, gate_id, approver, reason, approved=False)

    def invalidate(self, run_id: str, gate_id: str, actor: str, reason: str) -> Gate:
        self._require_text(actor, "actor")
        self._require_text(reason, "reason")
        record = self._required_gate(run_id, gate_id)
        gate = self._gate_from_record(record)
        invalidated = Gate(
            gate_id=gate.gate_id,
            name=gate.name,
            input_hash=gate.input_hash,
            plan_version=gate.plan_version,
            scope_version=gate.scope_version,
            status=GateStatus.REJECTED,
            rationale=reason.strip(),
        )
        payload = dict(record.payload)
        payload.update(invalidated.model_dump(mode="json"))
        payload["invalidated_by"] = actor.strip()
        self._repository.replace_or_upsert("gates", run_id, gate_id, payload)
        return invalidated

    def is_approved(self, request: GateRequest) -> bool:
        input_hash = self._input_hash(request)
        gate_id = self._gate_id(request.name, input_hash)
        record = self._find_gate(request.run_id, gate_id)
        if record is None or self._gate_from_record(record).status is not GateStatus.APPROVED:
            return False
        approvals = (
            self._approval_from_record(item)
            for item in self._repository.list_records("approvals", request.run_id)
        )
        return any(
            approval.gate_id == gate_id
            and approval.input_hash == input_hash
            and approval.approved
            for approval in approvals
        )

    def _decide(
        self,
        run_id: str,
        gate_id: str,
        approver: str,
        reason: str,
        approved: bool,
    ) -> Approval:
        self._require_text(approver, "approver")
        self._require_text(reason, "reason")
        record = self._required_gate(run_id, gate_id)
        requester = self._required_payload_text(record, "requester_actor")
        if requester == approver.strip():
            raise GateDecisionError("approver must differ from requester")
        gate = self._gate_from_record(record)
        next_status = GateStatus.APPROVED if approved else GateStatus.REJECTED
        decided_gate = transition_gate(gate, next_status)
        decided_at = datetime.now(timezone.utc)
        approval_id = sha256(
            f"{gate_id}|{approver.strip()}|{approved}|{decided_at.isoformat()}".encode(
                "utf-8"
            )
        ).hexdigest()
        approval = Approval(
            approval_id=approval_id,
            gate_id=gate.gate_id,
            gate_name=gate.name,
            input_hash=gate.input_hash,
            plan_version=gate.plan_version,
            scope_version=gate.scope_version,
            approved=approved,
            approver=approver.strip(),
            reason=reason.strip(),
            decided_at=decided_at,
        )
        payload = dict(record.payload)
        payload.update(decided_gate.model_dump(mode="json"))
        self._repository.replace_or_upsert("gates", run_id, gate_id, payload)
        self._repository.replace_or_upsert(
            "approvals", run_id, approval_id, approval.model_dump(mode="json")
        )
        return approval

    def _required_gate(self, run_id: str, gate_id: str) -> StoredRecord:
        record = self._find_gate(run_id, gate_id)
        if record is None:
            raise GateDecisionError("gate not found")
        return record

    def _find_gate(self, run_id: str, gate_id: str) -> StoredRecord | None:
        return next(
            (
                record
                for record in self._repository.list_records("gates", run_id)
                if record.record_id == gate_id
            ),
            None,
        )

    @staticmethod
    def _gate_payload(gate: Gate, request: GateRequest) -> dict[str, JsonValue]:
        return {
            "gate_id": gate.gate_id,
            "name": gate.name.value,
            "input_hash": gate.input_hash,
            "plan_version": gate.plan_version,
            "scope_version": gate.scope_version,
            "status": gate.status.value,
            "rationale": "",
            "requester_actor": request.requester_actor,
            "evidence_version": request.evidence_version,
            "budget_version": request.budget_version,
        }

    @staticmethod
    def _gate_from_record(record: StoredRecord) -> Gate:
        rationale = record.payload.get("rationale")
        return Gate(
            gate_id=GateService._required_payload_text(record, "gate_id"),
            name=GateName(GateService._required_payload_text(record, "name")),
            input_hash=GateService._required_payload_text(record, "input_hash"),
            plan_version=GateService._required_payload_text(record, "plan_version"),
            scope_version=GateService._required_payload_text(record, "scope_version"),
            status=GateStatus(GateService._required_payload_text(record, "status")),
            rationale=rationale if isinstance(rationale, str) and rationale else None,
        )

    @staticmethod
    def _approval_from_record(record: StoredRecord) -> Approval:
        approved = record.payload.get("approved")
        if not isinstance(approved, bool):
            raise GateDecisionError("invalid stored approval field: approved")
        decided_at = GateService._required_payload_text(record, "decided_at")
        return Approval(
            approval_id=GateService._required_payload_text(record, "approval_id"),
            gate_id=GateService._required_payload_text(record, "gate_id"),
            gate_name=GateName(GateService._required_payload_text(record, "gate_name")),
            input_hash=GateService._required_payload_text(record, "input_hash"),
            plan_version=GateService._required_payload_text(record, "plan_version"),
            scope_version=GateService._required_payload_text(record, "scope_version"),
            approved=approved,
            approver=GateService._required_payload_text(record, "approver"),
            reason=GateService._required_payload_text(record, "reason"),
            decided_at=datetime.fromisoformat(decided_at.replace("Z", "+00:00")),
        )

    @staticmethod
    def _required_payload_text(record: StoredRecord, key: str) -> str:
        value = record.payload.get(key)
        if not isinstance(value, str) or not value:
            raise GateDecisionError(f"invalid stored gate field: {key}")
        return value

    @staticmethod
    def _input_hash(request: GateRequest) -> str:
        payload: dict[str, JsonValue] = {
            "name": request.name.value,
            "plan_version": request.plan_version,
            "scope_version": request.scope_version,
            "evidence_version": request.evidence_version,
            "budget_version": request.budget_version,
        }
        return sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _gate_id(name: GateName, input_hash: str) -> str:
        return f"{name.value}:{input_hash[:20]}"

    @staticmethod
    def _validate_request(request: GateRequest) -> None:
        for field, value in (
            ("run_id", request.run_id),
            ("requester_actor", request.requester_actor),
            ("plan_version", request.plan_version),
            ("scope_version", request.scope_version),
            ("evidence_version", request.evidence_version),
            ("budget_version", request.budget_version),
        ):
            GateService._require_text(value, field)

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not value.strip():
            raise GateDecisionError(f"{field} must not be blank")
