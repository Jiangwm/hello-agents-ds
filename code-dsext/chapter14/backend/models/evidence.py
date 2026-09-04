from __future__ import annotations

from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, Self, assert_never

from pydantic import Field, model_validator

from .common import DomainValidationError, StrictModel


NonEmpty = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


@unique
class EvidenceStatus(StrEnum):
    FOUND = "found"
    NEGATIVE_RESULT = "negative_result"
    MISSING_DATA = "missing_data"
    CONFLICT = "conflict"


class EvidenceRef(StrictModel):
    evidence_id: NonEmpty
    source_uri: NonEmpty
    source_version: NonEmpty
    retrieved_at: datetime
    permission_level: NonEmpty
    source_quality: NonEmpty
    tool_call_id: NonEmpty
    query: NonEmpty
    partition: NonEmpty
    status: EvidenceStatus
    negative_result: bool
    content_hash: Sha256
    confidence: Confidence
    data_window: NonEmpty

    @model_validator(mode="after")
    def validate_retrieval(self) -> Self:
        if self.retrieved_at.tzinfo is None:
            raise DomainValidationError(
                "evidence.naive_retrieved_at", "retrieved_at 必须包含时区"
            )
        match self.status:
            case EvidenceStatus.FOUND | EvidenceStatus.MISSING_DATA | EvidenceStatus.CONFLICT:
                if self.negative_result:
                    raise DomainValidationError(
                        "evidence.invalid_negative_result", "该证据状态不能标记负面结果"
                    )
            case EvidenceStatus.NEGATIVE_RESULT:
                if not self.negative_result:
                    raise DomainValidationError(
                        "evidence.missing_negative_result", "无结果证据必须标记负面结果"
                    )
            case unreachable:
                assert_never(unreachable)
        return self


@unique
class NoteType(StrEnum):
    FACT = "fact"
    NEGATIVE_RESULT = "negative_result"
    HYPOTHESIS = "hypothesis"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    DECISION = "decision"
    VALIDATION_PLAN = "validation_plan"
    SUMMARY = "summary"


@unique
class NoteStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ResearchNote(StrictModel):
    note_id: NonEmpty
    note_type: NoteType
    status: NoteStatus
    todo_id: NonEmpty
    body: NonEmpty
    input_hash: Sha256
    evidence_ids: tuple[NonEmpty, ...] = ()
    reviewer: str | None = None
    supporting_evidence_ids: tuple[NonEmpty, ...] = ()
    counter_evidence_ids: tuple[NonEmpty, ...] = ()
    missing_evidence_ids: tuple[NonEmpty, ...] = ()
    next_action: str | None = None

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise DomainValidationError(
                "note.duplicate_evidence_id", "研究笔记中的证据 ID 不能重复"
            )
        references = (
            self.evidence_ids
            + self.supporting_evidence_ids
            + self.counter_evidence_ids
            + self.missing_evidence_ids
        )
        if len(references) != len(set(references)):
            raise DomainValidationError(
                "note.duplicate_evidence_reference", "笔记证据引用不能跨分类重复"
            )
        match self.status:
            case NoteStatus.APPROVED | NoteStatus.REJECTED:
                if self.reviewer is None:
                    raise DomainValidationError(
                        "note.missing_reviewer", "审批或拒绝笔记必须记录 reviewer"
                    )
            case NoteStatus.DRAFT | NoteStatus.SUPERSEDED:
                if self.reviewer is not None:
                    raise DomainValidationError(
                        "note.unexpected_reviewer", "草稿或已替换笔记不能记录 reviewer"
                    )
            case unreachable:
                assert_never(unreachable)
        return self
