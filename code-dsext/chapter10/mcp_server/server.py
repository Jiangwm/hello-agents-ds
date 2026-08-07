from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Callable, Mapping

from shared_schemas import (
    AuditTrail,
    EvidenceRef,
    ProtocolResponse,
    redact_sensitive,
)


MAX_TIME_WINDOW_HOURS = 24
MAX_RESULT_ROWS = 50


def _read_only_schema(
    name: str,
    description: str,
    properties: dict[str, object],
    required: list[str],
    *,
    output_properties: dict[str, object] | None = None,
    max_window_hours: int | None = None,
) -> dict[str, object]:
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if max_window_hours is not None:
        input_schema["x-max-window-hours"] = max_window_hours
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": {
            "type": "object",
            "properties": output_properties or {},
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
        },
        "errors": [
            "UNAUTHENTICATED",
            "FORBIDDEN",
            "INVALID_ARGUMENT",
            "NOT_FOUND",
            "TOOL_NOT_FOUND",
            "WINDOW_EXCEEDED",
            "RATE_LIMITED",
        ],
    }


TOOL_SCHEMAS = (
    _read_only_schema(
        "list_equipment",
        "列出当前租户可访问的脱敏设备及测点。",
        {},
        [],
        output_properties={
            "equipment": {"type": "array"},
        },
    ),
    _read_only_schema(
        "query_timeseries",
        "按设备、测点和 UTC 时间窗读取脱敏时序并计算确定性摘要。",
        {
            "equipment_id": {"type": "string"},
            "measurement": {"type": "string"},
            "start_time": {"type": "string", "format": "date-time"},
            "end_time": {"type": "string", "format": "date-time"},
            "max_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RESULT_ROWS,
                "default": 20,
            },
        },
        ["equipment_id", "measurement", "start_time", "end_time"],
        output_properties={
            "unit": {
                "type": "string",
                "description": "由数据字典按测点返回",
            },
            "rows": {"type": "array", "maxItems": MAX_RESULT_ROWS},
            "summary": {"type": "object"},
        },
        max_window_hours=MAX_TIME_WINDOW_HOURS,
    ),
    _read_only_schema(
        "get_batch_quality",
        "读取当前租户的批次质量确定性摘要。",
        {"batch_id": {"type": "string"}},
        ["batch_id"],
        output_properties={
            "batch": {"type": "object"},
        },
    ),
    _read_only_schema(
        "lookup_alarm_event",
        "按告警码和 UTC 时间范围读取脱敏事件。",
        {
            "alarm_code": {"type": "string"},
            "start_time": {"type": "string", "format": "date-time"},
            "end_time": {"type": "string", "format": "date-time"},
            "max_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RESULT_ROWS,
                "default": 20,
            },
        },
        ["alarm_code", "start_time", "end_time"],
        output_properties={"events": {"type": "array"}},
        max_window_hours=MAX_TIME_WINDOW_HOURS,
    ),
    _read_only_schema(
        "get_data_dictionary",
        "读取测点类型、单位和采样频率。",
        {"field": {"type": "string"}},
        [],
        output_properties={"fields": {"type": "array"}},
    ),
)


@dataclass(frozen=True)
class Identity:
    actor_id: str
    tenant_id: str
    scopes: frozenset[str]


DEFAULT_IDENTITIES = {
    "demo-token-tenant-a": Identity(
        actor_id="analyst-a",
        tenant_id="tenant-a",
        scopes=frozenset(
            {
                "equipment:read",
                "timeseries:read",
                "quality:read",
                "alarm:read",
                "dictionary:read",
            }
        ),
    ),
    "demo-token-tenant-b": Identity(
        actor_id="analyst-b",
        tenant_id="tenant-b",
        scopes=frozenset(
            {
                "equipment:read",
                "timeseries:read",
                "quality:read",
                "alarm:read",
                "dictionary:read",
            }
        ),
    ),
}

TOOL_SCOPES = {
    "list_equipment": "equipment:read",
    "query_timeseries": "timeseries:read",
    "get_batch_quality": "quality:read",
    "lookup_alarm_event": "alarm:read",
    "get_data_dictionary": "dictionary:read",
}


class ProtocolFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class IndustrialDataMCPServer:
    def __init__(
        self,
        data_root: Path,
        audit_trail: AuditTrail,
        identities: Mapping[str, Identity] | None = None,
        rate_limit_per_minute: int = 60,
    ) -> None:
        self.data_root = data_root.resolve()
        self.audit_trail = audit_trail
        self.identities = dict(identities or DEFAULT_IDENTITIES)
        self.rate_limit_per_minute = rate_limit_per_minute
        self._rate_events: dict[tuple[str, str], list[float]] = {}
        self._handlers: dict[
            str,
            Callable[
                [Mapping[str, object], Identity],
                tuple[dict[str, object], EvidenceRef],
            ],
        ] = {
            "list_equipment": self._list_equipment,
            "query_timeseries": self._query_timeseries,
            "get_batch_quality": self._get_batch_quality,
            "lookup_alarm_event": self._lookup_alarm_event,
            "get_data_dictionary": self._get_data_dictionary,
        }
        self._schemas = {str(item["name"]): item for item in TOOL_SCHEMAS}

    def list_tools(self) -> list[dict[str, object]]:
        return [deepcopy(schema) for schema in TOOL_SCHEMAS]

    def authenticate(self, access_token: str) -> Identity | None:
        return self.identities.get(access_token)

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        access_token: str,
        request_id: str,
    ) -> ProtocolResponse:
        identity = self.authenticate(access_token)
        if identity is None:
            return self._failure_response(
                tool_name=tool_name,
                arguments=arguments,
                request_id=request_id,
                identity=None,
                failure=ProtocolFailure(
                    "UNAUTHENTICATED",
                    "访问令牌无效",
                ),
            )
        try:
            if tool_name not in self._handlers:
                raise ProtocolFailure(
                    "TOOL_NOT_FOUND",
                    f"工具未注册或不允许调用：{tool_name}",
                )
            required_scope = TOOL_SCOPES[tool_name]
            if required_scope not in identity.scopes:
                raise ProtocolFailure(
                    "FORBIDDEN",
                    f"缺少工具权限：{required_scope}",
                )
            self._check_rate_limit(identity, tool_name)
            self._validate_arguments(tool_name, arguments)
            data, evidence = self._handlers[tool_name](arguments, identity)
            response = ProtocolResponse(
                request_id=request_id,
                protocol="MCP",
                service="industrial-data-mcp",
                status="succeeded",
                data=redact_sensitive(data),
                evidence_refs=(evidence,),
            )
            self.audit_trail.append(
                request_id=request_id,
                protocol="MCP",
                service="industrial-data-mcp",
                operation=tool_name,
                actor_id=identity.actor_id,
                tenant_id=identity.tenant_id,
                parameters=arguments,
                status=response.status,
                response=response.to_dict(),
                evidence_ids=(evidence.evidence_id,),
            )
            return response
        except ProtocolFailure as failure:
            return self._failure_response(
                tool_name=tool_name,
                arguments=arguments,
                request_id=request_id,
                identity=identity,
                failure=failure,
            )

    def _list_equipment(
        self,
        arguments: Mapping[str, object],
        identity: Identity,
    ) -> tuple[dict[str, object], EvidenceRef]:
        del arguments
        path = self.data_root / "equipment.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        visible = [
            {key: value for key, value in item.items() if key != "tenant_id"}
            for item in items
            if item["tenant_id"] == identity.tenant_id
        ]
        visible.sort(key=lambda item: str(item["equipment_id"]))
        data = {"equipment": visible}
        return data, self._evidence("list_equipment", path, "全部可见设备", data)

    def _query_timeseries(
        self,
        arguments: Mapping[str, object],
        identity: Identity,
    ) -> tuple[dict[str, object], EvidenceRef]:
        equipment_id = str(arguments["equipment_id"])
        measurement = str(arguments["measurement"])
        start, end = self._parse_window(arguments)
        max_rows = int(arguments.get("max_rows", 20))
        dictionary = json.loads(
            (self.data_root / "data_dictionary.json").read_text(encoding="utf-8")
        )
        if measurement not in dictionary:
            raise ProtocolFailure(
                "NOT_FOUND",
                f"数据字典中不存在测点：{measurement}",
            )
        equipment = self._visible_equipment(identity)
        available = {
            str(item["equipment_id"]): {
                str(measurement_item["name"])
                for measurement_item in item["measurements"]
            }
            for item in equipment
        }
        if equipment_id not in available or measurement not in available[equipment_id]:
            raise ProtocolFailure(
                "NOT_FOUND",
                f"设备或测点不存在，或当前租户无权访问：{equipment_id}/{measurement}",
            )
        unit = dictionary[measurement]["unit"]
        path = self.data_root / "timeseries.csv"
        values: list[tuple[str, float | None]] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamp = datetime.fromisoformat(row["timestamp"])
                raw_value = row.get(measurement, "")
                if (
                    row["tenant_id"] == identity.tenant_id
                    and row["equipment_id"] == equipment_id
                    and start <= timestamp <= end
                ):
                    value = (
                        None
                        if raw_value in (None, "")
                        else float(raw_value)
                    )
                    values.append((row["timestamp"], value))
        if not values:
            raise ProtocolFailure(
                "NOT_FOUND",
                "指定时间窗没有可访问的时序数据",
            )
        sampled = values[:max_rows]
        numeric_values = [
            value for _, value in values if value is not None
        ]
        rows = [
            {"timestamp": timestamp, "value": value}
            for timestamp, value in sampled
        ]
        data = {
            "equipment_id": equipment_id,
            "measurement": measurement,
            "unit": unit,
            "rows": rows,
            "summary": {
                "count": len(values),
                "valid_count": len(numeric_values),
                "missing_count": len(values) - len(numeric_values),
                "returned_rows": len(rows),
                "truncated": len(values) > len(rows),
                "minimum": (
                    None if not numeric_values else min(numeric_values)
                ),
                "maximum": (
                    None if not numeric_values else max(numeric_values)
                ),
                "average": (
                    None
                    if not numeric_values
                    else round(sum(numeric_values) / len(numeric_values), 6)
                ),
            },
        }
        data_range = f"{arguments['start_time']} 至 {arguments['end_time']}"
        return data, self._evidence("query_timeseries", path, data_range, data)

    def _get_batch_quality(
        self,
        arguments: Mapping[str, object],
        identity: Identity,
    ) -> tuple[dict[str, object], EvidenceRef]:
        batch_id = str(arguments["batch_id"])
        path = self.data_root / "batch_quality.csv"
        batch: dict[str, object] | None = None
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if (
                    row["tenant_id"] == identity.tenant_id
                    and row["batch_id"] == batch_id
                ):
                    produced = int(row["produced_qty"])
                    qualified = int(row["qualified_qty"])
                    batch = {
                        key: value
                        for key, value in row.items()
                        if key != "tenant_id"
                    }
                    batch["produced_qty"] = produced
                    batch["qualified_qty"] = qualified
                    batch["first_pass_rate"] = round(qualified / produced, 6)
                    break
        if batch is None:
            raise ProtocolFailure(
                "NOT_FOUND",
                f"批次不存在或无权访问：{batch_id}",
            )
        data = {"batch": batch}
        return data, self._evidence(
            "get_batch_quality",
            path,
            str(batch["inspection_time"]),
            data,
        )

    def _lookup_alarm_event(
        self,
        arguments: Mapping[str, object],
        identity: Identity,
    ) -> tuple[dict[str, object], EvidenceRef]:
        alarm_code = str(arguments["alarm_code"])
        start, end = self._parse_window(arguments)
        max_rows = int(arguments.get("max_rows", 20))
        path = self.data_root / "alarms.csv"
        events: list[dict[str, object]] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                event_time = datetime.fromisoformat(row["event_time"])
                if (
                    row["tenant_id"] == identity.tenant_id
                    and row["alarm_code"] == alarm_code
                    and start <= event_time <= end
                ):
                    events.append(
                        {
                            key: value
                            for key, value in row.items()
                            if key != "tenant_id"
                        }
                    )
        events.sort(key=lambda item: str(item["event_time"]))
        data = {
            "alarm_code": alarm_code,
            "events": events[:max_rows],
            "matched_count": len(events),
            "truncated": len(events) > max_rows,
        }
        data_range = f"{arguments['start_time']} 至 {arguments['end_time']}"
        return data, self._evidence("lookup_alarm_event", path, data_range, data)

    def _get_data_dictionary(
        self,
        arguments: Mapping[str, object],
        identity: Identity,
    ) -> tuple[dict[str, object], EvidenceRef]:
        del identity
        path = self.data_root / "data_dictionary.json"
        dictionary = json.loads(path.read_text(encoding="utf-8"))
        field = arguments.get("field")
        if field is not None:
            field_name = str(field)
            if field_name not in dictionary:
                raise ProtocolFailure(
                    "NOT_FOUND",
                    f"数据字典中不存在字段：{field_name}",
                )
            selected = {field_name: dictionary[field_name]}
        else:
            selected = dictionary
        fields = [
            {"name": name, **metadata}
            for name, metadata in sorted(selected.items())
        ]
        data = {"fields": fields}
        return data, self._evidence(
            "get_data_dictionary",
            path,
            "字典版本：2026-07-29-v1",
            data,
        )

    def _visible_equipment(self, identity: Identity) -> list[dict[str, object]]:
        path = self.data_root / "equipment.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        return [
            item
            for item in items
            if item["tenant_id"] == identity.tenant_id
        ]

    def _parse_window(
        self,
        arguments: Mapping[str, object],
    ) -> tuple[datetime, datetime]:
        try:
            start = datetime.fromisoformat(str(arguments["start_time"]))
            end = datetime.fromisoformat(str(arguments["end_time"]))
        except ValueError as error:
            raise ProtocolFailure(
                "INVALID_ARGUMENT",
                "时间必须是带时区的 ISO 8601 格式",
            ) from error
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ProtocolFailure(
                "INVALID_ARGUMENT",
                "时间必须包含时区",
            )
        if start >= end:
            raise ProtocolFailure(
                "INVALID_ARGUMENT",
                "start_time 必须早于 end_time",
            )
        if (end - start).total_seconds() > MAX_TIME_WINDOW_HOURS * 3600:
            raise ProtocolFailure(
                "WINDOW_EXCEEDED",
                f"查询时间窗不能超过 {MAX_TIME_WINDOW_HOURS} 小时",
            )
        return start, end

    def _validate_arguments(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> None:
        schema = self._schemas[tool_name]["inputSchema"]
        properties = schema["properties"]
        required = schema["required"]
        missing = [name for name in required if name not in arguments]
        if missing:
            raise ProtocolFailure(
                "INVALID_ARGUMENT",
                "缺少必填参数：" + "、".join(missing),
            )
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            raise ProtocolFailure(
                "INVALID_ARGUMENT",
                "包含未声明参数：" + "、".join(unexpected),
            )
        for name, value in arguments.items():
            property_schema = properties[name]
            expected_type = property_schema.get("type")
            if expected_type == "string" and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ProtocolFailure(
                    "INVALID_ARGUMENT",
                    f"{name} 必须是非空字符串",
                )
            if expected_type == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ProtocolFailure(
                        "INVALID_ARGUMENT",
                        f"{name} 必须是整数",
                    )
                minimum = int(property_schema.get("minimum", value))
                maximum = int(property_schema.get("maximum", value))
                if not minimum <= value <= maximum:
                    raise ProtocolFailure(
                        "INVALID_ARGUMENT",
                        f"{name} 必须在 {minimum} 到 {maximum} 之间",
                    )

    def _check_rate_limit(self, identity: Identity, tool_name: str) -> None:
        now = time.monotonic()
        key = (identity.actor_id, tool_name)
        active = [
            timestamp
            for timestamp in self._rate_events.get(key, [])
            if now - timestamp < 60
        ]
        if len(active) >= self.rate_limit_per_minute:
            raise ProtocolFailure(
                "RATE_LIMITED",
                "一分钟内的工具调用次数已达到上限",
                retryable=True,
            )
        active.append(now)
        self._rate_events[key] = active

    def _failure_response(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, object],
        request_id: str,
        identity: Identity | None,
        failure: ProtocolFailure,
    ) -> ProtocolResponse:
        error = {
            "code": failure.code,
            "message": failure.message,
            "retryable": failure.retryable,
        }
        response = ProtocolResponse(
            request_id=request_id,
            protocol="MCP",
            service="industrial-data-mcp",
            status="failed",
            error=error,
        )
        self.audit_trail.append(
            request_id=request_id,
            protocol="MCP",
            service="industrial-data-mcp",
            operation=tool_name,
            actor_id="anonymous" if identity is None else identity.actor_id,
            tenant_id="unknown" if identity is None else identity.tenant_id,
            parameters=arguments,
            status=response.status,
            response=response.to_dict(),
            error_code=failure.code,
        )
        return response

    @staticmethod
    def _evidence(
        operation: str,
        path: Path,
        data_range: str,
        data: Mapping[str, object],
    ) -> EvidenceRef:
        file_checksum = sha256(path.read_bytes()).hexdigest()
        payload = json.dumps(
            {
                "operation": operation,
                "source_checksum": file_checksum,
                "data": data,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_id = f"EV-{sha256(payload.encode('utf-8')).hexdigest()[:16].upper()}"
        return EvidenceRef(
            evidence_id=evidence_id,
            source=path.name,
            data_range=data_range,
            checksum=file_checksum,
        )
