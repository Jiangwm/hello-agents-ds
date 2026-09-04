from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, Protocol, runtime_checkable


SCHEMA_VERSION = 1
_RECORD_TABLES = frozenset({"todos", "evidence", "notes", "gates", "approvals", "audit"})
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonInput = Mapping[str, JsonValue]


@runtime_checkable
class ModelPayload(Protocol):
    def model_dump(self, *, mode: str) -> Mapping[str, JsonValue]: ...


class RepositoryError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str) -> None:
        RuntimeError.__init__(self, detail)
        self.code = code
        self.detail = detail

    def __setattr__(self, name: str, value: JsonValue) -> None:
        if name in frozenset({"code", "detail"}) and hasattr(self, name):
            raise AttributeError("repository error fields are immutable")
        RuntimeError.__setattr__(self, name, value)


class InvalidRepositoryInput(RepositoryError):
    pass


class InvalidJsonPayload(RepositoryError):
    pass


class PayloadValidationError(RepositoryError):
    pass


class VersionConflict(RepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    version: int
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StoredRecord:
    table: str
    run_id: str
    record_id: str
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StoredEvent:
    event_id: int
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, JsonValue]


def canonical_json(payload: JsonInput) -> str:
    try:
        return json.dumps(
            _payload_mapping(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise PayloadValidationError("payload_json", "payload is not canonical JSON") from error


class Repository:
    def __init__(
        self, database_path: str | Path, workspace_root: str | Path | None = None
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root
            else self._sample_root()
        )
        if _is_within(self.database_path, root):
            raise InvalidRepositoryInput(
                "workspace_database", "database_path must be outside workspace_root"
            )

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_info'"
            ).fetchone()
            if not exists:
                for statement in self._schema_statements():
                    connection.execute(statement)
            version = connection.execute(
                "SELECT schema_version FROM schema_info WHERE singleton = 1"
            ).fetchone()
            if version is None or version[0] != SCHEMA_VERSION:
                raise RepositoryError("unsupported_schema", "unsupported schema_version")

    def create_run(self, run_id: str, payload: JsonInput) -> RunRecord:
        payload_text = canonical_json(payload)
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO runs (id, version, payload) VALUES (?, 1, ?)",
                (run_id, payload_text),
            )
        return RunRecord(run_id, 1, _decode_payload(payload_text))

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT version, payload FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunRecord(run_id, row[0], _decode_payload(row[1]))

    def update_run(
        self, run_id: str, payload: JsonInput, expected_version: int
    ) -> RunRecord:
        if expected_version < 1:
            raise InvalidRepositoryInput(
                "invalid_version", "expected_version must be positive"
            )
        payload_text = canonical_json(payload)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET version = version + 1, payload = ? "
                "WHERE id = ? AND version = ?",
                (payload_text, run_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict("version_conflict", "run version conflict")
        return RunRecord(run_id, expected_version + 1, _decode_payload(payload_text))

    def replace_or_upsert(
        self, table: str, run_id: str, record_id: str, payload: JsonInput
    ) -> StoredRecord:
        self._require_record_table(table)
        payload_text = canonical_json(payload)
        with self._transaction() as connection:
            connection.execute(
                f"INSERT INTO {table} (run_id, id, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, id) DO UPDATE SET payload = excluded.payload",
                (run_id, record_id, payload_text),
            )
        return StoredRecord(table, run_id, record_id, _decode_payload(payload_text))

    def list_records(self, table: str, run_id: str) -> list[StoredRecord]:
        self._require_record_table(table)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT id, payload FROM {table} WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [
            StoredRecord(table, run_id, row[0], _decode_payload(row[1]))
            for row in rows
        ]

    def append_event(
        self, run_id: str, event_type: str, payload: JsonInput
    ) -> StoredEvent:
        payload_text = canonical_json(payload)
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO events (run_id, sequence, event_type, payload) "
                "VALUES (?, COALESCE((SELECT MAX(sequence) + 1 FROM events "
                "WHERE run_id = ?), 1), ?, ?)",
                (run_id, run_id, event_type, payload_text),
            )
            row = connection.execute(
                "SELECT sequence FROM events WHERE event_id = ?", (cursor.lastrowid,)
            ).fetchone()
        return StoredEvent(
            cursor.lastrowid, run_id, row[0], event_type, _decode_payload(payload_text)
        )

    def list_events(self, run_id: str, after_id: int = 0) -> list[StoredEvent]:
        if after_id < 0:
            raise InvalidRepositoryInput("invalid_cursor", "after_id must not be negative")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_id, sequence, event_type, payload FROM events "
                "WHERE run_id = ? AND event_id > ? ORDER BY sequence",
                (run_id, after_id),
            ).fetchall()
        return [
            StoredEvent(row[0], run_id, row[1], row[2], _decode_payload(row[3]))
            for row in rows
        ]

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except (sqlite3.Error, RepositoryError, json.JSONDecodeError):
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _schema_statements(self) -> list[str]:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        return [statement.strip() for statement in schema.split(";") if statement.strip()]

    def _sample_root(self) -> Path:
        return Path(__file__).resolve().parents[2] / "sample_workspace"

    def _require_record_table(self, table: str) -> None:
        if table not in _RECORD_TABLES:
            raise InvalidRepositoryInput("record_table", "unsupported record table")


def _payload_mapping(payload: JsonInput) -> dict[str, JsonValue]:
    if isinstance(payload, ModelPayload):
        payload = payload.model_dump(mode="json")
    elif is_dataclass(payload) and not isinstance(payload, type):
        payload = asdict(payload)
    if not isinstance(payload, Mapping):
        raise PayloadValidationError(
            "payload_shape", "payload must be a mapping or typed model"
        )
    return _json_mapping(payload)


def _decode_payload(payload: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise InvalidJsonPayload("invalid_json", "invalid JSON payload") from error
    if not isinstance(decoded, dict):
        raise InvalidJsonPayload("invalid_json", "invalid JSON payload")
    return _json_mapping(decoded)


def _json_mapping(values: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {_valid_json_key(key): _json_value(value) for key, value in values.items()}


def _valid_json_key(key: str) -> str:
    if not isinstance(key, str):
        raise PayloadValidationError("payload_key", "payload keys must be strings")
    return key


def _json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return _json_mapping(value)
    raise PayloadValidationError(
        "payload_value", "payload contains a non-JSON value"
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
