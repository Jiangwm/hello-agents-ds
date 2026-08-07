from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
import unicodedata


REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "industrial_problem",
        "equipment_scope",
        "allowed_read_only_tools",
        "required_evidence",
        "forbidden_inferences",
        "reference_tool_calls",
        "observations",
        "reference_report",
        "allowed_report_actions",
        "allowed_diagnoses",
        "difficulty",
        "risk",
        "source",
        "expert_review",
        "split",
        "leakage_group",
    }
)
REVIEW_FIELDS = frozenset(
    {"status", "role", "reviewer_id", "reviewed_at", "rubric_version", "input_hash"}
)
TOOL_CALL_FIELDS = frozenset({"tool_call_id", "tool", "parameters"})
OBSERVATION_FIELDS = frozenset(
    {"evidence_id", "tool_call_id", "source_window", "value", "unit", "content_hash"}
)
REPORT_FIELDS = frozenset(
    {
        "facts",
        "hypotheses",
        "unknowns",
        "diagnosis_ranking",
        "confidence",
        "evidence_ids",
        "safety_notice",
        "recommended_actions",
    }
)
VALID_SPLITS = frozenset({"train", "validation", "blind_test", "high_risk_holdout"})
HIGH_RISK_LEVELS = frozenset({"high", "critical"})
EXPORTABLE_RISK_LEVELS = frozenset({"low", "medium"})
APPROVED_REVIEW_STATUS = "approved"
APPROVED_READ_ONLY_TOOLS = frozenset(
    {
        "query_desensitized_trend",
        "read_alarm_history",
        "read_lab_result",
        "read_maintenance_log",
        "read_operator_log",
        "read_safety_note",
        "read_shift_note",
    }
)
APPROVED_REPORT_ACTIONS = frozenset(
    {
        ("advisory", "提交维修人员复核联轴器和轴承状态。"),
        ("advisory", "安排维修人员人工检查联轴器和轴承。"),
        ("advisory", "申请补充只读压降历史数据。"),
        ("advisory", "申请读取历史压降数据供人工复核。"),
        ("advisory", "仅保存观察并等待专家审核。"),
        ("advisory", "提交领域专家人工审核当前观察。"),
        ("advisory", "请求仪表校验历史。"),
        ("advisory", "申请补充仪表历史校验记录。"),
        ("advisory", "补充物料批次的只读属性。"),
        ("advisory", "申请补充只读物料批次信息。"),
        ("escalate", "升级安全专业人员人工审核。"),
        ("escalate", "提交安全工程师人工复核。"),
        ("escalate", "通知人工核验现场状态。"),
        ("escalate", "升级值班工程师开展现场核验。"),
    }
)
CONTROL_TOOL_PREFIXES = (
    "set_",
    "write_",
    "update_",
    "delete_",
    "start_",
    "stop_",
    "shutdown_",
    "restart_",
    "open_",
    "close_",
    "command_",
    "control_",
    "actuate_",
)
CONTROL_TOOL_MARKERS = (
    "open_valve",
    "close_valve",
    "plc_",
    "dcs_",
    "plc_write",
    "force_output",
    "production_control",
)


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON at line {line_number}") from error
            _validate_case_shape(case, line_number)
            _validate_review_hash(case)
            cases.append(case)
    validate_split_integrity(cases)
    return cases


def validate_split_integrity(cases: Sequence[Mapping[str, Any]]) -> None:
    case_ids: set[str] = set()
    seen_fingerprints: dict[str, dict[str, str]] = {
        "leakage_group": {},
        "industrial_problem": {},
        "reference_tool_calls": {},
        "reference_report": {},
    }
    for position, case in enumerate(cases, start=1):
        _validate_case_shape(case, position)
        case_id = case["case_id"]
        if case_id in case_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        case_ids.add(case_id)
        split = case["split"]
        if split not in VALID_SPLITS:
            raise ValueError(f"unsupported split for {case_id}: {split}")
        if case["risk"] in HIGH_RISK_LEVELS and split != "high_risk_holdout":
            raise ValueError(
                "high and critical cases must use high_risk_holdout split"
            )
        for field, value in _leakage_values(case).items():
            fingerprint = _fingerprint(value)
            previous_split = seen_fingerprints[field].setdefault(fingerprint, split)
            if previous_split != split:
                raise ValueError(
                    f"{field} fingerprint appears in both "
                    f"{previous_split} and {split}"
                )


def build_sft_records(
    cases: Sequence[Mapping[str, Any]], split: str = "train"
) -> list[dict[str, Any]]:
    return [_build_sft_record(case) for case in _exportable_cases(cases, split)]


def build_grpo_records(
    cases: Sequence[Mapping[str, Any]], split: str = "train"
) -> list[dict[str, Any]]:
    return [
        {
            "prompt": _render_prompt(case),
            "case_id": case["case_id"],
            "schema_version": case["schema_version"],
            "metadata": _metadata(case),
        }
        for case in _exportable_cases(cases, split)
    ]


def _exportable_cases(
    cases: Sequence[Mapping[str, Any]], split: str
) -> list[Mapping[str, Any]]:
    validate_split_integrity(cases)
    selected: list[Mapping[str, Any]] = []
    for case in cases:
        if case["split"] != split or case["risk"] not in EXPORTABLE_RISK_LEVELS:
            continue
        if case["expert_review"]["status"] != APPROVED_REVIEW_STATUS:
            continue
        _validate_review_hash(case)
        selected.append(case)
    return selected


def _build_sft_record(case: Mapping[str, Any]) -> dict[str, Any]:
    prompt = _render_prompt(case)
    tool_calls = [
        {
            "id": call["tool_call_id"],
            "type": "function",
            "function": {
                "name": call["tool"],
                "arguments": _canonical_json(call["parameters"]),
            },
        }
        for call in case["reference_tool_calls"]
    ]
    observations_by_call = {
        observation["tool_call_id"]: observation
        for observation in case["observations"]
    }
    final_report = _canonical_json(case["reference_report"])
    messages = [
        {
            "role": "system",
            "content": "你是工业诊断教学助手。仅调用允许的只读工具，并以证据区分事实、假设和未知项。",
        },
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": "", "tool_calls": tool_calls},
        *[
            {
                "role": "tool",
                "tool_call_id": call["tool_call_id"],
                "content": _canonical_json(observations_by_call[call["tool_call_id"]]),
            }
            for call in case["reference_tool_calls"]
        ],
        {"role": "assistant", "content": final_report},
    ]
    return {
        "messages": messages,
        "prompt": prompt,
        "completion": final_report,
        "text": "\n".join(message["content"] for message in messages),
        "metadata": _metadata(case),
    }


def _validate_case_shape(case: Any, line_number: int) -> None:
    if not isinstance(case, Mapping):
        raise ValueError(f"case at line {line_number} must be an object")
    missing_fields = REQUIRED_FIELDS.difference(case)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"missing required fields at line {line_number}: {missing}")
    _validate_non_empty_string(case, "schema_version", line_number)
    _validate_non_empty_string(case, "case_id", line_number)
    _validate_non_empty_string(case, "industrial_problem", line_number)
    _validate_non_empty_string(case, "leakage_group", line_number)
    if not isinstance(case["equipment_scope"], Mapping):
        raise ValueError(f"equipment_scope at line {line_number} must be an object")
    _validate_non_empty_list(case, "allowed_read_only_tools", line_number)
    for tool in case["allowed_read_only_tools"]:
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(
                f"allowed_read_only_tools at line {line_number} must contain names"
            )
        if _is_production_control_tool(tool):
            raise ValueError(
                f"production control tool is prohibited at line {line_number}: {tool}"
            )
        if tool not in APPROVED_READ_ONLY_TOOLS:
            raise ValueError(
                f"tool is not in the approved read-only catalog at line "
                f"{line_number}: {tool}"
            )
    _validate_non_empty_list(case, "required_evidence", line_number)
    _validate_non_empty_list(case, "forbidden_inferences", line_number)
    _validate_non_empty_list(case, "reference_tool_calls", line_number)
    _validate_non_empty_list(case, "observations", line_number)
    _validate_non_empty_list(case, "allowed_diagnoses", line_number)
    _validate_non_empty_list(case, "allowed_report_actions", line_number)
    _validate_review(case, line_number)
    allowed_report_actions = _validate_allowed_report_actions(case, line_number)
    tool_call_ids = _validate_tool_calls(case, line_number)
    evidence_ids = _validate_observations(case, line_number, tool_call_ids)
    _validate_report(
        case,
        line_number,
        evidence_ids,
        allowed_report_actions,
    )


def _validate_review(case: Mapping[str, Any], line_number: int) -> None:
    review = case["expert_review"]
    if not isinstance(review, Mapping) or REVIEW_FIELDS.difference(review):
        raise ValueError(f"expert_review at line {line_number} is incomplete")
    for field in REVIEW_FIELDS:
        if not isinstance(review[field], str) or not review[field]:
            raise ValueError(f"expert_review.{field} at line {line_number} must be set")


def _validate_tool_calls(case: Mapping[str, Any], line_number: int) -> set[str]:
    tool_call_ids: set[str] = set()
    for call in case["reference_tool_calls"]:
        if not isinstance(call, Mapping) or TOOL_CALL_FIELDS.difference(call):
            raise ValueError(
                f"reference_tool_calls at line {line_number} require tool_call_id, tool and parameters"
            )
        if call["tool"] not in case["allowed_read_only_tools"]:
            raise ValueError(f"tool at line {line_number} is not in the allowlist")
        if not isinstance(call["tool_call_id"], str) or not call["tool_call_id"]:
            raise ValueError(f"tool_call_id at line {line_number} must be set")
        if not isinstance(call["parameters"], Mapping):
            raise ValueError(f"tool parameters at line {line_number} must be an object")
        if call["tool_call_id"] in tool_call_ids:
            raise ValueError(f"duplicate tool_call_id at line {line_number}")
        tool_call_ids.add(call["tool_call_id"])
    return tool_call_ids


def _is_production_control_tool(tool: str) -> bool:
    normalised = tool.strip().casefold()
    return normalised.startswith(CONTROL_TOOL_PREFIXES) or any(
        marker in normalised for marker in CONTROL_TOOL_MARKERS
    )


def _validate_observations(
    case: Mapping[str, Any], line_number: int, tool_call_ids: set[str]
) -> set[str]:
    observation_call_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for observation in case["observations"]:
        if not isinstance(observation, Mapping) or OBSERVATION_FIELDS.difference(observation):
            raise ValueError(f"observations at line {line_number} are incomplete")
        evidence_id = observation["evidence_id"]
        tool_call_id = observation["tool_call_id"]
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError(f"evidence_id at line {line_number} must be set")
        if evidence_id in evidence_ids:
            raise ValueError(f"duplicate evidence_id at line {line_number}")
        if tool_call_id not in tool_call_ids or tool_call_id in observation_call_ids:
            raise ValueError(f"tool calls and observations must bind one-to-one at line {line_number}")
        if observation["content_hash"] != _observation_hash(observation):
            raise ValueError(f"observation content_hash mismatch at line {line_number}")
        evidence_ids.add(evidence_id)
        observation_call_ids.add(tool_call_id)
    if observation_call_ids != tool_call_ids:
        raise ValueError(f"tool calls and observations must bind one-to-one at line {line_number}")
    return evidence_ids


def _validate_report(
    case: Mapping[str, Any],
    line_number: int,
    evidence_ids: set[str],
    allowed_report_actions: set[tuple[str, str]],
) -> None:
    report = case["reference_report"]
    if not isinstance(report, Mapping) or REPORT_FIELDS.difference(report):
        raise ValueError(f"reference_report at line {line_number} is incomplete")
    if not isinstance(report["evidence_ids"], list) or not set(report["evidence_ids"]).issubset(evidence_ids):
        raise ValueError(f"reference_report evidence_ids at line {line_number} are invalid")
    if not isinstance(report["confidence"], (int, float)) or not 0 <= report["confidence"] <= 1:
        raise ValueError(f"reference_report confidence at line {line_number} is invalid")
    if not isinstance(report["diagnosis_ranking"], list) or not report["diagnosis_ranking"]:
        raise ValueError(f"reference_report diagnosis_ranking at line {line_number} is invalid")
    for diagnosis in report["diagnosis_ranking"]:
        if not isinstance(diagnosis, Mapping) or diagnosis.get("diagnosis") not in case["allowed_diagnoses"]:
            raise ValueError(f"reference_report diagnosis_ranking at line {line_number} is invalid")
    if not isinstance(report["recommended_actions"], list) or not report["recommended_actions"]:
        raise ValueError(f"reference_report recommended_actions at line {line_number} are invalid")
    for action in report["recommended_actions"]:
        if not isinstance(action, Mapping) or action.get("type") not in {"advisory", "escalate"}:
            raise ValueError(f"reference_report actions at line {line_number} must be advisory or escalate")
        if set(action) != {"type", "content"}:
            raise ValueError(
                f"reference_report actions at line {line_number} must contain "
                "exactly type and content"
            )
        content = action.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                f"reference_report action content at line {line_number} is invalid"
            )
        action_pair = (action["type"], content)
        if action_pair not in APPROVED_REPORT_ACTIONS:
            raise ValueError(
                f"reference_report action is not in the approved safety catalog "
                f"at line {line_number}"
            )
        if action_pair not in allowed_report_actions:
            raise ValueError(
                f"reference_report action is not allowed for this case at line "
                f"{line_number}"
            )
        if case["risk"] in HIGH_RISK_LEVELS and action["type"] != "escalate":
            raise ValueError(
                f"high-risk reference_report actions must escalate at line "
                f"{line_number}"
            )


def _validate_allowed_report_actions(
    case: Mapping[str, Any], line_number: int
) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for action in case["allowed_report_actions"]:
        if not isinstance(action, Mapping) or set(action) != {"type", "content"}:
            raise ValueError(
                f"allowed_report_actions at line {line_number} must contain "
                "exactly type and content"
            )
        action_type = action.get("type")
        content = action.get("content")
        if not isinstance(action_type, str) or not isinstance(content, str):
            raise ValueError(
                f"allowed_report_actions at line {line_number} are invalid"
            )
        action_pair = (action_type, content)
        if action_pair not in APPROVED_REPORT_ACTIONS:
            raise ValueError(
                f"allowed_report_actions at line {line_number} contain an "
                "unapproved safety action"
            )
        if case["risk"] in HIGH_RISK_LEVELS and action_type != "escalate":
            raise ValueError(
                f"high-risk allowed_report_actions must escalate at line "
                f"{line_number}"
            )
        allowed.add(action_pair)
    if len(allowed) < 2:
        raise ValueError(
            f"allowed_report_actions at line {line_number} must contain at "
            "least two distinct expert-reviewed actions"
        )
    return allowed


def _validate_review_hash(case: Mapping[str, Any]) -> None:
    if case["expert_review"]["input_hash"] != _input_hash(case):
        raise ValueError(f"expert_review input_hash mismatch for {case['case_id']}")


def _leakage_values(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "leakage_group": case["leakage_group"],
        "industrial_problem": case["industrial_problem"],
        "reference_tool_calls": case["reference_tool_calls"],
        "reference_report": case["reference_report"],
    }


def _render_prompt(case: Mapping[str, Any]) -> str:
    return "\n".join(
        (
            f"工业问题：{case['industrial_problem']}",
            f"设备范围：{_canonical_json(case['equipment_scope'])}",
            f"允许只读工具：{', '.join(case['allowed_read_only_tools'])}",
            f"必需证据：{'; '.join(case['required_evidence'])}",
            f"禁止推断：{'; '.join(case['forbidden_inferences'])}",
        )
    )


def _metadata(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "split": case["split"],
        "risk": case["risk"],
        "difficulty": case["difficulty"],
        "source": case["source"],
        "review_status": case["expert_review"]["status"],
        "reviewer_role": case["expert_review"]["role"],
        "rubric_version": case["expert_review"]["rubric_version"],
        "input_hash": case["expert_review"]["input_hash"],
    }


def _validate_non_empty_list(case: Mapping[str, Any], field: str, line_number: int) -> None:
    if not isinstance(case[field], list) or not case[field]:
        raise ValueError(f"{field} at line {line_number} must be a non-empty list")


def _validate_non_empty_string(case: Mapping[str, Any], field: str, line_number: int) -> None:
    if not isinstance(case[field], str) or not case[field]:
        raise ValueError(f"{field} at line {line_number} must be a non-empty string")


def _input_hash(case: Mapping[str, Any]) -> str:
    return _fingerprint({key: value for key, value in case.items() if key != "expert_review"})


def _observation_hash(observation: Mapping[str, Any]) -> str:
    return _fingerprint(
        {key: value for key, value in observation.items() if key != "content_hash"}
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(_normalise(value)).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value
