from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ingestion import IngestionPipeline
from memory import WorkingMemory
from retrieval import HybridRetriever
from schemas import (
    AssistantAuditRecord,
    AssistantResponse,
    Citation,
    RetrievalHit,
    SourceType,
)


class EquipmentKnowledgeSystem:
    def __init__(
        self,
        retriever: HybridRetriever,
        equipment_models: dict[str, str],
        analysis_date: date,
    ) -> None:
        self.retriever = retriever
        self.equipment_models = dict(equipment_models)
        self.analysis_date = analysis_date
        self.audit_records: list[AssistantAuditRecord] = []

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> "EquipmentKnowledgeSystem":
        pipeline = IngestionPipeline(data_dir)
        chunks = pipeline.ingest_directory("documents")
        chunks.extend(pipeline.ingest_csv("work_orders.csv"))
        chunks.extend(pipeline.ingest_csv("procedures.csv"))
        equipment_models = cls._load_equipment_registry(
            data_dir / "equipment_registry.csv"
        )
        scenario = json.loads(
            (data_dir / "scenario.json").read_text(encoding="utf-8")
        )
        try:
            analysis_date = date.fromisoformat(scenario["analysis_date"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("scenario.json 缺少有效 analysis_date") from error
        return cls(
            HybridRetriever(chunks),
            equipment_models,
            analysis_date,
        )

    def ask(
        self,
        query: str,
        *,
        equipment_id: str,
        role: str,
        top_k: int = 4,
        working_memory: WorkingMemory | None = None,
        as_of: date | None = None,
    ) -> AssistantResponse:
        mentioned_equipment_ids = set(
            re.findall(r"\b[A-Z]{2,}-\d+\b", query.upper())
        )
        if (
            mentioned_equipment_ids
            and mentioned_equipment_ids != {equipment_id.upper()}
        ):
            return self._finalize(
                query=query,
                equipment_id=equipment_id,
                role=role,
                reason_code="equipment_conflict",
                response=AssistantResponse(
                    status="refused",
                    answer="问题中的设备编号与检索范围存在设备编号冲突。",
                    needed_information=("确认唯一的目标设备编号",),
                ),
            )
        equipment_model = self.equipment_models.get(equipment_id)
        if equipment_model is None:
            return self._finalize(
                query=query,
                equipment_id=equipment_id,
                role=role,
                reason_code="unknown_equipment",
                response=AssistantResponse(
                    status="refused",
                    answer="无法确认设备型号，暂不检索，避免混用其他设备知识。",
                    needed_information=("已登记的设备编号或设备型号",),
                ),
            )
        if "未确认的假设" in query:
            response = self._answer_open_hypotheses(
                equipment_id=equipment_id,
                role=role,
                working_memory=working_memory,
            )
            return self._finalize(
                query=query,
                equipment_id=equipment_id,
                role=role,
                reason_code="working_memory_query",
                response=response,
            )
        if not any(
            role in chunk.allowed_roles
            and equipment_model in chunk.equipment_models
            for chunk in self.retriever.chunks
        ):
            return self._finalize(
                query=query,
                equipment_id=equipment_id,
                role=role,
                reason_code="access_denied",
                response=AssistantResponse(
                    status="refused",
                    answer="当前角色无权访问该设备的知识与维修记录。",
                    needed_information=("申请设备知识访问权限",),
                ),
            )
        history_query = any(
            marker in query
            for marker in ("过去", "历史", "工单", "相似告警", "共同点")
        )
        comparison_query = "共同点" in query
        current_date = as_of or self.analysis_date
        history_start = (
            current_date - timedelta(days=92)
            if history_query and "三个月" in query
            else None
        )
        source_types = frozenset(
            {
                SourceType.HISTORICAL
                if history_query
                else SourceType.AUTHORITATIVE
            }
        )
        hits = self.retriever.search(
            query,
            role=role,
            equipment_model=equipment_model,
            equipment_id=equipment_id,
            source_types=source_types,
            top_k=max(top_k * 3, 6) if comparison_query else top_k,
            min_score=(
                0.04
                if comparison_query
                else (0.08 if history_query else 0.12)
            ),
            as_of=current_date,
            start_date=history_start,
        )
        if comparison_query:
            hits = tuple(
                sorted(
                    hits,
                    key=lambda hit: hit.chunk.event_date or date.min,
                    reverse=True,
                )[:top_k]
            )
        if not hits:
            return self._finalize(
                query=query,
                equipment_id=equipment_id,
                role=role,
                reason_code="insufficient_retrieval",
                response=AssistantResponse(
                    status="refused",
                    answer="现有授权知识中没有达到相关性阈值的证据，不能可靠回答。",
                    needed_information=("更具体的告警代码、现象或部件名称",),
                ),
            )

        labels = {
            SourceType.AUTHORITATIVE: "文档事实",
            SourceType.HISTORICAL: "历史经验",
        }
        facts = [
            f"[{labels[hit.chunk.source_type]}] {hit.chunk.content}"
            for hit in hits
        ]
        if comparison_query:
            facts.insert(0, self._comparison_summary(query, hits))
        citations = tuple(
            Citation(
                chunk_id=hit.chunk.chunk_id,
                document_id=hit.chunk.document_id,
                title=hit.chunk.title,
                version=hit.chunk.version,
                source_type=hit.chunk.source_type.value,
                source_path=hit.chunk.source_path,
                paragraph_id=hit.chunk.paragraph_id,
                page=hit.chunk.page,
                score=hit.score,
            )
            for hit in hits
        )
        answer = "\n\n".join(facts)
        return self._finalize(
            query=query,
            equipment_id=equipment_id,
            role=role,
            reason_code=(
                "history_comparison"
                if comparison_query
                else (
                    "historical_answer"
                    if history_query
                    else "authoritative_answer"
                )
            ),
            response=AssistantResponse(
                status="answered",
                answer=answer,
                citations=citations,
                limitations=(
                    "仅提供只读教学证据，不执行设备控制或替代安全作业规程。",
                ),
                data_window=(
                    f"{history_start.isoformat()} 至 {current_date.isoformat()}"
                    if history_start
                    else None
                ),
            ),
        )

    @staticmethod
    def _comparison_summary(
        query: str,
        hits: tuple[RetrievalHit, ...],
    ) -> str:
        corpus = "\n".join(hit.chunk.content for hit in hits)
        concepts = (
            ("主轴过热", ("主轴过热",)),
            ("冷却/风机链路", ("冷却", "风机", "滤网")),
            ("润滑链路", ("润滑", "油位")),
        )
        shared = [
            label
            for label, aliases in concepts
            if any(alias in query for alias in aliases)
            and any(alias in corpus for alias in aliases)
        ]
        if not shared:
            return "[共同点] 未发现稳定的共同观察；这不是根因结论。"
        return (
            "[共同点] "
            + "、".join(shared)
            + "；这些只是现象重合，不是根因结论。"
        )

    @staticmethod
    def _answer_open_hypotheses(
        *,
        equipment_id: str,
        role: str,
        working_memory: WorkingMemory | None,
    ) -> AssistantResponse:
        if working_memory is None:
            return AssistantResponse(
                status="refused",
                answer="当前会话没有调查工作记忆，无法列出未确认假设。",
                needed_information=("包含假设状态的调查会话",),
            )
        if working_memory.equipment_id != equipment_id:
            return AssistantResponse(
                status="refused",
                answer="工作记忆属于其他设备，已阻止跨设备访问。",
                needed_information=("当前设备对应的调查会话",),
            )
        if role not in working_memory.allowed_roles:
            return AssistantResponse(
                status="refused",
                answer="当前角色无权读取该设备的调查工作记忆。",
            )
        open_hypotheses = tuple(
            hypothesis.text
            for hypothesis in working_memory.hypotheses
            if hypothesis.state == "open"
        )
        if not open_hypotheses:
            return AssistantResponse(
                status="answered",
                answer="[调查状态] 当前没有尚未确认的假设。",
            )
        answer = "\n".join(
            f"[尚未确认的假设] {text}" for text in open_hypotheses
        )
        return AssistantResponse(
            status="answered",
            answer=answer,
            limitations=("工作记忆中的假设不是已确认根因。",),
        )

    def _finalize(
        self,
        *,
        query: str,
        equipment_id: str,
        role: str,
        reason_code: str,
        response: AssistantResponse,
    ) -> AssistantResponse:
        self.audit_records.append(
            AssistantAuditRecord(
                audit_id=f"AST-AUD-{len(self.audit_records) + 1:04d}",
                occurred_at=datetime.now(timezone.utc),
                status=response.status,
                reason_code=reason_code,
                query_sha256=hashlib.sha256(
                    query.encode("utf-8")
                ).hexdigest(),
                equipment_id=equipment_id,
                role=role,
                citation_chunk_ids=tuple(
                    citation.chunk_id for citation in response.citations
                ),
            )
        )
        return response

    @staticmethod
    def _load_equipment_registry(path: Path) -> dict[str, str]:
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                rows = csv.DictReader(handle)
                registry = {
                    row["equipment_id"].strip(): row["equipment_model"].strip()
                    for row in rows
                }
        except (OSError, KeyError) as error:
            raise ValueError(f"设备台账读取失败：{error}") from error
        if not registry:
            raise ValueError("设备台账为空")
        return registry
