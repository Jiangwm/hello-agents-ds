from __future__ import annotations

import json
from typing import Literal, Mapping

try:
    from backend.services.audit import redact_sensitive
    from backend.services.repository import JsonValue, Repository, StoredEvent
except ModuleNotFoundError as error:
    if error.name != "backend":
        raise
    from services.audit import redact_sensitive
    from services.repository import JsonValue, Repository, StoredEvent


type EventType = Literal[
    "status",
    "todo_list",
    "task_status",
    "tool_call",
    "evidence",
    "note",
    "budget",
    "gate",
    "blocked",
    "conflict",
    "report",
    "done",
    "error",
]
_EVENT_TYPES = frozenset(
    {
        "status",
        "todo_list",
        "task_status",
        "tool_call",
        "evidence",
        "note",
        "budget",
        "gate",
        "blocked",
        "conflict",
        "report",
        "done",
        "error",
    }
)
_TERMINAL_EVENT_TYPES = frozenset({"done", "error"})


class EventInputError(ValueError):
    pass


class EventService:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def emit(
        self,
        run_id: str,
        event_type: EventType,
        payload: Mapping[str, JsonValue],
    ) -> StoredEvent:
        if event_type not in _EVENT_TYPES:
            raise EventInputError("event_type")
        if any(
            event.event_type in _TERMINAL_EVENT_TYPES for event in self.replay(run_id)
        ):
            raise EventInputError("run_terminal")
        safe_payload = redact_sensitive(dict(payload))
        if not isinstance(safe_payload, dict):
            raise EventInputError("payload")
        return self._repository.append_event(run_id, event_type, safe_payload)

    def replay(self, run_id: str, after_id: int = 0) -> tuple[StoredEvent, ...]:
        if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
            raise EventInputError("after_id")
        return tuple(self._repository.list_events(run_id, after_id))

    def encode_sse(self, event: StoredEvent) -> str:
        payload = json.dumps(
            event.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return (
            f"id: {event.event_id}\n"
            f"event: {event.event_type}\n"
            f"data: {payload}\n\n"
        )

    def stream_frames(
        self, run_id: str, after_id: int = 0, keepalive: bool = False
    ) -> tuple[str, ...]:
        frames: list[str] = []
        for event in self.replay(run_id, after_id):
            frames.append(self.encode_sse(event))
            if event.event_type in _TERMINAL_EVENT_TYPES:
                break
        if not frames and keepalive:
            frames.append(": keepalive\n\n")
        return tuple(frames)
