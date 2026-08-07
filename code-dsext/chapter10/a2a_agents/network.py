from __future__ import annotations

from datetime import datetime
from math import sqrt
from typing import Iterable, Mapping

from mcp_client import IndustrialMCPClient
from shared_schemas import (
    A2AMessage,
    AnalysisTask,
    AuditTrail,
    EvidenceRef,
    ProtocolResponse,
)


class IndustrialA2ANetwork:
    def __init__(self, audit_trail: AuditTrail) -> None:
        self.audit_trail = audit_trail

    def run(
        self,
        task: AnalysisTask,
        *,
        client: IndustrialMCPClient,
        selected_service: Mapping[str, object],
    ) -> tuple[A2AMessage, ...]:
        data_quality = self._run_data_quality(
            task,
            client=client,
            selected_service=selected_service,
        )
        if data_quality.status != "succeeded":
            report = self._degraded_report(task, data_quality)
            return (data_quality, report)
        timeseries = self._run_timeseries(
            task,
            data_quality,
            selected_service,
        )
        if timeseries.status != "succeeded":
            report = self._degraded_report(task, timeseries)
            return (data_quality, timeseries, report)
        diagnosis = self._run_diagnosis(
            task,
            timeseries=timeseries,
            client=client,
        )
        report = self._run_report(
            task,
            data_quality=data_quality,
            timeseries=timeseries,
            diagnosis=diagnosis,
            selected_service=selected_service,
        )
        return (data_quality, timeseries, diagnosis, report)

    def _run_data_quality(
        self,
        task: AnalysisTask,
        *,
        client: IndustrialMCPClient,
        selected_service: Mapping[str, object],
    ) -> A2AMessage:
        query = client.call(
            "query_timeseries",
            {
                "equipment_id": task.equipment_id,
                "measurement": task.measurement,
                "start_time": task.start_time,
                "end_time": task.end_time,
                "max_rows": 50,
            },
            request_id=task.request_id,
        )
        dictionary = client.call(
            "get_data_dictionary",
            {"field": task.measurement},
            request_id=task.request_id,
        )
        failed = next(
            (
                response
                for response in (query, dictionary)
                if response.status != "succeeded"
            ),
            None,
        )
        evidence = _merge_evidence(
            query.evidence_refs,
            dictionary.evidence_refs,
        )
        if failed is not None:
            return self._failed_message(
                task,
                sender="data_quality_agent",
                recipient="report_agent",
                capability="data_quality_check",
                evidence=evidence,
                response=failed,
            )
        rows = list(query.data["rows"])
        timestamps = [
            datetime.fromisoformat(str(row["timestamp"])) for row in rows
        ]
        expected_minutes = _sampling_minutes(
            str(dictionary.data["fields"][0]["sampling_frequency"])
        )
        misaligned = sum(
            1
            for previous, current in zip(timestamps, timestamps[1:])
            if (current - previous).total_seconds() != expected_minutes * 60
        )
        payload = {
            "facts": {
                "row_count": query.data["summary"]["count"],
                "missing_value_count": sum(
                    1 for row in rows if row.get("value") is None
                ),
                "misaligned_intervals": misaligned,
                "unit": query.data["unit"],
                "sampling_frequency": dictionary.data["fields"][0][
                    "sampling_frequency"
                ],
            },
            "series": rows,
            "selected_service_id": selected_service["service_id"],
        }
        return self._successful_message(
            task,
            sender="data_quality_agent",
            recipient="timeseries_agent",
            capability="data_quality_check",
            evidence=evidence,
            payload=payload,
        )

    def _run_timeseries(
        self,
        task: AnalysisTask,
        data_quality: A2AMessage,
        selected_service: Mapping[str, object],
    ) -> A2AMessage:
        rows = list(data_quality.payload["series"])
        values = [
            float(row["value"])
            for row in rows
            if row.get("value") is not None
        ]
        execution = {
            "service_id": selected_service["service_id"],
            "algorithm": "drift_detection",
            "algorithm_version": selected_service["algorithms"][
                "drift_detection"
            ],
        }
        if len(values) < 2:
            return self._failed_message(
                task,
                sender="timeseries_agent",
                recipient="report_agent",
                capability="timeseries_analysis",
                evidence=data_quality.evidence_refs,
                response=ProtocolResponse(
                    request_id=task.request_id,
                    protocol="A2A",
                    service=str(selected_service["service_id"]),
                    status="degraded",
                    error={
                        "code": "INSUFFICIENT_DATA",
                        "message": "有效数值样本少于 2 条，无法进行时序分析",
                        "retryable": False,
                    },
                    metadata={"execution": execution},
                ),
                payload={
                    "facts": dict(data_quality.payload["facts"]),
                    "hypotheses": [],
                    "unknowns": [
                        "有效数值样本不足，不能计算漂移、突变或周期异常"
                    ],
                    "execution": execution,
                },
            )
        steps = [
            round(abs(current - previous), 6)
            for previous, current in zip(values, values[1:])
        ]
        maximum_step = max(steps, default=0.0)
        periodicity = _periodicity_assessment(values)
        facts = {
            "start_value": values[0],
            "end_value": values[-1],
            "change": round(values[-1] - values[0], 6),
            "maximum_step": maximum_step,
            "candidate_anomaly_count": int(maximum_step >= 1.0),
            **periodicity,
        }
        unknowns = (
            ["有效样本少于 8 点，尚不能评估周期异常"]
            if periodicity["periodicity_status"] == "insufficient_samples"
            else []
        )
        return self._successful_message(
            task,
            sender="timeseries_agent",
            recipient="equipment_diagnosis_agent",
            capability="timeseries_analysis",
            evidence=data_quality.evidence_refs,
            payload={
                "facts": facts,
                "execution": execution,
                "unknowns": unknowns,
            },
        )

    def _run_diagnosis(
        self,
        task: AnalysisTask,
        *,
        timeseries: A2AMessage,
        client: IndustrialMCPClient,
    ) -> A2AMessage:
        alarms = client.call(
            "lookup_alarm_event",
            {
                "alarm_code": task.alarm_code,
                "start_time": task.start_time,
                "end_time": task.end_time,
                "max_rows": 50,
            },
            request_id=task.request_id,
        )
        evidence = _merge_evidence(
            timeseries.evidence_refs,
            alarms.evidence_refs,
        )
        if alarms.status != "succeeded":
            return self._failed_message(
                task,
                sender="equipment_diagnosis_agent",
                recipient="report_agent",
                capability="equipment_diagnosis",
                evidence=evidence,
                response=alarms,
            )
        alarm_codes = [
            str(event["alarm_code"]) for event in alarms.data["events"]
        ]
        anomaly_count = int(
            timeseries.payload["facts"]["candidate_anomaly_count"]
        )
        hypotheses: list[str] = []
        if anomaly_count and alarm_codes:
            hypotheses.append(
                f"时序突变与 {alarm_codes[0]} 告警在同一窗口共现，"
                "可能相关；该相关性不能确认根因"
            )
        return self._successful_message(
            task,
            sender="equipment_diagnosis_agent",
            recipient="report_agent",
            capability="equipment_diagnosis",
            evidence=evidence,
            payload={
                "facts": {
                    "alarm_count": alarms.data["matched_count"],
                    "alarm_codes": alarm_codes,
                },
                "hypotheses": hypotheses,
                "unknowns": [
                    "尚未读取维修工单与现场点检记录",
                    "尚未由设备工程师确认告警与振动变化的因果关系",
                ],
            },
        )

    def _run_report(
        self,
        task: AnalysisTask,
        *,
        data_quality: A2AMessage,
        timeseries: A2AMessage,
        diagnosis: A2AMessage,
        selected_service: Mapping[str, object],
    ) -> A2AMessage:
        evidence = _merge_evidence(
            data_quality.evidence_refs,
            timeseries.evidence_refs,
            diagnosis.evidence_refs,
        )
        facts = {
            **dict(data_quality.payload["facts"]),
            **dict(timeseries.payload["facts"]),
            **dict(diagnosis.payload.get("facts", {})),
            "selected_service_id": selected_service["service_id"],
        }
        status = (
            "succeeded"
            if diagnosis.status == "succeeded"
            else "degraded"
        )
        error = None if status == "succeeded" else diagnosis.error
        message = A2AMessage(
            request_id=task.request_id,
            task_id=task.task_id,
            sender="report_agent",
            recipient="human_reviewer",
            capability="auditable_reporting",
            input_data_range=task.data_range(),
            evidence_refs=evidence,
            status=status,
            payload={
                "facts": facts,
                "hypotheses": list(
                    diagnosis.payload.get("hypotheses", [])
                ),
                "unknowns": [
                    *list(timeseries.payload.get("unknowns", [])),
                    *list(diagnosis.payload.get("unknowns", [])),
                ],
                "conclusion": (
                    "已形成只读教学分析报告；异常候选仅供排查，"
                    "最终根因与任何生产处置必须由领域负责人复核"
                ),
                "next_steps": [
                    "由设备工程师复核维修工单与现场点检记录",
                    "若需跨域取数或生产试验，先完成人工审批",
                ],
            },
            error=error,
        )
        self._record(task, message)
        return message

    def _degraded_report(
        self,
        task: AnalysisTask,
        failed_message: A2AMessage,
    ) -> A2AMessage:
        message = A2AMessage(
            request_id=task.request_id,
            task_id=task.task_id,
            sender="report_agent",
            recipient="human_reviewer",
            capability="auditable_reporting",
            input_data_range=task.data_range(),
            evidence_refs=failed_message.evidence_refs,
            status="degraded",
            payload={
                "facts": dict(
                    failed_message.payload.get("facts", {})
                ),
                "hypotheses": [],
                "unknowns": [
                    "上游数据不可用，未生成分析结论",
                    *list(
                        failed_message.payload.get("unknowns", [])
                    ),
                ],
                "conclusion": "数据不足，流程已降级停止",
                "next_steps": ["检查服务状态后使用同一 request_id 重试"],
            },
            error=failed_message.error,
        )
        self._record(task, message)
        return message

    def _successful_message(
        self,
        task: AnalysisTask,
        *,
        sender: str,
        recipient: str,
        capability: str,
        evidence: tuple[EvidenceRef, ...],
        payload: Mapping[str, object],
    ) -> A2AMessage:
        message = A2AMessage(
            request_id=task.request_id,
            task_id=task.task_id,
            sender=sender,
            recipient=recipient,
            capability=capability,
            input_data_range=task.data_range(),
            evidence_refs=evidence,
            status="succeeded",
            payload=payload,
        )
        self._record(task, message)
        return message

    def _failed_message(
        self,
        task: AnalysisTask,
        *,
        sender: str,
        recipient: str,
        capability: str,
        evidence: tuple[EvidenceRef, ...],
        response: ProtocolResponse,
        payload: Mapping[str, object] | None = None,
    ) -> A2AMessage:
        message = A2AMessage(
            request_id=task.request_id,
            task_id=task.task_id,
            sender=sender,
            recipient=recipient,
            capability=capability,
            input_data_range=task.data_range(),
            evidence_refs=evidence,
            status="degraded",
            payload=payload
            or {
                "facts": {},
                "hypotheses": [],
                "unknowns": ["上游工具未返回可验证数据"],
            },
            error=response.error
            or {
                "code": "UPSTREAM_FAILED",
                "message": "上游工具调用失败",
                "retryable": False,
            },
        )
        self._record(task, message)
        return message

    def _record(self, task: AnalysisTask, message: A2AMessage) -> None:
        self.audit_trail.append(
            request_id=task.request_id,
            protocol="A2A",
            service=message.sender,
            operation=message.capability,
            actor_id=message.sender,
            tenant_id=task.tenant_id,
            parameters={
                "task_id": task.task_id,
                "input_data_range": task.data_range().to_dict(),
                "evidence_ids": [
                    evidence.evidence_id
                    for evidence in message.evidence_refs
                ],
            },
            status=message.status,
            response=message.to_dict(),
            evidence_ids=tuple(
                evidence.evidence_id for evidence in message.evidence_refs
            ),
            error_code=(
                None if message.error is None else str(message.error["code"])
            ),
        )


def _merge_evidence(
    *groups: Iterable[EvidenceRef],
) -> tuple[EvidenceRef, ...]:
    unique: dict[str, EvidenceRef] = {}
    for group in groups:
        for evidence in group:
            unique[evidence.evidence_id] = evidence
    return tuple(unique.values())


def _sampling_minutes(value: str) -> int:
    if not value.endswith("min"):
        raise ValueError(f"不支持的采样频率：{value}")
    return int(value[:-3])


def _periodicity_assessment(values: list[float]) -> dict[str, object]:
    minimum_samples = 8
    if len(values) < minimum_samples:
        return {
            "periodicity_status": "insufficient_samples",
            "periodicity_lag": None,
            "periodicity_score": None,
        }
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    candidates: list[tuple[float, int]] = []
    maximum_lag = min(4, len(values) // 2)
    for lag in range(2, maximum_lag + 1):
        left = centered[:-lag]
        right = centered[lag:]
        denominator = sqrt(
            sum(value * value for value in left)
            * sum(value * value for value in right)
        )
        score = (
            0.0
            if denominator == 0
            else sum(a * b for a, b in zip(left, right)) / denominator
        )
        candidates.append((score, lag))
    score, lag = max(candidates)
    return {
        "periodicity_status": (
            "candidate" if score >= 0.8 else "no_candidate"
        ),
        "periodicity_lag": lag,
        "periodicity_score": round(score, 6),
    }
