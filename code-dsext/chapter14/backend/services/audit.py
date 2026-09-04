from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence, assert_never

try:
    from backend.models import AuditRecord
except ModuleNotFoundError as error:
    if error.name != "backend":
        raise
    from models import AuditRecord


REDACTED = "[REDACTED]"
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | tuple[JsonValue, ...] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
_SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "raw_line",
    "raw_row",
    "raw_record",
)


@dataclass(frozen=True, slots=True)
class AuditInputError(ValueError):
    field: str

    def __str__(self) -> str:
        return "audit_input_invalid"


@dataclass(frozen=True, slots=True)
class AuditSerializationError(ValueError):
    value_type: str

    def __str__(self) -> str:
        return "audit_serialization_failed"


@dataclass(frozen=True, slots=True)
class AuditChainVerificationError(ValueError):
    reason: str = "verification"

    def __str__(self) -> str:
        return "audit_chain_verification_failed"


@dataclass(frozen=True, slots=True)
class ChainedAuditRecord:
    record: AuditRecord
    previous_hash: str
    record_hash: str


def canonical_json_bytes(value: JsonValue) -> bytes:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise AuditSerializationError(type(value).__name__) from error
    return serialized.encode("utf-8")


def canonical_sha256(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_sensitive(value: JsonValue) -> JsonValue:
    match value:
        case dict() as mapping:
            return {
                key: REDACTED if _is_sensitive_key(key) else redact_sensitive(item)
                for key, item in mapping.items()
            }
        case list() as items:
            return [redact_sensitive(item) for item in items]
        case tuple() as items:
            return tuple(redact_sensitive(item) for item in items)
        case None | bool() | int() | float() | str():
            return value
        case unreachable:
            assert_never(unreachable)


def build_audit_record(
    prev_hash: str, fields: Mapping[str, JsonValue]
) -> ChainedAuditRecord:
    safe_fields = redact_sensitive(dict(fields))
    if not isinstance(safe_fields, dict):
        raise AuditInputError("fields")
    details = _details_tuple(safe_fields.get("details", {}))
    record = AuditRecord(
        record_id=_required_text(safe_fields, "record_id"),
        run_id=_required_text(safe_fields, "run_id"),
        action=_required_text(safe_fields, "action"),
        actor_id=_optional_text(safe_fields, "actor_id", "system"),
        details=details,
    )
    record_hash = canonical_sha256(_chain_payload(prev_hash, record))
    return ChainedAuditRecord(record, prev_hash, record_hash)


def verify_audit_chain(records: Sequence[ChainedAuditRecord]) -> bool:
    try:
        if not records:
            raise AuditChainVerificationError()
        expected_previous_hash = ""
        for chained in records:
            if (
                not isinstance(chained, ChainedAuditRecord)
                or chained.previous_hash != expected_previous_hash
                or len(chained.record_hash) != 64
                or canonical_sha256(
                    _chain_payload(chained.previous_hash, chained.record)
                )
                != chained.record_hash
            ):
                raise AuditChainVerificationError()
            expected_previous_hash = chained.record_hash
        return True
    except AuditChainVerificationError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise AuditChainVerificationError() from error


def _details_tuple(value: JsonValue) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise AuditInputError("details")
    return tuple(
        (str(key), canonical_json_bytes(item).decode("utf-8"))
        for key, item in sorted(value.items(), key=lambda item: str(item[0]))
    )


def _chain_payload(previous_hash: str, record: AuditRecord) -> JsonObject:
    return {"previous_hash": previous_hash, "record": _audit_record_payload(record)}


def _audit_record_payload(record: AuditRecord) -> JsonObject:
    return {
        "record_id": record.record_id,
        "run_id": record.run_id,
        "action": record.action,
        "actor_id": record.actor_id,
        "occurred_at": record.occurred_at.isoformat(),
        "details": tuple(tuple(item) for item in record.details),
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _required_text(fields: JsonObject, name: str) -> str:
    value = fields.get(name)
    if not isinstance(value, str) or not value:
        raise AuditInputError(name)
    return value


def _optional_text(fields: JsonObject, name: str, default: str) -> str:
    value = fields.get(name, default)
    if not isinstance(value, str) or not value:
        raise AuditInputError(name)
    return value
