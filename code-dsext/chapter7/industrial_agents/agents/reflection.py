from __future__ import annotations

from .base import IndustrialAgent
from ..schemas import IndustrialMessage, IndustrialTask, MessageRole, RunStatus


class ReflectionIndustrialAgent(IndustrialAgent):
    def __init__(self, name, llm, draft_agent: IndustrialAgent) -> None:
        super().__init__(
            name,
            llm,
            draft_agent.tool_registry,
            draft_agent.config,
        )
        self.draft_agent = draft_agent

    def run(self, task: IndustrialTask):
        self._begin_run()
        draft = self.draft_agent.run(task)
        evidence_ids = tuple(item.evidence_id for item in draft.evidence)
        messages = list(draft.messages)
        messages.append(
            IndustrialMessage(
                MessageRole.USER,
                "请复核下面的分析草稿：保留所有数字与证据 ID，"
                "将无证据因果断言改为假设，明确未知项和人工复核边界。\n\n"
                + draft.to_markdown(),
                evidence_ids=evidence_ids,
            )
        )
        response = self._invoke(messages)
        conclusion = response.content.strip() or draft.conclusion
        missing_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in conclusion
        ]
        if missing_ids:
            conclusion += "\n\n证据引用：" + "、".join(missing_ids)
        draft_validation_failed = (
            draft.metadata.get("evidence_validation", {}).get("status")
            == "failed"
        )
        status = (
            RunStatus.COMPLETED
            if draft_validation_failed and draft.evidence
            else draft.status
        )
        draft_limitations = tuple(
            limitation
            for limitation in draft.limitations
            if not (
                draft_validation_failed
                and limitation.startswith("证据校验未通过：")
            )
        )
        messages.append(
            IndustrialMessage(
                MessageRole.ASSISTANT,
                conclusion,
                evidence_ids=evidence_ids,
            )
        )
        return self._build_result(
            task=task,
            status=status,
            conclusion=conclusion,
            evidence=draft.evidence,
            confidence=min(draft.confidence, 0.8),
            limitations=(
                *draft_limitations,
                "Reflection 只复核既有证据与措辞，不确认最终根因。",
            ),
            recommended_next_steps=draft.next_steps,
            messages=messages,
            metadata={
                "paradigm": "reflection",
                "draft_agent": self.draft_agent.name,
                "draft_metadata": dict(draft.metadata),
            },
        )
