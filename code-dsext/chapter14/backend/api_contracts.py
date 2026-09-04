from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    reviewer: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    note_id: Annotated[str, Field(min_length=1)] | None = None
    approved: bool | None = None

    @model_validator(mode="after")
    def validate_note_decision(self) -> Self:
        if (self.note_id is None) != (self.approved is None):
            raise ValueError("note_id and approved must be provided together")
        return self


__all__ = ("ReviewInput",)
