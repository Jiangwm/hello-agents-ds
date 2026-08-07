from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Callable, Mapping, Sequence


CHAPTER_DIR = Path(__file__).resolve().parent
DATA_DIR = CHAPTER_DIR / "data"
BATCH_PATH = DATA_DIR / "batch_master.csv"
PROCESS_PATH = DATA_DIR / "process_timeseries.csv"
QUALITY_PATH = DATA_DIR / "quality_results.csv"
EVENT_PATH = DATA_DIR / "events.csv"
SPEC_PATH = DATA_DIR / "process_specs.csv"
MAX_DATA_FILE_BYTES = 1_000_000
MAX_TOOL_CALLS = 8

SOURCE_NAMES = {
    BATCH_PATH: "code-dsext/chapter4/data/batch_master.csv",
    PROCESS_PATH: "code-dsext/chapter4/data/process_timeseries.csv",
    QUALITY_PATH: "code-dsext/chapter4/data/quality_results.csv",
    EVENT_PATH: "code-dsext/chapter4/data/events.csv",
    SPEC_PATH: "code-dsext/chapter4/data/process_specs.csv",
}
PARAMETERS = {
    "temperature_c": ("温度", "°C"),
    "pressure_mpa": ("压力", "MPa"),
    "speed_m_min": ("速度", "m/min"),
    "flow_l_min": ("流量", "L/min"),
}
BATCH_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,39}$")


@dataclass(frozen=True)
class RCARequest:
    normal_batch_ids: tuple[str, ...]
    abnormal_batch_ids: tuple[str, ...]
    focus: str = "产品一次合格率下降"
    case_id: str = "RCA-CUSTOM"


@dataclass(frozen=True)
class ToolPayload:
    status: str
    summary: str
    query: str
    data_range: str
    source_files: tuple[str, ...]
    data: Mapping[str, object]
    error: str = ""


@dataclass(frozen=True)
class ToolObservation:
    evidence_id: str
    tool_name: str
    status: str
    summary: str
    query: str
    data_range: str
    source_files: tuple[str, ...]
    data: Mapping[str, object]
    error: str = ""


@dataclass(frozen=True)
class ActionTrace:
    round_number: int
    thought: str
    action: str
    observation: str
    data_range: str
    evidence_id: str
    status: str


@dataclass(frozen=True)
class RCAOutcome:
    agent_name: str
    status: str
    report: str
    trace: tuple[ActionTrace, ...]
    evidence: tuple[ToolObservation, ...]
    tool_call_count: int
    evidence_completeness: float
    auditability: float


def _safe_cell(row: Mapping[str, str | None], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} 不能为空")
    value = value.strip()
    if len(value) > 500 or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} 含不安全文本")
    return value


def _read_rows(path: Path, required_fields: set[str]) -> list[dict[str, str]]:
    if path.stat().st_size > MAX_DATA_FILE_BYTES:
        raise ValueError(f"{path.name} 超过 1 MB 教学样例上限")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = required_fields - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} 缺少字段：{', '.join(sorted(missing))}")
        return [
            {field: _safe_cell(row, field) for field in required_fields}
            for row in reader
        ]


def _validate_batch_ids(batch_ids: Sequence[str], group_name: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(batch_id.strip().upper() for batch_id in batch_ids))
    if not normalized:
        raise ValueError(f"{group_name}不能为空")
    invalid = [batch_id for batch_id in normalized if not BATCH_ID_PATTERN.fullmatch(batch_id)]
    if invalid:
        raise ValueError(f"{group_name}包含无效批次编号：{', '.join(invalid)}")
    return normalized


def _load_batches() -> dict[str, dict[str, str]]:
    fields = {
        "batch_id",
        "product_id",
        "line_id",
        "shift_id",
        "material_lot",
        "recipe_version",
        "start_time",
        "end_time",
    }
    rows = _read_rows(BATCH_PATH, fields)
    return {row["batch_id"].upper(): row for row in rows}


def _select_batches(
    batch_ids: Sequence[str],
    group_name: str,
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    normalized = _validate_batch_ids(batch_ids, group_name)
    batches = _load_batches()
    missing = [batch_id for batch_id in normalized if batch_id not in batches]
    if missing:
        raise LookupError(f"批次主表中未找到：{', '.join(missing)}")
    return normalized, [batches[batch_id] for batch_id in normalized]


def _load_quality() -> dict[str, dict[str, object]]:
    fields = {
        "batch_id",
        "inspected_count",
        "passed_count",
        "primary_defect",
        "defect_count",
        "measurement_name",
        "measurement_mean",
    }
    result: dict[str, dict[str, object]] = {}
    for row in _read_rows(QUALITY_PATH, fields):
        inspected = int(row["inspected_count"])
        passed = int(row["passed_count"])
        defect_count = int(row["defect_count"])
        if inspected <= 0 or not 0 <= passed <= inspected or defect_count < 0:
            raise ValueError(f"{row['batch_id']} 的质检计数无效")
        result[row["batch_id"].upper()] = {
            "inspected_count": inspected,
            "passed_count": passed,
            "first_pass_yield_pct": round(passed / inspected * 100, 2),
            "primary_defect": row["primary_defect"],
            "defect_count": defect_count,
            "measurement_name": row["measurement_name"],
            "measurement_mean": float(row["measurement_mean"]),
        }
    return result


def _load_events() -> list[dict[str, str]]:
    return _read_rows(
        EVENT_PATH,
        {"timestamp", "batch_id", "event_type", "event_code", "description"},
    )


def _load_process_rows() -> list[dict[str, object]]:
    fields = {"timestamp", "batch_id", *PARAMETERS}
    rows: list[dict[str, object]] = []
    for source in _read_rows(PROCESS_PATH, fields):
        rows.append(
            {
                "timestamp": datetime.fromisoformat(
                    source["timestamp"].replace(" ", "T")
                ),
                "batch_id": source["batch_id"].upper(),
                **{parameter: float(source[parameter]) for parameter in PARAMETERS},
            }
        )
    return rows


def _load_specs() -> dict[tuple[str, str], dict[str, object]]:
    fields = {
        "product_id",
        "parameter",
        "lower_limit",
        "upper_limit",
        "unit",
        "spec_id",
        "source",
    }
    specs: dict[tuple[str, str], dict[str, object]] = {}
    for row in _read_rows(SPEC_PATH, fields):
        lower = float(row["lower_limit"])
        upper = float(row["upper_limit"])
        if row["parameter"] not in PARAMETERS or lower >= upper:
            raise ValueError(f"{row['spec_id']} 的参数或限值无效")
        specs[(row["product_id"], row["parameter"])] = {
            "product_id": row["product_id"],
            "parameter": row["parameter"],
            "lower_limit": lower,
            "upper_limit": upper,
            "unit": row["unit"],
            "spec_id": row["spec_id"],
            "source": row["source"],
        }
    return specs


def get_batch_profile(batch_id: str) -> ToolPayload:
    normalized, selected = _select_batches((batch_id,), "batch_id")
    batch = selected[0]
    quality = _load_quality().get(normalized[0])
    if quality is None:
        raise LookupError(f"质量结果表中未找到：{normalized[0]}")
    events = [
        {
            "timestamp": row["timestamp"],
            "event_type": row["event_type"],
            "event_code": row["event_code"],
            "description": row["description"],
        }
        for row in _load_events()
        if row["batch_id"].upper() == normalized[0]
    ]
    data = {
        "batch": batch,
        "quality": quality,
        "events": tuple(events),
    }
    return ToolPayload(
        status="success",
        summary=(
            f"{normalized[0]} 一次合格率 {quality['first_pass_yield_pct']:.2f}%，"
            f"主缺陷为{quality['primary_defect']}，关联 {len(events)} 条事件。"
        ),
        query=f"batch_id = {normalized[0]}",
        data_range=(
            f"批次 {normalized[0]}；生产窗口 {batch['start_time']} 至 "
            f"{batch['end_time']}（Asia/Shanghai）；关联事件按 batch_id"
        ),
        source_files=(
            SOURCE_NAMES[BATCH_PATH],
            SOURCE_NAMES[QUALITY_PATH],
            SOURCE_NAMES[EVENT_PATH],
        ),
        data=data,
    )


def _group_quality(
    batch_ids: tuple[str, ...],
    quality: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    rows = [quality.get(batch_id) for batch_id in batch_ids]
    missing = [
        batch_id
        for batch_id, row in zip(batch_ids, rows, strict=True)
        if row is None
    ]
    if missing:
        raise LookupError(f"质量结果表中未找到：{', '.join(missing)}")
    valid_rows = [row for row in rows if row is not None]
    inspected = sum(int(row["inspected_count"]) for row in valid_rows)
    passed = sum(int(row["passed_count"]) for row in valid_rows)
    defects: dict[str, int] = {}
    for row in valid_rows:
        name = str(row["primary_defect"])
        defects[name] = defects.get(name, 0) + int(row["defect_count"])
    return {
        "batch_ids": batch_ids,
        "inspected_count": inspected,
        "passed_count": passed,
        "first_pass_yield_pct": round(passed / inspected * 100, 2),
        "defect_counts": defects,
    }


def compare_time_windows(
    normal_batch_ids: Sequence[str],
    abnormal_batch_ids: Sequence[str],
) -> ToolPayload:
    normal_ids, normal_batches = _select_batches(normal_batch_ids, "正常组")
    abnormal_ids, abnormal_batches = _select_batches(abnormal_batch_ids, "异常组")
    overlap = set(normal_ids) & set(abnormal_ids)
    if overlap:
        raise ValueError(f"正常组与异常组不能重叠：{', '.join(sorted(overlap))}")

    all_batches = normal_batches + abnormal_batches
    products = {batch["product_id"] for batch in all_batches}
    lines = {batch["line_id"] for batch in all_batches}
    if len(products) != 1 or len(lines) != 1:
        raise ValueError("正常组与异常组必须属于同一产品和产线")

    quality = _load_quality()
    normal = _group_quality(normal_ids, quality)
    abnormal = _group_quality(abnormal_ids, quality)
    gap = round(
        float(abnormal["first_pass_yield_pct"])
        - float(normal["first_pass_yield_pct"]),
        2,
    )
    context = {
        "normal_recipe_versions": tuple(
            sorted({batch["recipe_version"] for batch in normal_batches})
        ),
        "abnormal_recipe_versions": tuple(
            sorted({batch["recipe_version"] for batch in abnormal_batches})
        ),
        "normal_material_lots": tuple(
            sorted({batch["material_lot"] for batch in normal_batches})
        ),
        "abnormal_material_lots": tuple(
            sorted({batch["material_lot"] for batch in abnormal_batches})
        ),
    }
    data = {
        "product_id": next(iter(products)),
        "line_id": next(iter(lines)),
        "normal": normal,
        "abnormal": abnormal,
        "yield_gap_percentage_points": gap,
        "context": context,
    }
    normal_range = (
        f"{min(batch['start_time'] for batch in normal_batches)} 至 "
        f"{max(batch['end_time'] for batch in normal_batches)}"
    )
    abnormal_range = (
        f"{min(batch['start_time'] for batch in abnormal_batches)} 至 "
        f"{max(batch['end_time'] for batch in abnormal_batches)}"
    )
    return ToolPayload(
        status="success",
        summary=(
            f"异常组一次合格率 {abnormal['first_pass_yield_pct']:.2f}%，"
            f"正常组 {normal['first_pass_yield_pct']:.2f}%，"
            f"相差 {gap:.2f} 个百分点。"
        ),
        query=(
            f"normal_batch_ids IN {normal_ids}; "
            f"abnormal_batch_ids IN {abnormal_ids}; 同产品同产线"
        ),
        data_range=(
            f"正常组 {normal_range}；异常组 {abnormal_range}"
            "（Asia/Shanghai）"
        ),
        source_files=(SOURCE_NAMES[BATCH_PATH], SOURCE_NAMES[QUALITY_PATH]),
        data=data,
    )


def detect_parameter_shift(
    normal_batch_ids: Sequence[str],
    abnormal_batch_ids: Sequence[str],
) -> ToolPayload:
    normal_ids, normal_batches = _select_batches(normal_batch_ids, "正常组")
    abnormal_ids, abnormal_batches = _select_batches(abnormal_batch_ids, "异常组")
    products = {
        batch["product_id"]
        for batch in normal_batches + abnormal_batches
    }
    if len(products) != 1:
        raise ValueError("参数漂移比较要求正常组与异常组属于同一产品")
    product_id = next(iter(products))
    rows = _load_process_rows()
    normal_rows = [row for row in rows if row["batch_id"] in normal_ids]
    abnormal_rows = [row for row in rows if row["batch_id"] in abnormal_ids]
    if not normal_rows or not abnormal_rows:
        raise LookupError("工艺时序表缺少正常组或异常组采样")

    specs = _load_specs()
    shifts: list[dict[str, object]] = []
    for parameter, (label, unit) in PARAMETERS.items():
        normal_values = [float(row[parameter]) for row in normal_rows]
        abnormal_values = [float(row[parameter]) for row in abnormal_rows]
        normal_mean = mean(normal_values)
        abnormal_mean = mean(abnormal_values)
        delta = abnormal_mean - normal_mean
        normal_sigma = pstdev(normal_values)
        spec = specs.get((product_id, parameter))
        if spec:
            scale = float(spec["upper_limit"]) - float(spec["lower_limit"])
            outside_count = sum(
                value < float(spec["lower_limit"])
                or value > float(spec["upper_limit"])
                for value in abnormal_values
            )
        else:
            scale = max(abs(normal_mean), 1.0)
            outside_count = None
        shifts.append(
            {
                "parameter": parameter,
                "label": label,
                "unit": unit,
                "normal_mean": round(normal_mean, 3),
                "abnormal_mean": round(abnormal_mean, 3),
                "delta": round(delta, 3),
                "normal_sigma": round(normal_sigma, 3),
                "effect_pct_of_scale": round(abs(delta) / scale * 100, 2),
                "outside_spec_abnormal_count": outside_count,
                "normal_sample_count": len(normal_values),
                "abnormal_sample_count": len(abnormal_values),
            }
        )
    shifts.sort(
        key=lambda item: (
            -float(item["effect_pct_of_scale"]),
            str(item["parameter"]),
        )
    )
    sufficient = len(normal_rows) >= 6 and len(abnormal_rows) >= 6
    status = "success" if sufficient else "insufficient"
    limitation = "" if sufficient else "每组工艺采样少于 6 条，漂移排序仅作线索"
    normal_range = (
        f"{min(row['timestamp'] for row in normal_rows):%Y-%m-%d %H:%M:%S} 至 "
        f"{max(row['timestamp'] for row in normal_rows):%Y-%m-%d %H:%M:%S}"
    )
    abnormal_range = (
        f"{min(row['timestamp'] for row in abnormal_rows):%Y-%m-%d %H:%M:%S} 至 "
        f"{max(row['timestamp'] for row in abnormal_rows):%Y-%m-%d %H:%M:%S}"
    )
    return ToolPayload(
        status=status,
        summary=(
            f"最显著漂移参数为{shifts[0]['label']}："
            f"{shifts[0]['normal_mean']} → {shifts[0]['abnormal_mean']} "
            f"{shifts[0]['unit']}。"
            + (f" {limitation}。" if limitation else "")
        ),
        query=(
            f"normal_batch_ids IN {normal_ids}; "
            f"abnormal_batch_ids IN {abnormal_ids}; 按参数均值比较"
        ),
        data_range=(
            f"正常组采样 {normal_range}；异常组采样 {abnormal_range}"
            "（Asia/Shanghai）"
        ),
        source_files=(SOURCE_NAMES[PROCESS_PATH], SOURCE_NAMES[SPEC_PATH]),
        data={
            "product_id": product_id,
            "shifts": tuple(shifts),
            "limitation": limitation,
        },
    )


def lookup_process_spec(product_id: str, parameter: str) -> ToolPayload:
    product = product_id.strip().upper()
    normalized_parameter = parameter.strip()
    if normalized_parameter not in PARAMETERS:
        raise ValueError(f"不支持的工艺参数：{parameter}")
    spec = _load_specs().get((product, normalized_parameter))
    if spec is None:
        return ToolPayload(
            status="insufficient",
            summary=f"未找到产品 {product} 的 {normalized_parameter} 工艺规范。",
            query=f"product_id = {product}; parameter = {normalized_parameter}",
            data_range=f"产品 {product}；参数 {normalized_parameter}",
            source_files=(SOURCE_NAMES[SPEC_PATH],),
            data={"product_id": product, "parameter": normalized_parameter},
            error="缺少适用工艺规范",
        )
    return ToolPayload(
        status="success",
        summary=(
            f"{PARAMETERS[normalized_parameter][0]}规范 "
            f"{spec['lower_limit']}–{spec['upper_limit']} {spec['unit']}，"
            f"编号 {spec['spec_id']}。"
        ),
        query=f"product_id = {product}; parameter = {normalized_parameter}",
        data_range=(
            f"产品 {product}；参数 {normalized_parameter}；"
            f"规范 {spec['spec_id']}"
        ),
        source_files=(SOURCE_NAMES[SPEC_PATH],),
        data=spec,
    )


class IndustrialToolExecutor:
    def __init__(self, max_calls: int = MAX_TOOL_CALLS) -> None:
        if not 1 <= max_calls <= MAX_TOOL_CALLS:
            raise ValueError(f"max_calls 必须在 1 到 {MAX_TOOL_CALLS} 之间")
        self.max_calls = max_calls
        self.observations: list[ToolObservation] = []
        self.available_tools: dict[str, Callable[..., ToolPayload]] = {
            "get_batch_profile": get_batch_profile,
            "compare_time_windows": compare_time_windows,
            "detect_parameter_shift": detect_parameter_shift,
            "lookup_process_spec": lookup_process_spec,
        }

    def execute(self, tool_name: str, **tool_input: object) -> ToolObservation:
        evidence_id = f"EV-{len(self.observations) + 1:03d}"
        if len(self.observations) >= self.max_calls:
            observation = ToolObservation(
                evidence_id,
                tool_name,
                "error",
                "已达到只读工具调用上限。",
                repr(tool_input),
                "未取得数据",
                (),
                {},
                "工具调用上限",
            )
        elif tool_name not in self.available_tools:
            observation = ToolObservation(
                evidence_id,
                tool_name,
                "error",
                f"拒绝未注册工具：{tool_name}。",
                repr(tool_input),
                "未取得数据",
                (),
                {},
                "工具不在白名单",
            )
        else:
            try:
                payload = self.available_tools[tool_name](**tool_input)
                observation = ToolObservation(
                    evidence_id,
                    tool_name,
                    payload.status,
                    payload.summary,
                    payload.query,
                    payload.data_range,
                    payload.source_files,
                    payload.data,
                    payload.error,
                )
            except (csv.Error, LookupError, OSError, TypeError, ValueError) as error:
                observation = ToolObservation(
                    evidence_id,
                    tool_name,
                    "error",
                    f"{tool_name} 调用失败：{error}",
                    repr(tool_input),
                    "未取得数据",
                    (),
                    {},
                    str(error),
                )
        self.observations.append(observation)
        return observation


def build_demo_request(scenario: str) -> RCARequest:
    if scenario == "insufficient":
        return RCARequest(
            ("BATCH-I-N01",),
            ("BATCH-I-A01",),
            "P-200 产品一次合格率下降",
            "RCA-P200-INSUFFICIENT-001",
        )
    if scenario in {"default", "confounded", "tool-failure"}:
        return RCARequest(
            ("BATCH-N001", "BATCH-N002"),
            ("BATCH-A001", "BATCH-A002"),
            "P-100 产品一次合格率突然下降",
            "RCA-P100-001",
        )
    raise ValueError(f"未知场景：{scenario}")


def parse_batch_ids(value: str) -> tuple[str, ...]:
    return _validate_batch_ids(value.split(","), "批次列表")


def _latest_success(
    evidence: Sequence[ToolObservation],
    tool_name: str,
) -> ToolObservation | None:
    for observation in reversed(evidence):
        if observation.tool_name == tool_name and observation.status == "success":
            return observation
    return None


def _observations_for(
    evidence: Sequence[ToolObservation],
    tool_name: str,
) -> tuple[ToolObservation, ...]:
    return tuple(item for item in evidence if item.tool_name == tool_name)


def _event_context(
    profiles: Sequence[ToolObservation],
) -> dict[str, tuple[str, ...]]:
    event_codes: dict[str, set[str]] = {}
    for profile in profiles:
        if profile.status != "success":
            continue
        events = profile.data.get("events", ())
        if not isinstance(events, tuple):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type", "unknown"))
            event_codes.setdefault(event_type, set()).add(
                str(event.get("event_code", ""))
            )
    return {
        event_type: tuple(sorted(codes))
        for event_type, codes in sorted(event_codes.items())
    }


def build_rca_report(
    request: RCARequest,
    evidence: Sequence[ToolObservation],
    agent_name: str,
    candidate_synthesis_completed: bool = True,
) -> tuple[str, str, float, float]:
    comparison = _latest_success(evidence, "compare_time_windows")
    shift = _latest_success(evidence, "detect_parameter_shift")
    spec = _latest_success(evidence, "lookup_process_spec")
    profiles = _observations_for(evidence, "get_batch_profile")
    event_context = _event_context(profiles)

    required_statuses = [
        comparison is not None,
        shift is not None,
        spec is not None,
        any(profile.status == "success" for profile in profiles),
    ]
    evidence_completeness = round(sum(required_statuses) / len(required_statuses), 2)
    traceable = [
        bool(item.query and item.data_range and item.source_files)
        for item in evidence
        if item.status != "error"
    ]
    auditability = round(sum(traceable) / len(traceable), 2) if traceable else 0.0
    has_insufficient = any(item.status == "insufficient" for item in evidence)
    has_error = any(item.status == "error" for item in evidence)
    status = (
        "completed"
        if (
            evidence_completeness == 1.0
            and not has_insufficient
            and candidate_synthesis_completed
        )
        else "incomplete"
    )

    lines = [
        "# 生产异常根因分析报告",
        "",
        f"> 分析范式：{agent_name}；状态：{status}",
        "",
        "## 1. 事件范围与影响",
        "",
        f"- [已确认事实] 案例编号：`{request.case_id}`。",
        f"- [已确认事实] 分析焦点：{request.focus}。",
        f"- [已确认事实] 正常组：`{', '.join(request.normal_batch_ids)}`；"
        f"异常组：`{', '.join(request.abnormal_batch_ids)}`。",
    ]
    if comparison:
        normal = comparison.data["normal"]
        abnormal = comparison.data["abnormal"]
        gap = float(comparison.data["yield_gap_percentage_points"])
        lines.extend(
            [
                f"- [已确认事实] 正常组一次合格率 "
                f"{normal['first_pass_yield_pct']:.2f}%，异常组 "
                f"{abnormal['first_pass_yield_pct']:.2f}%，变化 "
                f"{gap:.2f} 个百分点 [{comparison.evidence_id}]。",
                f"- [已确认事实] 主要缺陷计数："
                f"{abnormal['defect_counts']} [{comparison.evidence_id}]。",
            ]
        )
    else:
        lines.append("- [尚无证据] 正常组与异常组的质量影响尚未完成可比计算。")

    lines.extend(
        [
            "",
            "## 2. 正常组和异常组定义",
            "",
            "- [已确认事实] 两组按显式批次编号定义，不由模型猜测时间窗。",
        ]
    )
    if comparison:
        lines.append(
            f"- [已确认事实] 可比维度：产品 `{comparison.data['product_id']}`、"
            f"产线 `{comparison.data['line_id']}` [{comparison.evidence_id}]。"
        )
        context = comparison.data["context"]
        lines.extend(
            [
                f"- [已确认事实] 正常组程序版本 "
                f"{context['normal_recipe_versions']}，异常组 "
                f"{context['abnormal_recipe_versions']} [{comparison.evidence_id}]。",
                f"- [已确认事实] 正常组物料批次 "
                f"{context['normal_material_lots']}，异常组 "
                f"{context['abnormal_material_lots']} [{comparison.evidence_id}]。",
            ]
        )
    else:
        lines.append("- [尚无证据] 尚未验证产品、产线与抽样口径是否可比。")

    lines.extend(["", "## 3. 数据证据及查询条件", ""])
    for observation in evidence:
        sources = "、".join(f"`{source}`" for source in observation.source_files)
        if observation.status == "error":
            lines.append(
                f"- [{observation.evidence_id}] 失败：{observation.summary}；"
                f"输入：`{observation.query}`；"
                f"数据范围：{observation.data_range}。"
            )
        else:
            lines.append(
                f"- [{observation.evidence_id}] `{observation.tool_name}`"
                f"（{observation.status}）：{observation.summary}"
                f" 查询条件：`{observation.query}`；"
                f"数据范围：{observation.data_range}；来源：{sources}。"
            )

    lines.extend(["", "## 4. 候选根因排序与反证", ""])
    candidate_number = 1
    if not candidate_synthesis_completed:
        lines.append(
            f"{candidate_number}. [尚无证据] 计划中的候选根因形成步骤未完成，"
            "本次不输出候选排序。"
        )
        candidate_number += 1
    elif shift:
        shifts = shift.data.get("shifts", ())
        top = shifts[0] if isinstance(shifts, tuple) and shifts else None
        if isinstance(top, dict):
            support = (
                f"{top['label']}均值由 {top['normal_mean']} 变为 "
                f"{top['abnormal_mean']} {top['unit']}，"
                f"漂移占参考尺度 {top['effect_pct_of_scale']:.2f}%"
            )
            if spec:
                support += (
                    f"；规范为 {spec.data['lower_limit']}–"
                    f"{spec.data['upper_limit']} {spec.data['unit']}"
                )
            lines.extend(
                [
                    f"{candidate_number}. [数据支持的假设] **{top['label']}漂移"
                    f"可能参与异常形成**。",
                    "   - 排查优先级：高（质量差异、参数漂移和规范核对"
                    "三类证据同时存在）。",
                    f"   - 支持：{support} [{shift.evidence_id}]"
                    + (
                        f"[{spec.evidence_id}]"
                        if spec
                        else ""
                    )
                    + (
                        f"[{comparison.evidence_id}]。"
                        if comparison
                        else "。"
                    ),
                    "   - 反证/限制：当前为批次间相关关系，未进行受控干预，"
                    "不能单独确认因果。",
                ]
            )
            candidate_number += 1
    else:
        lines.append(
            f"{candidate_number}. [尚无证据] 尚未形成可复核的工艺参数漂移排序。"
        )
        candidate_number += 1

    if comparison:
        context = comparison.data["context"]
        recipe_changed = (
            context["normal_recipe_versions"]
            != context["abnormal_recipe_versions"]
        )
        material_changed = (
            context["normal_material_lots"]
            != context["abnormal_material_lots"]
        )
        if recipe_changed:
            lines.extend(
                [
                    f"{candidate_number}. [数据支持的假设] **程序版本变化"
                    f"可能是共同因素** [{comparison.evidence_id}]。",
                    "   - 排查优先级：中（目前仅有批次间共现证据）。",
                    "   - 反证/限制：程序版本与其他批次条件同时变化，"
                    "没有隔离变量的对照批次。",
                ]
            )
            candidate_number += 1
        if material_changed:
            lines.extend(
                [
                    f"{candidate_number}. [数据支持的假设] **物料批次变化"
                    f"可能是共同因素** [{comparison.evidence_id}]。",
                    "   - 排查优先级：中（目前仅有批次间共现证据）。",
                    "   - 反证/限制：缺少来料检验与同物料跨版本对照数据。",
                ]
            )
            candidate_number += 1

    confounders = tuple(
        event_type
        for event_type in ("material_change", "recipe_version")
        if event_type in event_context
    )
    if len(confounders) > 1:
        profile_refs = "".join(
            f"[{profile.evidence_id}]"
            for profile in profiles
            if profile.status == "success"
        )
        lines.append(
            f"- [尚无证据] `material_change` 与 `recipe_version` "
            f"在异常批次共同出现 {profile_refs}，当前证据无法区分二者贡献。"
        )

    lines.extend(
        [
            "",
            "## 5. 建议补充采集的数据",
            "",
            "- [尚无证据] 采集“旧程序版本 + 新物料”和“新程序版本 + 旧物料”"
            "的受控对照批次。",
            "- [尚无证据] 补充加热区校准记录、测点原始精度和更长正常基线。",
            "- [尚无证据] 关联来料检验、环境条件和设备维护记录，排除遗漏变量。",
        ]
    )
    if has_error:
        lines.append("- [尚无证据] 重新核对失败工具调用中的批次编号和数据完整性。")
    if has_insufficient:
        lines.append("- [尚无证据] 增加每组工艺采样数量后再计算漂移稳定性。")

    lines.extend(
        [
            "",
            "## 6. 需人工审批的验证实验",
            "",
            "1. 由工艺、质量与设备工程师共同审批单变量对照实验，"
            "一次只改变程序版本或物料批次。",
            "2. 工程师审批实验窗口、产品隔离方案与回退条件；"
            "智能体只读取实验结果并更新证据。",
            "3. 在人工复核数据口径、规范版本和安全风险后，"
            "才能签署最终根因结论。",
            "",
            "## 审计摘要",
            "",
            f"- 只读工具调用次数：{len(evidence)}",
            f"- 关键证据完整度：{evidence_completeness:.0%}",
            f"- 查询可审计率：{auditability:.0%}",
            "",
            "> 安全边界：本示例只读取脱敏教学数据并做确定性计算；"
            "不写入 PLC、MES、DCS，不修改配方或工艺参数，"
            "不把相关性自动判定为因果。",
        ]
    )
    return "\n".join(lines), status, evidence_completeness, auditability


def render_trace(trace: Sequence[ActionTrace]) -> str:
    lines: list[str] = []
    for item in trace:
        lines.extend(
            [
                f"--- 回合 {item.round_number} ---",
                f"Thought: {item.thought}",
                f"Action: {item.action}",
                f"Observation: [{item.evidence_id}] {item.observation}",
                f"Data Range: {item.data_range}",
                f"Status: {item.status}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()
