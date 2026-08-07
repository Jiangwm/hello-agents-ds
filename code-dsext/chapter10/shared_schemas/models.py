from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source: str
    data_range: str
    checksum: str

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "data_range": self.data_range,
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class ProtocolResponse:
    request_id: str
    protocol: str
    service: str
    status: str
    data: Mapping[str, object] = field(default_factory=dict)
    evidence_refs: tuple[EvidenceRef, ...] = ()
    error: Mapping[str, object] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "protocol": self.protocol,
            "service": self.service,
            "status": self.status,
            "data": dict(self.data),
            "evidence_refs": [
                evidence.to_dict() for evidence in self.evidence_refs
            ],
            "error": None if self.error is None else dict(self.error),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DataRange:
    start_time: str
    end_time: str
    equipment_ids: tuple[str, ...]
    measurements: tuple[str, ...]

    def __post_init__(self) -> None:
        start = datetime.fromisoformat(self.start_time)
        end = datetime.fromisoformat(self.end_time)
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("数据范围时间必须包含时区")
        if start >= end:
            raise ValueError("数据范围开始时间必须早于结束时间")
        if not self.equipment_ids or not self.measurements:
            raise ValueError("数据范围必须包含设备和测点")

    def to_dict(self) -> dict[str, object]:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "equipment_ids": list(self.equipment_ids),
            "measurements": list(self.measurements),
        }


@dataclass(frozen=True)
class AnalysisTask:
    task_id: str
    request_id: str
    tenant_id: str
    line_id: str
    equipment_id: str
    measurement: str
    start_time: str
    end_time: str
    alarm_code: str

    def data_range(self) -> DataRange:
        return DataRange(
            start_time=self.start_time,
            end_time=self.end_time,
            equipment_ids=(self.equipment_id,),
            measurements=(self.measurement,),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "line_id": self.line_id,
            "equipment_id": self.equipment_id,
            "measurement": self.measurement,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "alarm_code": self.alarm_code,
        }


@dataclass(frozen=True)
class A2AMessage:
    request_id: str
    task_id: str
    sender: str
    recipient: str
    capability: str
    input_data_range: DataRange
    evidence_refs: tuple[EvidenceRef, ...]
    status: str
    payload: Mapping[str, object]
    error: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("request_id", self.request_id),
            ("task_id", self.task_id),
            ("sender", self.sender),
            ("recipient", self.recipient),
            ("capability", self.capability),
        ):
            if not value.strip():
                raise ValueError(f"{name} 不能为空")
        if self.status not in {"succeeded", "degraded", "failed"}:
            raise ValueError(f"未知 A2A 状态：{self.status}")
        if self.status == "succeeded" and not self.evidence_refs:
            raise ValueError("成功的 A2A 消息必须携带证据引用")
        if self.status != "succeeded" and self.error is None:
            raise ValueError("非成功 A2A 消息必须携带错误")

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "capability": self.capability,
            "input_data_range": self.input_data_range.to_dict(),
            "evidence_refs": [
                evidence.to_dict() for evidence in self.evidence_refs
            ],
            "status": self.status,
            "payload": dict(self.payload),
            "error": None if self.error is None else dict(self.error),
        }
