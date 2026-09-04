from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from backend.models import EvidenceRef, GateName, NoteStatus, NoteType, ResearchNote, ResearchRun
from backend.services.evidence import EvidenceLedger
from backend.services.gates import GateRequest
from backend.services.repository import JsonValue, Repository, StoredRecord, canonical_json
from backend.services.synthesis import SynthesisOutcome, SynthesisResult


type ClaimPartition = Literal["内部事实", "外部通用知识"]


@dataclass(frozen=True, slots=True)
class ReportingError(Exception):
    code: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    claim_id: str
    content: str
    note_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    partition: ClaimPartition
    content_hash: str
    critical: bool


@dataclass(frozen=True, slots=True)
class ResearchReport:
    run_id: str
    scope_version: str
    plan_version: str
    markdown: str
    claims: tuple[ClaimRecord, ...]
    note_hashes: tuple[tuple[str, str], ...]
    evidence_hashes: tuple[tuple[str, str], ...]
    synthesis_outcome: SynthesisOutcome
    validation_plan: tuple[str, ...]
    report_hash: str


class ReportingService:
    def __init__(self, repository: Repository, ledger: EvidenceLedger) -> None:
        self._repository = repository
        self._ledger = ledger

    def generate(self, run: ResearchRun, approved_notes: tuple[ResearchNote, ...],
                 synthesis: SynthesisResult) -> ResearchReport:
        self._require_candidate(run, approved_notes, synthesis)
        evidence: dict[str, EvidenceRef] = {}
        for note in approved_notes:
            evidence.update(self._validate_note(run, note))
        claims = self._claims(approved_notes, evidence)
        claim_evidence = {item for claim in claims for item in claim.evidence_ids}
        claim_notes = {item for claim in claims for item in claim.note_ids}
        for candidate in synthesis.candidates:
            if (
                not set(candidate.supporting_note_ids) <= claim_notes
                or not set(candidate.supporting_evidence_ids) <= claim_evidence
            ):
                raise ReportingError("report.untraced_claim", "关键 claim 必须引用有效证据")
        report = ResearchReport(
            run_id=run.run_id,
            scope_version=run.scope_version,
            plan_version=run.plan_version,
            markdown=self._markdown(run, approved_notes, claims, synthesis),
            claims=claims,
            note_hashes=tuple(sorted((item.note_id, item.input_hash) for item in approved_notes)),
            evidence_hashes=tuple(sorted((key, value.content_hash) for key, value in evidence.items())),
            synthesis_outcome=synthesis.outcome,
            validation_plan=synthesis.validation_plan,
            report_hash="",
        )
        return replace(report, report_hash=compute_report_hash(report))

    def final_gate_request(
        self, run: ResearchRun, report: ResearchReport, requester_actor: str
    ) -> GateRequest:
        if not requester_actor.strip():
            raise ReportingError("report.blank_requester", "requester_actor 不能为空")
        if not report_is_current(run, report):
            raise ReportingError("report.stale", "报告与当前研究版本不一致")
        if report.synthesis_outcome is not SynthesisOutcome.CANDIDATE:
            raise ReportingError("report.blocked_synthesis", "冲突或未知综合结果不能请求结论批准")
        return GateRequest(
            run_id=run.run_id,
            name=GateName.FINAL_CONCLUSION,
            requester_actor=requester_actor.strip(),
            plan_version=run.plan_version,
            scope_version=run.scope_version,
            evidence_version=report_binding_hash(report),
            budget_version=_budget_version(run),
        )

    def _validate_note(
        self, run: ResearchRun, note: ResearchNote
    ) -> dict[str, EvidenceRef]:
        if note.status is not NoteStatus.APPROVED:
            raise ReportingError("report.note_not_approved", f"笔记未批准: {note.note_id}")
        record = self._record("notes", run.run_id, note.note_id)
        if record.payload.get("current") is not True:
            raise ReportingError("report.note_not_current", f"笔记不是当前修订: {note.note_id}")
        if record.payload.get("scope_version") != run.scope_version:
            raise ReportingError("report.note_stale", f"笔记批准绑定已失效: {note.note_id}")
        if (
            record.payload.get("approval_scope_version") != run.scope_version
            or record.payload.get("note_hash") != note.input_hash
        ):
            raise ReportingError("report.note_stale", f"笔记批准绑定已失效: {note.note_id}")
        found: dict[str, EvidenceRef] = {}
        referenced = note.evidence_ids + note.supporting_evidence_ids + note.counter_evidence_ids
        for evidence_id in referenced:
            stored = self._record("evidence", run.run_id, evidence_id)
            payload = {key: stored.payload.get(key) for key in EvidenceRef.model_fields}
            evidence = EvidenceRef.model_validate_json(canonical_json(payload))
            if self._ledger.revalidate(evidence).status != "valid":
                raise ReportingError("report.evidence_stale", f"证据已失效: {evidence_id}")
            found[evidence_id] = evidence
        stored_hashes = record.payload.get("evidence_hashes")
        expected_hashes = {key: value.content_hash for key, value in found.items()}
        approval: dict[str, JsonValue] = {
            "note_hash": note.input_hash,
            "evidence_hashes": expected_hashes,
            "scope_version": run.scope_version,
        }
        approval_hash = sha256(canonical_json(approval).encode("utf-8")).hexdigest()
        if stored_hashes != expected_hashes or record.payload.get("approval_input_hash") != approval_hash:
            raise ReportingError("report.note_stale", f"笔记证据绑定已失效: {note.note_id}")
        return found

    def _record(self, table: str, run_id: str, record_id: str) -> StoredRecord:
        record = next(
            (item for item in self._repository.list_records(table, run_id) if item.record_id == record_id),
            None,
        )
        if record is None:
            raise ReportingError("report.missing_record", f"缺少记录: {table}/{record_id}")
        return record

    @staticmethod
    def _require_candidate(run: ResearchRun, notes: tuple[ResearchNote, ...],
                           synthesis: SynthesisResult) -> None:
        if not notes:
            raise ReportingError("report.no_approved_notes", "生成报告需要至少一条批准笔记")
        if len({item.note_id for item in notes}) != len(notes):
            raise ReportingError("report.duplicate_note", "报告笔记 ID 不能重复")
        if synthesis.scope_version != run.scope_version:
            raise ReportingError("report.scope_mismatch", "综合结果不属于当前范围版本")
        if synthesis.outcome is not SynthesisOutcome.CANDIDATE:
            raise ReportingError("report.blocked_synthesis", "冲突或未知综合结果不能生成报告")

    @staticmethod
    def _claims(
        notes: tuple[ResearchNote, ...], evidence: dict[str, EvidenceRef]
    ) -> tuple[ClaimRecord, ...]:
        claims: list[ClaimRecord] = []
        for note in sorted(notes, key=lambda item: item.note_id):
            referenced = note.evidence_ids + note.supporting_evidence_ids + note.counter_evidence_ids
            grouped: dict[ClaimPartition, list[str]] = {"内部事实": [], "外部通用知识": []}
            for evidence_id in referenced:
                grouped[_partition(evidence[evidence_id])].append(evidence_id)
            for partition in ("内部事实", "外部通用知识"):
                if grouped[partition]:
                    claims.append(_claim(
                        note.body, (note.note_id,), tuple(sorted(grouped[partition])),
                        partition, _is_critical(note.note_type),
                    ))
        return tuple(claims)

    @staticmethod
    def _markdown(run: ResearchRun, notes: tuple[ResearchNote, ...],
                  claims: tuple[ClaimRecord, ...], synthesis: SynthesisResult) -> str:
        internal = [item.content for item in claims if item.partition == "内部事实"]
        external = [item.content for item in claims if item.partition == "外部通用知识"]
        candidates = [item.claim for item in synthesis.candidates]
        counters = [item.body for item in notes if item.note_type is NoteType.NEGATIVE_RESULT]
        sections = (
            ("问题定义", run.scope.objective),
            ("影响范围", "、".join(run.scope.asset_ids) or "当前范围未列出"),
            ("数据证据", f"内部事实\n{_lines(internal)}\n\n外部通用知识\n{_lines(external)}"),
            ("候选根因", _lines(candidates)),
            ("反证", _lines(counters)),
            ("结论", "advisory_only=true；correlation_only=true；待人工确认；不得替代验证实验"),
            ("验证计划", _lines(list(synthesis.validation_plan))),
        )
        return "\n\n".join(f"## {heading}\n{content}" for heading, content in sections)


def _claim(
    content: str,
    note_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    partition: ClaimPartition,
    critical: bool,
) -> ClaimRecord:
    payload: dict[str, JsonValue] = {
        "content": content, "note_ids": list(note_ids),
        "evidence_ids": list(evidence_ids), "partition": partition,
    }
    content_hash = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ClaimRecord(
        claim_id=f"claim:{content_hash[:20]}", content=content, note_ids=note_ids,
        evidence_ids=evidence_ids, partition=partition,
        content_hash=content_hash, critical=critical,
    )


def _partition(evidence: EvidenceRef) -> ClaimPartition:
    return "外部通用知识" if evidence.partition == "external_general" else "内部事实"


def _is_critical(note_type: NoteType) -> bool:
    return note_type in {NoteType.FACT, NoteType.HYPOTHESIS, NoteType.DECISION, NoteType.SUMMARY}


def _lines(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) or "- 无已批准笔记支持"


def report_payload(report: ResearchReport) -> dict[str, JsonValue]:
    return {
        "run_id": report.run_id, "scope_version": report.scope_version,
        "plan_version": report.plan_version, "markdown": report.markdown,
        "claims": [
            {
                "claim_id": item.claim_id, "content": item.content,
                "note_ids": list(item.note_ids), "evidence_ids": list(item.evidence_ids),
                "partition": item.partition, "content_hash": item.content_hash,
                "critical": item.critical,
            }
            for item in report.claims
        ],
        "note_hashes": {key: value for key, value in report.note_hashes},
        "evidence_hashes": {key: value for key, value in report.evidence_hashes},
        "synthesis_outcome": report.synthesis_outcome.value,
        "validation_plan": list(report.validation_plan),
    }


def compute_report_hash(report: ResearchReport) -> str:
    return sha256(canonical_json(report_payload(report)).encode("utf-8")).hexdigest()


def report_binding_hash(report: ResearchReport) -> str:
    payload: dict[str, JsonValue] = {
        "report_hash": report.report_hash,
        "note_hashes": {key: value for key, value in report.note_hashes},
        "evidence_hashes": {key: value for key, value in report.evidence_hashes},
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def report_is_current(run: ResearchRun, report: ResearchReport) -> bool:
    return (
        report.run_id == run.run_id
        and report.scope_version == run.scope_version
        and report.plan_version == run.plan_version
        and report.report_hash == compute_report_hash(report)
    )


def _budget_version(run: ResearchRun) -> str:
    payload: dict[str, JsonValue] = {
        "todos": [item.model_dump(mode="json") for item in run.todos],
        "budget": run.budget.model_dump(mode="json") if run.budget else None,
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = (
    "ClaimRecord", "ReportingError", "ReportingService", "ResearchReport",
    "compute_report_hash", "report_binding_hash", "report_is_current", "report_payload",
)
