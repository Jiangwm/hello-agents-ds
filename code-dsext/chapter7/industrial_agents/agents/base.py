from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
import json
import math
import re
from typing import Iterable, Mapping, Sequence

from ..llms import LLMAdapter
from ..schemas import (
    AnalysisResult,
    ApprovalContext,
    Evidence,
    IndustrialAgentConfig,
    IndustrialMessage,
    IndustrialTask,
    MessageRole,
    RunStatus,
    ToolResult,
)
from ..tools import ToolRegistry


class IndustrialAgent(ABC):
    """通过稳定接口组合模型适配器与白名单工具。"""

    def __init__(
        self,
        name: str,
        llm: LLMAdapter,
        tool_registry: ToolRegistry,
        config: IndustrialAgentConfig,
    ) -> None:
        self.name = name
        self.llm = llm
        self.tool_registry = tool_registry
        self.config = config
        self._audit_start_index = len(tool_registry.audit_records)

    @abstractmethod
    def run(self, task: IndustrialTask) -> AnalysisResult:
        """执行一次有界、可审计的工业分析任务。"""

    def _initial_messages(
        self,
        task: IndustrialTask,
        instruction: str,
    ) -> list[IndustrialMessage]:
        tool_definitions = self.tool_registry.model_tools(self.config)
        system_content = (
            "你是离线工业数据分析教学智能体。只使用已注册的只读工具，"
            "数字结论必须引用工具证据 ID；区分事实、假设与未知，"
            "不得给出生产控制指令，也不得把相关性表述为因果性。"
            f"\n\n当前范式要求：{instruction}"
            "\n\n可用白名单工具 Schema："
            + json.dumps(tool_definitions, ensure_ascii=False)
        )
        task_payload = {
            "task_id": task.task_id,
            "objective": task.objective,
            "inputs": dict(task.inputs),
            "data_sources": list(task.data_sources),
            "constraints": list(task.constraints),
            "equipment_scope": list(task.equipment_scope),
        }
        return [
            IndustrialMessage(MessageRole.SYSTEM, system_content),
            IndustrialMessage(
                MessageRole.USER,
                json.dumps(task_payload, ensure_ascii=False, default=str),
                equipment_scope=task.equipment_scope,
            ),
        ]

    def _begin_run(self) -> None:
        self._audit_start_index = len(self.tool_registry.audit_records)

    def _invoke(
        self,
        messages: Sequence[IndustrialMessage],
        *,
        tools: Sequence[Mapping[str, object]] = (),
    ):
        return self.llm.invoke(
            messages,
            timeout_seconds=self.config.timeout_seconds,
            tools=tools,
        )

    def _execute_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        *,
        approval: ApprovalContext | None = None,
    ) -> ToolResult:
        return self.tool_registry.execute(
            name,
            dict(arguments),
            self.config,
            approval=approval,
        )

    def _build_result(
        self,
        *,
        task: IndustrialTask,
        status: RunStatus,
        conclusion: str,
        evidence: Iterable[Evidence],
        confidence: float,
        limitations: Iterable[str],
        recommended_next_steps: Iterable[str],
        messages: Iterable[IndustrialMessage],
        metadata: Mapping[str, object] | None = None,
    ) -> AnalysisResult:
        unique_evidence: dict[str, Evidence] = {}
        for item in evidence:
            unique_evidence[item.evidence_id] = item
        evidence_items = tuple(unique_evidence.values())
        original_conclusion = conclusion
        conclusion, validation = self._validate_conclusion(
            task,
            conclusion,
            evidence_items,
        )
        limitation_items = tuple(limitations)
        validation_issues = tuple(validation["issues"])
        if validation_issues:
            if status == RunStatus.COMPLETED:
                status = RunStatus.PARTIAL
            confidence = min(confidence, 0.45)
            limitation_items = (
                *limitation_items,
                "证据校验未通过：" + "；".join(validation_issues),
            )
        message_items = list(messages)
        updated_message = False
        for index in range(len(message_items) - 1, -1, -1):
            message = message_items[index]
            if (
                message.role == MessageRole.ASSISTANT
                and message.content == original_conclusion
                and not message.tool_calls
            ):
                message_items[index] = replace(
                    message,
                    content=conclusion,
                    evidence_ids=tuple(item.evidence_id for item in evidence_items),
                )
                updated_message = True
                break
        if not updated_message:
            message_items.append(
                IndustrialMessage(
                    MessageRole.ASSISTANT,
                    conclusion,
                    evidence_ids=tuple(
                        item.evidence_id
                        for item in evidence_items
                    ),
                )
            )
        result_metadata = dict(metadata or {})
        result_metadata.update(
            {
                "agent": self.name,
                "provider": self.llm.provider,
                "model": self.llm.model,
                "evidence_validation": validation,
            }
        )
        return AnalysisResult(
            task_id=task.task_id,
            status=status,
            conclusion=conclusion,
            evidence=evidence_items,
            confidence=confidence,
            limitations=limitation_items,
            next_steps=tuple(recommended_next_steps),
            audit_records=self.tool_registry.audit_records[
                self._audit_start_index:
            ],
            messages=tuple(message_items),
            metadata=result_metadata,
        )

    @staticmethod
    def _validate_conclusion(
        task: IndustrialTask,
        conclusion: str,
        evidence: tuple[Evidence, ...],
    ) -> tuple[str, dict[str, object]]:
        valid_ids = tuple(item.evidence_id for item in evidence)
        referenced_ids = tuple(
            sorted(set(re.findall(r"EV-[A-Za-z0-9_-]+", conclusion)))
        )
        unknown_ids = tuple(
            evidence_id
            for evidence_id in referenced_ids
            if evidence_id not in valid_ids
        )
        support_payload = {
            "task_id": task.task_id,
            "objective": task.objective,
            "equipment_scope": task.equipment_scope,
            "data_sources": task.data_sources,
            "inputs": task.inputs,
            "evidence": [item.to_dict() for item in evidence],
        }
        support_text = json.dumps(
            support_payload,
            ensure_ascii=False,
            default=str,
        )
        unsupported_numbers = _unsupported_numbers(conclusion, support_text)
        causal_terms = (
            "导致",
            "造成",
            "证明",
            "根因是",
            "原因是",
            "必然引发",
        )
        hedge_terms = (
            "可能",
            "疑似",
            "假设",
            "待验证",
            "相关",
            "无法",
            "未能",
            "不能确认",
        )
        sentences = re.split(r"[。；;！？!?\n]+", conclusion)
        unhedged_causality = any(
            any(term in sentence for term in causal_terms)
            and not any(term in sentence for term in hedge_terms)
            for sentence in sentences
        )
        issues: list[str] = []
        if unknown_ids:
            issues.append("引用了未知证据 ID：" + "、".join(unknown_ids))
        if unsupported_numbers:
            issues.append("存在无证据数字：" + "、".join(unsupported_numbers))
        if unhedged_causality:
            issues.append("存在未经证据支持的因果措辞")
        missing_ids = tuple(
            evidence_id
            for evidence_id in valid_ids
            if evidence_id not in referenced_ids
        )
        repaired = bool(missing_ids)
        if missing_ids:
            conclusion = conclusion.rstrip() + "\n\n证据引用：" + "、".join(missing_ids)
        return conclusion, {
            "status": "failed" if issues else ("repaired" if repaired else "passed"),
            "issues": tuple(issues),
            "referenced_evidence_ids": tuple(
                evidence_id
                for evidence_id in referenced_ids
                if evidence_id in valid_ids
            ),
            "added_evidence_ids": missing_ids,
        }

    @staticmethod
    def _tool_message(
        result: ToolResult,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> IndustrialMessage:
        content = json.dumps(
            result.output,
            ensure_ascii=False,
            default=str,
        )
        if tool_call_id is None:
            return IndustrialMessage(
                MessageRole.USER,
                "受控工具输出：" + content,
                evidence_ids=tuple(
                    item.evidence_id
                    for item in result.evidence
                ),
            )
        return IndustrialMessage(
            MessageRole.TOOL,
            content,
            evidence_ids=tuple(item.evidence_id for item in result.evidence),
            name=tool_name,
            tool_call_id=tool_call_id,
        )


def _unsupported_numbers(claim: str, support: str) -> tuple[str, ...]:
    pattern = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?")
    clean_claim = re.sub(r"EV-[A-Za-z0-9_-]+", "", claim)
    support_values = [
        float(token.rstrip("%"))
        for token in pattern.findall(support)
    ]
    unsupported: list[str] = []
    for token in pattern.findall(clean_claim):
        raw_value = float(token.rstrip("%"))
        candidates = (raw_value, raw_value / 100) if token.endswith("%") else (raw_value,)
        decimal_places = (
            len(token.rstrip("%").partition(".")[2])
            if "." in token
            else 0
        )
        matched = any(
            math.isclose(candidate, value, rel_tol=1e-9, abs_tol=1e-9)
            or round(value, decimal_places) == candidate
            for candidate in candidates
            for value in support_values
        )
        if not matched and token not in unsupported:
            unsupported.append(token)
    return tuple(unsupported)
