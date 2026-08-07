from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence


CHAPTER_DIR = Path(__file__).resolve().parent
SENSOR_DATA_PATH = CHAPTER_DIR / "data" / "sensor_sample.csv"
RULE_DATA_PATH = CHAPTER_DIR / "data" / "alarm_rules.csv"
SENSOR_SOURCE = "code-dsext/chapter1/data/sensor_sample.csv"
MAX_TOOL_CALLS = 3
METRICS = {
    "temperature_c": ("温度", "°C"),
    "vibration_mm_s": ("振动速度", "mm/s"),
    "current_a": ("电流", "A"),
}
TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")
ALARM_PATTERN = re.compile(r"\bALM-[A-Z0-9]+-\d{3}\b", re.I)
EQUIPMENT_PATTERN = re.compile(
    r"\b(?!ALM-)[A-Z][A-Z0-9]*-[A-Z0-9-]+\b",
    re.I,
)


# --- 1. 定义 Thought-Action-Observation 协议 ---
@dataclass(frozen=True)
class PlannerStep:
    thought: str
    action: str


@dataclass(frozen=True)
class ActionTrace:
    round_number: int
    thought: str
    action: str
    observation: str


@dataclass(frozen=True)
class TriageOutcome:
    status: str
    report: str
    trace: tuple[ActionTrace, ...]
    tool_call_count: int


class Planner(Protocol):
    def plan(
        self,
        intent: dict[str, object],
        evidence: Mapping[str, object],
    ) -> PlannerStep: ...


# --- 2. 实现两个固定路径、只读、确定性工具 ---
def _cell(row: Mapping[str, str | None], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} 不能为空")
    value = value.strip()
    if len(value) > 500 or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} 含不安全文本")
    return value


def _read_rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    if path.stat().st_size > 1_000_000:
        raise ValueError(f"{path.name} 超过 1 MB 教学样例上限")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} 缺少字段：{', '.join(sorted(missing))}")
        return [
            {key: _cell(row, key) for key in required}
            for row in reader
        ]


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace(" ", "T"))


def _number(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def query_sensor_window(
    equipment_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    fields = {
        "timestamp",
        "equipment_id",
        "temperature_c",
        "vibration_mm_s",
        "current_a",
        "alarm_code",
        "status",
    }
    rows: list[dict[str, object]] = []
    try:
        for source in _read_rows(SENSOR_DATA_PATH, fields):
            timestamp = _parse_time(source["timestamp"])
            if (
                source["equipment_id"].upper() != equipment_id.upper()
                or not start <= timestamp <= end
            ):
                continue
            rows.append(
                {
                    "timestamp": timestamp,
                    "temperature_c": Decimal(source["temperature_c"]),
                    "vibration_mm_s": Decimal(source["vibration_mm_s"]),
                    "current_a": Decimal(source["current_a"]),
                    "alarm_code": source["alarm_code"].upper(),
                }
            )
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"sensor_sample.csv 数据格式错误：{error}") from error

    summaries: dict[str, dict[str, Decimal]] = {}
    for metric in METRICS:
        values = [row[metric] for row in rows]
        if values:
            decimals = [value for value in values if isinstance(value, Decimal)]
            summaries[metric] = {
                "minimum": min(decimals),
                "maximum": max(decimals),
                "mean": (sum(decimals) / Decimal(len(decimals))).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                ),
            }
    alarm_codes = tuple(
        sorted({str(row["alarm_code"]) for row in rows if row["alarm_code"]})
    )
    return {"rows": tuple(rows), "summaries": summaries, "alarm_codes": alarm_codes}


def lookup_alarm_rule(alarm_code: str) -> dict[str, object] | None:
    fields = {
        "rule_id",
        "alarm_code",
        "metric",
        "operator",
        "threshold",
        "unit",
        "possible_causes",
        "inspection_items",
        "missing_evidence",
        "safety_level",
        "rule_source",
    }
    try:
        for row in _read_rows(RULE_DATA_PATH, fields):
            if row["alarm_code"].upper() != alarm_code.upper():
                continue
            if row["metric"] not in METRICS or row["operator"] not in {">", ">=", "<", "<="}:
                raise ValueError("规则测点或运算符无效")
            return {
                "rule_id": row["rule_id"],
                "alarm_code": row["alarm_code"].upper(),
                "metric": row["metric"],
                "operator": row["operator"],
                "threshold": Decimal(row["threshold"]),
                "unit": row["unit"],
                "possible_causes": tuple(row["possible_causes"].split("|")),
                "inspection_items": tuple(row["inspection_items"].split("|")),
                "missing_evidence": tuple(row["missing_evidence"].split("|")),
                "safety_level": row["safety_level"],
                "rule_source": row["rule_source"],
            }
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"alarm_rules.csv 数据格式错误：{error}") from error
    return None


# --- 3. 解析问题，并由离线 Planner 动态选择下一动作 ---
def parse_question(question: str) -> dict[str, object]:
    alarm_match = ALARM_PATTERN.search(question)
    alarm_code = alarm_match.group(0).upper() if alarm_match else None
    equipment_match = EQUIPMENT_PATTERN.search(ALARM_PATTERN.sub(" ", question))
    equipment_id = equipment_match.group(0).upper() if equipment_match else None
    if equipment_id is None:
        press_match = re.search(r"冲压机\s*([A-Z0-9]+)", question, re.I)
        if press_match:
            equipment_id = f"PRESS-{press_match.group(1).upper()}"

    times = TIME_PATTERN.findall(question)
    missing = [] if equipment_id else ["设备编号"]
    errors: list[str] = []
    start: datetime | None = None
    end: datetime | None = None
    if len(times) < 2:
        missing.append("开始时间和结束时间（YYYY-MM-DD HH:MM）")
    elif len(times) > 2:
        errors.append("一次仅支持一个时间窗")
    else:
        start, end = (_parse_time(value) for value in times)
        if start > end:
            errors.append("开始时间不能晚于结束时间")
    return {
        "equipment_id": equipment_id,
        "start": start,
        "end": end,
        "alarm_code": alarm_code,
        "missing": tuple(missing),
        "errors": tuple(errors),
    }


class OfflinePlanner:
    def plan(
        self,
        intent: dict[str, object],
        evidence: Mapping[str, object],
    ) -> PlannerStep:
        if intent["alarm_code"] and "lookup_alarm_rule" not in evidence:
            return PlannerStep("已知告警码，先取得规则依据。", "lookup_alarm_rule")
        if "query_sensor_window" not in evidence:
            return PlannerStep("缺少传感器事实，读取设备时间窗。", "query_sensor_window")
        sensor = evidence["query_sensor_window"]
        if not intent["alarm_code"] and isinstance(sensor, dict):
            codes = sensor["alarm_codes"]
            if isinstance(codes, tuple) and len(codes) == 1:
                intent["alarm_code"] = codes[0]
                return PlannerStep("数据中只有一个告警码，查询其规则。", "lookup_alarm_rule")
        return PlannerStep("已取得当前可用证据，生成报告。", "Finish")


# --- 4. 显式循环执行白名单 Action，并记录每轮 Observation ---
class EquipmentAlarmTriageAgent:
    def __init__(
        self,
        planner: Planner | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
    ) -> None:
        if not 1 <= max_tool_calls <= MAX_TOOL_CALLS:
            raise ValueError(f"max_tool_calls 必须在 1 到 {MAX_TOOL_CALLS} 之间")
        self.planner = planner or OfflinePlanner()
        self.max_tool_calls = max_tool_calls
        self.available_tools: dict[str, Callable[..., object]] = {
            "query_sensor_window": query_sensor_window,
            "lookup_alarm_rule": lookup_alarm_rule,
        }

    def run(self, question: str) -> TriageOutcome:
        intent = parse_question(question)
        if intent["missing"] or intent["errors"]:
            report = _clarification_report(intent["missing"], intent["errors"])
            return TriageOutcome("needs_clarification", report, (), 0)

        evidence: dict[str, object] = {}
        issues: list[str] = []
        trace: list[ActionTrace] = []
        tool_calls = 0
        while True:
            step = self.planner.plan(intent, evidence)
            round_number = len(trace) + 1
            if step.action == "Finish":
                trace.append(
                    ActionTrace(
                        round_number,
                        step.thought,
                        "Finish",
                        "结束工具调用，使用已记录证据生成报告。",
                    )
                )
                break
            if step.action not in self.available_tools:
                issue = f"拒绝未注册动作：{step.action}"
                issues.append(issue)
                trace.append(ActionTrace(round_number, step.thought, step.action, issue))
                break
            if tool_calls >= self.max_tool_calls:
                issue = f"已达到 {self.max_tool_calls} 次只读工具调用上限"
                issues.append(issue)
                trace.append(ActionTrace(round_number, step.thought, step.action, issue))
                break

            result = self._execute(step.action, intent)
            evidence[step.action] = result
            tool_calls += 1
            trace.append(
                ActionTrace(
                    round_number,
                    step.thought,
                    step.action,
                    _observation(step.action, result),
                )
            )

        sensor = evidence.get("query_sensor_window")
        if not isinstance(sensor, dict):
            issues.append("尚未取得传感器数据")
        else:
            rows = sensor["rows"]
            codes = sensor["alarm_codes"]
            alarm_code = intent["alarm_code"]
            if not rows:
                issues.append("缺少该设备在指定时间窗内的有效采样")
            elif not codes:
                issues.append("时间窗内没有告警码标记")
            elif not alarm_code and len(codes) > 1:
                issues.append("时间窗内包含多个告警码，请指定本次告警码")
            elif alarm_code and alarm_code not in codes:
                issues.append(
                    f"指定告警码 {alarm_code} 未出现在该时间窗的数据记录中"
                )

        report = _build_report(intent, evidence, issues)
        rule = evidence.get("lookup_alarm_rule")
        has_rows = isinstance(sensor, dict) and bool(sensor["rows"])
        if any("多个告警码" in issue for issue in issues):
            status = "needs_clarification"
        elif issues or not has_rows or not isinstance(rule, dict):
            status = "incomplete"
        else:
            status = "completed"
        return TriageOutcome(status, report, tuple(trace), tool_calls)

    def _execute(self, action: str, intent: Mapping[str, object]) -> object:
        if action == "lookup_alarm_rule":
            alarm_code = intent["alarm_code"]
            if not isinstance(alarm_code, str):
                raise ValueError("查询规则前必须确定告警码")
            return self.available_tools[action](alarm_code)
        equipment_id, start, end = (
            intent["equipment_id"],
            intent["start"],
            intent["end"],
        )
        if not isinstance(equipment_id, str) or not isinstance(start, datetime) or not isinstance(end, datetime):
            raise ValueError("查询传感器前必须确定设备和时间窗")
        return self.available_tools[action](equipment_id, start, end)


def _observation(action: str, result: object) -> str:
    if action == "lookup_alarm_rule":
        if not isinstance(result, dict):
            return "规则表中未找到对应告警码。"
        return f"命中规则 {result['rule_id']}，来源 {result['rule_source']}。"
    if not isinstance(result, dict):
        return "传感器查询没有可用结果。"
    return (
        f"读取 {len(result['rows'])} 条采样；"
        f"告警码：{'、'.join(result['alarm_codes']) or '无'}。"
    )


# --- 5. 由确定性证据生成报告，并提供命令行入口 ---
def _matches(value: Decimal, operator: str, threshold: Decimal) -> bool:
    return {
        ">": value > threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        "<=": value <= threshold,
    }[operator]


def _clarification_report(missing: object, errors: object) -> str:
    lines = ["# 需要补充信息", ""]
    if isinstance(missing, tuple) and missing:
        lines.append(f"- 缺少：{'；'.join(missing)}")
    if isinstance(errors, tuple):
        lines.extend(f"- 输入问题：{error}" for error in errors)
    lines.extend(
        [
            "- 本次未调用任何工具，也未猜测设备与时间。",
            "",
            "> 风险声明：本智能体仅用于离线辅助分诊，不控制设备。",
        ]
    )
    return "\n".join(lines)


def _build_report(
    intent: Mapping[str, object],
    evidence: Mapping[str, object],
    issues: Sequence[str],
) -> str:
    sensor = evidence.get("query_sensor_window")
    rule = evidence.get("lookup_alarm_rule")
    start, end = intent["start"], intent["end"]
    lines = [
        "# 设备告警初步分诊报告",
        "",
        "## 数据事实",
        "",
        f"- 设备：`{intent['equipment_id']}`",
        f"- 时间窗：`{start:%Y-%m-%d %H:%M}` 至 `{end:%Y-%m-%d %H:%M}`（含边界）",
        f"- 数据来源：`{SENSOR_SOURCE}`",
    ]
    if isinstance(sensor, dict) and sensor["rows"]:
        lines.append(f"- 样本数：{len(sensor['rows'])}")
        for metric, (label, unit) in METRICS.items():
            summary = sensor["summaries"][metric]
            lines.append(
                f"- {label}：最小 {_number(summary['minimum'])} {unit}，"
                f"最大 {_number(summary['maximum'])} {unit}，"
                f"均值 {_number(summary['mean'])} {unit}"
            )
        lines.append(f"- 数据中的告警码：{'、'.join(sensor['alarm_codes']) or '无'}")
    elif isinstance(sensor, dict):
        lines.append("- 传感器数据：该时间窗未找到采样记录。")
    else:
        lines.append("- 传感器数据：尚未查询，不能判断该时间窗是否有采样。")

    lines.extend(["", "## 规则匹配", ""])
    if isinstance(rule, dict):
        metric = rule["metric"]
        lines.extend(
            [
                f"- 告警码：`{rule['alarm_code']}`",
                f"- 规则编号：`{rule['rule_id']}`",
                f"- 规则来源：`{rule['rule_source']}`",
                f"- 判定条件：{METRICS[metric][0]} {rule['operator']} "
                f"{_number(rule['threshold'])} {rule['unit']}",
                f"- 安全等级：{rule['safety_level']}",
            ]
        )
        if isinstance(sensor, dict):
            hits = [
                row
                for row in sensor["rows"]
                if _matches(row[metric], rule["operator"], rule["threshold"])
            ]
            points = "；".join(
                f"{row['timestamp']:%Y-%m-%d %H:%M}="
                f"{_number(row[metric])} {rule['unit']}"
                for row in hits
            )
            lines.append(
                f"- 异常测点：{len(hits)} 个采样满足规则阈值"
                + (f"（{points}）" if points else "。")
            )
        lines.append(
            "- 规则列出的可能原因（仅为待验证假设）："
            + "、".join(rule["possible_causes"])
        )
    elif "lookup_alarm_rule" in evidence:
        alarm_code = intent["alarm_code"]
        lines.append(
            f"- 未在规则表中找到 `{alarm_code}`，不生成阈值或故障原因。"
            if alarm_code
            else "- 尚未确定唯一告警码，无法引用规则。"
        )
    elif intent["alarm_code"]:
        lines.append(
            f"- 尚未查询 `{intent['alarm_code']}` 的规则，无法判断阈值匹配。"
        )
    else:
        lines.append("- 尚未确定唯一告警码，无法引用规则。")

    lines.extend(["", "## 建议人工动作", ""])
    if isinstance(rule, dict):
        lines.extend(
            f"{index}. {item}"
            for index, item in enumerate(rule["inspection_items"], start=1)
        )
    else:
        lines.append("1. 由值班工程师核对告警码及适用规则。")

    unknowns = list(issues)
    if isinstance(rule, dict):
        unknowns.extend(
            f"尚缺少{item}，无法确认具体故障原因"
            for item in rule["missing_evidence"]
        )
    lines.extend(["", "## 不确定性与风险", ""])
    lines.extend(f"- {item}" for item in dict.fromkeys(unknowns))
    lines.append(
        "- 本报告仅用于离线辅助分诊；不写入 PLC/MES/DCS，不控制设备，"
        "不把相关性或规则假设当作已确认故障。"
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行设备告警分诊智能体")
    parser.add_argument("question", nargs="?", help="设备告警自然语言问题")
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        choices=range(1, MAX_TOOL_CALLS + 1),
        default=MAX_TOOL_CALLS,
    )
    parser.add_argument("--show-trace", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    question = args.question or input("请输入设备告警问题：").strip()
    if not question:
        parser.error("问题不能为空")
    try:
        outcome = EquipmentAlarmTriageAgent(
            max_tool_calls=args.max_tool_calls
        ).run(question)
    except (csv.Error, OSError, ValueError) as error:
        parser.exit(1, f"数据加载失败：{error}\n")
    if args.show_trace:
        for item in outcome.trace:
            print(f"--- 回合 {item.round_number} ---")
            print(f"Thought: {item.thought}")
            print(f"Action: {item.action}")
            print(f"Observation: {item.observation}\n")
    print(outcome.report)
    return 0 if outcome.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
