from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Callable, Final, Literal, assert_never

from ..models.approval import Approval, Gate
from ..models.common import GateName, GateStatus
from ..models.event import AuditRecord
from .repository import JsonValue, Repository, canonical_json


type Domain = Literal["quality", "process", "equipment", "material", "internal_docs", "external_knowledge"]
DOMAIN_NAMES: Final[frozenset[str]] = frozenset(("quality", "process", "equipment", "material", "internal_docs", "external_knowledge"))


@dataclass(frozen=True, slots=True)
class ActorContext:
    actor_id: str
    tenant_id: str
    line_id: str
    role: str
    allowed_domains: frozenset[Domain]


@dataclass(frozen=True, slots=True)
class AccessRequest:
    run_id: str
    tenant_id: str
    line_id: str
    domain: Domain
    scope_hash: str
    plan_version: str
    scope_version: str


@dataclass(frozen=True, slots=True)
class AllowedAccess:
    request: AccessRequest
    gate_id: str | None


@dataclass(frozen=True, slots=True)
class AwaitingCrossDomainApproval:
    request: AccessRequest
    gate: Gate


@dataclass(frozen=True, slots=True)
class DeniedAccess:
    request: AccessRequest
    reason: str = "permission denied"


type AccessDecision = AllowedAccess | AwaitingCrossDomainApproval | DeniedAccess


@dataclass(frozen=True, slots=True)
class AuthorizationInputError(ValueError):
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class AuthorizationService:
    def __init__(
        self, repository: Repository, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.repository = repository
        self._clock = clock or _utc_now

    def authorize(self, actor: ActorContext, request: AccessRequest) -> AccessDecision:
        if not _is_bound(actor, request) or not _is_valid_request(request):
            return self._record(actor, request, DeniedAccess(request))
        if request.domain in actor.allowed_domains:
            return self._record(actor, request, AllowedAccess(request, None))
        gate, corrupt = self._find_gate(request)
        if corrupt:
            return self._record(actor, request, DeniedAccess(request))
        if gate is None:
            gate = self._create_gate(request)
            return self._record(actor, request, AwaitingCrossDomainApproval(request, gate))
        if self._has_current_approval(actor, request, gate):
            return self._record(actor, request, AllowedAccess(request, gate.gate_id))
        return self._record(actor, request, AwaitingCrossDomainApproval(request, gate))

    def approve_cross_domain(
        self,
        requester: ActorContext,
        request: AccessRequest,
        gate_id: str,
        approver: ActorContext,
        reason: str,
        expires_at: datetime,
    ) -> None:
        gate, corrupt = self._find_gate(request)
        valid = (
            not corrupt
            and gate is not None
            and gate.gate_id == gate_id
            and _is_bound(requester, request)
            and _is_bound(approver, request)
            and requester.actor_id != approver.actor_id
            and bool(reason.strip())
            and expires_at.tzinfo is not None
            and expires_at > self._now()
        )
        if not valid:
            self._record(requester, request, DeniedAccess(request))
            if requester.actor_id == approver.actor_id:
                raise AuthorizationInputError(
                    "authorization.independent_approver", "independent approver required"
                )
            raise AuthorizationInputError(
                "authorization.permission_denied", "permission denied"
            )
        assert gate is not None
        approval = Approval(
            approval_id=f"approval-{gate_id}", gate_id=gate_id,
            gate_name=GateName.CROSS_DOMAIN_ACCESS, input_hash=gate.input_hash,
            plan_version=request.plan_version, scope_version=request.scope_version,
            approved=True, approver=approver.actor_id, reason=reason.strip(),
            decided_at=self._now(),
        )
        payload = approval.model_dump(mode="json")
        payload.update(
            requester_id=requester.actor_id, tenant_id=request.tenant_id,
            line_id=request.line_id, domain=request.domain, scope_hash=request.scope_hash,
            expires_at=expires_at.astimezone(timezone.utc).isoformat(),
        )
        self.repository.replace_or_upsert(
            "approvals", request.run_id, approval.approval_id, payload
        )
        self._store_gate(request, gate.model_copy(update={"status": GateStatus.APPROVED}))

    def _create_gate(self, request: AccessRequest) -> Gate:
        input_hash = _input_hash(request)
        gate = Gate(
            gate_id=f"cross-domain-{input_hash}", name=GateName.CROSS_DOMAIN_ACCESS,
            input_hash=input_hash, plan_version=request.plan_version,
            scope_version=request.scope_version, status=GateStatus.AWAITING_HUMAN,
            rationale="cross-domain access requires human approval",
        )
        self._store_gate(request, gate)
        return gate

    def _store_gate(self, request: AccessRequest, gate: Gate) -> None:
        payload = gate.model_dump(mode="json")
        payload.update(
            tenant_id=request.tenant_id, line_id=request.line_id,
            domain=request.domain, scope_hash=request.scope_hash,
        )
        self.repository.replace_or_upsert("gates", request.run_id, gate.gate_id, payload)

    def _find_gate(self, request: AccessRequest) -> tuple[Gate | None, bool]:
        gate_id = f"cross-domain-{_input_hash(request)}"
        for stored in self.repository.list_records("gates", request.run_id):
            if stored.record_id == gate_id:
                return _gate_from_payload(stored.payload, request, gate_id)
        return None, False

    def _has_current_approval(
        self, actor: ActorContext, request: AccessRequest, gate: Gate
    ) -> bool:
        if gate.status is not GateStatus.APPROVED:
            return False
        approval_id = f"approval-{gate.gate_id}"
        for stored in self.repository.list_records("approvals", request.run_id):
            if stored.record_id == approval_id:
                return _approval_matches(
                    stored.payload, actor, request, gate, self._now()
                )
        return False

    def _record(
        self, actor: ActorContext, request: AccessRequest, decision: AccessDecision
    ) -> AccessDecision:
        match decision:
            case AllowedAccess(gate_id=gate_id):
                action, status, gate_value = "authorization_allowed", "allowed", gate_id or "none"
            case AwaitingCrossDomainApproval(gate=gate):
                action, status, gate_value = "authorization_awaiting", "awaiting_human", gate.gate_id
            case DeniedAccess():
                action, status, gate_value = "authorization_denied", "denied", "none"
            case unreachable:
                assert_never(unreachable)
        record = AuditRecord(
            record_id=f"authorization-{len(self.repository.list_records('audit', request.run_id)) + 1:06d}",
            run_id=request.run_id, action=action, actor_id=actor.actor_id,
            details=(
                ("tenant_id", request.tenant_id), ("line_id", request.line_id),
                ("domain", request.domain), ("scope_hash", request.scope_hash),
                ("plan_version", request.plan_version), ("scope_version", request.scope_version),
                ("status", status), ("gate_id", gate_value),
            ),
        )
        self.repository.replace_or_upsert("audit", request.run_id, record.record_id, record)
        return decision

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise AuthorizationInputError(
                "authorization.naive_clock", "authorization clock must be timezone aware"
            )
        return now.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_bound(actor: ActorContext, request: AccessRequest) -> bool:
    return actor.tenant_id == request.tenant_id and actor.line_id == request.line_id


def _is_valid_request(request: AccessRequest) -> bool:
    values = (
        request.run_id, request.tenant_id, request.line_id, request.scope_hash,
        request.plan_version, request.scope_version,
    )
    return (
        request.domain in DOMAIN_NAMES and all(values) and len(request.scope_hash) == 64
        and all(character in "0123456789abcdef" for character in request.scope_hash.lower())
    )


def _input_hash(request: AccessRequest) -> str:
    payload: dict[str, JsonValue] = {
        "run_id": request.run_id, "tenant_id": request.tenant_id,
        "line_id": request.line_id, "domain": request.domain,
        "scope_hash": request.scope_hash, "plan_version": request.plan_version,
        "scope_version": request.scope_version,
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _gate_from_payload(
    payload: dict[str, JsonValue], request: AccessRequest, gate_id: str
) -> tuple[Gate | None, bool]:
    expected: tuple[tuple[str, JsonValue], ...] = (
        ("gate_id", gate_id), ("name", GateName.CROSS_DOMAIN_ACCESS),
        ("input_hash", _input_hash(request)), ("plan_version", request.plan_version),
        ("scope_version", request.scope_version), ("tenant_id", request.tenant_id),
        ("line_id", request.line_id), ("domain", request.domain),
        ("scope_hash", request.scope_hash),
    )
    status = payload.get("status")
    if (
        any(payload.get(key) != value for key, value in expected)
        or not isinstance(status, str)
        or status not in {GateStatus.AWAITING_HUMAN, GateStatus.APPROVED}
    ):
        return None, True
    return Gate(
        gate_id=gate_id, name=GateName.CROSS_DOMAIN_ACCESS,
        input_hash=_input_hash(request), plan_version=request.plan_version,
        scope_version=request.scope_version, status=GateStatus(status),
        rationale="cross-domain access requires human approval",
    ), False


def _approval_matches(
    payload: dict[str, JsonValue], actor: ActorContext, request: AccessRequest,
    gate: Gate, now: datetime,
) -> bool:
    expected: tuple[tuple[str, JsonValue], ...] = (
        ("gate_id", gate.gate_id), ("gate_name", GateName.CROSS_DOMAIN_ACCESS),
        ("input_hash", gate.input_hash), ("plan_version", request.plan_version),
        ("scope_version", request.scope_version), ("approved", True),
        ("requester_id", actor.actor_id), ("tenant_id", request.tenant_id),
        ("line_id", request.line_id), ("domain", request.domain),
        ("scope_hash", request.scope_hash),
    )
    approver, reason, expiry = (
        payload.get("approver"), payload.get("reason"), payload.get("expires_at")
    )
    if (
        any(payload.get(key) != value for key, value in expected)
        or not isinstance(approver, str) or approver == actor.actor_id
        or not isinstance(reason, str) or not reason or not isinstance(expiry, str)
    ):
        return False
    try:
        expires_at = datetime.fromisoformat(expiry)
    except ValueError:
        return False
    return expires_at.tzinfo is not None and expires_at > now
