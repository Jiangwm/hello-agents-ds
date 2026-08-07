"""模型提供商无关的适配器接口及离线、OpenAI 兼容实现。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Iterable, Mapping, Sequence

from industrial_agents.exceptions import LLMAdapterError
from industrial_agents.schemas import (
    IndustrialMessage,
    LLMResponse,
    MessageRole,
    ToolCall,
)


class LLMAdapter(ABC):
    """统一模型调用的最小公共接口。"""

    provider: str
    model: str

    @abstractmethod
    def invoke(
        self,
        messages: Sequence[IndustrialMessage],
        timeout_seconds: float | None = None,
        tools: Sequence[Mapping[str, object]] = (),
    ) -> LLMResponse:
        """调用模型并返回标准化响应。"""


class MockLLMAdapter(LLMAdapter):
    """用于离线测试的脚本化模型适配器。"""

    def __init__(
        self,
        responses: Iterable[str | LLMResponse] = (),
        *,
        provider: str = "mock",
        model: str = "mock-industrial-v1",
    ) -> None:
        self.provider = provider
        self.model = model
        self._responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def invoke(
        self,
        messages: Sequence[IndustrialMessage],
        timeout_seconds: float | None = None,
        tools: Sequence[Mapping[str, object]] = (),
    ) -> LLMResponse:
        recorded_messages = tuple(messages)
        self.calls.append(
            {
                "messages": recorded_messages,
                "timeout_seconds": timeout_seconds,
                "tools": tuple(tools),
            }
        )
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise LLMAdapterError(
                "MockLLMAdapter 的脚本化响应已耗尽",
                provider=self.provider,
                model=self.model,
                metadata={"call_count": len(self.calls)},
            ) from exc

        if isinstance(response, str):
            return LLMResponse(
                content=response,
                provider=self.provider,
                model=self.model,
                metadata={"adapter": "mock"},
            )
        if not isinstance(response, LLMResponse):
            raise LLMAdapterError(
                "MockLLMAdapter 仅接受 str 或 LLMResponse",
                provider=self.provider,
                model=self.model,
                metadata={"response_type": type(response).__name__},
            )
        if response.provider is None or response.model is None:
            return replace(
                response,
                provider=response.provider or self.provider,
                model=response.model or self.model,
            )
        return response


class OpenAICompatibleLLMAdapter(LLMAdapter):
    """支持云端和本地 OpenAI 兼容服务的延迟导入适配器。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str = "openai-compatible",
        timeout_seconds: float = 30.0,
        client: object | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model 不能为空")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self._client = client

    def invoke(
        self,
        messages: Sequence[IndustrialMessage],
        timeout_seconds: float | None = None,
        tools: Sequence[Mapping[str, object]] = (),
    ) -> LLMResponse:
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        )
        if effective_timeout <= 0:
            raise LLMAdapterError(
                "timeout_seconds 必须大于 0",
                provider=self.provider,
                model=self.model,
                metadata={"timeout_seconds": effective_timeout},
            )
        try:
            _validate_tool_protocol(messages)
            request: dict[str, object] = {
                "model": self.model,
                "messages": [_message_payload(message) for message in messages],
                "timeout": effective_timeout,
            }
            if tools:
                request["tools"] = list(tools)
            response = self._get_client().chat.completions.create(
                **request,
            )
            return self._to_response(response)
        except LLMAdapterError as exc:
            if exc.provider is not None and exc.model is not None:
                raise
            raise LLMAdapterError(
                str(exc),
                provider=self.provider,
                model=self.model,
                metadata={
                    **exc.metadata,
                    "base_url": self.base_url,
                    "timeout_seconds": effective_timeout,
                },
            ) from exc
        except Exception as exc:
            raise LLMAdapterError(
                "OpenAI 兼容模型调用失败",
                provider=self.provider,
                model=self.model,
                metadata={
                    "base_url": self.base_url,
                    "timeout_seconds": effective_timeout,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    def _get_client(self) -> object:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMAdapterError(
                "未安装 openai；请安装后再使用 OpenAICompatibleLLMAdapter",
                provider=self.provider,
                model=self.model,
                metadata={"base_url": self.base_url},
            ) from exc
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        return self._client

    def _to_response(self, response: object) -> LLMResponse:
        choices = _value(response, "choices", ())
        if not choices:
            raise LLMAdapterError(
                "模型响应未包含 choices",
                provider=self.provider,
                model=self.model,
                metadata=_response_metadata(response),
            )
        choice = choices[0]
        message = _value(choice, "message")
        if message is None:
            raise LLMAdapterError(
                "模型响应未包含 message",
                provider=self.provider,
                model=self.model,
                metadata=_response_metadata(response),
            )
        tool_calls = _tool_calls(_value(message, "tool_calls", ()))
        return LLMResponse(
            content=_content_text(_value(message, "content", "")),
            tool_calls=tool_calls,
            provider=self.provider,
            model=self.model,
            metadata={
                **_response_metadata(response),
                "finish_reason": _value(choice, "finish_reason"),
            },
        )


def _message_payload(message: IndustrialMessage) -> dict[str, object]:
    payload = {"role": message.role.value, "content": message.content}
    if message.name is not None:
        payload["name"] = message.name
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _validate_tool_protocol(
    messages: Sequence[IndustrialMessage],
) -> None:
    pending_call_ids: set[str] = set()
    for message in messages:
        if pending_call_ids:
            if message.role != MessageRole.TOOL:
                raise ValueError(
                    "assistant tool_calls 后必须紧跟匹配的 tool 消息"
                )
            if message.tool_call_id not in pending_call_ids:
                raise ValueError(
                    f"未知或重复的 tool_call_id：{message.tool_call_id}"
                )
            pending_call_ids.remove(message.tool_call_id)
            continue
        if message.role == MessageRole.TOOL:
            raise ValueError(
                f"tool_call_id 未匹配前序 assistant 调用：{message.tool_call_id}"
            )
        if message.role == MessageRole.ASSISTANT and message.tool_calls:
            call_ids = tuple(call.call_id for call in message.tool_calls)
            if len(set(call_ids)) != len(call_ids):
                raise ValueError("assistant tool_calls 的 call_id 不得重复")
            pending_call_ids.update(call_ids)
    if pending_call_ids:
        raise ValueError(
            "缺少 tool 调用结果：" + "、".join(sorted(pending_call_ids))
        )


def _value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _content_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            text = _value(item, "text", "")
            if isinstance(text, str):
                fragments.append(text)
        return "".join(fragments)
    return str(content)


def _tool_calls(value: object) -> tuple[ToolCall, ...]:
    if value is None:
        return ()
    calls: list[ToolCall] = []
    for index, raw_call in enumerate(value):
        function = _value(raw_call, "function", {})
        name = _value(function, "name")
        arguments = _arguments_mapping(_value(function, "arguments", {}))
        if not isinstance(name, str) or not name.strip():
            raise LLMAdapterError(
                "结构化工具调用缺少函数名称",
                metadata={"tool_call_index": index},
            )
        call_id = _value(raw_call, "id", f"tool-call-{index + 1}")
        calls.append(ToolCall(call_id=str(call_id), name=name, arguments=arguments))
    return tuple(calls)


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if isinstance(arguments, Mapping):
        return arguments
    if not isinstance(arguments, str):
        raise LLMAdapterError("工具调用参数必须是 JSON 对象")
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        raise LLMAdapterError("工具调用参数不是合法 JSON 对象") from exc
    if not isinstance(parsed, Mapping):
        raise LLMAdapterError("工具调用参数必须是 JSON 对象")
    return parsed


def _response_metadata(response: object) -> dict[str, object]:
    usage = _value(response, "usage")
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    return {
        "response_id": _value(response, "id"),
        "created": _value(response, "created"),
        "usage": usage,
    }
