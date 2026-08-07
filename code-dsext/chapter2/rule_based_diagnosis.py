from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence


CHAPTER_DIR = Path(__file__).resolve().parent
RULE_DATA_PATH = CHAPTER_DIR / "rules" / "pump_fault_rules.json"
DIALOGUE_CASES_PATH = CHAPTER_DIR / "data" / "dialogue_cases.json"
RISK_ORDER = {"紧急": 4, "高": 3, "中": 2, "低": 1}
FEATURE_LABELS = {
    "evidence_conflict": "多个规则部分命中",
    "fallback": "未命中已知规则",
    "fire_smoke": "冒烟或起火",
    "large_leakage": "大量泄漏",
    "personnel_injury": "人员伤害",
    "inlet_pressure_low": "入口压力偏低",
    "outlet_pressure_low": "出口压力偏低",
    "vibration_high": "振动升高",
    "noise_abnormal": "噪声异常",
    "flow_fluctuating": "流量波动",
    "bearing_temperature_high": "轴承温度升高",
    "high_frequency_vibration_high": "高频振动升高",
    "filter_dp_high": "过滤器压差增大",
    "flow_decreasing": "流量持续下降",
    "flow_low": "流量偏低",
    "seal_leakage_abnormal": "密封泄漏量异常",
    "seal_chamber_temperature_high": "密封腔温度异常",
}
TEXT_FEATURE_PATTERNS = {
    "fire_smoke": (
        re.compile(r"(?:冒烟|起火|明火|燃烧)"),
    ),
    "large_leakage": (
        re.compile(r"(?:大量泄漏|喷射泄漏|泄漏失控)"),
    ),
    "personnel_injury": (
        re.compile(r"(?:人员受伤|有人受伤|人员伤害)"),
    ),
    "inlet_pressure_low": (
        re.compile(r"入口(?:压力|压).{0,6}(?:偏低|下降|降低|不足|低)"),
    ),
    "outlet_pressure_low": (
        re.compile(r"出口(?:压力|压).{0,6}(?:偏低|下降|降低|不足|低)"),
    ),
    "vibration_high": (
        re.compile(r"振动.{0,6}(?:升高|增大|剧烈|偏高|超标|高)"),
    ),
    "noise_abnormal": (
        re.compile(r"(?:异响|噪声.{0,6}(?:异常|增大|升高|大))"),
    ),
    "flow_fluctuating": (
        re.compile(r"流量.{0,6}(?:波动|不稳|忽高忽低)"),
    ),
    "bearing_temperature_high": (
        re.compile(r"轴承温度.{0,6}(?:升高|偏高|超标|过热|高)"),
    ),
    "high_frequency_vibration_high": (
        re.compile(r"高频振动.{0,6}(?:升高|偏高|超标|高)"),
    ),
    "filter_dp_high": (
        re.compile(r"过滤器(?:压差|差压).{0,6}(?:增大|升高|偏高|超标|高)"),
    ),
    "flow_decreasing": (
        re.compile(r"流量.{0,6}(?:持续下降|下降|越来越小|降低)"),
    ),
    "flow_low": (
        re.compile(r"流量.{0,6}(?:偏低|不足|低)"),
    ),
    "seal_leakage_abnormal": (
        re.compile(r"密封.{0,6}(?:泄漏|漏液|渗漏).{0,6}(?:增大|异常|严重|多)?"),
    ),
    "seal_chamber_temperature_high": (
        re.compile(r"密封腔温度.{0,6}(?:升高|偏高|超标|过热|高)"),
    ),
}
NORMALIZATION_REPLACEMENTS = (
    ("循环水泵", "循环泵"),
    ("水泵", "循环泵"),
    ("泵组", "循环泵"),
    ("进口压力", "入口压力"),
    ("进液压力", "入口压力"),
    ("吸入压力", "入口压力"),
    ("进水压力", "入口压力"),
    ("出水压力", "出口压力"),
    ("轴瓦温度", "轴承温度"),
    ("滤网压差", "过滤器压差"),
    ("过滤器差压", "过滤器压差"),
    ("密封室温度", "密封腔温度"),
    ("振得厉害", "振动升高"),
    ("抖得厉害", "振动升高"),
    ("流量忽高忽低", "流量波动"),
)
NEGATED_SYMPTOM_PATTERN = re.compile(
    r"(?:没有|没|无|未见|未)"
    r"(?:异响|冒烟|起火|明火|燃烧|(?:大量|严重|喷射)?泄漏|"
    r"人员伤害|人员受伤|人受伤|异常振动)|"
    r"(?:振动|入口压力|出口压力|轴承温度|密封腔温度|过滤器压差)"
    r"(?:(?:没有|没|未|无)(?:升高|下降|增大|降低|超标|异常)|不高|不低|正常)|"
    r"流量(?:稳定|无波动|未下降|没下降|没有下降)"
)
INLET_PRESSURE_PATTERN = re.compile(
    r"入口(?:压力|压)\s*(?:为|是|=|:|：)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*(MPa|kPa|bar)\b",
    re.IGNORECASE,
)
BEARING_TEMPERATURE_PATTERN = re.compile(
    r"轴承温度\s*(?:为|是|=|:|：)?\s*(-?\d+(?:\.\d+)?)\s*"
    r"(?:℃|°C|C|摄氏度)",
    re.IGNORECASE,
)
HIGH_FREQUENCY_VIBRATION_PATTERN = re.compile(
    r"高频振动\s*(?:为|是|=|:|：)?\s*(-?\d+(?:\.\d+)?)\s*mm/s\b",
    re.IGNORECASE,
)
FILTER_DP_PATTERN = re.compile(
    r"过滤器(?:压差|差压)\s*(?:为|是|=|:|：)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*(MPa|kPa|bar)\b",
    re.IGNORECASE,
)
SEAL_LEAKAGE_PATTERN = re.compile(
    r"密封(?:泄漏量|漏量)\s*(?:为|是|=|:|：)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*mL/min\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NecessaryCondition:
    label: str
    any_of: tuple[str, ...]
    question: str


@dataclass(frozen=True)
class DiagnosisRule:
    rule_id: str
    priority: str
    trigger_features: tuple[str, ...]
    minimum_trigger_count: int
    necessary_conditions: tuple[NecessaryCondition, ...]
    candidate_fault: str
    risk_level: str
    follow_up: str
    inspection_steps: tuple[str, ...]


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    priority: str
    candidate_fault: str
    risk_level: str
    satisfaction: float
    matched_conditions: tuple[str, ...]
    missing_conditions: tuple[str, ...]
    inspection_steps: tuple[str, ...]
    follow_up_question: str | None
    default_follow_up: str


@dataclass(frozen=True)
class DiagnosisOutcome:
    status: str
    report: str
    raw_input: str
    normalized_input: str
    matched_rule_ids: tuple[str, ...]
    matches: tuple[RuleMatch, ...]
    follow_up_question: str | None


@dataclass(frozen=True)
class ValidationCaseResult:
    case_id: str
    expected_status: str
    actual_status: str
    expected_rule_ids: tuple[str, ...]
    actual_rule_ids: tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class ValidationSummary:
    dataset_id: str
    total_cases: int
    correct_hits: int
    incorrect_hits: int
    fallback_count: int
    follow_up_count: int
    case_results: tuple[ValidationCaseResult, ...]


def _read_text(path: Path) -> str:
    if path.stat().st_size > 1_000_000:
        raise ValueError(f"{path.name} 超过 1 MB 教学样例上限")
    return path.read_text(encoding="utf-8")


def load_rules(path: Path = RULE_DATA_PATH) -> tuple[str, tuple[DiagnosisRule, ...]]:
    payload = json.loads(_read_text(path))
    rules = []
    for item in payload["rules"]:
        conditions = tuple(
            NecessaryCondition(
                label=condition["label"],
                any_of=tuple(condition["any_of"]),
                question=condition["question"],
            )
            for condition in item["necessary_conditions"]
        )
        rules.append(
            DiagnosisRule(
                rule_id=item["rule_id"],
                priority=item["priority"],
                trigger_features=tuple(item["trigger_features"]),
                minimum_trigger_count=item["minimum_trigger_count"],
                necessary_conditions=conditions,
                candidate_fault=item["candidate_fault"],
                risk_level=item["risk_level"],
                follow_up=item["follow_up"],
                inspection_steps=tuple(item["inspection_steps"]),
            )
        )
    return payload["ruleset_id"], tuple(rules)


def _number(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _normalize_input(text: str) -> str:
    normalized = text
    for source, target in NORMALIZATION_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    normalized = re.sub(
        r"(?:入口|进口|进液|吸入|进水)压(?!力)",
        "入口压力",
        normalized,
    )
    normalized = re.sub(
        r"(?:出口|出水)压(?!力)",
        "出口压力",
        normalized,
    )
    return normalized


def _extract_features(text: str) -> dict[str, str]:
    features = {}
    pressure_match = INLET_PRESSURE_PATTERN.search(text)
    if pressure_match:
        value = Decimal(pressure_match.group(1))
        unit = pressure_match.group(2).lower()
        pressure_mpa = {
            "mpa": value,
            "kpa": value / Decimal("1000"),
            "bar": value / Decimal("10"),
        }[unit]
        if pressure_mpa <= Decimal("0.12"):
            features["inlet_pressure_low"] = (
                f"入口压力 {_number(pressure_mpa)} MPa（≤ 0.12 MPa）"
            )
    temperature_match = BEARING_TEMPERATURE_PATTERN.search(text)
    if temperature_match:
        temperature = Decimal(temperature_match.group(1))
        if temperature >= Decimal("80"):
            features["bearing_temperature_high"] = (
                f"轴承温度 {_number(temperature)} °C（≥ 80 °C）"
            )
    vibration_match = HIGH_FREQUENCY_VIBRATION_PATTERN.search(text)
    if vibration_match:
        vibration = Decimal(vibration_match.group(1))
        if vibration >= Decimal("5"):
            features["high_frequency_vibration_high"] = (
                f"高频振动 {_number(vibration)} mm/s（≥ 5 mm/s）"
            )
    filter_dp_match = FILTER_DP_PATTERN.search(text)
    if filter_dp_match:
        value = Decimal(filter_dp_match.group(1))
        unit = filter_dp_match.group(2).lower()
        pressure_kpa = {
            "mpa": value * Decimal("1000"),
            "kpa": value,
            "bar": value * Decimal("100"),
        }[unit]
        if pressure_kpa >= Decimal("50"):
            features["filter_dp_high"] = (
                f"过滤器压差 {_number(pressure_kpa)} kPa（≥ 50 kPa）"
            )
    leakage_match = SEAL_LEAKAGE_PATTERN.search(text)
    if leakage_match:
        leakage = Decimal(leakage_match.group(1))
        if leakage >= Decimal("50"):
            features["seal_leakage_abnormal"] = (
                f"密封泄漏量 {_number(leakage)} mL/min（≥ 50 mL/min）"
            )
    pattern_text = NEGATED_SYMPTOM_PATTERN.sub("[已否定症状]", text)
    for feature, patterns in TEXT_FEATURE_PATTERNS.items():
        if feature in features:
            continue
        for pattern in patterns:
            match = pattern.search(pattern_text)
            if match:
                features[feature] = match.group(0)
                break
    return features


def _evaluate_rule(
    rule: DiagnosisRule,
    features: Mapping[str, str],
) -> RuleMatch | None:
    trigger_count = len(set(rule.trigger_features) & set(features))
    if trigger_count < rule.minimum_trigger_count:
        return None
    matched = tuple(
        condition.label
        for condition in rule.necessary_conditions
        if any(feature in features for feature in condition.any_of)
    )
    missing = tuple(
        condition.label
        for condition in rule.necessary_conditions
        if not any(feature in features for feature in condition.any_of)
    )
    follow_up = next(
        (
            condition.question
            for condition in rule.necessary_conditions
            if condition.label in missing
        ),
        None,
    )
    return RuleMatch(
        rule_id=rule.rule_id,
        priority=rule.priority,
        candidate_fault=rule.candidate_fault,
        risk_level=rule.risk_level,
        satisfaction=len(matched) / len(rule.necessary_conditions),
        matched_conditions=matched,
        missing_conditions=missing,
        inspection_steps=rule.inspection_steps,
        follow_up_question=follow_up,
        default_follow_up=rule.follow_up,
    )


def _build_report(
    raw_input: str,
    ruleset_id: str,
    features: Mapping[str, str],
    match: RuleMatch,
    follow_up_question: str | None = None,
    related_matches: Sequence[RuleMatch] = (),
) -> str:
    lines = [
        "# 泵组故障问诊结果",
        "",
        "## 观察事实",
        "",
        f"- 原始输入：{raw_input}",
    ]
    lines.extend(
        f"- {FEATURE_LABELS[feature]}：`{evidence}`"
        for feature, evidence in features.items()
    )
    lines.extend(
        [
            "",
            "## 规则推断",
            "",
            f"- 规则库：`{ruleset_id}`",
            f"- 命中规则：`{match.rule_id}`",
            f"- 候选故障：{match.candidate_fault}",
            f"- 风险等级：{match.risk_level}",
            f"- 匹配证据：{'、'.join(match.matched_conditions)}",
            f"- 未满足条件：{'、'.join(match.missing_conditions) or '无'}",
        ]
    )
    if related_matches:
        lines.extend(["- 部分命中候选（按必要条件满足率、风险等级排序）："])
        for related in related_matches:
            lines.append(
                f"  - `{related.rule_id}` {related.candidate_fault}："
                f"满足率 {related.satisfaction:.0%}；"
                f"已满足 {'、'.join(related.matched_conditions) or '无'}；"
                f"未满足 {'、'.join(related.missing_conditions) or '无'}"
            )
    lines.extend(["", "## 下一步检查", ""])
    lines.extend(
        f"{index}. {step}"
        for index, step in enumerate(match.inspection_steps, start=1)
    )
    if follow_up_question:
        lines.extend(
            [
                "",
                "## 追问",
                "",
                f"- {follow_up_question}",
            ]
        )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 规则推断只给出待验证候选，不代表已确认根因。",
            "- 规则库覆盖范围有限，可能遗漏模糊、组合或新型故障。",
            "- 本助手仅用于离线教学与班组预检，不替代联锁系统、设备工程师或正式检修规程。",
        ]
    )
    return "\n".join(lines)


def _rank_matches(matches: Sequence[RuleMatch]) -> tuple[RuleMatch, ...]:
    return tuple(
        sorted(
            matches,
            key=lambda match: (
                -match.satisfaction,
                -RISK_ORDER.get(match.risk_level, 0),
                match.rule_id,
            ),
        )
    )


class RuleBasedPumpDiagnosisAssistant:
    def __init__(self, rules_path: Path = RULE_DATA_PATH) -> None:
        self.ruleset_id, self.rules = load_rules(rules_path)

    def diagnose(self, user_input: str) -> DiagnosisOutcome:
        raw_input = user_input.strip()
        if not raw_input:
            raise ValueError("问诊输入不能为空")
        normalized_input = _normalize_input(raw_input)
        features = _extract_features(normalized_input)
        matches = tuple(
            match
            for rule in self.rules
            if (match := _evaluate_rule(rule, features)) is not None
        )
        safety = next(
            (
                match
                for match in matches
                if match.priority == "safety" and not match.missing_conditions
            ),
            None,
        )
        if safety:
            return DiagnosisOutcome(
                status="safety_escalation",
                report=_build_report(
                    raw_input,
                    self.ruleset_id,
                    features,
                    safety,
                ),
                raw_input=raw_input,
                normalized_input=normalized_input,
                matched_rule_ids=(safety.rule_id,),
                matches=(safety,),
                follow_up_question=None,
            )
        if not matches:
            fallback_rule = next(
                rule for rule in self.rules if rule.rule_id == "PUMP-FALLBACK-001"
            )
            fallback = _evaluate_rule(fallback_rule, {"fallback": "未命中已知规则"})
            if fallback is None:
                raise ValueError("兜底规则配置无效")
            return DiagnosisOutcome(
                status="escalated",
                report=_build_report(
                    raw_input,
                    self.ruleset_id,
                    features,
                    fallback,
                ),
                raw_input=raw_input,
                normalized_input=normalized_input,
                matched_rule_ids=(fallback.rule_id,),
                matches=(fallback,),
                follow_up_question=None,
            )
        strong_matches = tuple(
            match for match in matches if match.priority == "strong"
        )
        complete_strong = tuple(
            match for match in strong_matches if not match.missing_conditions
        )
        partial_strong = tuple(
            match for match in strong_matches if match.missing_conditions
        )
        if complete_strong:
            ranked_strong = _rank_matches(strong_matches)
            primary = ranked_strong[0]
            return DiagnosisOutcome(
                status="candidate_found",
                report=_build_report(
                    raw_input,
                    self.ruleset_id,
                    features,
                    primary,
                    related_matches=ranked_strong[1:],
                ),
                raw_input=raw_input,
                normalized_input=normalized_input,
                matched_rule_ids=tuple(
                    match.rule_id for match in ranked_strong
                ),
                matches=ranked_strong,
                follow_up_question=None,
            )
        if not complete_strong and len(partial_strong) >= 2:
            conflict_rule = next(
                rule for rule in self.rules if rule.rule_id == "PUMP-CONFLICT-001"
            )
            conflict = _evaluate_rule(
                conflict_rule,
                {**features, "evidence_conflict": "多个强特征规则部分命中"},
            )
            if conflict is None:
                raise ValueError("证据冲突规则配置无效")
            ranked_partial = _rank_matches(partial_strong)
            follow_up = ranked_partial[0].follow_up_question
            return DiagnosisOutcome(
                status="needs_clarification",
                report=_build_report(
                    raw_input,
                    self.ruleset_id,
                    features,
                    conflict,
                    follow_up,
                    ranked_partial,
                ),
                raw_input=raw_input,
                normalized_input=normalized_input,
                matched_rule_ids=(
                    conflict.rule_id,
                    *(match.rule_id for match in ranked_partial),
                ),
                matches=(conflict, *ranked_partial),
                follow_up_question=follow_up,
            )
        if partial_strong:
            ranked_partial = _rank_matches(partial_strong)
            primary = ranked_partial[0]
            return DiagnosisOutcome(
                status="needs_clarification",
                report=_build_report(
                    raw_input,
                    self.ruleset_id,
                    features,
                    primary,
                    primary.follow_up_question,
                    ranked_partial[1:],
                ),
                raw_input=raw_input,
                normalized_input=normalized_input,
                matched_rule_ids=tuple(
                    match.rule_id for match in ranked_partial
                ),
                matches=ranked_partial,
                follow_up_question=primary.follow_up_question,
            )
        weak_matches = _rank_matches(
            tuple(match for match in matches if match.priority == "weak")
        )
        if weak_matches:
            primary = weak_matches[0]
            follow_up = primary.follow_up_question or primary.default_follow_up
            return DiagnosisOutcome(
                status="needs_clarification",
                report=_build_report(
                    raw_input,
                    self.ruleset_id,
                    features,
                    primary,
                    follow_up,
                    weak_matches[1:],
                ),
                raw_input=raw_input,
                normalized_input=normalized_input,
                matched_rule_ids=tuple(
                    match.rule_id for match in weak_matches
                ),
                matches=weak_matches,
                follow_up_question=follow_up,
            )
        raise ValueError("规则优先级配置无可执行路径")


def run_validation(
    cases_path: Path = DIALOGUE_CASES_PATH,
    assistant: RuleBasedPumpDiagnosisAssistant | None = None,
) -> ValidationSummary:
    payload = json.loads(_read_text(cases_path))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("dialogue_cases.json 缺少 cases 列表")
    engine = assistant or RuleBasedPumpDiagnosisAssistant()
    results = []
    fallback_count = 0
    follow_up_count = 0
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("dialogue_cases.json 用例必须是对象")
        case_id = item.get("case_id")
        user_input = item.get("input")
        expected_status = item.get("expected_status")
        expected_ids = item.get("expected_rule_ids")
        if (
            not isinstance(case_id, str)
            or not isinstance(user_input, str)
            or not isinstance(expected_status, str)
            or not isinstance(expected_ids, list)
            or not all(isinstance(rule_id, str) for rule_id in expected_ids)
        ):
            raise ValueError("dialogue_cases.json 用例字段格式错误")
        outcome = engine.diagnose(user_input)
        expected_rule_ids = tuple(expected_ids)
        passed = (
            outcome.status == expected_status
            and outcome.matched_rule_ids == expected_rule_ids
        )
        results.append(
            ValidationCaseResult(
                case_id=case_id,
                expected_status=expected_status,
                actual_status=outcome.status,
                expected_rule_ids=expected_rule_ids,
                actual_rule_ids=outcome.matched_rule_ids,
                passed=passed,
            )
        )
        fallback_count += outcome.status == "escalated"
        follow_up_count += outcome.status == "needs_clarification"
    correct_hits = sum(result.passed for result in results)
    return ValidationSummary(
        dataset_id=str(payload.get("dataset_id", "")),
        total_cases=len(results),
        correct_hits=correct_hits,
        incorrect_hits=len(results) - correct_hits,
        fallback_count=fallback_count,
        follow_up_count=follow_up_count,
        case_results=tuple(results),
    )


def _print_validation(summary: ValidationSummary) -> None:
    total = summary.total_cases or 1
    print("# 离线验证结果")
    print()
    print(f"- 数据集：`{summary.dataset_id}`")
    print(f"- 用例总数：{summary.total_cases}")
    print(
        f"- 正确命中：{summary.correct_hits}"
        f"（{summary.correct_hits / total:.1%}）"
    )
    print(
        f"- 错误命中：{summary.incorrect_hits}"
        f"（{summary.incorrect_hits / total:.1%}）"
    )
    print(
        f"- 兜底：{summary.fallback_count}"
        f"（{summary.fallback_count / total:.1%}）"
    )
    print(
        f"- 追问：{summary.follow_up_count}"
        f"（{summary.follow_up_count / total:.1%}）"
    )
    failures = tuple(result for result in summary.case_results if not result.passed)
    if failures:
        print()
        print("## 未通过用例")
        print()
        for result in failures:
            print(
                f"- `{result.case_id}`：期望 {result.expected_status}/"
                f"{','.join(result.expected_rule_ids)}，实际 "
                f"{result.actual_status}/{','.join(result.actual_rule_ids)}"
            )


def _print_outcome(outcome: DiagnosisOutcome, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(asdict(outcome), ensure_ascii=False, indent=2))
    else:
        print(outcome.report)


def _interactive_loop(
    assistant: RuleBasedPumpDiagnosisAssistant,
    output_format: str,
) -> int:
    print("规则驱动的泵组故障问诊助手（输入 quit/exit/退出 结束）")
    while True:
        try:
            user_input = input("操作员：").strip()
        except EOFError:
            print()
            return 0
        if user_input.lower() in {"quit", "exit", "退出"}:
            return 0
        if not user_input:
            print("请输入泵组现象或测量值。")
            continue
        outcome = assistant.diagnose(user_input)
        _print_outcome(outcome, output_format)
        if outcome.status != "needs_clarification" or not outcome.follow_up_question:
            continue
        try:
            answer = input("\n补充：").strip()
        except EOFError:
            print()
            return 0
        if not answer or answer.lower() in {"quit", "exit", "退出"}:
            continue
        updated = assistant.diagnose(f"{user_input}；{answer}")
        _print_outcome(updated, output_format)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行规则驱动的泵组故障问诊助手"
    )
    parser.add_argument("question", nargs="?", help="泵组现象或结构化测量值")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入交互问诊，每个问题最多追问一次",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="运行 20 条离线对话用例并输出统计",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
        help="单次或交互问诊输出格式",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.validate and (args.question or args.interactive):
        parser.error("--validate 不能与问题或 --interactive 同时使用")
    if args.question and args.interactive:
        parser.error("问题参数不能与 --interactive 同时使用")
    try:
        if args.validate:
            summary = run_validation()
            _print_validation(summary)
            return 0 if summary.incorrect_hits == 0 else 1
        assistant = RuleBasedPumpDiagnosisAssistant()
        if args.interactive or not args.question:
            return _interactive_loop(assistant, args.output_format)
        outcome = assistant.diagnose(args.question)
        _print_outcome(outcome, args.output_format)
        return 0 if outcome.status in {
            "candidate_found",
            "safety_escalation",
        } else 2
    except (json.JSONDecodeError, OSError, ValueError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
