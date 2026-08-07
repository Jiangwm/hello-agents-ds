from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
from uuid import uuid4


NoteType = Literal[
    "fact", "hypothesis", "decision", "todo", "risk", "summary"
]

NOTE_TYPES = frozenset(
    {"fact", "hypothesis", "decision", "todo", "risk", "summary"}
)
NOTE_STATUSES = frozenset({"active", "resolved", "superseded", "archived"})
RISK_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


@dataclass(frozen=True)
class NoteRecord:
    id: str
    scenario_id: str
    note_type: NoteType
    status: str
    title: str
    content: str
    fields: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(data.pop("fields"))
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NoteRecord":
        required = {"id", "scenario_id", "note_type", "status", "title", "content"}
        missing = required.difference(data)
        if missing:
            raise ValueError(f"笔记缺少字段: {', '.join(sorted(missing))}")
        reserved = required | {"created_at", "updated_at", "fields"}
        extra_fields = dict(data.get("fields", {}))
        extra_fields.update({key: value for key, value in data.items() if key not in reserved})
        record = cls(
            id=_required_text(data["id"], "id"),
            scenario_id=_required_text(data["scenario_id"], "scenario_id"),
            note_type=_note_type(data["note_type"]),
            status=_note_status(data["status"]),
            title=_required_text(data["title"], "title"),
            content=_required_text(data["content"], "content"),
            fields=extra_fields,
            created_at=_required_text(data.get("created_at") or _utc_now(), "created_at"),
            updated_at=_required_text(data.get("updated_at") or _utc_now(), "updated_at"),
        )
        _validate_record(record)
        return record


class InvestigationNoteStore:
    def __init__(self, storage_path: str | Path) -> None:
        path = Path(storage_path)
        self.path = path / "investigation_notes.json" if path.suffix == "" else path
        self._records = self._load()

    def create(
        self,
        *,
        scenario_id: str,
        note_type: NoteType,
        title: str,
        content: str,
        status: str = "active",
        note_id: str | None = None,
        **fields: Any,
    ) -> NoteRecord:
        identifier = note_id or f"note_{uuid4().hex}"
        if identifier in self._records:
            raise ValueError(f"笔记已存在: {identifier}")
        now = _utc_now()
        record = NoteRecord(
            id=_required_text(identifier, "id"),
            scenario_id=_required_text(scenario_id, "scenario_id"),
            note_type=_note_type(note_type),
            status=_note_status(status),
            title=_required_text(title, "title"),
            content=_required_text(content, "content"),
            fields=dict(fields),
            created_at=now,
            updated_at=now,
        )
        _validate_record(record)
        self._records[record.id] = record
        self._save()
        return record

    def update(self, note_id: str, **changes: Any) -> NoteRecord:
        previous = self.get(note_id)
        if previous is None:
            raise KeyError(f"笔记不存在: {note_id}")
        if "id" in changes and changes["id"] != note_id:
            raise ValueError("不允许修改笔记 id")
        data = previous.to_dict()
        fields = {
            key: value
            for key, value in data.items()
            if key
            not in {
                "id",
                "scenario_id",
                "note_type",
                "status",
                "title",
                "content",
                "created_at",
                "updated_at",
            }
        }
        for key, value in changes.items():
            if key in {"scenario_id", "note_type", "status", "title", "content"}:
                data[key] = value
            elif key not in {"id", "created_at", "updated_at"}:
                fields[key] = value
        data.update(fields)
        data["updated_at"] = _utc_now()
        updated = NoteRecord.from_dict(data)
        self._records[note_id] = updated
        self._save()
        return updated

    def get(self, note_id: str) -> NoteRecord | None:
        return self._records.get(note_id)

    def list(
        self,
        *,
        scenario_id: str | None = None,
        note_type: NoteType | None = None,
        status: str | None = None,
    ) -> list[NoteRecord]:
        if note_type is not None:
            _note_type(note_type)
        if status is not None:
            _note_status(status)
        records = (
            record
            for record in self._records.values()
            if (scenario_id is None or record.scenario_id == scenario_id)
            and (note_type is None or record.note_type == note_type)
            and (status is None or record.status == status)
        )
        return sorted(records, key=lambda record: (record.updated_at, record.id), reverse=True)

    def counts(self, *, scenario_id: str | None = None) -> dict[str, Any]:
        records = self.list(scenario_id=scenario_id)
        by_type = {note_type: 0 for note_type in sorted(NOTE_TYPES)}
        by_status = {status: 0 for status in sorted(NOTE_STATUSES)}
        for record in records:
            by_type[record.note_type] += 1
            by_status[record.status] += 1
        return {"total": len(records), "by_type": by_type, "by_status": by_status}

    def _load(self) -> dict[str, NoteRecord]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as error:
            raise ValueError(f"笔记存储不是有效 JSON: {self.path}") from error
        records_data = payload.get("notes", []) if isinstance(payload, dict) else payload
        if not isinstance(records_data, list):
            raise ValueError("笔记存储中的 notes 必须是列表")
        records: dict[str, NoteRecord] = {}
        for item in records_data:
            if not isinstance(item, Mapping):
                raise ValueError("笔记存储包含非对象记录")
            record = NoteRecord.from_dict(item)
            if record.id in records:
                raise ValueError(f"笔记存储包含重复 ID: {record.id}")
            records[record.id] = record
        return records

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "notes": [
                record.to_dict()
                for record in sorted(self._records.values(), key=lambda item: item.id)
            ],
        }
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(self.path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _note_type(value: Any) -> NoteType:
    if value not in NOTE_TYPES:
        raise ValueError(f"不支持的笔记类型: {value!r}")
    return value


def _note_status(value: Any) -> str:
    if value not in NOTE_STATUSES:
        raise ValueError(f"不支持的笔记状态: {value!r}")
    return value


def _required_id_list(value: Any, name: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{name} 必须是{'可为空的' if allow_empty else '非空的'}列表")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} 的元素必须是非空字符串")


def _validate_record(record: NoteRecord) -> None:
    fields = record.fields
    if record.note_type == "fact":
        _required_id_list(fields.get("evidence_ids"), "evidence_ids")
    elif record.note_type == "hypothesis":
        _required_id_list(
            fields.get("supporting_evidence_ids"),
            "supporting_evidence_ids",
            allow_empty=True,
        )
        _required_id_list(
            fields.get("counter_evidence_ids"),
            "counter_evidence_ids",
            allow_empty=True,
        )
        _required_text(fields.get("missing_data"), "missing_data")
        _required_text(fields.get("next_action"), "next_action")
    elif record.note_type == "decision":
        if fields.get("human_confirmed") is not True:
            raise ValueError("decision 必须设置 human_confirmed=True")
        _required_text(fields.get("approver"), "approver")
    elif record.note_type == "todo":
        _required_text(fields.get("owner"), "owner")
        _required_text(fields.get("due_date"), "due_date")
        _required_id_list(fields.get("dependencies"), "dependencies", allow_empty=True)
    elif record.note_type == "risk":
        if fields.get("severity") not in RISK_SEVERITIES:
            raise ValueError("risk 的 severity 必须为 low/medium/high/critical")
        _required_text(fields.get("mitigation"), "mitigation")
    elif record.note_type == "summary":
        _required_text(fields.get("stage"), "stage")
        _required_id_list(
            fields.get("retained_note_ids"),
            "retained_note_ids",
            allow_empty=True,
        )
        compressed_count = fields.get("compressed_count")
        if (
            not isinstance(compressed_count, int)
            or isinstance(compressed_count, bool)
            or compressed_count < 0
        ):
            raise ValueError("compressed_count 必须是非负整数")
