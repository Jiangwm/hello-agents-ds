from __future__ import annotations

from .base import IndustrialAgent
from ..schemas import (
    Evidence,
    IndustrialMessage,
    IndustrialTask,
    MessageRole,
    RunStatus,
)


class FunctionCallIndustrialAgent(IndustrialAgent):
    def run(self, task: IndustrialTask):
        self._begin_run()
        messages = self._initial_messages(
            task,
            "通过结构化 function tool_calls 选择工具；"
            "证据充分时返回不含 tool_calls 的最终结论。",
        )
        evidence: list[Evidence] = []
        model_tools = self.tool_registry.model_tools(self.config)

        for round_number in range(1, self.config.max_rounds + 1):
            response = self._invoke(messages, tools=model_tools)
            messages.append(
                IndustrialMessage(
                    MessageRole.ASSISTANT,
                    response.content or "请求执行结构化工具调用。",
                    tool_calls=response.tool_calls,
                )
            )
            if not response.tool_calls:
                conclusion = response.content.strip() or "模型未返回最终结论。"
                completed = bool(evidence)
                messages[-1] = IndustrialMessage(
                    MessageRole.ASSISTANT,
                    conclusion,
                    evidence_ids=tuple(
                        item.evidence_id for item in evidence
                    ),
                )
                return self._build_result(
                    task=task,
                    status=(
                        RunStatus.COMPLETED
                        if completed
                        else RunStatus.PARTIAL
                    ),
                    conclusion=conclusion,
                    evidence=evidence,
                    confidence=0.8 if completed else 0.2,
                    limitations=(
                        (
                            "结构化调用仍受模型选择偏差影响。"
                            if completed
                            else "模型未调用工具，结论缺少可验证证据。"
                        ),
                    ),
                    recommended_next_steps=("人工复核证据范围和工具审计。",),
                    messages=messages,
                    metadata={
                        "paradigm": "function_call",
                        "rounds": round_number,
                    },
                )
            for tool_call in response.tool_calls:
                result = self._execute_tool(
                    tool_call.name,
                    tool_call.arguments,
                )
                evidence.extend(result.evidence)
                messages.append(
                    self._tool_message(
                        result,
                        tool_call_id=tool_call.call_id,
                        tool_name=tool_call.name,
                    )
                )

        return self._build_result(
            task=task,
            status=RunStatus.PARTIAL,
            conclusion="达到最大结构化调用轮次，尚未形成完整结论。",
            evidence=evidence,
            confidence=0.35,
            limitations=("达到有界执行上限，禁止继续自动调用工具。",),
            recommended_next_steps=("由人工审阅审计记录后决定是否继续。",),
            messages=messages,
            metadata={
                "paradigm": "function_call",
                "rounds": self.config.max_rounds,
            },
        )
