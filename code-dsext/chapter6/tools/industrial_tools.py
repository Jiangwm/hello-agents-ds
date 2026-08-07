from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Callable, Mapping

from schemas.models import Evidence, ToolExecution, validate_evidence_payload


@dataclass(frozen=True)
class ToolPayload:
    status: str
    claim: str
    query: str
    data_range: str
    source_files: tuple[str, ...]
    data: Mapping[str, object]
    error: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source_files = tuple(self.source_files)
        limitations = tuple(self.limitations)
        validate_evidence_payload(
            status=self.status,
            query=self.query,
            data_range=self.data_range,
            source_files=source_files,
            data=self.data,
            error=self.error,
        )
        object.__setattr__(self, "source_files", source_files)
        object.__setattr__(self, "limitations", limitations)

    @property
    def metrics(self) -> Mapping[str, object]:
        return self.data


class ReadOnlyQualityTools:
    TOOL_PREFIXES = {
        "analyze_quality_shift": "EV-DATA",
        "analyze_process_shift": "EV-PROC",
        "lookup_process_spec": "EV-SPEC",
        "inspect_equipment_context": "EV-EQUIP",
    }

    def __init__(self, data_dir: Path, max_calls: int = 12) -> None:
        resolved_data_dir = data_dir.resolve()
        if not resolved_data_dir.is_dir():
            raise ValueError(f"数据目录不存在：{resolved_data_dir}")
        if not 1 <= max_calls <= 50:
            raise ValueError("max_calls 必须在 1 到 50 之间")
        self.data_dir = resolved_data_dir
        self.max_calls = max_calls
        self.tool_call_count = 0
        self._cache: dict[str, Evidence] = {}
        self._prefix_counts: dict[str, int] = {}
        self._guard_count = 0
        self._lock = threading.Lock()
        self._tools: dict[str, Callable[..., ToolPayload]] = {
            "analyze_quality_shift": self._analyze_quality_shift,
            "analyze_process_shift": self._analyze_process_shift,
            "lookup_process_spec": self._lookup_process_spec,
            "inspect_equipment_context": self._inspect_equipment_context,
        }

    def execute(
        self,
        tool_name: str,
        source_agent: str,
        **tool_input: object,
    ) -> ToolExecution:
        if tool_name not in self._tools:
            with self._lock:
                evidence_id = self._next_guard_evidence_id()
            return ToolExecution(
                Evidence(
                    evidence_id=evidence_id,
                    source_agent=source_agent,
                    tool_name=tool_name,
                    status="blocked",
                    claim=f"只读工具箱拒绝未注册操作：{tool_name}。",
                    query=repr(tool_input),
                    data_range="未取得数据",
                    source_files=(),
                    data={"allowed_tools": tuple(sorted(self._tools))},
                    limitations=("系统没有 PLC、MES、DCS 或工艺参数写入工具。",),
                ),
                False,
            )

        cache_key = json.dumps(
            [tool_name, sorted(tool_input.items())],
            ensure_ascii=False,
            sort_keys=True,
            default=list,
        )
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return ToolExecution(cached, False)
            if self.tool_call_count >= self.max_calls:
                guard_evidence_id = self._next_guard_evidence_id()
                return ToolExecution(
                    Evidence(
                        evidence_id=guard_evidence_id,
                        source_agent=source_agent,
                        tool_name=tool_name,
                        status="blocked",
                        claim="只读工具调用已达到有界执行上限。",
                        query=repr(tool_input),
                        data_range="未取得数据",
                        source_files=(),
                        data={"max_tool_calls": self.max_calls},
                        limitations=("需由人工负责人决定是否扩大工具调用预算。",),
                    ),
                    False,
                )
            self.tool_call_count += 1
            evidence_id = self._next_evidence_id(tool_name)

        try:
            payload = self._tools[tool_name](**tool_input)
        except (csv.Error, LookupError, OSError, TypeError, ValueError) as error:
            payload = ToolPayload(
                status="error",
                claim=f"{tool_name} 调用失败：{error}",
                query=repr(tool_input),
                data_range="未取得数据",
                source_files=(),
                data={},
                error=str(error),
                limitations=("失败调用不能用于支持候选根因。",),
            )

        evidence = Evidence(
            evidence_id=evidence_id,
            source_agent=source_agent,
            tool_name=tool_name,
            status=payload.status,
            claim=payload.claim,
            query=payload.query,
            data_range=payload.data_range,
            source_files=payload.source_files,
            data=payload.data,
            error=payload.error,
            limitations=payload.limitations,
        )
        with self._lock:
            self._cache[cache_key] = evidence
        return ToolExecution(evidence, True)

    def _next_evidence_id(self, tool_name: str) -> str:
        prefix = self.TOOL_PREFIXES[tool_name]
        count = self._prefix_counts.get(prefix, 0) + 1
        self._prefix_counts[prefix] = count
        return f"{prefix}-{count:03d}"

    def _next_guard_evidence_id(self) -> str:
        self._guard_count += 1
        return f"EV-GUARD-{self._guard_count:03d}"

    def _read_rows(self, filename: str) -> list[dict[str, str]]:
        path = (self.data_dir / filename).resolve()
        if path.parent != self.data_dir:
            raise ValueError("数据文件必须位于固定只读目录")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _selected_rows(
        rows: list[dict[str, str]],
        field: str,
        values: tuple[str, ...],
    ) -> list[dict[str, str]]:
        value_set = set(values)
        selected = [row for row in rows if row.get(field) in value_set]
        found = {row.get(field, "") for row in selected}
        missing = sorted(value_set - found)
        if missing:
            raise LookupError(f"缺少 {field}：{', '.join(missing)}")
        return selected

    @staticmethod
    def _single_value(rows: list[dict[str, str]], field: str) -> str:
        values = {row[field] for row in rows}
        if len(values) != 1:
            raise ValueError(f"{field} 必须保持一致，实际为 {sorted(values)}")
        return next(iter(values))

    @staticmethod
    def _time_range(rows: list[dict[str, str]], field: str) -> tuple[str, str]:
        values = sorted(row[field] for row in rows)
        return values[0], values[-1]

    def _analyze_quality_shift(
        self,
        normal_batch_ids: tuple[str, ...],
        abnormal_batch_ids: tuple[str, ...],
    ) -> ToolPayload:
        quality_rows = self._read_rows("quality_results.csv")
        normal_rows = self._selected_rows(
            quality_rows,
            "batch_id",
            normal_batch_ids,
        )
        abnormal_rows = self._selected_rows(
            quality_rows,
            "batch_id",
            abnormal_batch_ids,
        )
        all_rows = normal_rows + abnormal_rows
        product_id = self._single_value(all_rows, "product_id")
        line_id = self._single_value(all_rows, "line_id")
        normal_total = sum(int(row["inspected_qty"]) for row in normal_rows)
        abnormal_total = sum(int(row["inspected_qty"]) for row in abnormal_rows)
        normal_pass_rate = (
            sum(int(row["pass_qty"]) for row in normal_rows) / normal_total
        )
        abnormal_pass_rate = (
            sum(int(row["pass_qty"]) for row in abnormal_rows) / abnormal_total
        )
        normal_mean = fmean(float(row["dimension_mean_mm"]) for row in normal_rows)
        abnormal_mean = fmean(
            float(row["dimension_mean_mm"]) for row in abnormal_rows
        )
        specs = self._read_rows("process_specs.csv")
        dimension_spec = next(
            (
                row
                for row in specs
                if row["product_id"] == product_id
                and row["parameter"] == "dimension_mean_mm"
            ),
            None,
        )
        out_of_spec_batches = None
        if dimension_spec is not None:
            lower = float(dimension_spec["lower_limit"])
            upper = float(dimension_spec["upper_limit"])
            out_of_spec_batches = sum(
                not lower <= float(row["dimension_mean_mm"]) <= upper
                for row in abnormal_rows
            )
        status = (
            "success"
            if (
                len(normal_rows) >= 2
                and len(abnormal_rows) >= 2
                and dimension_spec is not None
            )
            else "insufficient"
        )
        pass_rate_change_pp = (abnormal_pass_rate - normal_pass_rate) * 100
        dimension_shift = abnormal_mean - normal_mean
        window_start = min(row["window_start"] for row in all_rows)
        window_end = max(row["window_end"] for row in all_rows)
        limitations = (
            "质量差异描述的是异常结果，不能单独证明工艺或设备因果。",
        )
        if status == "insufficient":
            if len(normal_rows) < 2 or len(abnormal_rows) < 2:
                limitations += ("每组至少需要两个可比批次才能形成稳定对照。",)
            if dimension_spec is None:
                limitations += ("缺少尺寸规范，尺寸是否越限为 unknown。",)
        return ToolPayload(
            status=status,
            claim=(
                f"异常组一次合格率较正常组变化 {pass_rate_change_pp:.1f} 个百分点，"
                f"尺寸均值上移 {dimension_shift:.4f} mm。"
            ),
            query=(
                f"product={product_id}; line={line_id}; "
                f"normal={','.join(normal_batch_ids)}; "
                f"abnormal={','.join(abnormal_batch_ids)}"
            ),
            data_range=f"{window_start} 至 {window_end}",
            source_files=("quality_results.csv", "process_specs.csv"),
            data={
                "product_id": product_id,
                "line_id": line_id,
                "normal_pass_rate": round(normal_pass_rate, 4),
                "abnormal_pass_rate": round(abnormal_pass_rate, 4),
                "pass_rate_change_pp": round(pass_rate_change_pp, 2),
                "normal_dimension_mean_mm": round(normal_mean, 4),
                "abnormal_dimension_mean_mm": round(abnormal_mean, 4),
                "dimension_shift_mm": round(dimension_shift, 4),
                "abnormal_out_of_spec_batches": out_of_spec_batches,
                "dimension_spec_status": (
                    "available" if dimension_spec is not None else "unknown"
                ),
                "normal_batch_count": len(normal_rows),
                "abnormal_batch_count": len(abnormal_rows),
                "temporal_alignment": True,
            },
            limitations=limitations,
        )

    def _analyze_process_shift(
        self,
        normal_batch_ids: tuple[str, ...],
        abnormal_batch_ids: tuple[str, ...],
    ) -> ToolPayload:
        process_rows = self._read_rows("process_timeseries.csv")
        normal_rows = self._selected_rows(
            process_rows,
            "batch_id",
            normal_batch_ids,
        )
        abnormal_rows = self._selected_rows(
            process_rows,
            "batch_id",
            abnormal_batch_ids,
        )
        quality_rows = self._read_rows("quality_results.csv")
        selected_quality = self._selected_rows(
            quality_rows,
            "batch_id",
            normal_batch_ids + abnormal_batch_ids,
        )
        product_id = self._single_value(selected_quality, "product_id")
        specs = {
            row["parameter"]: row
            for row in self._read_rows("process_specs.csv")
            if row["product_id"] == product_id
        }
        parameters = (
            "spindle_temperature_c",
            "feed_rate_mm_s",
            "clamp_pressure_mpa",
        )
        shifts: list[dict[str, object]] = []
        for parameter in parameters:
            normal_mean = fmean(float(row[parameter]) for row in normal_rows)
            abnormal_mean = fmean(float(row[parameter]) for row in abnormal_rows)
            delta = abnormal_mean - normal_mean
            spec = specs.get(parameter)
            if spec is None:
                reference_span = max(abs(normal_mean), 1.0)
                out_of_spec_samples = None
                unit = "unknown"
            else:
                lower = float(spec["lower_limit"])
                upper = float(spec["upper_limit"])
                reference_span = upper - lower
                out_of_spec_samples = sum(
                    not lower <= float(row[parameter]) <= upper
                    for row in abnormal_rows
                )
                unit = spec["unit"]
            shifts.append(
                {
                    "parameter": parameter,
                    "normal_mean": round(normal_mean, 4),
                    "abnormal_mean": round(abnormal_mean, 4),
                    "delta": round(delta, 4),
                    "normalized_shift": round(
                        abs(delta) / max(reference_span, 0.000001),
                        4,
                    ),
                    "out_of_spec_samples": out_of_spec_samples,
                    "unit": unit,
                }
            )
        shifts.sort(
            key=lambda item: float(item["normalized_shift"]),
            reverse=True,
        )
        enough_samples = len(normal_rows) >= 6 and len(abnormal_rows) >= 6
        complete_specs = all(parameter in specs for parameter in parameters)
        status = "success" if enough_samples and complete_specs else "insufficient"
        top = shifts[0]
        start, end = self._time_range(
            normal_rows + abnormal_rows,
            "timestamp",
        )
        limitations = (
            "参数漂移排序是确定性筛查结果，不是统计显著性或因果检验。",
        )
        if not enough_samples:
            limitations += ("任一对照组少于 6 条工艺采样。",)
        if not complete_specs:
            limitations += ("缺少部分产品工艺参数规范。",)
        return ToolPayload(
            status=status,
            claim=(
                f"{top['parameter']} 的标准化均值漂移最大，"
                f"正常组 {top['normal_mean']}，异常组 {top['abnormal_mean']}，"
                f"差值 {top['delta']} {top['unit']}。"
            ),
            query=(
                f"product={product_id}; "
                f"normal={','.join(normal_batch_ids)}; "
                f"abnormal={','.join(abnormal_batch_ids)}; "
                f"parameters={','.join(parameters)}"
            ),
            data_range=f"{start} 至 {end}",
            source_files=("process_timeseries.csv", "process_specs.csv"),
            data={
                "product_id": product_id,
                "normal_samples": len(normal_rows),
                "abnormal_samples": len(abnormal_rows),
                "top_parameter": top["parameter"],
                "shifts": tuple(shifts),
                "temporal_alignment": True,
            },
            limitations=limitations,
        )

    def _lookup_process_spec(
        self,
        product_id: str,
        parameter: str,
    ) -> ToolPayload:
        row = next(
            (
                item
                for item in self._read_rows("process_specs.csv")
                if item["product_id"] == product_id
                and item["parameter"] == parameter
            ),
            None,
        )
        if row is None:
            return ToolPayload(
                status="insufficient",
                claim=f"未找到 {product_id}/{parameter} 的适用教学规范。",
                query=f"product={product_id}; parameter={parameter}",
                data_range="未取得适用规范",
                source_files=("process_specs.csv",),
                data={
                    "product_id": product_id,
                    "parameter": parameter,
                    "temporal_alignment": False,
                },
                limitations=("缺少规范时不能判断参数是否越限。",),
            )
        return ToolPayload(
            status="success",
            claim=(
                f"{parameter} 适用范围为 {row['lower_limit']} 至 "
                f"{row['upper_limit']} {row['unit']}。"
            ),
            query=f"product={product_id}; parameter={parameter}",
            data_range=(
                f"{row['effective_from']} 起生效；revision={row['revision']}"
            ),
            source_files=("process_specs.csv",),
            data={
                "product_id": product_id,
                "parameter": parameter,
                "lower_limit": float(row["lower_limit"]),
                "upper_limit": float(row["upper_limit"]),
                "unit": row["unit"],
                "spec_id": row["spec_id"],
                "revision": row["revision"],
                "source": row["source"],
                "temporal_alignment": True,
            },
            limitations=("该规范仅为脱敏教学数据，不可作为生产控制依据。",),
        )

    def _inspect_equipment_context(
        self,
        abnormal_batch_ids: tuple[str, ...],
    ) -> ToolPayload:
        quality_rows = self._selected_rows(
            self._read_rows("quality_results.csv"),
            "batch_id",
            abnormal_batch_ids,
        )
        equipment_ids = tuple(
            sorted({row["equipment_id"] for row in quality_rows})
        )
        window_start = min(row["window_start"] for row in quality_rows)
        window_end = max(row["window_end"] for row in quality_rows)
        alarms = [
            row
            for row in self._read_rows("equipment_alarms.csv")
            if row["batch_id"] in set(abnormal_batch_ids)
            and row["equipment_id"] in set(equipment_ids)
        ]
        maintenance = [
            row
            for row in self._read_rows("maintenance_records.csv")
            if row["equipment_id"] in set(equipment_ids)
        ]
        first_start = datetime.fromisoformat(window_start)
        last_end = datetime.fromisoformat(window_end)
        overdue_before_event = any(
            datetime.fromisoformat(row["start_time"]) < first_start
            and datetime.fromisoformat(row["next_due"]) < first_start
            for row in maintenance
        )
        post_window_actions = tuple(
            row["maintenance_id"]
            for row in maintenance
            if datetime.fromisoformat(row["start_time"]) > last_end
        )
        status = "success" if alarms and maintenance else "insufficient"
        limitations = (
            "告警和维护记录的时间共现不能单独证明设备状态导致尺寸漂移。",
        )
        if not alarms:
            limitations += ("异常批次窗口内没有可用设备告警。",)
        if not maintenance:
            limitations += ("没有匹配设备的维护记录。",)
        return ToolPayload(
            status=status,
            claim=(
                f"异常窗口匹配 {len(alarms)} 条设备告警和 "
                f"{len(maintenance)} 条维护记录；"
                f"窗口前保养逾期={'是' if overdue_before_event else '否'}。"
            ),
            query=(
                f"abnormal={','.join(abnormal_batch_ids)}; "
                f"equipment={','.join(equipment_ids)}"
            ),
            data_range=f"{window_start} 至 {window_end}，并核对相邻维护记录",
            source_files=("equipment_alarms.csv", "maintenance_records.csv"),
            data={
                "equipment_ids": equipment_ids,
                "alarm_count": len(alarms),
                "alarm_codes": tuple(row["alarm_code"] for row in alarms),
                "alarm_severities": tuple(row["severity"] for row in alarms),
                "maintenance_ids": tuple(
                    row["maintenance_id"] for row in maintenance
                ),
                "overdue_before_event": overdue_before_event,
                "post_window_actions": post_window_actions,
                "temporal_alignment": bool(alarms),
            },
            limitations=limitations,
        )
