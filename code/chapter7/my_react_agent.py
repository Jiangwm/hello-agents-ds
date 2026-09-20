# my_react_agent.py
"""基于 hello-agents 1.0 的自定义 ReActAgent。

1.0 起 ReAct 改为 Function Calling：
- Thought / Finish 为内置工具
- 业务工具通过 ToolRegistry 注入
- 不再使用 Thought:/Action: 文本解析与 _parse_output
"""
from typing import Optional

from hello_agents import ReActAgent, HelloAgentsLLM, Config, ToolRegistry
from hello_agents.agents.react_agent import DEFAULT_REACT_SYSTEM_PROMPT

# 保留旧版文本模板，便于对照学习；运行时会转换为系统提示词
MY_REACT_PROMPT = """你是一个具备推理和行动能力的AI助手。你可以通过思考分析问题，然后调用合适的工具来获取信息，最终给出准确的答案。

## 可用工具
{tools}

## 工作流程
请使用 Function Calling 完成任务：
1. 需要推理时调用 Thought 工具
2. 需要外部信息时调用业务工具
3. 得出结论时调用 Finish 工具返回最终答案

## 重要提醒
1. 主动使用 Thought 记录推理过程
2. 可以多次调用工具获取信息
3. 只有确信有足够信息时才调用 Finish

## 当前任务说明
用户问题与执行历史由框架在多轮对话中自动维护。
原始模板占位（兼容旧教材）：question={question}；history={history}
"""


def _prompt_to_system(custom_prompt: str) -> str:
    """将旧版 {tools}/{question}/{history} 模板转为 1.0 系统提示词。

    旧教材常用 Thought:/Action: 文本格式；1.0 必须改用 Function Calling，
    因此在自定义内容前叠加官方工作流说明，并明确禁止文本 Action。
    """
    try:
        role_part = custom_prompt.format(
            tools="（工具通过 Function Calling schema 自动注入）",
            question="（运行时由用户消息提供）",
            history="（由多轮 assistant/tool 消息自动维护）",
        )
    except (KeyError, ValueError, IndexError):
        role_part = custom_prompt

    return (
        f"{DEFAULT_REACT_SYSTEM_PROMPT}\n\n"
        "## 额外角色 / 任务设定\n"
        "请忽略任何要求用 `Thought:` / `Action:` 纯文本格式回复的说明；"
        "必须通过 Function Calling 调用 Thought、业务工具和 Finish。\n\n"
        f"{role_part}"
    )


class MyReActAgent(ReActAgent):
    """
    重写的 ReAct Agent - 推理与行动结合（适配 hello-agents 1.0 Function Calling）
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        tool_registry: ToolRegistry,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5,
        custom_prompt: Optional[str] = None,
    ):
        # custom_prompt：兼容旧教材的模板参数，映射为系统提示词
        if custom_prompt:
            system_prompt = _prompt_to_system(custom_prompt)
        elif system_prompt is None:
            system_prompt = DEFAULT_REACT_SYSTEM_PROMPT

        # 1.0 签名：tool_registry 是第 3 个位置参数，需用关键字传参避免错位
        super().__init__(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            system_prompt=system_prompt,
            config=config,
            max_steps=max_steps,
        )
        self.prompt_template = custom_prompt if custom_prompt else MY_REACT_PROMPT
        print(f"✅ {name} 初始化完成，最大步数: {max_steps}")

    def run(self, input_text: str, **kwargs) -> str:
        """运行 ReAct Agent（Function Calling 循环由父类实现）。

        每次 run 重建 TraceLogger：父类 finalize() 会关闭文件句柄，
        同一实例连续多次 run 会触发 "I/O operation on closed file"。
        """
        if self.config.trace_enabled:
            from hello_agents.observability import TraceLogger

            self.trace_logger = TraceLogger(
                output_dir=self.config.trace_dir,
                sanitize=self.config.trace_sanitize,
                html_include_raw_response=self.config.trace_html_include_raw_response,
            )
            self.trace_logger.log_event(
                "session_start",
                {
                    "agent_name": self.name,
                    "agent_type": self.__class__.__name__,
                },
            )
        return super().run(input_text, **kwargs)
