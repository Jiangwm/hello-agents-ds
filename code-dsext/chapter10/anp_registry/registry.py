from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Mapping

from shared_schemas import AuditTrail, ProtocolResponse


@dataclass(frozen=True)
class ServiceDescriptor:
    service_id: str
    service_name: str
    capabilities: tuple[str, ...]
    line_ids: tuple[str, ...]
    tenant_ids: tuple[str, ...]
    data_start: str
    data_end: str
    algorithms: Mapping[str, str]
    load: float
    timeout_ms: int
    healthy: bool
    traceable: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ServiceDescriptor:
        return cls(
            service_id=str(value["service_id"]),
            service_name=str(value["service_name"]),
            capabilities=tuple(str(item) for item in value["capabilities"]),
            line_ids=tuple(str(item) for item in value["line_ids"]),
            tenant_ids=tuple(str(item) for item in value["tenant_ids"]),
            data_start=str(value["data_start"]),
            data_end=str(value["data_end"]),
            algorithms={
                str(name): str(version)
                for name, version in value["algorithms"].items()
            },
            load=float(value["load"]),
            timeout_ms=int(value["timeout_ms"]),
            healthy=bool(value["healthy"]),
            traceable=bool(value["traceable"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "service_id": self.service_id,
            "service_name": self.service_name,
            "capabilities": list(self.capabilities),
            "line_ids": list(self.line_ids),
            "tenant_ids": list(self.tenant_ids),
            "data_start": self.data_start,
            "data_end": self.data_end,
            "algorithms": dict(self.algorithms),
            "load": self.load,
            "timeout_ms": self.timeout_ms,
            "healthy": self.healthy,
            "traceable": self.traceable,
        }


@dataclass(frozen=True)
class ServiceRequirements:
    capability: str
    line_id: str
    tenant_id: str
    start_time: str
    end_time: str
    algorithm: str
    minimum_algorithm_version: str
    max_load: float
    max_timeout_ms: int
    require_traceable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "line_id": self.line_id,
            "tenant_id": self.tenant_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "algorithm": self.algorithm,
            "minimum_algorithm_version": self.minimum_algorithm_version,
            "max_load": self.max_load,
            "max_timeout_ms": self.max_timeout_ms,
            "require_traceable": self.require_traceable,
        }


class ANPRegistry:
    def __init__(
        self,
        services: tuple[ServiceDescriptor, ...],
        audit_trail: AuditTrail,
    ) -> None:
        self.services = services
        self.audit_trail = audit_trail

    @classmethod
    def from_json(
        cls,
        path: Path,
        *,
        audit_trail: AuditTrail,
    ) -> ANPRegistry:
        payload = json.loads(path.read_text(encoding="utf-8"))
        services = tuple(ServiceDescriptor.from_dict(item) for item in payload)
        return cls(services, audit_trail)

    def select(
        self,
        requirements: ServiceRequirements,
        *,
        request_id: str,
    ) -> ProtocolResponse:
        eligible: list[ServiceDescriptor] = []
        rejected: dict[str, str] = {}
        for service in self.services:
            reasons = self._rejection_reasons(service, requirements)
            if reasons:
                rejected[service.service_id] = "；".join(reasons)
            else:
                eligible.append(service)
        eligible.sort(
            key=lambda service: (
                service.load,
                service.timeout_ms,
                service.service_id,
            )
        )
        if eligible:
            selected = eligible[0]
            response = ProtocolResponse(
                request_id=request_id,
                protocol="ANP",
                service="industrial-anp-registry",
                status="succeeded",
                data={
                    "selected_service": selected.to_dict(),
                    "eligible_service_ids": [
                        service.service_id for service in eligible
                    ],
                    "rejected_services": rejected,
                    "selection_reason": (
                        "满足数据域、租户、算法、健康与追溯约束后，"
                        "按负载、超时和服务 ID 确定性排序"
                    ),
                },
            )
        else:
            response = ProtocolResponse(
                request_id=request_id,
                protocol="ANP",
                service="industrial-anp-registry",
                status="failed",
                data={
                    "eligible_service_ids": [],
                    "rejected_services": rejected,
                },
                error={
                    "code": "NO_SERVICE_AVAILABLE",
                    "message": "没有服务同时满足数据域、租户和运行约束",
                    "retryable": True,
                },
                metadata={
                    "degradation": (
                        "停止 A2A 分析，不跨租户或降低算法版本要求自动选取服务"
                    )
                },
            )
        self.audit_trail.append(
            request_id=request_id,
            protocol="ANP",
            service="industrial-anp-registry",
            operation="select_service",
            actor_id="protocol-hub",
            tenant_id=requirements.tenant_id,
            parameters=requirements.to_dict(),
            status=response.status,
            response=response.to_dict(),
            error_code=(
                None if response.error is None else str(response.error["code"])
            ),
        )
        return response

    def dispatch(
        self,
        *,
        service_id: str,
        task_id: str,
        tenant_id: str,
        line_id: str,
        request_id: str,
    ) -> ProtocolResponse:
        selected = next(
            (
                service
                for service in self.services
                if service.service_id == service_id
            ),
            None,
        )
        if (
            selected is None
            or tenant_id not in selected.tenant_ids
            or line_id not in selected.line_ids
        ):
            response = ProtocolResponse(
                request_id=request_id,
                protocol="ANP",
                service="industrial-anp-registry",
                status="failed",
                error={
                    "code": "DISPATCH_REJECTED",
                    "message": "所选服务不能承接该租户和产线任务",
                    "retryable": False,
                },
            )
        else:
            response = ProtocolResponse(
                request_id=request_id,
                protocol="ANP",
                service="industrial-anp-registry",
                status="succeeded",
                data={
                    "assignment": {
                        "task_id": task_id,
                        "service_id": selected.service_id,
                        "capability": "timeseries_analysis",
                        "algorithm": "drift_detection",
                        "algorithm_version": selected.algorithms[
                            "drift_detection"
                        ],
                    }
                },
            )
        self.audit_trail.append(
            request_id=request_id,
            protocol="ANP",
            service="industrial-anp-registry",
            operation="dispatch_task",
            actor_id="protocol-hub",
            tenant_id=tenant_id,
            parameters={
                "task_id": task_id,
                "service_id": service_id,
                "line_id": line_id,
            },
            status=response.status,
            response=response.to_dict(),
            error_code=(
                None if response.error is None else str(response.error["code"])
            ),
        )
        return response

    @staticmethod
    def _rejection_reasons(
        service: ServiceDescriptor,
        requirements: ServiceRequirements,
    ) -> list[str]:
        reasons: list[str] = []
        if requirements.capability not in service.capabilities:
            reasons.append("能力不匹配")
        if requirements.line_id not in service.line_ids:
            reasons.append("产线数据域不覆盖")
        if requirements.tenant_id not in service.tenant_ids:
            reasons.append("租户无权限")
        requested_start = datetime.fromisoformat(requirements.start_time)
        requested_end = datetime.fromisoformat(requirements.end_time)
        available_start = datetime.fromisoformat(service.data_start)
        available_end = datetime.fromisoformat(service.data_end)
        if requested_start < available_start or requested_end > available_end:
            reasons.append("时间范围不覆盖")
        version = service.algorithms.get(requirements.algorithm)
        if version is None:
            reasons.append("算法不支持")
        elif _version_tuple(version) < _version_tuple(
            requirements.minimum_algorithm_version
        ):
            reasons.append("算法版本过低")
        if service.load > requirements.max_load:
            reasons.append("负载超过上限")
        if service.timeout_ms > requirements.max_timeout_ms:
            reasons.append("预计超时超过上限")
        if not service.healthy:
            reasons.append("服务不健康")
        if requirements.require_traceable and not service.traceable:
            reasons.append("结果不可追溯")
        return reasons


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in value.split("."))
