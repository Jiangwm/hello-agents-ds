from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Literal, TypeAlias, assert_never

from backend.models import EvidenceRef
from backend.services.authorization import (
    AccessRequest,
    ActorContext,
    AllowedAccess,
    AuthorizationService,
    AwaitingCrossDomainApproval,
    DeniedAccess,
)
from backend.services.evidence import EvidenceLedger
from backend.tools.workspace import CsvReadResult, ReadOnlyWorkspace


BlockedAccess: TypeAlias = AwaitingCrossDomainApproval | DeniedAccess


@dataclass(frozen=True, slots=True)
class DomainToolInputError(ValueError):
    code: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class DomainRow:
    record_id: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    source_uri: str
    source_version: str
    permission: str
    content_hash: str
    data_window: str
    query: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _DomainScope:
    batch_id: str
    start_at: str
    end_at: str


@dataclass(frozen=True, slots=True)
class DomainFinding:
    tenant_id: str
    line_id: str
    batch_id: str
    start_at: str
    end_at: str
    equipment_events: tuple[DomainRow, ...]
    maintenance_events: tuple[DomainRow, ...]
    material_lots: tuple[DomainRow, ...]
    sources: tuple[SourceMetadata, ...]
    evidence: tuple[EvidenceRef, ...]
    no_result: bool
    conflict: bool
    correlation_only: Literal[True]
    created_at: datetime


class OperationsTools:
    def __init__(
        self,
        workspace: ReadOnlyWorkspace,
        authorization: AuthorizationService,
        evidence_ledger: EvidenceLedger,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._workspace = workspace
        self._authorization = authorization
        self._ledger = evidence_ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def search(
        self,
        actor: ActorContext,
        equipment_request: AccessRequest,
        material_request: AccessRequest,
        *,
        batch_id: str,
        start_at: str,
        end_at: str,
        tool_call_id: str,
    ) -> DomainFinding | BlockedAccess:
        equipment_access = self._authorization.authorize(actor, equipment_request)
        match equipment_access:
            case AllowedAccess():
                pass
            case AwaitingCrossDomainApproval() | DeniedAccess():
                return equipment_access
            case unreachable:
                assert_never(unreachable)
        material_access = self._authorization.authorize(actor, material_request)
        match material_access:
            case AllowedAccess():
                pass
            case AwaitingCrossDomainApproval() | DeniedAccess():
                return material_access
            case unreachable:
                assert_never(unreachable)
        if equipment_request.domain != "equipment" or material_request.domain != "material":
            raise DomainToolInputError(
                "operations.invalid_domains",
                "operations requests must target equipment and material domains",
            )
        start = _aware_datetime(start_at)
        end = _aware_datetime(end_at)
        if not batch_id or not tool_call_id or start > end:
            raise DomainToolInputError(
                "operations.invalid_scope", "invalid operations search scope"
            )
        scope = _DomainScope(batch_id, start_at, end_at)
        query = {
            "tenant_id": equipment_request.tenant_id,
            "line_id": equipment_request.line_id,
        }
        equipment = self._workspace.read_csv("equipment_events.csv", query=query)
        maintenance = self._workspace.read_csv("maintenance_events.csv", query=query)
        material = self._workspace.read_csv(
            "material_lots.csv",
            query={
                "tenant_id": material_request.tenant_id,
                "line_id": material_request.line_id,
            },
        )
        event_rows = tuple(
            row for row in equipment.rows
            if _within(row.get("timestamp", ""), start, end)
        )
        event_ids = frozenset(row.get("event_id", "") for row in event_rows)
        maintenance_ids = frozenset(
            row.get("maintenance_id", "") for row in event_rows
        )
        maintenance_rows = tuple(
            row for row in maintenance.rows
            if _within(row.get("start_at", ""), start, end)
            and (
                row.get("maintenance_id", "") in maintenance_ids
                or row.get("related_event_id", "") in event_ids
            )
        )
        material_rows = tuple(
            row for row in material.rows
            if row.get("material_lot_id") == batch_id
            and _within(row.get("received_at", ""), start, end)
        )
        filtered = (
            _filtered(equipment, event_rows, scope),
            _filtered(maintenance, maintenance_rows, scope),
            _filtered(material, material_rows, scope),
        )
        evidence = tuple(
            self._ledger.record(
                result,
                tool_call_id=tool_call_id,
                evidence_id=f"{tool_call_id}-{index}",
            )
            for index, result in enumerate(filtered, start=1)
        )
        return DomainFinding(
            tenant_id=equipment_request.tenant_id,
            line_id=equipment_request.line_id,
            batch_id=batch_id,
            start_at=start_at,
            end_at=end_at,
            equipment_events=_domain_rows(event_rows, "event_id", "timestamp"),
            maintenance_events=_domain_rows(
                maintenance_rows, "maintenance_id", "start_at"
            ),
            material_lots=_domain_rows(
                material_rows, "material_lot_id", "received_at"
            ),
            sources=tuple(_metadata(item) for item in filtered),
            evidence=evidence,
            no_result=not (event_rows or maintenance_rows or material_rows),
            conflict=_has_conflict(event_rows, maintenance_rows),
            correlation_only=True,
            created_at=_aware_clock(self._clock()),
        )


def _filtered(
    result: CsvReadResult,
    rows: tuple[dict[str, str], ...],
    scope: _DomainScope,
) -> CsvReadResult:
    query = dict(result.query)
    query.update(
        {
            "batch_id": scope.batch_id,
            "start_at": scope.start_at,
            "end_at": scope.end_at,
        }
    )
    return replace(result, query=query, rows=rows, no_result=not rows)


def _domain_rows(
    rows: tuple[dict[str, str], ...], identifier: str, time_field: str
) -> tuple[DomainRow, ...]:
    ordered = sorted(rows, key=lambda row: (row.get(time_field, ""), row.get(identifier, "")))
    return tuple(
        DomainRow(
            record_id=row.get(identifier, "unknown"),
            fields=tuple(sorted(row.items())),
        )
        for row in ordered
    )


def _metadata(result: CsvReadResult) -> SourceMetadata:
    return SourceMetadata(
        source_uri=result.resource,
        source_version=result.version,
        permission=result.permission,
        content_hash=result.content_hash,
        data_window=result.data_window,
        query=tuple(sorted(result.query.items())),
    )


def _has_conflict(
    events: tuple[dict[str, str], ...], maintenance: tuple[dict[str, str], ...]
) -> bool:
    maintenance_ids = frozenset(row.get("maintenance_id", "") for row in maintenance)
    event_ids = frozenset(row.get("event_id", "") for row in events)
    return any(
        row.get("maintenance_id", "") in maintenance_ids
        or row.get("related_event_id", "") in event_ids
        for row in (*events, *maintenance)
    )


def _within(value: str, start: datetime, end: datetime) -> bool:
    try:
        observed = _aware_datetime(value)
    except (ValueError, DomainToolInputError):
        return False
    return start <= observed <= end


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DomainToolInputError("operations.invalid_timestamp", value) from error
    if parsed.tzinfo is None:
        raise DomainToolInputError(
            "operations.naive_timestamp", "timestamps must include timezone"
        )
    return parsed


def _aware_clock(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DomainToolInputError(
            "domain_tools.naive_clock", "clock must return a timezone-aware datetime"
        )
    return value


__all__ = [
    "DomainFinding", "DomainRow", "DomainToolInputError", "OperationsTools",
    "SourceMetadata",
]
