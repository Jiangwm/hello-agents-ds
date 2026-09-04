from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import assert_never

from backend.models import EvidenceRef, NoteStatus, NoteType, ResearchNote
from backend.services.evidence import EvidenceLedger
from backend.services.repository import JsonValue, Repository, StoredRecord, canonical_json


@dataclass(frozen=True, slots=True)
class NoteServiceError(Exception):
    code: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class NoteService:
    def __init__(self, repository: Repository, ledger: EvidenceLedger, run_id: str) -> None:
        if ledger.run_id != run_id:
            raise NoteServiceError("note.run_mismatch", "ledger 必须与笔记使用同一 run")
        self._repository = repository
        self._ledger = ledger
        self._run_id = run_id

    def create(
        self, note_id: str, note_type: NoteType, todo_id: str, body: str,
        scope_version: str, *,
        evidence_ids: tuple[str, ...] = (),
        supporting_evidence_ids: tuple[str, ...] = (),
        counter_evidence_ids: tuple[str, ...] = (),
        missing_evidence_ids: tuple[str, ...] = (),
        next_action: str | None = None,
    ) -> ResearchNote:
        for value, field in ((note_id, "note_id"), (todo_id, "todo_id"),
                             (body, "body"), (scope_version, "scope_version")):
            self._require_text(value, field)
        if self._find(note_id) is not None:
            raise NoteServiceError("note.duplicate", f"笔记已存在: {note_id}")
        note = self._build_note(
            note_id, note_type, todo_id, body, evidence_ids, supporting_evidence_ids,
            counter_evidence_ids, missing_evidence_ids, next_action)
        self._store(note, scope_version, 1, None, True)
        return note

    def revise(
        self, note_id: str, body: str, scope_version: str, *,
        revised_note_id: str | None = None,
        evidence_ids: tuple[str, ...] | None = None,
        supporting_evidence_ids: tuple[str, ...] | None = None,
        counter_evidence_ids: tuple[str, ...] | None = None,
        missing_evidence_ids: tuple[str, ...] | None = None,
        next_action: str | None = None,
    ) -> ResearchNote:
        record, original = self._required_current(note_id)
        revision_value = record.payload.get("revision", 1)
        if not isinstance(revision_value, int):
            raise NoteServiceError("note.stored_field", "笔记字段无效: revision")
        revision = revision_value + 1
        new_id = revised_note_id or f"{record.payload.get('root_note_id', note_id)}:r{revision}"
        revised = self._build_note(
            new_id, original.note_type, original.todo_id, body,
            original.evidence_ids if evidence_ids is None else evidence_ids,
            original.supporting_evidence_ids if supporting_evidence_ids is None else supporting_evidence_ids,
            original.counter_evidence_ids if counter_evidence_ids is None else counter_evidence_ids,
            original.missing_evidence_ids if missing_evidence_ids is None else missing_evidence_ids,
            original.next_action if next_action is None else next_action)
        self._supersede(record, "revision_created", "system", current=False)
        self._store(revised, scope_version, revision, original.note_id, True)
        return revised

    def approve(self, note_id: str, reviewer: str, reason: str,
                scope_version: str) -> ResearchNote:
        self._require_text(reviewer, "reviewer")
        self._require_text(reason, "reason")
        record, note = self._required_current(note_id)
        if note.status is not NoteStatus.DRAFT:
            raise NoteServiceError("note.not_draft", "只有草稿笔记可以批准")
        if self._text(record, "scope_version") != scope_version:
            raise NoteServiceError("note.scope_mismatch", "批准范围与笔记范围不一致")
        hashes = self._valid_evidence_hashes(note, fail_invalid=True)
        bindings: dict[str, JsonValue] = {
            "note_hash": note.input_hash, "evidence_hashes": dict(hashes),
            "approval_input_hash": self._approval_hash(note.input_hash, hashes, scope_version),
            "approval_scope_version": scope_version,
        }
        return self._persist_decision(record, note, NoteStatus.APPROVED, reviewer, reason, bindings)

    def reject(self, note_id: str, reviewer: str, reason: str) -> ResearchNote:
        self._require_text(reviewer, "reviewer")
        self._require_text(reason, "reason")
        record, note = self._required_current(note_id)
        if note.status is not NoteStatus.DRAFT:
            raise NoteServiceError("note.not_draft", "只有草稿笔记可以拒绝")
        return self._persist_decision(record, note, NoteStatus.REJECTED, reviewer, reason, {})

    def list_current(self) -> tuple[ResearchNote, ...]:
        return tuple(
            self._note(record) for record in self._repository.list_records("notes", self._run_id)
            if record.payload.get("current") is True
        )

    def list_approved_valid(self, scope_version: str) -> tuple[ResearchNote, ...]:
        self.revalidate(scope_version)
        return tuple(note for note in self.list_current() if note.status is NoteStatus.APPROVED)

    def revalidate(self, scope_version: str) -> tuple[str, ...]:
        self._require_text(scope_version, "scope_version")
        invalidated: list[str] = []
        for record in self._repository.list_records("notes", self._run_id):
            note = self._note(record)
            if record.payload.get("current") is not True or note.status is not NoteStatus.APPROVED:
                continue
            reason = self._invalid_reason(record, note, scope_version)
            if reason is not None:
                self._supersede(record, reason, "system")
                invalidated.append(note.note_id)
        return tuple(invalidated)

    def supersede_for_scope(self, scope_version: str, *, actor: str = "system",
                            reason: str = "scope_version_changed") -> tuple[str, ...]:
        for value, field in ((scope_version, "scope_version"), (actor, "actor"), (reason, "reason")):
            self._require_text(value, field)
        invalidated: list[str] = []
        for record in self._repository.list_records("notes", self._run_id):
            note = self._note(record)
            if (record.payload.get("current") is True and note.status is NoteStatus.APPROVED
                    and self._text(record, "scope_version") != scope_version):
                self._supersede(record, reason, actor)
                invalidated.append(note.note_id)
        return tuple(invalidated)

    def _persist_decision(
        self, record: StoredRecord, note: ResearchNote, status: NoteStatus, reviewer: str,
        reason: str, bindings: dict[str, JsonValue],
    ) -> ResearchNote:
        decided = note.model_copy(update={"status": status, "reviewer": reviewer.strip()})
        payload = dict(record.payload)
        payload.update(decided.model_dump(mode="json"))
        payload.update(bindings)
        payload["review_reason"] = reason.strip()
        self._repository.replace_or_upsert("notes", self._run_id, note.note_id, payload)
        self._audit(f"note.{status.value}", note.note_id, reviewer, reason)
        return decided

    def _invalid_reason(self, record: StoredRecord, note: ResearchNote, scope_version: str) -> str | None:
        if self._text(record, "scope_version") != scope_version:
            return "scope_version_changed"
        if self._note_hash(note) != note.input_hash or record.payload.get("note_hash") != note.input_hash:
            return "note_hash_changed"
        hashes = self._valid_evidence_hashes(note, fail_invalid=False)
        if record.payload.get("evidence_hashes") != dict(hashes):
            return "evidence_hash_changed"
        expected = self._approval_hash(note.input_hash, hashes, scope_version)
        return "approval_binding_changed" if record.payload.get("approval_input_hash") != expected else None

    def _valid_evidence_hashes(self, note: ResearchNote, *, fail_invalid: bool) -> tuple[tuple[str, str], ...]:
        hashes: list[tuple[str, str]] = []
        referenced = note.evidence_ids + note.supporting_evidence_ids + note.counter_evidence_ids
        records = self._repository.list_records("evidence", self._run_id)
        for evidence_id in referenced:
            record = next((item for item in records if item.record_id == evidence_id), None)
            if record is None:
                if fail_invalid:
                    raise NoteServiceError("note.evidence_missing", f"证据不存在: {evidence_id}")
                return ()
            evidence = self._evidence(record)
            if self._ledger.revalidate(evidence).status != "valid":
                if fail_invalid:
                    raise NoteServiceError("note.evidence_invalid", f"证据已失效: {evidence_id}")
                return ()
            hashes.append((evidence_id, evidence.content_hash))
        return tuple(hashes)

    def _supersede(self, record: StoredRecord, reason: str, actor: str, *, current: bool = True) -> None:
        note = self._note(record)
        superseded = note.model_copy(update={"status": NoteStatus.SUPERSEDED, "reviewer": None})
        payload = dict(record.payload)
        payload.update(superseded.model_dump(mode="json"))
        payload.update({"superseded_reason": reason, "current": current})
        self._repository.replace_or_upsert("notes", self._run_id, note.note_id, payload)
        self._audit("note.superseded", note.note_id, actor, reason)

    def _store(
        self, note: ResearchNote, scope_version: str, revision: int, previous_note_id: str | None,
        current: bool,
    ) -> None:
        payload = note.model_dump(mode="json")
        payload.update({"scope_version": scope_version, "note_hash": note.input_hash,
            "revision": revision, "previous_note_id": previous_note_id,
            "root_note_id": note.note_id if previous_note_id is None else previous_note_id,
            "current": current})
        self._repository.replace_or_upsert("notes", self._run_id, note.note_id, payload)

    def _build_note(
        self, note_id: str, note_type: NoteType, todo_id: str, body: str,
        evidence_ids: tuple[str, ...], supporting: tuple[str, ...], counter: tuple[str, ...],
        missing: tuple[str, ...], next_action: str | None,
    ) -> ResearchNote:
        self._validate_type(note_type, evidence_ids, supporting, counter, missing, next_action)
        note = ResearchNote(note_id=note_id, note_type=note_type, status=NoteStatus.DRAFT,
            todo_id=todo_id, body=body, input_hash="0" * 64, evidence_ids=evidence_ids,
            supporting_evidence_ids=supporting,
            counter_evidence_ids=counter, missing_evidence_ids=missing,
            next_action=next_action)
        return note.model_copy(update={"input_hash": self._note_hash(note)})

    @staticmethod
    def _note_hash(note: ResearchNote) -> str:
        payload = {key: value for key, value in note.model_dump(mode="json").items()
                   if key not in {"input_hash", "status", "reviewer"}}
        return sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _approval_hash(note_hash: str, hashes: tuple[tuple[str, str], ...], scope_version: str) -> str:
        payload: dict[str, JsonValue] = {"note_hash": note_hash,
            "evidence_hashes": dict(hashes), "scope_version": scope_version}
        return sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_type(
        note_type: NoteType, evidence_ids: tuple[str, ...], supporting: tuple[str, ...],
        counter: tuple[str, ...], missing: tuple[str, ...], next_action: str | None) -> None:
        match note_type:
            case NoteType.FACT | NoteType.NEGATIVE_RESULT:
                if not evidence_ids:
                    raise NoteServiceError("note.evidence_required", "事实与负面结果必须引用证据")
            case NoteType.HYPOTHESIS:
                if not (supporting and counter and missing and next_action and next_action.strip()):
                    raise NoteServiceError("note.hypothesis_fields", "假设必须记录支持、反证、缺失证据和下一步")
            case NoteType.CONFLICT | NoteType.UNKNOWN | NoteType.DECISION | NoteType.VALIDATION_PLAN | NoteType.SUMMARY:
                return
            case unreachable:
                assert_never(unreachable)

    def _find(self, note_id: str) -> StoredRecord | None:
        return next((item for item in self._repository.list_records("notes", self._run_id) if item.record_id == note_id), None)

    def _required_current(self, note_id: str) -> tuple[StoredRecord, ResearchNote]:
        record = self._find(note_id)
        if record is None:
            raise NoteServiceError("note.not_found", f"笔记不存在: {note_id}")
        if record.payload.get("current") is not True:
            raise NoteServiceError("note.not_current", "只能操作当前笔记修订")
        return record, self._note(record)

    @staticmethod
    def _note(record: StoredRecord) -> ResearchNote:
        payload = {key: record.payload.get(key) for key in ResearchNote.model_fields}
        return ResearchNote.model_validate_json(canonical_json(payload))

    @staticmethod
    def _evidence(record: StoredRecord) -> EvidenceRef:
        payload = {key: record.payload.get(key) for key in EvidenceRef.model_fields}
        return EvidenceRef.model_validate_json(canonical_json(payload))

    @staticmethod
    def _text(record: StoredRecord, field: str) -> str:
        value = record.payload.get(field)
        if not isinstance(value, str):
            raise NoteServiceError("note.stored_field", f"笔记字段无效: {field}")
        return value

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not value.strip():
            raise NoteServiceError("note.blank_field", f"{field} 不能为空")

    def _audit(self, action: str, note_id: str, actor: str, reason: str) -> None:
        occurred_at = datetime.now(timezone.utc).isoformat()
        audit_id = sha256(f"{action}|{note_id}|{actor}|{occurred_at}".encode()).hexdigest()
        self._repository.replace_or_upsert("audit", self._run_id, audit_id,
            {"record_id": audit_id, "run_id": self._run_id, "action": action,
            "actor_id": actor.strip(), "occurred_at": occurred_at,
            "details": {"note_id": note_id, "reason": reason.strip()}})
