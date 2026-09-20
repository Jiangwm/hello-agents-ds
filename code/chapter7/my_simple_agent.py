# my_simple_agent.py
"""基于 hello-agents 1.0 的自定义 SimpleAgent。

1.0 起工具调用改为 OpenAI Function Calling（invoke_with_tools），
llm.invoke() 返回 LLMResponse，需取 .content 再写入 Message。
"""
from typing import Optional, Iterator, Any

from hello_agents import SimpleAgent, HelloAgentsLLM, Config, Message


def _as_text(response: Any) -> str:
    """将 LLM 返回值统一为字符串（兼容 LLMResponse / str）。"""
    if response is None:
        return ""
    if hasattr(response, "content"):
        return response.content or ""
    return str(response)


class MySimpleAgent(SimpleAgent):
    """
    重写的简单对话 Agent
    展示如何基于框架基类构建自定义 Agent（适配 hello-agents 1.0）
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional["ToolRegistry"] = None,
        enable_tool_calling: bool = True,
        max_tool_iterations: int = 3,
    ):
        # 1.0：tool_registry / enable_tool_calling 需传给父类
        super().__init__(
            name=name,
            llm=llm,
            system_prompt=system_prompt,
            config=config,
            tool_registry=tool_registry,
            enable_tool_calling=enable_tool_calling,
            max_tool_iterations=max_tool_iterations,
        )
        print(f"✅ {name} 初始化完成，工具调用: {'启用' if self.enable_tool_calling else '禁用'}")

    def run(self, input_text: str, max_tool_iterations: Optional[int] = None, **kwargs) -> str:
        """
        运行方法：委托父类完成 Function Calling 循环，并补充教学用日志。
        """
        print(f"🤖 {self.name} 正在处理: {input_text}")

        if max_tool_iterations is not None:
            self.max_tool_iterations = max_tool_iterations

        # 无工具：直接调用 LLM，显式处理 LLMResponse
        if not self.enable_tool_calling or not self.tool_registry:
            messages = self._build_messages(input_text)
            llm_response = self.llm.invoke(messages, **kwargs)
            response_text = _as_text(llm_response)
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(response_text, "assistant"))
            print(f"✅ {self.name} 响应完成")
            return response_text

        # 有工具：复用父类 Function Calling 实现
        response_text = super().run(input_text, **kwargs)
        print(f"✅ {self.name} 响应完成")
        return response_text

    def stream_run(self, input_text: str, **kwargs) -> Iterator[str]:
        """自定义流式运行（带实时打印）。"""
        print(f"🌊 {self.name} 开始流式处理: {input_text}")

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        for msg in self._history:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": input_text})

        full_response = ""
        print("📝 实时响应: ", end="")
        for chunk in self.llm.stream_invoke(messages, **kwargs):
            full_response += chunk
            print(chunk, end="", flush=True)
            yield chunk
        print()

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(full_response, "assistant"))
        print(f"✅ {self.name} 流式响应完成")

    def add_tool(self, tool, auto_expand: bool = True) -> None:
        """添加工具到 Agent（便利方法）。"""
        if not self.tool_registry:
            from hello_agents import ToolRegistry

            self.tool_registry = ToolRegistry()
            self.enable_tool_calling = True

        self.tool_registry.register_tool(tool, auto_expand=auto_expand)
        print(f"🔧 工具 '{getattr(tool, 'name', tool)}' 已添加")

    def has_tools(self) -> bool:
        """检查是否有可用工具。"""
        return self.enable_tool_calling and self.tool_registry is not None

    def remove_tool(self, tool_name: str) -> bool:
        """移除工具（便利方法）。"""
        if self.tool_registry:
            self.tool_registry.unregister(tool_name)
            return True
        return False

    def list_tools(self) -> list:
        """列出所有可用工具。"""
        if self.tool_registry:
            return self.tool_registry.list_tools()
        return []
