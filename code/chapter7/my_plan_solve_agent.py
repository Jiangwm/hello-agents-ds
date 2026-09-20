# my_plan_solve_agent.py
"""基于 hello-agents 1.0 的自定义 Plan-and-Solve Agent。

参考第四章 Plan_and_solve 与 my_react_agent 的适配方式：
- Planner 输出 Python 列表计划（文本解析，契合 chapter7 提示词）
- Executor 按步执行并维护历史
- llm.invoke() 返回 LLMResponse，统一取 .content
- 支持 custom_prompts={"planner": ..., "executor": ...}
"""
import ast
import re
from typing import Optional, Dict, Any, List

from hello_agents import HelloAgentsLLM, Config, Message
from hello_agents.core.agent import Agent


DEFAULT_PLANNER_PROMPT = """
你是一个顶级的AI规划专家。你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
请确保计划中的每个步骤都是一个独立的、可执行的子任务，并且严格按照逻辑顺序排列。
你的输出必须是一个Python列表，其中每个元素都是一个描述子任务的字符串。

问题: {question}

请严格按照以下格式输出你的计划:
```python
["步骤1", "步骤2", "步骤3", ...]
```
"""

DEFAULT_EXECUTOR_PROMPT = """
你是一位顶级的AI执行专家。你的任务是严格按照给定的计划，一步步地解决问题。
你将收到原始问题、完整的计划、以及到目前为止已经完成的步骤和结果。
请你专注于解决"当前步骤"，并仅输出该步骤的最终答案，不要输出任何额外的解释或对话。

# 原始问题:
{question}

# 完整计划:
{plan}

# 历史步骤与结果:
{history}

# 当前步骤:
{current_step}

请仅输出针对"当前步骤"的回答:
"""


def _as_text(response: Any) -> str:
    """将 LLM 返回值统一为字符串（兼容 LLMResponse / str）。"""
    if response is None:
        return ""
    if hasattr(response, "content"):
        return response.content or ""
    return str(response)


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format_prompt(template: str, **kwargs) -> str:
    return template.format_map(_SafeFormat(**kwargs))


def _parse_plan(response_text: str) -> List[str]:
    """从模型输出中解析 Python 列表计划。"""
    text = (response_text or "").strip()
    if not text:
        return []

    plan_str = None
    if "```python" in text:
        try:
            plan_str = text.split("```python", 1)[1].split("```", 1)[0].strip()
        except IndexError:
            plan_str = None
    elif "```" in text:
        try:
            plan_str = text.split("```", 1)[1].split("```", 1)[0].strip()
        except IndexError:
            plan_str = None

    if plan_str is None:
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            plan_str = match.group(0)

    if not plan_str:
        return []

    try:
        plan = ast.literal_eval(plan_str)
        if isinstance(plan, list) and all(isinstance(s, str) for s in plan):
            return plan
        if isinstance(plan, list):
            return [str(s) for s in plan]
    except (ValueError, SyntaxError) as e:
        print(f"❌ 解析计划时出错: {e}")
        print(f"原始片段: {plan_str}")
    return []


class MyPlanner:
    """规划器：将复杂问题分解为步骤列表。"""

    def __init__(self, llm: HelloAgentsLLM, prompt_template: str):
        self.llm = llm
        self.prompt_template = prompt_template

    def plan(self, question: str, **kwargs) -> List[str]:
        print("--- 正在生成计划 ---")
        prompt = _format_prompt(self.prompt_template, question=question)
        messages = [{"role": "user", "content": prompt}]
        response_text = _as_text(self.llm.invoke(messages, **kwargs))
        print(f"✅ 计划原始输出:\n{response_text}")

        plan = _parse_plan(response_text)
        if plan:
            print("✅ 计划已解析:")
            for i, step in enumerate(plan, 1):
                print(f"  {i}. {step}")
        else:
            print("❌ 未能解析出有效计划")
        return plan


class MyExecutor:
    """执行器：按计划逐步求解。"""

    def __init__(self, llm: HelloAgentsLLM, prompt_template: str):
        self.llm = llm
        self.prompt_template = prompt_template

    def execute(self, question: str, plan: List[str], **kwargs) -> str:
        history = ""
        final_answer = ""

        print("\n--- 正在执行计划 ---")
        plan_text = "\n".join(f"{i}. {step}" for i, step in enumerate(plan, 1))

        for i, step in enumerate(plan, 1):
            print(f"\n-> 正在执行步骤 {i}/{len(plan)}: {step}")
            prompt = _format_prompt(
                self.prompt_template,
                question=question,
                plan=plan_text,
                history=history if history else "无",
                current_step=step,
            )
            messages = [{"role": "user", "content": prompt}]
            response_text = _as_text(self.llm.invoke(messages, **kwargs))

            history += f"步骤 {i}: {step}\n结果: {response_text}\n\n"
            final_answer = response_text
            print(f"✅ 步骤 {i} 已完成，结果: {final_answer}")

        return final_answer


class MyPlanAndSolveAgent(Agent):
    """
    重写的 Plan-and-Solve Agent - 分解规划与逐步执行（适配 hello-agents 1.0）
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        custom_prompts: Optional[Dict[str, str]] = None,
    ):
        super().__init__(name=name, llm=llm, system_prompt=system_prompt, config=config)

        prompts = {
            "planner": DEFAULT_PLANNER_PROMPT,
            "executor": DEFAULT_EXECUTOR_PROMPT,
            **(custom_prompts or {}),
        }
        self.prompts = prompts
        self.planner = MyPlanner(self.llm, prompts["planner"])
        self.executor = MyExecutor(self.llm, prompts["executor"])
        print(f"✅ {name} 初始化完成")

    def run(self, input_text: str, **kwargs) -> str:
        """运行 Plan-and-Solve：先规划，再逐步执行。"""
        print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

        plan = self.planner.plan(input_text, **kwargs)
        if not plan:
            final_answer = "无法生成有效的行动计划，任务终止。"
            print(f"\n--- 任务终止 ---\n{final_answer}")
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(final_answer, "assistant"))
            return final_answer

        final_answer = self.executor.execute(input_text, plan, **kwargs)
        print(f"\n--- 任务完成 ---\n最终答案: {final_answer}")

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))
        return final_answer
