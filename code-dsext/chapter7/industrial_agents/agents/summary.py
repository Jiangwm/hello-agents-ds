from __future__ import annotations

import json

from .base import IndustrialAgent
from ..schemas import IndustrialMessage, IndustrialTask, MessageRole, RunStatus


class SummaryAgent(IndustrialAgent):
    def run(self, task: IndustrialTask):
        self._begin_run()
        file_path = task.inputs.get("file_path")
        if file_path is None and task.data_sources:
            file_path = task.data_sources[0]
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("SummaryAgent 需要 inputs.file_path 或 data_sources")

        arguments: dict[str, object] = {"file_path": file_path}

        profile = self._execute_tool("dataset_profile", arguments)
        evidence_ids = tuple(item.evidence_id for item in profile.evidence)
        messages = self._initial_messages(
            task,
            "根据给定的数据画像生成一次性摘要，不发起额外工具调用。",
        )
        messages.append(self._tool_message(profile))
        messages.append(
            IndustrialMessage(
                MessageRole.USER,
                "请只依据下列工具输出生成摘要，并在结论中引用证据 ID："
                + json.dumps(evidence_ids, ensure_ascii=False),
                evidence_ids=evidence_ids,
            )
        )
        response = self._invoke(messages)
        conclusion = response.content.strip() or "模型未返回摘要。"
        messages.append(
            IndustrialMessage(
                MessageRole.ASSISTANT,
                conclusion,
                evidence_ids=evidence_ids,
            )
        )
        limitations = ["本结果仅覆盖离线样例数据，不代表生产状态。"]
        if profile.truncated:
            limitations.append("输入或输出达到框架上限，画像基于截断数据。")
        return self._build_result(
            task=task,
            status=RunStatus.COMPLETED,
            conclusion=conclusion,
            evidence=profile.evidence,
            confidence=0.85,
            limitations=limitations,
            recommended_next_steps=("由工艺或质量负责人复核数据范围与结论。",),
            messages=messages,
            metadata={"paradigm": "summary", "llm_calls": 1},
        )
