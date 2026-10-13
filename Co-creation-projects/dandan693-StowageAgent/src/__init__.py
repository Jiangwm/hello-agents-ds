"""配载智能体的核心模块。

各模块职责：

    llm.py       大语言模型基础（怎么跟模型说话）
    tools.py     工具系统（给智能体装上"手"）
    memory.py    记忆系统（短期对话 + 长期笔记）
    rag.py       检索增强（让模型能"翻资料"）
    context.py   上下文工程（拼提示词、裁长度）
    agent.py     ReAct 循环（想一步、做一步）
    evaluate.py  性能评估（给 Agent 打分）
"""

from .agent import ReActAgent
from .context import ContextBuilder
from .evaluate import check_tools, evaluate_agent
from .llm import LLM, LLMError
from .memory import Memory
from .rag import Retriever
from .tools import (
    BayTool,
    CoordTool,
    KnowledgeTool,
    StowageCheckTool,
    ToolRegistry,
)

__all__ = [
    "LLM",
    "LLMError",
    "ReActAgent",
    "ContextBuilder",
    "Memory",
    "Retriever",
    "ToolRegistry",
    "BayTool",
    "CoordTool",
    "StowageCheckTool",
    "KnowledgeTool",
    "check_tools",
    "evaluate_agent",
]
