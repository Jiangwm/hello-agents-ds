from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from backend.models.evidence import (
    EvidenceRef,
    EvidenceStatus,
    NoteStatus,
    NoteType,
    ResearchNote,
)


class SynthesisOutcome(StrEnum):
    CANDIDATE = "candidate"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class SynthesisValidationError(Exception):
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class CandidateConclusion:
    claim: str
    supporting_note_ids: tuple[str, ...]
    counter_note_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    falsification_conditions: tuple[str, ...]
    unknowns: tuple[str, ...]
    confidence: float
    correlation_only: Literal[True] = True


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    outcome: SynthesisOutcome
    candidates: tuple[CandidateConclusion, ...]
    blocked_reason: str | None
    validation_plan: tuple[str, ...]
    scope_version: str


class SynthesisService:
    def synthesize(
        self,
        notes: tuple[ResearchNote, ...],
        evidence: tuple[EvidenceRef, ...],
        scope_version: str,
    ) -> SynthesisResult:
        if not scope_version.strip():
            raise SynthesisValidationError(
                code="synthesis.blank_scope_version",
                detail="scope_version must not be blank",
            )
        current = tuple(sorted(
            (item for item in notes if item.status is NoteStatus.APPROVED),
            key=lambda item: item.note_id,
        ))
        evidence_by_id = {item.evidence_id: item for item in evidence}
        explicit_conflicts = tuple(
            item for item in current if item.note_type is NoteType.CONFLICT
        )
        if explicit_conflicts or _has_mutually_exclusive_facts(current, evidence_by_id):
            actions = tuple(
                item.next_action for item in explicit_conflicts if item.next_action
            )
            return SynthesisResult(
                outcome=SynthesisOutcome.CONFLICT,
                candidates=(),
                blocked_reason="存在待人工裁决的冲突证据或冲突笔记",
                validation_plan=("由人工复核冲突证据后重新综合",) + actions,
                scope_version=scope_version,
            )

        candidates: list[CandidateConclusion] = []
        for item in current:
            if not _is_candidate_type(item.note_type):
                continue
            support_ids = tuple(sorted(set(
                item.supporting_evidence_ids or item.evidence_ids
            )))
            internal_ids = tuple(
                evidence_id for evidence_id in support_ids
                if evidence_id in evidence_by_id
                and evidence_by_id[evidence_id].partition != "external_general"
                and evidence_by_id[evidence_id].status is EvidenceStatus.FOUND
            )
            if not internal_ids:
                continue
            counter_ids = tuple(sorted(set(item.counter_evidence_ids)))
            support_confidence = sum(
                evidence_by_id[evidence_id].confidence for evidence_id in internal_ids
            ) / len(internal_ids)
            known_counters = tuple(
                evidence_id for evidence_id in counter_ids
                if evidence_id in evidence_by_id
            )
            counter_confidence = (
                sum(evidence_by_id[evidence_id].confidence for evidence_id in known_counters)
                / len(known_counters)
                if known_counters else 0.0
            )
            if support_confidence >= 0.75 and counter_confidence >= 0.75:
                return SynthesisResult(
                    outcome=SynthesisOutcome.CONFLICT,
                    candidates=(),
                    blocked_reason="高质量支持证据与反证尚未完成裁决",
                    validation_plan=(
                        "由人工裁决支持证据与反证的适用窗口",
                        item.next_action or "复核对照窗口并记录裁决依据",
                    ),
                    scope_version=scope_version,
                )
            counter_notes = tuple(sorted(
                note.note_id for note in current
                if note.note_type is NoteType.NEGATIVE_RESULT
                and bool(set(note.evidence_ids) & set(known_counters))
            ))
            confidence = round(
                support_confidence * (1.0 - 0.5 * counter_confidence), 6
            )
            falsification = (
                ("反证若经复核成立则否定该候选",)
                if known_counters
                else ("独立验证窗口未复现同方向关联则否定该候选",)
            )
            candidates.append(CandidateConclusion(
                claim=f"候选关联因素（待验证）：{item.body}",
                supporting_note_ids=(item.note_id,),
                counter_note_ids=counter_notes,
                supporting_evidence_ids=internal_ids,
                counter_evidence_ids=known_counters,
                falsification_conditions=falsification,
                unknowns=("因果关系尚未验证",),
                confidence=confidence,
            ))

        if candidates:
            return SynthesisResult(
                outcome=SynthesisOutcome.CANDIDATE,
                candidates=tuple(candidates),
                blocked_reason=None,
                validation_plan=("由人工复核候选并执行独立窗口验证",),
                scope_version=scope_version,
            )
        has_external = any(
            item.partition == "external_general" for item in evidence
        )
        plan = (
            ("补充当前范围的内部事实证据后重新综合",)
            if has_external
            else ("补充当前范围的内部事实与反证后重新综合",)
        )
        return SynthesisResult(
            outcome=SynthesisOutcome.UNKNOWN,
            candidates=(),
            blocked_reason=None,
            validation_plan=plan,
            scope_version=scope_version,
        )


def _is_candidate_type(note_type: NoteType) -> bool:
    return note_type in {NoteType.FACT, NoteType.HYPOTHESIS}


def _has_mutually_exclusive_facts(
    notes: tuple[ResearchNote, ...],
    evidence_by_id: dict[str, EvidenceRef],
) -> bool:
    statements: dict[str, set[bool]] = {}
    for note in notes:
        if note.note_type is not NoteType.FACT:
            continue
        evidence_ids = note.supporting_evidence_ids or note.evidence_ids
        internal = any(
            evidence_id in evidence_by_id
            and evidence_by_id[evidence_id].partition != "external_general"
            for evidence_id in evidence_ids
        )
        if not internal:
            continue
        normalized, negative = _normalized_statement(note.body)
        statements.setdefault(normalized, set()).add(negative)
    return any(values == {False, True} for values in statements.values())


def _normalized_statement(body: str) -> tuple[str, bool]:
    markers = ("未", "没有", "无", " not ", " no ")
    lowered = body.casefold()
    negative = any(marker in lowered for marker in markers)
    normalized = lowered
    for marker in markers:
        normalized = normalized.replace(marker, "")
    return "".join(normalized.split()), negative


__all__ = (
    "CandidateConclusion",
    "SynthesisOutcome",
    "SynthesisResult",
    "SynthesisService",
    "SynthesisValidationError",
)
