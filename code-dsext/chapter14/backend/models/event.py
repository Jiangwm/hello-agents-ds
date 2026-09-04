from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import Field

from .common import StrictModel


NonEmpty = Annotated[str, Field(min_length=1)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditRecord(StrictModel):
    record_id: NonEmpty
    run_id: NonEmpty
    action: NonEmpty
    actor_id: NonEmpty = "system"
    occurred_at: datetime = Field(default_factory=utc_now)
    details: tuple[tuple[NonEmpty, NonEmpty], ...] = ()


class StreamEvent(StrictModel):
    event_id: NonEmpty
    run_id: NonEmpty
    event_type: NonEmpty
    occurred_at: datetime = Field(default_factory=utc_now)
    payload: tuple[tuple[NonEmpty, NonEmpty], ...] = ()
