from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class DocumentStatus(str, Enum):
    EFFECTIVE = "effective"
    OBSOLETE = "obsolete"
    DRAFT = "draft"


class SourceType(str, Enum):
    AUTHORITATIVE = "authoritative"
    HISTORICAL = "historical"


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    title: str
    title_path: tuple[str, ...]
    paragraph_id: str
    version: str
    effective_date: date
    status: DocumentStatus
    source_type: SourceType
    equipment_models: tuple[str, ...]
    equipment_ids: tuple[str, ...]
    allowed_roles: frozenset[str]
    license_name: str
    content: str
    source_path: str
    page: int | None = None
    event_date: date | None = None


@dataclass(frozen=True)
class RetrievalHit:
    chunk: KnowledgeChunk
    score: float
    keyword_score: float
    vector_score: float


@dataclass(frozen=True)
class RetrievalAuditRecord:
    audit_id: str
    occurred_at: datetime
    status: str
    query_sha256: str
    role: str
    equipment_model: str
    equipment_id: str | None
    source_types: tuple[str, ...]
    top_k: int
    min_score: float
    returned_chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssistantAuditRecord:
    audit_id: str
    occurred_at: datetime
    status: str
    reason_code: str
    query_sha256: str
    equipment_id: str
    role: str
    citation_chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    document_id: str
    title: str
    version: str
    source_type: str
    source_path: str
    paragraph_id: str
    score: float
    page: int | None


@dataclass(frozen=True)
class AssistantResponse:
    status: str
    answer: str
    citations: tuple[Citation, ...] = ()
    needed_information: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    data_window: str | None = None
