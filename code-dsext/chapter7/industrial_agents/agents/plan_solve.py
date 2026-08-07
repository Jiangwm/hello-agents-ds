from __future__ import annotations

import json

from .base import IndustrialAgent
from .react import _parse_json_object
from ..exceptions import IndustrialAgentError, ToolError
from ..schemas import (
    Evidence,
    IndustrialMessage,
    IndustrialTask,
    MessageRole,
    RunStatus,
)


class PlanSolveIndustrialAgent(IndustrialAgent):
    def run(self, task: IndustrialTask):
        self._begin_run()
        messages = self._initial_messages(
            task,
            "先只返回 JSON 计划："
            '{"plan":[{"id":"S1","description":"说明",'
            '"tool":"工具名","arguments":{...}}]}。'
            "计划长度不得超过最大轮次，且只能使用白名单工具。",
        )
        plan_response = self._invoke(messages)
        messages.append(
            IndustrialMessage(MessageRole.ASSISTANT, plan_response.content)
        )
        plan_payload = _parse_json_object(plan_response.content)
        raw_plan = plan_payload.get("plan")
        if not isinstance(raw_plan, list) or not raw_plan:
            raise IndustrialAgentError("PlanSolve 响应缺少非空 plan")
        if len(raw_plan) > self.config.max_rounds:
            raise IndustrialAgentError("计划步骤超过 config.max_rounds")

        evidence: list[Evidence] = []
        step_states: list[dict[str, object]] = []
        failed = False
        for index, raw_step in enumerate(raw_plan, 1):
            if failed:
                step_states.append(
                    {
                        "id": f"S{index}",
                        "description": "上游步骤失败，未执行",
                        "status": "blocked",
                        "evidence_ids": [],
                    }
                )
                continue
            if not isinstance(raw_step, dict):
                raise IndustrialAgentError(f"计划第 {index} 步必须是 JSON 对象")
            tool_name = raw_step.get("tool")
            arguments = raw_step.get("arguments", {})
            if not isinstance(tool_name, str) or not tool_name:
                raise IndustrialAgentError(f"计划第 {index} 步缺少 tool")
            if not isinstance(arguments, dict):
                raise IndustrialAgentError(
                    f"计划第 {index} 步 arguments 必须是 JSON 对象"
                )
            state = {
                "id": str(raw_step.get("id", f"S{index}")),
                "description": str(raw_step.get("description", "")),
                "tool": tool_name,
                "status": "running",
                "evidence_ids": [],
            }
            try:
                result = self._execute_tool(tool_name, arguments)
            except ToolError as error:
                state["status"] = "failed"
                state["error"] = f"{type(error).__name__}: {error}"
                failed = True
            else:
                evidence.extend(result.evidence)
                state["status"] = "completed"
                state["evidence_ids"] = [
                    item.evidence_id for item in result.evidence
                ]
                messages.append(self._tool_message(result))
            step_states.append(state)

        synthesis_message = IndustrialMessage(
            MessageRole.USER,
            "请根据步骤状态与证据生成结论。"
            "必须显式引用证据 ID，失败或未知项不得编造："
            + json.dumps(step_states, ensure_ascii=False),
            evidence_ids=tuple(item.evidence_id for item in evidence),
        )
        messages.append(synthesis_message)
        synthesis = self._invoke(messages)
        conclusion = synthesis.content.strip() or "模型未返回计划汇总结论。"
        messages.append(
            IndustrialMessage(
                MessageRole.ASSISTANT,
                conclusion,
                evidence_ids=tuple(item.evidence_id for item in evidence),
            )
        )
        status = RunStatus.PARTIAL if failed else RunStatus.COMPLETED
        limitations = ["计划执行不会绕过工具注册表和数据目录边界。"]
        if failed:
            limitations.append("至少一个计划步骤失败，后续步骤已阻断。")
        return self._build_result(
            task=task,
            status=status,
            conclusion=conclusion,
            evidence=evidence,
            confidence=0.45 if failed else 0.82,
            limitations=limitations,
            recommended_next_steps=("人工复核步骤状态和失败审计记录。",),
            messages=messages,
            metadata={
                "paradigm": "plan_solve",
                "plan": step_states,
                "llm_calls": 2,
            },
        )
