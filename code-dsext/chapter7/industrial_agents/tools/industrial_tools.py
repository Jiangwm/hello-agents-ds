from __future__ import annotations

import csv
import hashlib
import json
import math
import stat
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Callable, Mapping

from ..exceptions import (
    ApprovalRequiredError,
    ToolExecutionError,
    ToolNotAllowedError,
    ToolValidationError,
)
from ..schemas import (
    ApprovalContext,
    AuditRecord,
    Evidence,
    IndustrialAgentConfig,
    RiskLevel,
    RunStatus,
    ToolResult,
)


CANONICAL_FIELDS = frozenset(
    {
        "timestamp",
        "equipment_id",
        "pressure_mpa",
        "temperature_c",
        "vibration_mm_s",
        "quality_score",
    }
)


@dataclass(frozen=True)
class ToolParameter:
    name: str
    type: type | tuple[type, ...]
    required: bool = True
    description: str = ""
    choices: tuple[object, ...] | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None

    def validate(self, value: object) -> object:
        if value is None:
            if self.required:
                raise ToolValidationError(f"缺少必填参数：{self.name}")
            return value
        if self.type is not object and not isinstance(value, self.type):
            expected = (
                ", ".join(item.__name__ for item in self.type)
                if isinstance(self.type, tuple)
                else self.type.__name__
            )
            raise ToolValidationError(f"参数 {self.name} 必须是 {expected}")
        if self.choices is not None and value not in self.choices:
            raise ToolValidationError(
                f"参数 {self.name} 必须是以下值之一：{', '.join(map(str, self.choices))}"
            )
        if self.minimum is not None or self.maximum is not None:
            if isinstance(value, bool):
                raise ToolValidationError(f"参数 {self.name} 不可为布尔值")
            if isinstance(value, (int, float)):
                comparable: float | int = value
            elif isinstance(value, (str, list, tuple, dict)):
                comparable = len(value)
            else:
                raise ToolValidationError(f"参数 {self.name} 不支持范围校验")
            if self.minimum is not None and comparable < self.minimum:
                raise ToolValidationError(
                    f"参数 {self.name} 不得小于 {self.minimum}"
                )
            if self.maximum is not None and comparable > self.maximum:
                raise ToolValidationError(
                    f"参数 {self.name} 不得大于 {self.maximum}"
                )
        return value


ToolHandler = Callable[[Mapping[str, object], IndustrialAgentConfig], ToolResult]


@dataclass(frozen=True)
class IndustrialTool:
    name: str
    description: str
    parameters: tuple[ToolParameter, ...]
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    risk_level: RiskLevel
    requires_approval: bool
    handler: ToolHandler = field(repr=False, compare=False)

    def validate_arguments(self, arguments: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            raise ToolValidationError("arguments 必须是映射")
        known = {parameter.name for parameter in self.parameters}
        unknown = sorted(set(arguments) - known)
        if unknown:
            raise ToolValidationError(f"不支持的参数：{', '.join(unknown)}")
        validated: dict[str, object] = {}
        for parameter in self.parameters:
            value = arguments.get(parameter.name)
            validated[parameter.name] = parameter.validate(value)
        return validated


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, IndustrialTool] = {}
        self._audit_records: list[AuditRecord] = []
        self._call_sequence = 0

    @property
    def audit_records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._audit_records)

    def register(self, tool: IndustrialTool) -> None:
        if not isinstance(tool, IndustrialTool):
            raise ToolValidationError("仅可注册 IndustrialTool")
        if tool.name in self._tools:
            raise ToolValidationError(f"工具已注册：{tool.name}")
        self._tools[tool.name] = tool

    def discover(self) -> tuple[IndustrialTool, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def model_tools(
        self,
        config: IndustrialAgentConfig,
    ) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                },
            }
            for tool in self.discover()
            if tool.name in config.tool_allowlist
        )

    def validate(
        self,
        name: str,
        arguments: Mapping[str, object],
        config: IndustrialAgentConfig,
        approval: ApprovalContext | None = None,
    ) -> dict[str, object]:
        if approval is not None and not isinstance(approval, ApprovalContext):
            raise ToolValidationError("approval 必须是 ApprovalContext")
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotAllowedError(f"未注册工具：{name}")
        allowlist = getattr(config, "tool_allowlist", frozenset())
        if name not in allowlist:
            raise ToolNotAllowedError(f"工具不在 allowlist 内：{name}")
        if tool.requires_approval and approval is None:
            raise ApprovalRequiredError(f"工具 {name} 需要人工审批")
        return tool.validate_arguments(arguments)

    def execute(
        self,
        name: str,
        arguments: Mapping[str, object],
        config: IndustrialAgentConfig,
        approval: ApprovalContext | None = None,
    ) -> ToolResult:
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        audit_approval = (
            approval
            if isinstance(approval, ApprovalContext)
            else None
        )
        try:
            validated = self.validate(name, arguments, config, approval)
            result = self._tools[name].handler(validated, config)
            if not isinstance(result, ToolResult):
                raise ToolExecutionError(f"工具 {name} 返回了无效结果")
            if approval is not None:
                result = replace(
                    result,
                    metadata={
                        **result.metadata,
                        "approval": approval.to_dict(),
                    },
                )
        except (
            ToolValidationError,
            ToolNotAllowedError,
            ToolExecutionError,
            ApprovalRequiredError,
        ) as error:
            self._append_audit(
                name,
                "failure",
                started_at,
                started,
                arguments,
                approval=audit_approval,
                error=error,
            )
            raise
        except Exception as error:
            wrapped = ToolExecutionError(f"工具 {name} 执行失败：{error}")
            self._append_audit(
                name,
                "failure",
                started_at,
                started,
                arguments,
                approval=audit_approval,
                error=wrapped,
            )
            raise wrapped from error
        self._append_audit(
            name,
            "success",
            started_at,
            started,
            arguments,
            evidence_ids=tuple(item.evidence_id for item in result.evidence),
            approval=audit_approval,
        )
        return result

    def _append_audit(
        self,
        tool_name: str,
        status: str,
        started_at: datetime,
        started: float,
        arguments: Mapping[str, object],
        evidence_ids: tuple[str, ...] = (),
        approval: ApprovalContext | None = None,
        error: Exception | None = None,
    ) -> None:
        payload = _json_ready(arguments)
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        self._call_sequence += 1
        input_summary = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(input_summary) > 1_000:
            input_summary = input_summary[:997] + "..."
        self._audit_records.append(
            AuditRecord(
                call_id=f"CALL-{self._call_sequence:04d}-{digest}",
                tool_name=tool_name,
                status=RunStatus.COMPLETED if status == "success" else RunStatus.FAILED,
                started_at=started_at,
                duration_ms=round((time.perf_counter() - started) * 1000),
                input_summary=input_summary,
                evidence_ids=evidence_ids,
                approval_id=approval.approval_id if approval else None,
                approver=approval.approver if approval else None,
                approved_at=approval.approved_at if approval else None,
                approval_reason=approval.reason if approval else None,
                error_type=type(error).__name__ if error else None,
                error_message=str(error) if error else None,
            )
        )


def _json_schema(parameters: tuple[ToolParameter, ...]) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for parameter in parameters:
        properties[parameter.name] = {
            "description": parameter.description,
            "type": _json_type(parameter.type),
        }
        if parameter.choices is not None:
            properties[parameter.name]["enum"] = list(parameter.choices)
        if parameter.minimum is not None:
            properties[parameter.name]["minimum"] = parameter.minimum
        if parameter.maximum is not None:
            properties[parameter.name]["maximum"] = parameter.maximum
        if parameter.required:
            required.append(parameter.name)
    return {"type": "object", "properties": properties, "required": required}


def _json_type(value_type: type | tuple[type, ...]) -> str:
    types = value_type if isinstance(value_type, tuple) else (value_type,)
    if any(item is str for item in types):
        return "string"
    if any(item in {int, float} for item in types):
        return "number"
    if any(item in {list, tuple} for item in types):
        return "array"
    return "object"


def _output_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["output", "evidence", "truncated", "metadata"],
    }


def _field_allowlist(config: IndustrialAgentConfig) -> frozenset[str]:
    configured = getattr(config, "field_allowlist", None)
    return CANONICAL_FIELDS if configured is None else frozenset(configured)


def _require_allowed_field(field_name: str, config: IndustrialAgentConfig) -> None:
    if field_name not in _field_allowlist(config):
        raise ToolNotAllowedError(f"字段不在 allowlist 内：{field_name}")


def _resolve_data_path(
    raw_path: object,
    config: IndustrialAgentConfig,
    allowed_suffixes: frozenset[str],
) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ToolValidationError("file_path 必须是非空相对路径")
    requested = Path(raw_path)
    if requested.is_absolute() or requested.drive or ".." in requested.parts:
        raise ToolNotAllowedError("拒绝绝对路径或越界路径")
    root = Path(config.data_root).resolve()
    candidate = (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ToolNotAllowedError("数据路径必须位于 config.data_root 内") from error
    if candidate.suffix.lower() not in allowed_suffixes:
        supported = ", ".join(sorted(allowed_suffixes))
        raise ToolNotAllowedError(f"不支持的数据后缀：{candidate.suffix}，仅允许 {supported}")
    if not candidate.is_file():
        raise ToolExecutionError(f"数据文件不存在：{requested.as_posix()}")
    return candidate


def _read_tabular(
    raw_path: object,
    config: IndustrialAgentConfig,
) -> tuple[list[dict[str, object]], tuple[str, ...], int, bool, Path]:
    path = _resolve_data_path(raw_path, config, frozenset({".csv", ".parquet"}))
    max_rows = config.max_input_rows
    if path.suffix.lower() == ".parquet":
        try:
            import pandas as pd
        except ImportError as error:
            raise ToolExecutionError("读取 Parquet 需要可选依赖 pandas") from error
        try:
            frame = pd.read_parquet(path)
        except Exception as error:
            raise ToolExecutionError(f"无法读取 Parquet：{path.name}") from error
        headers = tuple(str(item) for item in frame.columns)
        total_rows = len(frame)
        if not headers:
            raise ToolExecutionError(f"数据文件缺少表头：{path.name}")
        if total_rows == 0:
            raise ToolExecutionError(f"数据文件为空：{path.name}")
        truncated = total_rows > max_rows
        rows = frame.head(max_rows).to_dict(orient="records")
        return rows, headers, total_rows, truncated, path
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows: list[dict[str, object]] = []
            total_rows = 0
            for row in reader:
                total_rows += 1
                if len(rows) < max_rows:
                    rows.append(dict(row))
    except (OSError, csv.Error) as error:
        raise ToolExecutionError(f"无法读取 CSV：{path.name}") from error
    if not headers:
        raise ToolExecutionError(f"数据文件缺少表头：{path.name}")
    if total_rows == 0:
        raise ToolExecutionError(f"数据文件为空：{path.name}")
    return rows, headers, total_rows, total_rows > max_rows, path


def _require_header(field_name: str, headers: tuple[str, ...]) -> None:
    if field_name not in headers:
        raise ToolValidationError(f"数据缺少字段：{field_name}")


def _time_range(rows: list[dict[str, object]]) -> str:
    values = sorted(str(row["timestamp"]) for row in rows if row.get("timestamp"))
    return f"{values[0]} 至 {values[-1]}" if values else f"共 {len(rows)} 行"


def _evidence(
    tool_name: str,
    summary: str,
    data_range: str,
    details: dict[str, object],
) -> Evidence:
    canonical = {
        "tool_name": tool_name,
        "summary": summary,
        "data_range": data_range,
        "details": _json_ready(details),
    }
    evidence_id = "EV-" + hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return Evidence(
        evidence_id=evidence_id,
        source=f"local_tool:{tool_name}",
        summary=summary,
        data_range=data_range,
        details=details,
    )


def _dataset_profile(
    arguments: Mapping[str, object], config: IndustrialAgentConfig
) -> ToolResult:
    rows, headers, total_rows, input_truncated, path = _read_tabular(
        arguments["file_path"],
        config,
    )
    visible_fields = tuple(field for field in headers if field in _field_allowlist(config))
    numeric: dict[str, dict[str, float]] = {}
    field_types: dict[str, str] = {}
    missing_rates: dict[str, float] = {}
    for field_name in visible_fields:
        values: list[float] = []
        missing_count = 0
        for row in rows:
            value = row.get(field_name)
            if value is None or str(value).strip() == "":
                missing_count += 1
                continue
            try:
                values.append(float(str(value)))
            except (TypeError, ValueError):
                pass
        missing_rates[field_name] = round(missing_count / len(rows), 6)
        if field_name == "timestamp":
            field_types[field_name] = "datetime"
        elif len(values) + missing_count == len(rows):
            field_types[field_name] = "number"
        else:
            field_types[field_name] = "string"
        if values:
            numeric[field_name] = {
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "mean": round(fmean(values), 6),
            }
    data_range = _time_range(rows)
    details = {
        "source": path.name,
        "row_count": total_rows,
        "sampled_row_count": len(rows),
        "fields": visible_fields,
        "field_types": field_types,
        "missing_rates": missing_rates,
        "time_range": data_range,
        "numeric_summary": numeric,
        "input_truncated": input_truncated,
    }
    evidence = _evidence("dataset_profile", "已生成脱敏数据集概览。", data_range, details)
    return ToolResult(output=details, evidence=(evidence,), truncated=input_truncated, metadata={"source": path.name})


def _filtered_rows(
    arguments: Mapping[str, object], config: IndustrialAgentConfig
) -> tuple[list[dict[str, object]], tuple[str, ...], bool, Path]:
    rows, headers, _, truncated, path = _read_tabular(
        arguments["file_path"],
        config,
    )
    field_name = str(arguments["field"])
    _require_allowed_field(field_name, config)
    _require_header(field_name, headers)
    equipment_id = arguments.get("equipment_id")
    if equipment_id is not None:
        _require_allowed_field("equipment_id", config)
        _require_header("equipment_id", headers)
        rows = [row for row in rows if row.get("equipment_id") == equipment_id]
        if not rows:
            raise ToolExecutionError(f"未找到设备数据：{equipment_id}")
    return rows, headers, truncated, path


def _trend_analysis(
    arguments: Mapping[str, object], config: IndustrialAgentConfig
) -> ToolResult:
    rows, headers, input_truncated, path = _filtered_rows(arguments, config)
    field_name = str(arguments["field"])
    frequency = str(arguments.get("frequency") or "raw")
    rolling_window = int(arguments.get("rolling_window") or 1)
    group_by = arguments.get("group_by")
    if group_by is not None:
        group_by = str(group_by)
        _require_allowed_field(group_by, config)
        _require_header(group_by, headers)
    buckets: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        try:
            value = float(str(row[field_name]))
        except (TypeError, ValueError) as error:
            raise ToolExecutionError(f"字段 {field_name} 不是数值字段") from error
        bucket = _time_bucket(str(row.get("timestamp", "")), frequency)
        group_value = str(row.get(group_by, "all")) if group_by else "all"
        buckets.setdefault((group_value, bucket), []).append(value)
    grouped_series: dict[str, list[dict[str, object]]] = {}
    for (group_value, bucket), values in sorted(buckets.items()):
        grouped_series.setdefault(group_value, []).append(
            {"bucket": bucket, "group": group_value, "mean": round(fmean(values), 6), "sample_count": len(values)}
        )
    series: list[dict[str, object]] = []
    group_summaries: list[dict[str, object]] = []
    for group_value, points in grouped_series.items():
        for index, point in enumerate(points):
            window = points[max(0, index - rolling_window + 1) : index + 1]
            rolling_mean = fmean(float(item["mean"]) for item in window)
            series.append({**point, "rolling_mean": round(rolling_mean, 6)})
        group_values = [float(item["mean"]) for item in points]
        first_value, last_value = group_values[0], group_values[-1]
        group_slope = (last_value - first_value) / max(len(group_values) - 1, 1)
        group_summaries.append(
            {
                "group": group_value,
                "first_value": first_value,
                "last_value": last_value,
                "slope_per_sample": round(group_slope, 6),
                "direction": "up" if group_slope > 0 else "down" if group_slope < 0 else "flat",
            }
        )
    series.sort(key=lambda item: (str(item["group"]), str(item["bucket"])))
    values = [float(item["rolling_mean"]) for item in series]
    output_truncated = len(series) > config.max_result_rows
    group_summary_truncated = len(group_summaries) > config.max_result_rows
    output = {
        "source": path.name,
        "field": field_name,
        "frequency": frequency,
        "rolling_window": rolling_window,
        "group_by": group_by,
        "sample_count": len(rows),
        "series_count": len(series),
        "mean": round(fmean(values), 6),
        "group_summaries": group_summaries[: config.max_result_rows],
        "series": series[: config.max_result_rows],
    }
    if group_by is None:
        output.update(group_summaries[0])
    else:
        output["direction"] = "mixed"
    evidence = _evidence(
        "trend_analysis",
        f"{field_name} 的确定性趋势为 {output['direction']}。",
        _time_range(rows),
        {
            **output,
            "input_truncated": input_truncated,
            "output_truncated": output_truncated,
            "group_summary_truncated": group_summary_truncated,
        },
    )
    return ToolResult(output=output, evidence=(evidence,), truncated=input_truncated or output_truncated or group_summary_truncated, metadata={"source": path.name})


def _time_bucket(timestamp: str, frequency: str) -> str:
    if frequency == "raw":
        return timestamp
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ToolExecutionError(f"timestamp 不是 ISO-8601 时间：{timestamp}") from error
    if frequency == "hour":
        return moment.replace(minute=0, second=0, microsecond=0).isoformat()
    if frequency == "day":
        return moment.date().isoformat()
    raise ToolValidationError(f"不支持的 frequency：{frequency}")


def _anomaly_detection(
    arguments: Mapping[str, object], config: IndustrialAgentConfig
) -> ToolResult:
    rows, _headers, input_truncated, path = _filtered_rows(arguments, config)
    field_name = str(arguments["field"])
    threshold = float(arguments["z_threshold"])
    try:
        values = [float(str(row[field_name])) for row in rows]
    except (TypeError, ValueError) as error:
        raise ToolExecutionError(f"字段 {field_name} 不是数值字段") from error
    mean = fmean(values)
    deviation = math.sqrt(fmean((value - mean) ** 2 for value in values))
    candidates = []
    for row, value in zip(rows, values):
        z_score = 0.0 if deviation == 0 else abs(value - mean) / deviation
        if z_score >= threshold:
            candidates.append(
                {
                    "timestamp": row.get("timestamp"),
                    "equipment_id": row.get("equipment_id"),
                    "value": value,
                    "z_score": round(z_score, 4),
                }
            )
    output_truncated = len(candidates) > config.max_result_rows
    output = {
        "source": path.name,
        "field": field_name,
        "method": "population_z_score",
        "z_threshold": threshold,
        "mean": round(mean, 6),
        "standard_deviation": round(deviation, 6),
        "anomalies": candidates[: config.max_result_rows],
        "anomaly_count": len(candidates),
    }
    evidence = _evidence(
        "anomaly_detection",
        f"{field_name} 检出 {len(candidates)} 条可解释异常候选。",
        _time_range(rows),
        {**output, "input_truncated": input_truncated, "output_truncated": output_truncated},
    )
    return ToolResult(
        output=output,
        evidence=(evidence,),
        truncated=input_truncated or output_truncated,
        metadata={"source": path.name, "detection_parameters": {"z_threshold": threshold}},
    )


def _specification_lookup(
    arguments: Mapping[str, object], config: IndustrialAgentConfig
) -> ToolResult:
    query = str(arguments["query"]).strip().lower()
    if not query:
        raise ToolValidationError("query 不能为空")
    documents: list[tuple[Path, object]] = []
    for filename in ("process_specs.json", "data_dictionary.json", "cases.json"):
        path = _resolve_data_path(filename, config, frozenset({".json"}))
        try:
            documents.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError) as error:
            raise ToolExecutionError(f"无法读取本地知识文件：{path.name}") from error
    matches: list[dict[str, object]] = []
    input_truncated = False
    for path, payload in documents:
        records = _json_records(payload)
        if len(records) > config.max_input_rows:
            input_truncated = True
        for record in records[: config.max_input_rows]:
            rendered = json.dumps(record, ensure_ascii=False, sort_keys=True).lower()
            if query in rendered:
                matches.append({"source": path.name, "record": record})
    if not matches:
        raise ToolExecutionError(f"未找到本地规范、字典或案例匹配：{query}")
    output_truncated = len(matches) > config.max_result_rows
    output = {
        "query": query,
        "sources": tuple(path.name for path, _payload in documents),
        "match_count": len(matches),
        "matches": matches[: config.max_result_rows],
    }
    evidence = _evidence(
        "specification_lookup",
        f"本地规范、字典和案例检索到 {len(matches)} 条匹配。",
        "process_specs.json、data_dictionary.json、cases.json",
        {**output, "input_truncated": input_truncated, "output_truncated": output_truncated},
    )
    return ToolResult(output=output, evidence=(evidence,), truncated=input_truncated or output_truncated, metadata={"sources": output["sources"]})


def _json_records(payload: object) -> list[object]:
    if isinstance(payload, dict):
        records: list[object] = [payload]
        for value in payload.values():
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, (dict, str, int, float)))
        return records
    if isinstance(payload, list):
        return list(payload)
    return [payload]


def _evidence_export(
    arguments: Mapping[str, object], config: IndustrialAgentConfig
) -> ToolResult:
    package_name = str(arguments["package_name"])
    if Path(package_name).name != package_name or not package_name.replace("-", "").replace("_", "").isalnum():
        raise ToolValidationError("package_name 只能包含字母、数字、连字符和下划线")
    export_name = f"{package_name}.json"
    destination_dir = Path(config.export_directory).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / export_name
    if destination.exists():
        raise ToolExecutionError(f"证据包已存在，拒绝覆盖：{export_name}")
    package = {
        "package_name": package_name,
        "kind": "local_analysis_evidence_package",
        "notice": "仅用于本地教学分析包，不是生产写入。",
        "evidence": _json_ready(arguments["evidence"]),
    }
    try:
        destination.write_text(
            json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        destination.chmod(stat.S_IREAD)
    except OSError as error:
        raise ToolExecutionError(f"无法写入证据包：{export_name}") from error
    output = {"export_path": str(destination), "package_name": package_name, "read_only": True}
    evidence = _evidence("evidence_export", "已写入只读本地分析证据包。", "本地导出目录", output)
    return ToolResult(output=output, evidence=(evidence,), truncated=False, metadata={"source": destination.name})


def _json_ready(value: object) -> object:
    if isinstance(value, Evidence):
        return {
            "evidence_id": value.evidence_id,
            "source": value.source,
            "summary": value.summary,
            "data_range": value.data_range,
            "details": _json_ready(value.details),
        }
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def builtin_tools() -> tuple[IndustrialTool, ...]:
    common_file = ToolParameter("file_path", str, description="data_root 内的数据相对路径")
    field_name = ToolParameter("field", str, description="字段白名单中的数值字段")
    optional_equipment = ToolParameter("equipment_id", str, required=False, description="可选脱敏设备编号")
    return (
        IndustrialTool(
            "dataset_profile",
            "生成脱敏数据集概览。",
            (common_file,),
            _json_schema((common_file,)),
            _output_schema(),
            RiskLevel.LOW,
            False,
            _dataset_profile,
        ),
        IndustrialTool(
            "trend_analysis",
            "计算字段的确定性趋势。",
            (
                common_file,
                field_name,
                ToolParameter("frequency", str, required=False, description="重采样频率", choices=("raw", "hour", "day")),
                ToolParameter("rolling_window", int, required=False, description="滚动窗口", minimum=1, maximum=5000),
                ToolParameter("group_by", str, required=False, description="字段白名单中的分组字段"),
                optional_equipment,
            ),
            _json_schema(
                (
                    common_file,
                    field_name,
                    ToolParameter("frequency", str, required=False, description="重采样频率", choices=("raw", "hour", "day")),
                    ToolParameter("rolling_window", int, required=False, description="滚动窗口", minimum=1, maximum=5000),
                    ToolParameter("group_by", str, required=False, description="字段白名单中的分组字段"),
                    optional_equipment,
                )
            ),
            _output_schema(),
            RiskLevel.LOW,
            False,
            _trend_analysis,
        ),
        IndustrialTool(
            "anomaly_detection",
            "使用总体 Z 分数筛查异常候选。",
            (
                common_file,
                field_name,
                ToolParameter("z_threshold", (int, float), description="异常 Z 分数阈值", minimum=0.1, maximum=10.0),
                optional_equipment,
            ),
            _json_schema(
                (
                    common_file,
                    field_name,
                    ToolParameter("z_threshold", (int, float), description="异常 Z 分数阈值", minimum=0.1, maximum=10.0),
                    optional_equipment,
                )
            ),
            _output_schema(),
            RiskLevel.MEDIUM,
            False,
            _anomaly_detection,
        ),
        IndustrialTool(
            "specification_lookup",
            "跨本地规范、数据字典和案例检索。",
            (
                ToolParameter("query", str, description="本地知识检索词", minimum=1, maximum=120),
            ),
            _json_schema(
                (
                    ToolParameter("query", str, description="本地知识检索词", minimum=1, maximum=120),
                )
            ),
            _output_schema(),
            RiskLevel.LOW,
            False,
            _specification_lookup,
        ),
        IndustrialTool(
            "evidence_export",
            "在审批后导出只读本地分析证据包。",
            (
                ToolParameter("package_name", str, description="安全的导出包名", minimum=1, maximum=80),
                ToolParameter("evidence", dict, description="待封装的本地分析证据"),
            ),
            _json_schema(
                (
                    ToolParameter("package_name", str, description="安全的导出包名", minimum=1, maximum=80),
                    ToolParameter("evidence", dict, description="待封装的本地分析证据"),
                )
            ),
            _output_schema(),
            RiskLevel.HIGH,
            True,
            _evidence_export,
        ),
    )
