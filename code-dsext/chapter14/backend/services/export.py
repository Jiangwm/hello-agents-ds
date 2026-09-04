from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import tempfile

from backend.models import EvidenceRef, GateName, NoteStatus, ResearchNote, ResearchRun
from backend.services.evidence import EvidenceLedger
from backend.services.gates import GateRequest, GateService
from backend.services.reporting import (
    ResearchReport,
    _budget_version,
    compute_report_hash,
    report_binding_hash,
    report_is_current,
)
from backend.services.repository import JsonValue, Repository, StoredRecord, canonical_json
from backend.services.synthesis import SynthesisOutcome


_DENIED = "report must have a valid final-conclusion approval before export"
_SENSITIVE = (
    "token", "cookie", "api_key", "apikey", "password", "secret",
    "authorization", "raw_row", "raw_rows", "raw_line", "raw_record",
)


@dataclass(frozen=True, slots=True)
class ExportDeniedError(Exception):
    code: str = "export.denied"
    detail: str = _DENIED

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)

    def __str__(self) -> str:
        return _DENIED


@dataclass(frozen=True, slots=True)
class ExportWriteError(Exception):
    code: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    destination: Path
    manifest_sha256: str
    bytes_written: int


class ExportService:
    def __init__(
        self, repository: Repository, ledger: EvidenceLedger, gates: GateService
    ) -> None:
        self._repository = repository
        self._ledger = ledger
        self._gates = gates

    def export(
        self,
        run: ResearchRun,
        report: ResearchReport,
        final_request: GateRequest,
        destination: str | Path,
    ) -> ExportReceipt:
        self._authorize(run, report, final_request)
        target = Path(destination)
        if target.suffix.casefold() != ".json":
            raise ExportWriteError("export.json_required", "destination must use .json")
        package = self._package(run, report)
        manifest_sha = sha256(canonical_json(package).encode("utf-8")).hexdigest()
        exported = dict(package)
        exported["manifest"] = {"algorithm": "sha256", "sha256": manifest_sha}
        data = canonical_json(exported).encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f"{target.name}.",
                suffix=".tmp", delete=False,
            ) as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ExportWriteError("export.write_failed", str(error)) from error
        return ExportReceipt(target, manifest_sha, len(data))

    def _authorize(
        self, run: ResearchRun, report: ResearchReport, request: GateRequest
    ) -> None:
        valid_request = (
            request.run_id == run.run_id
            and request.name is GateName.FINAL_CONCLUSION
            and request.plan_version == run.plan_version
            and request.scope_version == run.scope_version
            and request.evidence_version == report_binding_hash(report)
            and request.budget_version == _budget_version(run)
        )
        if not (
            valid_request
            and report.synthesis_outcome is SynthesisOutcome.CANDIDATE
            and report_is_current(run, report)
            and self._gates.is_approved(request)
            and self._claims_valid(report)
            and self._records_current(run, report)
        ):
            raise ExportDeniedError()

    @staticmethod
    def _claims_valid(report: ResearchReport) -> bool:
        for claim in report.claims:
            payload: dict[str, JsonValue] = {
                "content": claim.content, "note_ids": list(claim.note_ids),
                "evidence_ids": list(claim.evidence_ids), "partition": claim.partition,
            }
            expected = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
            if claim.content_hash != expected or claim.claim_id != f"claim:{expected[:20]}":
                return False
            if claim.critical and (not claim.note_ids or not claim.evidence_ids):
                return False
        return True

    def _records_current(self, run: ResearchRun, report: ResearchReport) -> bool:
        for note_id, note_hash in report.note_hashes:
            record = self._find("notes", run.run_id, note_id)
            if record is None:
                return False
            if (
                record.payload.get("current") is not True
                or record.payload.get("status") != NoteStatus.APPROVED.value
                or record.payload.get("scope_version") != run.scope_version
                or record.payload.get("approval_scope_version") != run.scope_version
                or record.payload.get("note_hash") != note_hash
            ):
                return False
        for evidence_id, evidence_hash in report.evidence_hashes:
            record = self._find("evidence", run.run_id, evidence_id)
            if record is None or record.payload.get("content_hash") != evidence_hash:
                return False
            payload = {key: record.payload.get(key) for key in EvidenceRef.model_fields}
            evidence = EvidenceRef.model_validate_json(canonical_json(payload))
            if self._ledger.revalidate(evidence).status != "valid":
                return False
        return True

    def _package(
        self, run: ResearchRun, report: ResearchReport
    ) -> dict[str, JsonValue]:
        approved_notes = [
            self._sanitize(record.payload)
            for record in self._repository.list_records("notes", run.run_id)
            if record.payload.get("current") is True
            and record.payload.get("status") == NoteStatus.APPROVED.value
        ]
        return {
            "schema_version": "chapter14.evidence-package.v1",
            "run": {
                "run_id": run.run_id, "scope": run.scope.model_dump(mode="json"),
                "scope_version": run.scope_version, "plan_version": run.plan_version,
            },
            "report_markdown": report.markdown,
            "report_hash": report.report_hash,
            "claim_ledger": [
                {
                    "claim_id": item.claim_id, "content": item.content,
                    "note_ids": list(item.note_ids), "evidence_ids": list(item.evidence_ids),
                    "partition": item.partition, "content_hash": item.content_hash,
                    "critical": item.critical,
                }
                for item in report.claims
            ],
            "todos": [item.model_dump(mode="json") for item in run.todos],
            "budget": run.budget.model_dump(mode="json") if run.budget else None,
            "sources": [
                self._evidence_summary(run.run_id, evidence_id)
                for evidence_id, _ in report.evidence_hashes
            ],
            "approved_notes": approved_notes,
            "approvals": self._safe_records("approvals", run.run_id),
            "gates": self._safe_records("gates", run.run_id),
            "audit": self._safe_records("audit", run.run_id),
            "read_only": True,
            "offline": True,
            "advisory_only": True,
            "production_control": "prohibited",
        }

    def _evidence_summary(self, run_id: str, evidence_id: str) -> dict[str, JsonValue]:
        payload = self._safe_record("evidence", run_id, evidence_id)
        if payload is None:
            raise ExportDeniedError()
        summary = {
            key: payload.get(key) for key in EvidenceRef.model_fields
            if key not in {"content"}
        }
        summary["validation_status"] = payload.get("validation_status", "valid")
        return self._sanitize(summary)

    def _safe_records(self, table: str, run_id: str) -> list[JsonValue]:
        return [self._sanitize(record.payload) for record in self._repository.list_records(table, run_id)]

    def _safe_record(
        self, table: str, run_id: str, record_id: str
    ) -> dict[str, JsonValue] | None:
        record = self._find(table, run_id, record_id)
        return None if record is None else self._sanitize(record.payload)

    @staticmethod
    def _sanitize(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            key: "[REDACTED]" if _sensitive(key) else _sanitize_value(value)
            for key, value in payload.items()
        }

    def _find(self, table: str, run_id: str, record_id: str) -> StoredRecord | None:
        return next(
            (item for item in self._repository.list_records(table, run_id) if item.record_id == record_id),
            None,
        )


def _sanitize_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _sensitive(key) else _sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _sensitive(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE)


__all__ = ("ExportDeniedError", "ExportReceipt", "ExportService", "ExportWriteError")
