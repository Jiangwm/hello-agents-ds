from __future__ import annotations

import json
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from assistant import EquipmentKnowledgeSystem
from memory import (
    ApprovalContext,
    LongTermMemoryStore,
    MemoryConfirmationRequired,
    WorkingMemory,
)
from schemas import DocumentStatus


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 1.0
    return round(numerator / denominator, 6)


def _memory_governance_metrics(
    system: EquipmentKnowledgeSystem,
) -> dict[str, float]:
    created_at = datetime.combine(
        system.analysis_date,
        time(hour=9),
        tzinfo=timezone.utc,
    )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "evaluation_memories.json"
        store = LongTermMemoryStore(path)
        working = WorkingMemory(
            session_id="EVAL-CNC03-MEMORY",
            equipment_id="CNC-03",
            alert="主轴过热，联系人 eval@example.com",
        )
        working.add_evidence("WO-CNC03-20260718")
        working.add_hypothesis("冷却滤网再次堵塞", state="open")
        candidate = working.create_candidate(
            created_at=created_at,
            retention_days=30,
        )
        confirmation_blocked = False
        try:
            store.confirm(candidate, approval=None)
        except MemoryConfirmationRequired:
            confirmation_blocked = True
        no_unconfirmed_write = not path.exists()
        approval = ApprovalContext(
            approval_id="APR-EVAL-CNC03",
            approver="评估设备工程师",
            approver_role="equipment_engineer",
            reason="固定夹具验证确认门禁",
            approved_at=created_at,
        )
        stored = store.confirm(candidate, approval)
        authorized = store.search(
            equipment_id="CNC-03",
            role="equipment_engineer",
            query="滤网",
            as_of=created_at,
        )
        cross_equipment = store.search(
            equipment_id="CNC-04",
            role="equipment_engineer",
            query="滤网",
            as_of=created_at,
        )
        expired = store.search(
            equipment_id="CNC-03",
            role="equipment_engineer",
            query="",
            as_of=stored.expires_at + timedelta(seconds=1),
        )
        write_is_accurate = (
            path.exists()
            and stored.evidence_ids == ("WO-CNC03-20260718",)
            and stored.hypotheses[0].state == "open"
            and "[REDACTED_EMAIL]" in stored.alert
            and not stored.authoritative
        )
        isolation_is_correct = authorized == (stored,) and not cross_equipment
        return {
            "memory_confirmation_gate_accuracy": float(
                confirmation_blocked and no_unconfirmed_write
            ),
            "memory_write_accuracy": float(write_is_accurate),
            "memory_equipment_isolation_accuracy": float(
                isolation_is_correct
            ),
            "expired_memory_exposure_rate": float(bool(expired)),
        }


def run_evaluation(
    data_dir: Path,
    cases_path: Path,
) -> dict[str, int | float]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    system = EquipmentKnowledgeSystem.from_data_dir(data_dir)
    chunks_by_id = {
        chunk.chunk_id: chunk for chunk in system.retriever.chunks
    }

    recall_sum = 0.0
    recall_cases = 0
    reciprocal_rank_sum = 0.0
    status_matches = 0
    citation_total = 0
    valid_citations = 0
    version_valid = 0
    equipment_valid = 0
    assertions = 0
    supported_assertions = 0

    for case in cases:
        response = system.ask(
            str(case["query"]),
            equipment_id=str(case["equipment_id"]),
            role=str(case["role"]),
        )
        if response.status == case["expected_status"]:
            status_matches += 1
        expected = tuple(str(item) for item in case["expected_documents"])
        cited_documents = tuple(
            citation.document_id for citation in response.citations
        )
        if expected:
            recall_cases += 1
            recall_sum += _ratio(
                len(set(expected) & set(cited_documents)),
                len(set(expected)),
            )
            first_relevant = next(
                (
                    rank
                    for rank, document_id in enumerate(
                        cited_documents,
                        start=1,
                    )
                    if document_id in expected
                ),
                None,
            )
            if first_relevant:
                reciprocal_rank_sum += 1 / first_relevant

        equipment_id = str(case["equipment_id"])
        equipment_model = system.equipment_models[equipment_id]
        cited_chunks = []
        for citation in response.citations:
            citation_total += 1
            chunk = chunks_by_id.get(citation.chunk_id)
            if (
                chunk
                and chunk.document_id == citation.document_id
                and chunk.version == citation.version
                and chunk.paragraph_id == citation.paragraph_id
            ):
                valid_citations += 1
                cited_chunks.append(chunk)
            if chunk and chunk.status is DocumentStatus.EFFECTIVE:
                version_valid += 1
            if (
                chunk
                and equipment_model in chunk.equipment_models
                and (
                    not chunk.equipment_ids
                    or equipment_id in chunk.equipment_ids
                )
            ):
                equipment_valid += 1

        cited_contents = {chunk.content for chunk in cited_chunks}
        for paragraph in response.answer.split("\n\n"):
            if paragraph.startswith(("[文档事实] ", "[历史经验] ")):
                assertions += 1
                assertion = paragraph.split("] ", 1)[1]
                if assertion in cited_contents:
                    supported_assertions += 1

    report = {
        "case_count": len(cases),
        "recall_at_k": round(
            recall_sum / recall_cases if recall_cases else 1.0,
            6,
        ),
        "mrr": round(
            reciprocal_rank_sum / recall_cases if recall_cases else 1.0,
            6,
        ),
        "equipment_filter_accuracy": _ratio(
            equipment_valid,
            citation_total,
        ),
        "citation_correctness": _ratio(
            valid_citations,
            citation_total,
        ),
        "version_correctness": _ratio(
            version_valid,
            citation_total,
        ),
        "unsupported_assertion_rate": round(
            1.0 - _ratio(supported_assertions, assertions),
            6,
        ),
        "status_accuracy": _ratio(status_matches, len(cases)),
    }
    report.update(_memory_governance_metrics(system))
    return report
