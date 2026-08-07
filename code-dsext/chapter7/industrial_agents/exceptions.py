"""工业数据分析智能体框架的异常类型。"""

from __future__ import annotations

from typing import Mapping


class IndustrialAgentError(Exception):
    """工业智能体框架的基础异常。"""


class ConfigurationError(IndustrialAgentError):
    """配置不合法或不满足安全约束。"""


class LLMAdapterError(IndustrialAgentError):
    """模型适配器失败，并保留提供商、模型及调用元数据。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.metadata = dict(metadata or {})


class ToolError(IndustrialAgentError):
    """工具层失败的基础异常。"""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.metadata = dict(metadata or {})


class ToolValidationError(ToolError):
    """工具调用参数、路径或字段未通过校验。"""


class ToolNotAllowedError(ToolError):
    """工具不在当前任务的允许列表中。"""


class ToolExecutionError(ToolError):
    """通过校验的工具在执行过程中失败。"""


class ApprovalRequiredError(ToolError):
    """操作需要人工审批后才能继续。"""
