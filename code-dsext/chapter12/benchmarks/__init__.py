from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from human_review import validate_expert_review


VALID_LEVELS = frozenset({"L1", "L2", "L3", "L4"})
VALID_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
REQUIRED_SAFETY_SCENARIOS = frozenset(
    {
        "unauthorized_control",
        "sensitive_data",
        "missing_evidence",
        "unit_conflict",
        "prompt_injection",
    }
)
CONTROL_TOOL_PREFIXES = ("write_", "set_", "control_", "execute_")


def load_benchmark_cases(path: str | Path) -> list[dict[str, Any]]:
    cases = _load_jsonl(path)
    case_ids: set[str] = set()
    for line_number, case in enumerate(cases, start=1):
        _validate_case(case, line_number)
        case_id = str(case["case_id"])
        if case_id in case_ids:
            raise ValueError(f"重复 case_id：{case_id}")
        case_ids.add(case_id)
    return cases


def load_agent_traces(path: str | Path) -> list[dict[str, Any]]:
    traces = _load_jsonl(path)
    run_ids: set[str] = set()
    case_agents: set[tuple[str, str]] = set()
    for line_number, trace in enumerate(traces, start=1):
        _validate_trace(trace, line_number)
        run_id = str(trace["run_id"])
        case_agent = (str(trace["case_id"]), str(trace["agent_version"]))
        if run_id in run_ids:
            raise ValueError(f"重复 run_id：{run_id}")
        if case_agent in case_agents:
            raise ValueError(
                "同一案例和智能体存在重复轨迹："
                f"{case_agent[0]}/{case_agent[1]}"
            )
        run_ids.add(run_id)
        case_agents.add(case_agent)
    return traces


def validate_benchmark_suite(cases: Sequence[Mapping[str, Any]]) -> None:
    if not cases:
        raise ValueError("基准集不得为空")
    levels = {str(case.get("level")) for case in cases}
    missing_levels = VALID_LEVELS - levels
    if missing_levels:
        raise ValueError(
            "基准集缺少任务层级：" + ", ".join(sorted(missing_levels))
        )
    scenarios = {
        str(scenario)
        for case in cases
        for scenario in _list_value(
            case.get("safety_scenarios", []),
            "safety_scenarios",
        )
    }
    if scenarios != REQUIRED_SAFETY_SCENARIOS:
        missing = sorted(REQUIRED_SAFETY_SCENARIOS - scenarios)
        extra = sorted(scenarios - REQUIRED_SAFETY_SCENARIOS)
        raise ValueError(
            "安全场景覆盖不完整："
            f"缺少={','.join(missing) or '-'}；"
            f"多余={','.join(extra) or '-'}"
        )
    for case in cases:
        if case["expert_review"]["status"] != "approved":
            raise ValueError(f"案例未获专家批准：{case['case_id']}")


def file_sha256(path: str | Path) -> str:
    try:
        return sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError(f"无法读取输入文件：{path}") from error


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"无法读取 JSONL：{source}") from error
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"JSONL 第 {line_number} 行无效：{source}"
            ) from error
        if not isinstance(value, dict):
            raise ValueError(f"JSONL 第 {line_number} 行必须是对象：{source}")
        records.append(value)
    if not records:
        raise ValueError(f"JSONL 不得为空：{source}")
    return records


def _validate_case(case: Mapping[str, Any], line_number: int) -> None:
    for field in (
        "schema_version",
        "case_id",
        "case_version",
        "answer_version",
        "level",
        "device_type",
        "risk_level",
    ):
        _required_text(case, field, line_number)
    if case["schema_version"] != "1.0":
        raise ValueError(f"第 {line_number} 行 schema_version 不受支持")
    if case["level"] not in VALID_LEVELS:
        raise ValueError(f"第 {line_number} 行 level 不受支持")
    if case["risk_level"] not in VALID_RISK_LEVELS:
        raise ValueError(f"第 {line_number} 行 risk_level 不受支持")
    source = _required_mapping(case, "source", line_number)
    _required_text(source, "type", line_number)
    _required_text(source, "reference", line_number)
    safety_scenarios = _string_list(
        case.get("safety_scenarios"),
        "safety_scenarios",
        line_number,
    )
    unknown_scenarios = set(safety_scenarios) - REQUIRED_SAFETY_SCENARIOS
    if unknown_scenarios:
        raise ValueError(
            f"第 {line_number} 行存在未知安全场景："
            + ", ".join(sorted(unknown_scenarios))
        )
    if case["level"] != "L4" and safety_scenarios:
        raise ValueError(f"第 {line_number} 行非 L4 案例不能声明安全攻击场景")
    allowed_tools = _string_list(
        case.get("allowed_tools"),
        "allowed_tools",
        line_number,
    )
    if len(set(allowed_tools)) != len(allowed_tools):
        raise ValueError(f"第 {line_number} 行 allowed_tools 存在重复")
    if any(tool.lower().startswith(CONTROL_TOOL_PREFIXES) for tool in allowed_tools):
        raise ValueError(f"第 {line_number} 行只允许声明只读工具")
    evidence = _mapping_list(
        case.get("evidence_catalog"),
        "evidence_catalog",
        line_number,
    )
    evidence_ids = [
        _required_text(item, "evidence_id", line_number)
        for item in evidence
    ]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError(f"第 {line_number} 行 evidence_id 存在重复")
    expected = _required_mapping(case, "expected", line_number)
    expected_calls = _mapping_list(
        expected.get("tool_calls"),
        "expected.tool_calls",
        line_number,
    )
    for call in expected_calls:
        tool = _required_text(call, "tool", line_number)
        if tool not in allowed_tools:
            raise ValueError(f"第 {line_number} 行期望调用未进入只读白名单：{tool}")
        _required_mapping(call, "parameters", line_number)
    for field in (
        "analysis_steps",
        "evidence_ids",
        "fact_ids",
        "hypothesis_ids",
        "refusal_reasons",
        "allowed_action_ids",
    ):
        _string_list(expected.get(field), f"expected.{field}", line_number)
    if not set(expected["evidence_ids"]).issubset(evidence_ids):
        raise ValueError(f"第 {line_number} 行期望证据不在 evidence_catalog")
    confidence_range = _list_value(
        expected.get("confidence_range"),
        "expected.confidence_range",
    )
    if (
        len(confidence_range) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in confidence_range
        )
        or not 0 <= confidence_range[0] <= confidence_range[1] <= 1
    ):
        raise ValueError(f"第 {line_number} 行 confidence_range 无效")
    if type(expected.get("must_refuse")) is not bool:
        raise ValueError(f"第 {line_number} 行 must_refuse 必须是布尔值")
    if case["level"] == "L4" and expected["must_refuse"] is not True:
        raise ValueError(f"第 {line_number} 行 L4 案例必须要求安全拒答")
    if expected["must_refuse"] is True:
        _required_text(
            expected,
            "approved_refusal_conclusion",
            line_number,
        )
    elif "approved_refusal_conclusion" in expected:
        raise ValueError(
            f"第 {line_number} 行非拒答案例不得声明批准拒答结论"
        )
    for field in ("max_calls", "token_budget", "latency_budget_ms", "cost_budget"):
        value = expected.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"第 {line_number} 行 expected.{field} 无效")
    judge = _required_mapping(case, "judge_review", line_number)
    if judge.get("advisory_only") is not True:
        raise ValueError(f"第 {line_number} 行 Judge 只能作为建议")
    validate_expert_review(case)
    if case["expert_review"]["status"] != "approved":
        raise ValueError(f"第 {line_number} 行案例未通过专家审核")


def _validate_trace(trace: Mapping[str, Any], line_number: int) -> None:
    for field in (
        "trace_version",
        "fixture_version",
        "case_id",
        "agent_version",
        "run_id",
    ):
        _required_text(trace, field, line_number)
    if trace["trace_version"] != "1.0":
        raise ValueError(f"第 {line_number} 行 trace_version 不受支持")
    _mapping_list(trace.get("tool_calls"), "tool_calls", line_number)
    _string_list(trace.get("evidence_ids"), "evidence_ids", line_number)
    analysis = _required_mapping(trace, "analysis", line_number)
    _string_list(analysis.get("step_ids"), "analysis.step_ids", line_number)
    _string_list(analysis.get("fact_ids"), "analysis.fact_ids", line_number)
    _mapping_list(
        analysis.get("hypotheses"),
        "analysis.hypotheses",
        line_number,
    )
    _string_list(analysis.get("unknowns"), "analysis.unknowns", line_number)
    report = _required_mapping(trace, "report", line_number)
    _string_list(report.get("fact_ids"), "report.fact_ids", line_number)
    _string_list(report.get("citations"), "report.citations", line_number)
    _required_text(report, "conclusion", line_number)
    _mapping_list(
        report.get("recommended_actions"),
        "report.recommended_actions",
        line_number,
    )
    safety = _required_mapping(trace, "safety", line_number)
    if type(safety.get("refused")) is not bool:
        raise ValueError(f"第 {line_number} 行 safety.refused 必须是布尔值")
    _string_list(
        safety.get("refusal_reasons"),
        "safety.refusal_reasons",
        line_number,
    )
    _string_list(
        safety.get("observed_events"),
        "safety.observed_events",
        line_number,
    )
    usage = _required_mapping(trace, "usage", line_number)
    for field in (
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "estimated_cost",
    ):
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"第 {line_number} 行 usage.{field} 无效")
    stability = _required_mapping(trace, "stability", line_number)
    _string_list(
        stability.get("repeat_output_hashes"),
        "stability.repeat_output_hashes",
        line_number,
    )
    format_passes = _list_value(
        stability.get("format_variant_passes"),
        "stability.format_variant_passes",
    )
    if not format_passes or any(type(value) is not bool for value in format_passes):
        raise ValueError(f"第 {line_number} 行格式敏感性记录无效")
    if type(stability.get("data_perturbation_consistent")) is not bool:
        raise ValueError(f"第 {line_number} 行数据扰动记录必须是布尔值")


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
    line_number: int,
) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"第 {line_number} 行 {key} 必须是对象")
    return dict(item)


def _mapping_list(
    value: Any,
    label: str,
    line_number: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise ValueError(f"第 {line_number} 行 {label} 必须是对象数组")
    return [dict(item) for item in value]


def _string_list(value: Any, label: str, line_number: int) -> list[str]:
    items = _list_value(value, label)
    if any(not isinstance(item, str) or not item for item in items):
        raise ValueError(f"第 {line_number} 行 {label} 必须是字符串数组")
    return list(items)


def _list_value(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是数组")
    return list(value)


def _required_text(
    value: Mapping[str, Any],
    key: str,
    line_number: int,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"第 {line_number} 行 {key} 必须是非空字符串")
    return item.strip()


__all__ = [
    "file_sha256",
    "load_agent_traces",
    "load_benchmark_cases",
    "validate_benchmark_suite",
]
