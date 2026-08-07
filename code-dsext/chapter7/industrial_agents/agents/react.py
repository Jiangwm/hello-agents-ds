from __future__ import annotations

import json
from typing import Mapping

from .base import IndustrialAgent
from ..exceptions import IndustrialAgentError
from ..schemas import (
    Evidence,
    IndustrialMessage,
    IndustrialTask,
    MessageRole,
    RunStatus,
)


def _parse_json_object(content: str) -> Mapping[str, object]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise IndustrialAgentError("模型响应不是有效的 JSON 对象") from error
    if not isinstance(parsed, dict):
        raise IndustrialAgentError("模型响应必须是 JSON 对象")
    return parsed


class ReActIndustrialAgent(IndustrialAgent):
    def run(self, task: IndustrialTask):
        self._begin_run()
        messages = self._initial_messages(
            task,
            "每轮仅返回一个 JSON 对象："
            '{"action":{"tool":"工具名","arguments":{...}}}，'
            '或在证据充分时返回 {"finish":"结论"}；'
            "不要输出思维链。",
        )
        evidence: list[Evidence] = []

        for round_number in range(1, self.config.max_rounds + 1):
            response = self._invoke(messages)
            decision = _parse_json_object(response.content)
            messages.append(
                IndustrialMessage(MessageRole.ASSISTANT, response.content)
            )

            finish = decision.get("finish")
            if isinstance(finish, str) and finish.strip():
                evidence_ids = tuple(item.evidence_id for item in evidence)
                completed = bool(evidence)
                messages[-1] = IndustrialMessage(
                    MessageRole.ASSISTANT,
                    finish.strip(),
                    evidence_ids=evidence_ids,
                )
                return self._build_result(
                    task=task,
                    status=(
                        RunStatus.COMPLETED
                        if completed
                        else RunStatus.PARTIAL
                    ),
                    conclusion=finish.strip(),
                    evidence=evidence,
                    confidence=0.78 if completed else 0.2,
                    limitations=(
                        (
                            "结论来自有界工具循环，仍需领域专家复核。"
                            if completed
                            else "模型未调用工具，结论缺少可验证证据。"
                        ),
                    ),
                    recommended_next_steps=("核对证据范围并决定是否补充取数。",),
                    messages=messages,
                    metadata={
                        "paradigm": "react",
                        "rounds": round_number,
                    },
                )

            action = decision.get("action")
            if not isinstance(action, dict):
                raise IndustrialAgentError("ReAct 响应缺少 action 或 finish")
            tool_name = action.get("tool")
            arguments = action.get("arguments", {})
            if not isinstance(tool_name, str) or not tool_name:
                raise IndustrialAgentError("action.tool 必须是非空字符串")
            if not isinstance(arguments, dict):
                raise IndustrialAgentError("action.arguments 必须是 JSON 对象")
            tool_result = self._execute_tool(tool_name, arguments)
            evidence.extend(tool_result.evidence)
            messages.append(self._tool_message(tool_result))

        return self._build_result(
            task=task,
            status=RunStatus.PARTIAL,
            conclusion="达到最大轮次，尚未形成完整结论。",
            evidence=evidence,
            confidence=0.35,
            limitations=("达到有界执行上限，禁止继续自动取证。",),
            recommended_next_steps=("由人工检查审计记录并决定是否扩大分析范围。",),
            messages=messages,
            metadata={
                "paradigm": "react",
                "rounds": self.config.max_rounds,
            },
        )
