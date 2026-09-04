from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from typing import Callable, Literal, TypeAlias, assert_never

from backend.models import EvidenceRef
from backend.services.authorization import (
    AccessRequest,
    ActorContext,
    AllowedAccess,
    AuthorizationService,
    AwaitingCrossDomainApproval,
    DeniedAccess,
)
from backend.services.evidence import EvidenceLedger
from backend.tools.operations import DomainToolInputError
from backend.tools.workspace import DocumentReadResult, ReadOnlyWorkspace


BlockedAccess: TypeAlias = AwaitingCrossDomainApproval | DeniedAccess
DocumentPartition: TypeAlias = Literal["internal_fact", "external_general"]
_INTERNAL_RESOURCES = (
    "documents/historical_8d.md",
    "documents/process_spec.md",
    "documents/supplier_advisory.md",
)
_EXTERNAL_RESOURCES = ("external_knowledge/general_mechanism.md",)
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*|[\u4e00-\u9fff]", re.IGNORECASE)
_REFERENCE = re.compile(
    r"(?im)^\s*(?:source_?url|url|path)\s*:\s*(\S+)\s*$"
)


@dataclass(frozen=True, slots=True)
class DocumentHit:
    source_uri: str
    source_version: str
    canonical_reference: str
    text: str
    score: int
    matched_tokens: tuple[str, ...]
    partition: DocumentPartition


@dataclass(frozen=True, slots=True)
class DocumentSourceMetadata:
    source_uri: str
    source_version: str
    permission: str
    content_hash: str
    data_window: str


@dataclass(frozen=True, slots=True)
class DocumentSearchResult:
    query: str
    partition: DocumentPartition
    hits: tuple[DocumentHit, ...]
    sources: tuple[DocumentSourceMetadata, ...]
    evidence: tuple[EvidenceRef, ...]
    no_result: bool
    satisfies_internal_evidence: bool
    created_at: datetime


class DocumentSearchTool:
    def __init__(
        self,
        workspace: ReadOnlyWorkspace,
        authorization: AuthorizationService,
        evidence_ledger: EvidenceLedger,
        *,
        clock: Callable[[], datetime] | None = None,
        max_text_length: int = 1000,
    ) -> None:
        if max_text_length < 1:
            raise DomainToolInputError(
                "documents.invalid_max_text_length", "max_text_length must be positive"
            )
        self._workspace = workspace
        self._authorization = authorization
        self._ledger = evidence_ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_text_length = max_text_length

    def search(
        self,
        actor: ActorContext,
        request: AccessRequest,
        *,
        query: str,
        tool_call_id: str,
    ) -> DocumentSearchResult | BlockedAccess:
        access = self._authorization.authorize(actor, request)
        match access:
            case AllowedAccess():
                pass
            case AwaitingCrossDomainApproval() | DeniedAccess():
                return access
            case unreachable:
                assert_never(unreachable)
        resources, partition = _resource_partition(request.domain)
        tokens = _tokens(query)
        if not tokens or not tool_call_id:
            raise DomainToolInputError(
                "documents.empty_search", "query and tool_call_id must not be empty"
            )
        read_results = tuple(
            self._workspace.read_document(resource, query=query)
            for resource in resources
        )
        evaluated = tuple(
            _evaluate(item, tokens, partition) for item in read_results
        )
        evidence = tuple(
            self._ledger.record(
                replace(item, domain=partition, no_result=score == 0),
                tool_call_id=tool_call_id,
                evidence_id=f"{tool_call_id}-{index}",
            )
            for index, (item, score, _) in enumerate(evaluated, start=1)
        )
        hits = tuple(
            sorted(
                (
                    DocumentHit(
                        source_uri=item.resource,
                        source_version=item.version,
                        canonical_reference=_canonical_reference(item),
                        text=item.content[: self._max_text_length].rstrip(),
                        score=score,
                        matched_tokens=matched,
                        partition=partition,
                    )
                    for item, score, matched in evaluated
                    if score > 0
                ),
                key=lambda hit: (-hit.score, hit.source_uri.casefold()),
            )
        )
        return DocumentSearchResult(
            query=query,
            partition=partition,
            hits=hits,
            sources=tuple(_metadata(item) for item in read_results),
            evidence=evidence,
            no_result=not hits,
            satisfies_internal_evidence=partition == "internal_fact" and bool(hits),
            created_at=_aware_clock(self._clock()),
        )


def _resource_partition(
    domain: str,
) -> tuple[tuple[str, ...], DocumentPartition]:
    if domain == "internal_docs":
        return _INTERNAL_RESOURCES, "internal_fact"
    if domain == "external_knowledge":
        return _EXTERNAL_RESOURCES, "external_general"
    raise DomainToolInputError(
        "documents.invalid_domain",
        "document request must target internal_docs or external_knowledge",
    )


def _tokens(query: str) -> tuple[str, ...]:
    if not isinstance(query, str):
        raise DomainToolInputError("documents.invalid_query", "query must be text")
    return tuple(sorted({token.casefold() for token in _TOKEN.findall(query)}))


def _evaluate(
    item: DocumentReadResult,
    tokens: tuple[str, ...],
    partition: DocumentPartition,
) -> tuple[DocumentReadResult, int, tuple[str, ...]]:
    content = item.content.casefold()
    matched = tuple(token for token in tokens if token in content)
    score = sum(content.count(token) for token in matched)
    return replace(item, domain=partition), score, matched


def _canonical_reference(item: DocumentReadResult) -> str:
    match = _REFERENCE.search(item.content)
    if match is None:
        return item.resource.replace("\\", "/")
    return match.group(1).replace("\\", "/")


def _metadata(item: DocumentReadResult) -> DocumentSourceMetadata:
    return DocumentSourceMetadata(
        source_uri=item.resource,
        source_version=item.version,
        permission=item.permission,
        content_hash=item.content_hash,
        data_window=item.data_window,
    )


def _aware_clock(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DomainToolInputError(
            "domain_tools.naive_clock", "clock must return a timezone-aware datetime"
        )
    return value


__all__ = [
    "DocumentHit", "DocumentPartition", "DocumentSearchResult",
    "DocumentSearchTool", "DocumentSourceMetadata",
]
