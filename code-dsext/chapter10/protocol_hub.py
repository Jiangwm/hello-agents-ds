from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from a2a_agents import IndustrialA2ANetwork
from anp_registry import ANPRegistry, ServiceRequirements
from mcp_client import IndustrialMCPClient, InProcessTransport
from mcp_server import IndustrialDataMCPServer
from shared_schemas import (
    A2AMessage,
    AnalysisTask,
    AuditRecord,
    AuditTrail,
)


@dataclass(frozen=True)
class HubResult:
    request_id: str
    task_id: str
    status: str
    selected_service: Mapping[str, object]
    messages: tuple[A2AMessage, ...]
    report: Mapping[str, object]
    audit_records: tuple[AuditRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "status": self.status,
            "selected_service": dict(self.selected_service),
            "messages": [message.to_dict() for message in self.messages],
            "report": dict(self.report),
            "audit_records": [
                record.to_dict() for record in self.audit_records
            ],
        }


class IndustrialProtocolHub:
    def __init__(
        self,
        *,
        server: IndustrialDataMCPServer,
        registry: ANPRegistry,
        network: IndustrialA2ANetwork,
        audit_trail: AuditTrail,
    ) -> None:
        self.server = server
        self.registry = registry
        self.network = network
        self.audit_trail = audit_trail

    def run(
        self,
        task: AnalysisTask,
        *,
        access_token: str,
    ) -> HubResult:
        audit_start = len(self.audit_trail.records)
        identity = self.server.authenticate(access_token)
        if identity is None or identity.tenant_id != task.tenant_id:
            error_code = (
                "UNAUTHENTICATED"
                if identity is None
                else "TENANT_MISMATCH"
            )
            error = {
                "code": error_code,
                "message": (
                    "访问令牌无效"
                    if identity is None
                    else "任务租户与访问令牌租户不一致"
                ),
                "retryable": False,
            }
            self.audit_trail.append(
                request_id=task.request_id,
                protocol="HUB",
                service="industrial-protocol-hub",
                operation="authenticate_request",
                actor_id=(
                    "anonymous"
                    if identity is None
                    else identity.actor_id
                ),
                tenant_id=(
                    "unknown"
                    if identity is None
                    else identity.tenant_id
                ),
                parameters={
                    "task_id": task.task_id,
                    "claimed_tenant_id": task.tenant_id,
                },
                status="failed",
                response=error,
                error_code=error_code,
            )
            return HubResult(
                request_id=task.request_id,
                task_id=task.task_id,
                status="degraded",
                selected_service={},
                messages=(),
                report={
                    "facts": {},
                    "hypotheses": [],
                    "unknowns": ["身份认证失败，未执行服务发现与分析"],
                    "conclusion": "协议中枢已在服务发现前拒绝请求",
                    "next_steps": ["核对访问令牌与任务租户后重试"],
                    "error": error,
                },
                audit_records=tuple(
                    self.audit_trail.records[audit_start:]
                ),
            )
        selection = self.registry.select(
            ServiceRequirements(
                capability="timeseries_analysis",
                line_id=task.line_id,
                tenant_id=task.tenant_id,
                start_time=task.start_time,
                end_time=task.end_time,
                algorithm="drift_detection",
                minimum_algorithm_version="1.2.0",
                max_load=0.8,
                max_timeout_ms=1000,
                require_traceable=True,
            ),
            request_id=task.request_id,
        )
        if selection.status != "succeeded":
            report = {
                "facts": {},
                "hypotheses": [],
                "unknowns": ["没有符合权限与数据范围要求的分析服务"],
                "conclusion": "ANP 服务发现失败，A2A 分析未启动",
                "next_steps": ["恢复合规服务后使用同一 request_id 重试"],
                "degradation": selection.metadata.get("degradation", ""),
            }
            return HubResult(
                request_id=task.request_id,
                task_id=task.task_id,
                status="degraded",
                selected_service={},
                messages=(),
                report=report,
                audit_records=tuple(
                    self.audit_trail.records[audit_start:]
                ),
            )
        selected_service = dict(selection.data["selected_service"])
        dispatch = self.registry.dispatch(
            service_id=str(selected_service["service_id"]),
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            line_id=task.line_id,
            request_id=task.request_id,
        )
        if dispatch.status != "succeeded":
            return HubResult(
                request_id=task.request_id,
                task_id=task.task_id,
                status="degraded",
                selected_service=selected_service,
                messages=(),
                report={
                    "facts": {},
                    "hypotheses": [],
                    "unknowns": ["ANP 任务分派失败"],
                    "conclusion": "分析服务未执行任务",
                    "next_steps": ["检查服务注册信息后重试"],
                    "error": dispatch.error,
                },
                audit_records=tuple(
                    self.audit_trail.records[audit_start:]
                ),
            )
        client = IndustrialMCPClient(
            transport=InProcessTransport(self.server),
            access_token=access_token,
            audit_trail=self.audit_trail,
            actor_id=identity.actor_id,
            tenant_id=task.tenant_id,
            max_retries=1,
            timeout_seconds=2.0,
        )
        messages = self.network.run(
            task,
            client=client,
            selected_service=selected_service,
        )
        report = dict(messages[-1].payload)
        status = (
            "completed"
            if messages[-1].status == "succeeded"
            else "degraded"
        )
        return HubResult(
            request_id=task.request_id,
            task_id=task.task_id,
            status=status,
            selected_service=selected_service,
            messages=messages,
            report=report,
            audit_records=tuple(self.audit_trail.records[audit_start:]),
        )


def build_default_hub(chapter_dir: Path) -> IndustrialProtocolHub:
    data_root = chapter_dir / "data"
    audit_trail = AuditTrail()
    server = IndustrialDataMCPServer(
        data_root=data_root,
        audit_trail=audit_trail,
    )
    registry = ANPRegistry.from_json(
        data_root / "services.json",
        audit_trail=audit_trail,
    )
    network = IndustrialA2ANetwork(audit_trail)
    return IndustrialProtocolHub(
        server=server,
        registry=registry,
        network=network,
        audit_trail=audit_trail,
    )


__all__ = [
    "AnalysisTask",
    "HubResult",
    "IndustrialProtocolHub",
    "build_default_hub",
]
