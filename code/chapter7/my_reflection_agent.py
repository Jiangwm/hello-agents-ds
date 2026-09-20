# my_reflection_agent.py
"""基于 hello-agents 1.0 的自定义 ReflectionAgent。

参考第四章 Reflection 范式与 my_react_agent 的适配方式：
- 继承框架 ReflectionAgent，保留历史 / Trace 等基础设施
- 用可配置提示词模板驱动 initial → reflect → refine 循环
- llm.invoke() 返回 LLMResponse，统一取 .content
"""
from typing import Optional, Dict, Any

from hello_agents import ReflectionAgent, HelloAgentsLLM, Config, Message
from hello_agents.agents.reflection_agent import Memory


DEFAULT_PROMPTS = {
    "initial": """
请根据以下要求完成任务:

任务: {task}

请提供一个完整、准确的回答。
""",
    "reflect": """
请仔细审查以下回答，并找出可能的问题或改进空间:

# 原始任务:
{task}

# 当前回答:
{content}

请分析这个回答的质量，指出不足之处，并提出具体的改进建议。
如果回答已经很好，请回答"无需改进"。
""",
    "refine": """
请根据反馈意见改进你的回答:

# 原始任务:
{task}

# 上一轮回答:
{last_attempt}

# 反馈意见:
{feedback}

请提供一个改进后的回答。
""",
}


def _as_text(response: Any) -> str:
    """将 LLM 返回值统一为字符串（兼容 LLMResponse / str）。"""
    if response is None:
        return ""
    if hasattr(response, "content"):
        return response.content or ""
    return str(response)


class _SafeFormat(dict):
    """format 时忽略模板中未提供的占位符。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format_prompt(template: str, **kwargs) -> str:
    return template.format_map(_SafeFormat(**kwargs))


class MyReflectionAgent(ReflectionAgent):
    """
    重写的反思 Agent - 自我反思与迭代优化（适配 hello-agents 1.0）
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_iterations: int = 3,
        custom_prompts: Optional[Dict[str, str]] = None,
        tool_registry=None,
        enable_tool_calling: bool = False,
        max_tool_iterations: int = 3,
    ):
        # 工作流由 initial/reflect/refine 三阶段模板驱动，
        # 避免沿用父类“一次完成反思全流程”的 system_prompt
        if system_prompt is None:
            system_prompt = (
                "你是一个有用的AI助手。"
                "请仅根据当前用户消息完成指定的子任务，不要自行展开多步反思或优化流程。"
            )

        # 默认关闭工具调用：反思循环以文本生成为主，与 chapter7 测试一致
        super().__init__(
            name=name,
            llm=llm,
            system_prompt=system_prompt,
            config=config,
            max_iterations=max_iterations,
            tool_registry=tool_registry,
            enable_tool_calling=enable_tool_calling,
            max_tool_iterations=max_tool_iterations,
        )
        self.prompts = {**DEFAULT_PROMPTS, **(custom_prompts or {})}
        print(f"✅ {name} 初始化完成，最大迭代: {max_iterations}")

    def run(self, input_text: str, **kwargs) -> str:
        """运行 Reflection Agent（initial → reflect → refine）。"""
        print(f"\n🤖 {self.name} 开始处理任务: {input_text}")

        self.memory = Memory()  # 重置记忆

        # 1. 初始执行
        print("\n--- 正在进行初始尝试 ---")
        initial_result = self._execute_task(input_text, **kwargs)
        self.memory.add_record("execution", initial_result)

        # 2. 迭代：反思与优化
        for i in range(self.max_iterations):
            print(f"\n--- 第 {i + 1}/{self.max_iterations} 轮迭代 ---")

            print("\n-> 正在进行反思...")
            last_result = self.memory.get_last_execution()
            feedback = self._reflect_on_result(input_text, last_result, **kwargs)
            self.memory.add_record("reflection", feedback)

            if "无需改进" in feedback or "no need for improvement" in feedback.lower():
                print("\n✅ 反思认为结果已无需改进，任务完成。")
                break

            print("\n-> 正在进行优化...")
            refined_result = self._refine_result(
                input_text, last_result, feedback, **kwargs
            )
            self.memory.add_record("execution", refined_result)

        final_result = self.memory.get_last_execution()
        print(f"\n--- 任务完成 ---\n最终结果:\n{final_result}")

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_result, "assistant"))
        return final_result

    def _execute_task(self, task: str, **kwargs) -> str:
        prompt = _format_prompt(self.prompts["initial"], task=task)
        return self._invoke_prompt(prompt, **kwargs)

    def _reflect_on_result(self, task: str, result: str, **kwargs) -> str:
        # 兼容 chapter7 的 {content} 与 chapter4 的 {code}
        prompt = _format_prompt(
            self.prompts["reflect"],
            task=task,
            content=result,
            code=result,
        )
        return self._invoke_prompt(prompt, **kwargs)

    def _refine_result(
        self, task: str, last_attempt: str, feedback: str, **kwargs
    ) -> str:
        prompt = _format_prompt(
            self.prompts["refine"],
            task=task,
            last_attempt=last_attempt,
            last_code_attempt=last_attempt,
            feedback=feedback,
            content=last_attempt,
        )
        return self._invoke_prompt(prompt, **kwargs)

    def _invoke_prompt(self, prompt: str, **kwargs) -> str:
        messages = [{"role": "user", "content": prompt}]
        if self.system_prompt:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        return _as_text(self.llm.invoke(messages, **kwargs))
