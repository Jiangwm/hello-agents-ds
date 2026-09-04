from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Literal, TypeAlias, assert_never

from backend.models import EvidenceRef, EvidenceStatus
from backend.services.repository import Repository
from backend.tools.workspace import CsvReadResult, DocumentReadResult, ReadOnlyWorkspace


ReadResult: TypeAlias = CsvReadResult | DocumentReadResult
ValidationStatus: TypeAlias = Literal["valid", "missing", "invalidated"]


@dataclass(frozen=True, slots=True)
class EvidenceLedgerError(ValueError):
    code: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class EvidenceValidation:
    evidence_id: str
    status: ValidationStatus
    expected_hash: str
    current_hash: str | None
    revalidated_at: datetime


class EvidenceLedger:
    """Persists immutable source references and explicit negative results."""

    def __init__(
        self,
        repository: Repository,
        workspace: ReadOnlyWorkspace,
        run_id: str,
    ) -> None:
        if not run_id:
            raise EvidenceLedgerError("run_id", "run_id must not be empty")
        self.repository = repository
        self.workspace = workspace
        self.run_id = run_id

    def record(
        self,
        result: ReadResult,
        *,
        tool_call_id: str,
        evidence_id: str,
    ) -> EvidenceRef:
        negative = result.no_result
        evidence = EvidenceRef(
            evidence_id=evidence_id,
            source_uri=result.resource,
            source_version=result.version,
            retrieved_at=datetime.now(timezone.utc),
            permission_level=result.permission,
            source_quality="manifest_sha256_verified",
            tool_call_id=tool_call_id,
            query=self._query_text(result),
            partition=result.domain,
            status=(
                EvidenceStatus.NEGATIVE_RESULT
                if negative
                else EvidenceStatus.FOUND
            ),
            negative_result=negative,
            content_hash=result.content_hash,
            confidence=1.0,
            data_window=result.data_window,
        )
        self.repository.replace_or_upsert(
            "evidence",
            self.run_id,
            evidence.evidence_id,
            evidence.model_dump(mode="json"),
        )
        return evidence

    def revalidate(self, evidence: EvidenceRef) -> EvidenceValidation:
        current_hash = self.workspace.current_hash(evidence.source_uri)
        if current_hash is None:
            status: ValidationStatus = "missing"
        elif current_hash != evidence.content_hash:
            status = "invalidated"
        else:
            status = "valid"
        revalidated_at = datetime.now(timezone.utc)
        payload = evidence.model_dump(mode="json")
        payload.update(
            {
                "validation_status": status,
                "revalidated_at": revalidated_at.isoformat(),
                "current_hash": current_hash,
            }
        )
        self.repository.replace_or_upsert(
            "evidence", self.run_id, evidence.evidence_id, payload
        )
        return EvidenceValidation(
            evidence_id=evidence.evidence_id,
            status=status,
            expected_hash=evidence.content_hash,
            current_hash=current_hash,
            revalidated_at=revalidated_at,
        )

    @staticmethod
    def _query_text(result: ReadResult) -> str:
        match result:
            case CsvReadResult(query=query):
                if not query:
                    return "all rows"
                return json.dumps(
                    query,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            case DocumentReadResult(query=query):
                return query or "full document"
            case unreachable:
                assert_never(unreachable)
