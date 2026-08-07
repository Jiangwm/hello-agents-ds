from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import date, datetime, timezone

from schemas import (
    DocumentStatus,
    KnowledgeChunk,
    RetrievalAuditRecord,
    RetrievalHit,
    SourceType,
)


def _tokens(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    ascii_tokens = re.findall(r"[a-z0-9]+(?:[-_/][a-z0-9]+)*", normalized)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese_tokens: list[str] = []
    for run in chinese_runs:
        if len(run) == 1:
            chinese_tokens.append(run)
        else:
            chinese_tokens.extend(
                run[index : index + 2] for index in range(len(run) - 1)
            )
    return tuple(ascii_tokens + chinese_tokens)


class HybridRetriever:
    def __init__(self, chunks: list[KnowledgeChunk]) -> None:
        self.chunks = tuple(chunks)
        self._document_frequency = self._build_document_frequency()
        self.audit_records: list[RetrievalAuditRecord] = []

    def search(
        self,
        query: str,
        *,
        role: str,
        equipment_model: str,
        equipment_id: str | None = None,
        source_types: frozenset[SourceType] | None = None,
        top_k: int = 4,
        min_score: float = 0.08,
        as_of: date | None = None,
        start_date: date | None = None,
    ) -> tuple[RetrievalHit, ...]:
        if not query.strip():
            self._record_audit(
                query=query,
                role=role,
                equipment_model=equipment_model,
                equipment_id=equipment_id,
                source_types=source_types,
                top_k=top_k,
                min_score=min_score,
                status="refused",
                returned_chunk_ids=(),
            )
            return ()
        if top_k < 1:
            self._record_audit(
                query=query,
                role=role,
                equipment_model=equipment_model,
                equipment_id=equipment_id,
                source_types=source_types,
                top_k=top_k,
                min_score=min_score,
                status="failed",
                returned_chunk_ids=(),
            )
            raise ValueError("top_k 必须大于 0")
        current_date = as_of or date.today()
        query_counter = Counter(_tokens(query))
        visible_chunks = [
            chunk
            for chunk in self.chunks
            if self._is_visible(
                chunk,
                role=role,
                equipment_model=equipment_model,
                equipment_id=equipment_id,
                source_types=source_types,
                as_of=current_date,
                start_date=start_date,
            )
        ]
        candidates: list[RetrievalHit] = []
        for chunk in self._latest_versions(visible_chunks):
            chunk_counter = Counter(_tokens(chunk.content))
            keyword_score = self._keyword_score(
                query_counter,
                chunk_counter,
            )
            vector_score = self._cosine_score(
                query_counter,
                chunk_counter,
            )
            authority_boost = (
                0.04 if chunk.source_type is SourceType.AUTHORITATIVE else 0.0
            )
            score = 0.52 * keyword_score + 0.48 * vector_score
            score += authority_boost
            if score >= min_score:
                candidates.append(
                    RetrievalHit(
                        chunk=chunk,
                        score=round(min(score, 1.0), 6),
                        keyword_score=round(keyword_score, 6),
                        vector_score=round(vector_score, 6),
                    )
                )
        candidates.sort(
            key=lambda hit: (
                hit.score,
                hit.chunk.effective_date,
                hit.chunk.version,
            ),
            reverse=True,
        )
        results = tuple(candidates[:top_k])
        self._record_audit(
            query=query,
            role=role,
            equipment_model=equipment_model,
            equipment_id=equipment_id,
            source_types=source_types,
            top_k=top_k,
            min_score=min_score,
            status="completed" if results else "insufficient",
            returned_chunk_ids=tuple(
                hit.chunk.chunk_id for hit in results
            ),
        )
        return results

    @staticmethod
    def _latest_versions(
        chunks: list[KnowledgeChunk],
    ) -> tuple[KnowledgeChunk, ...]:
        latest: dict[
            tuple[SourceType, str, str, tuple[str, ...]],
            KnowledgeChunk,
        ] = {}
        for chunk in chunks:
            key = (
                chunk.source_type,
                chunk.document_id,
                chunk.paragraph_id,
                chunk.equipment_ids,
            )
            current = latest.get(key)
            if current is None or HybridRetriever._revision_key(
                chunk
            ) > HybridRetriever._revision_key(current):
                latest[key] = chunk
        return tuple(latest.values())

    @staticmethod
    def _revision_key(chunk: KnowledgeChunk) -> tuple[date, tuple[int, ...]]:
        version_numbers = tuple(
            int(value) for value in re.findall(r"\d+", chunk.version)
        )
        return chunk.effective_date, version_numbers

    def _record_audit(
        self,
        *,
        query: str,
        role: str,
        equipment_model: str,
        equipment_id: str | None,
        source_types: frozenset[SourceType] | None,
        top_k: int,
        min_score: float,
        status: str,
        returned_chunk_ids: tuple[str, ...],
    ) -> None:
        self.audit_records.append(
            RetrievalAuditRecord(
                audit_id=f"RAG-AUD-{len(self.audit_records) + 1:04d}",
                occurred_at=datetime.now(timezone.utc),
                status=status,
                query_sha256=hashlib.sha256(
                    query.encode("utf-8")
                ).hexdigest(),
                role=role,
                equipment_model=equipment_model,
                equipment_id=equipment_id,
                source_types=tuple(
                    sorted(
                        source_type.value
                        for source_type in (source_types or frozenset())
                    )
                ),
                top_k=top_k,
                min_score=min_score,
                returned_chunk_ids=returned_chunk_ids,
            )
        )

    def _build_document_frequency(self) -> Counter[str]:
        frequency: Counter[str] = Counter()
        for chunk in self.chunks:
            frequency.update(set(_tokens(chunk.content)))
        return frequency

    def _idf(self, token: str) -> float:
        total = max(len(self.chunks), 1)
        frequency = self._document_frequency.get(token, 0)
        return math.log((total + 1) / (frequency + 1)) + 1.0

    def _keyword_score(
        self,
        query: Counter[str],
        chunk: Counter[str],
    ) -> float:
        if not query:
            return 0.0
        matched = sum(
            self._idf(token) * min(count, chunk.get(token, 0))
            for token, count in query.items()
        )
        possible = sum(
            self._idf(token) * count for token, count in query.items()
        )
        return matched / possible if possible else 0.0

    def _cosine_score(
        self,
        query: Counter[str],
        chunk: Counter[str],
    ) -> float:
        if not query or not chunk:
            return 0.0
        query_vector = {
            token: count * self._idf(token) for token, count in query.items()
        }
        chunk_vector = {
            token: count * self._idf(token) for token, count in chunk.items()
        }
        dot = sum(
            weight * chunk_vector.get(token, 0.0)
            for token, weight in query_vector.items()
        )
        query_norm = math.sqrt(sum(value**2 for value in query_vector.values()))
        chunk_norm = math.sqrt(sum(value**2 for value in chunk_vector.values()))
        return dot / (query_norm * chunk_norm) if query_norm and chunk_norm else 0.0

    @staticmethod
    def _is_visible(
        chunk: KnowledgeChunk,
        *,
        role: str,
        equipment_model: str,
        equipment_id: str | None,
        source_types: frozenset[SourceType] | None,
        as_of: date,
        start_date: date | None,
    ) -> bool:
        if chunk.status is not DocumentStatus.EFFECTIVE:
            return False
        if chunk.effective_date > as_of:
            return False
        if start_date and chunk.event_date and chunk.event_date < start_date:
            return False
        if role not in chunk.allowed_roles:
            return False
        if chunk.license_name not in {"internal-training", "public"}:
            return False
        if equipment_model not in chunk.equipment_models:
            return False
        if source_types and chunk.source_type not in source_types:
            return False
        if chunk.equipment_ids and equipment_id not in chunk.equipment_ids:
            return False
        return True
