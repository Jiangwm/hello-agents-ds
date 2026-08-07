from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Mapping
from uuid import uuid4


SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "acknowledged_by",
        "email",
        "inspector_name",
        "operator_name",
        "password",
        "phone",
        "token",
    }
)


def redact_sensitive(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "***"
                if str(key).lower() in SENSITIVE_FIELD_NAMES
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    request_id: str
    timestamp: str
    protocol: str
    service: str
    operation: str
    actor_id: str
    tenant_id: str
    parameter_summary: Mapping[str, object]
    status: str
    response_bytes: int
    evidence_ids: tuple[str, ...]
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "protocol": self.protocol,
            "service": self.service,
            "operation": self.operation,
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "parameter_summary": dict(self.parameter_summary),
            "status": self.status,
            "response_bytes": self.response_bytes,
            "evidence_ids": list(self.evidence_ids),
            "error_code": self.error_code,
        }


class AuditTrail:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def append(
        self,
        *,
        request_id: str,
        protocol: str,
        service: str,
        operation: str,
        actor_id: str,
        tenant_id: str,
        parameters: Mapping[str, object],
        status: str,
        response: object,
        evidence_ids: tuple[str, ...] = (),
        error_code: str | None = None,
    ) -> AuditRecord:
        response_bytes = len(
            json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")
        )
        record = AuditRecord(
            audit_id=f"AUD-{uuid4().hex[:16].upper()}",
            request_id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            protocol=protocol,
            service=service,
            operation=operation,
            actor_id=actor_id,
            tenant_id=tenant_id,
            parameter_summary=redact_sensitive(dict(parameters)),
            status=status,
            response_bytes=response_bytes,
            evidence_ids=evidence_ids,
            error_code=error_code,
        )
        self.records.append(record)
        return record
