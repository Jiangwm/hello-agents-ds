from __future__ import annotations

from dataclasses import replace
from queue import Empty, Queue
from threading import Thread
from typing import Mapping, Protocol, cast

from shared_schemas import AuditTrail, ProtocolResponse


class MCPTransport(Protocol):
    def send(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        access_token: str,
        request_id: str,
        timeout_seconds: float,
    ) -> ProtocolResponse: ...


class InProcessTransport:
    def __init__(self, server: object) -> None:
        self.server = server

    def send(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        access_token: str,
        request_id: str,
        timeout_seconds: float,
    ) -> ProtocolResponse:
        result_queue: Queue[tuple[bool, object]] = Queue(maxsize=1)

        def invoke() -> None:
            try:
                response = self.server.call(
                    tool_name,
                    arguments,
                    access_token=access_token,
                    request_id=request_id,
                )
                result_queue.put_nowait((True, response))
            except Exception as error:
                result_queue.put_nowait((False, error))

        Thread(
            target=invoke,
            name=f"mcp-in-process-{request_id}",
            daemon=True,
        ).start()
        try:
            succeeded, result = result_queue.get(
                timeout=timeout_seconds
            )
        except Empty as error:
            raise TimeoutError(
                f"本地 MCP 调用超过 {timeout_seconds} 秒"
            ) from error
        if succeeded:
            return cast(ProtocolResponse, result)
        if isinstance(result, Exception):
            raise result
        raise RuntimeError("本地 MCP 传输返回未知结果")


class IndustrialMCPClient:
    def __init__(
        self,
        *,
        transport: MCPTransport,
        access_token: str,
        audit_trail: AuditTrail,
        actor_id: str,
        tenant_id: str,
        max_retries: int = 1,
        timeout_seconds: float = 2.0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.transport = transport
        self.access_token = access_token
        self.audit_trail = audit_trail
        self.actor_id = actor_id
        self.tenant_id = tenant_id
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        request_id: str,
    ) -> ProtocolResponse:
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.transport.send(
                    tool_name,
                    arguments,
                    access_token=self.access_token,
                    request_id=request_id,
                    timeout_seconds=self.timeout_seconds,
                )
            except (TimeoutError, ConnectionError) as error:
                is_timeout = isinstance(error, TimeoutError)
                error_code = (
                    "UPSTREAM_TIMEOUT" if is_timeout else "SERVICE_UNAVAILABLE"
                )
                status = "retrying" if attempt < attempts else "degraded"
                error_payload = {
                    "code": error_code,
                    "message": (
                        "上游调用超时"
                        if is_timeout
                        else "上游服务不可用"
                    ),
                    "retryable": True,
                }
                self.audit_trail.append(
                    request_id=request_id,
                    protocol="MCP",
                    service="industrial-mcp-client",
                    operation=tool_name,
                    actor_id=self.actor_id,
                    tenant_id=self.tenant_id,
                    parameters=arguments,
                    status=status,
                    response=error_payload,
                    error_code=error_code,
                )
                if attempt < attempts:
                    continue
                return ProtocolResponse(
                    request_id=request_id,
                    protocol="MCP",
                    service="industrial-mcp-client",
                    status="degraded",
                    error=error_payload,
                    metadata={
                        "attempts": attempt,
                        "max_retries": self.max_retries,
                        "timeout_seconds": self.timeout_seconds,
                        "degradation": (
                            "未生成分析结论；请稍后重试或改用已验证的本地数据快照"
                        ),
                    },
                )
            metadata = dict(response.metadata)
            metadata["attempts"] = attempt
            metadata["max_retries"] = self.max_retries
            response = replace(response, metadata=metadata)
            retryable = bool(
                response.error
                and response.error.get("retryable")
                and response.status != "succeeded"
            )
            if retryable and attempt < attempts:
                continue
            return response
        raise RuntimeError("MCP 重试循环未返回结果")
