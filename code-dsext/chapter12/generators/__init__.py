from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from human_review import normalize_judge_review


def generate_case_candidate(
    registry: Mapping[str, Any],
    *,
    template_id: str,
    candidate_id: str,
    fault_mechanism: str,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    template = _approved_template(registry, template_id)
    mechanisms = template.get("approved_fault_mechanisms")
    if not isinstance(mechanisms, list) or fault_mechanism not in mechanisms:
        raise ValueError(f"故障机制未获批准：{fault_mechanism}")
    rules = template.get("parameter_rules")
    if not isinstance(rules, Mapping):
        raise ValueError("模板缺少 parameter_rules")
    if not isinstance(parameters, Mapping):
        raise TypeError("parameters 必须是对象")
    provided = set(parameters)
    expected = set(rules)
    if provided != expected:
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        raise ValueError(
            "模板参数不完整："
            f"缺少={','.join(missing) or '-'}；"
            f"多余={','.join(extra) or '-'}"
        )
    checked_parameters = {
        name: _validate_parameter(name, parameters[name], rule)
        for name, rule in rules.items()
    }
    prompt_template = template.get("prompt_template")
    if not isinstance(prompt_template, str) or not prompt_template:
        raise ValueError("模板缺少 prompt_template")
    try:
        prompt = prompt_template.format(**checked_parameters)
    except (KeyError, ValueError) as error:
        raise ValueError("模板无法使用已批准参数渲染") from error
    candidate = {
        "schema_version": "1.0",
        "candidate_id": _non_empty_text(candidate_id, "candidate_id"),
        "candidate_version": "1.0.0",
        "case_version": _non_empty_text(
            template.get("template_version"),
            "template_version",
        ),
        "answer_version": _non_empty_text(
            template.get("answer_version"),
            "answer_version",
        ),
        "template_id": template["template_id"],
        "registry_version": _non_empty_text(
            registry.get("registry_version"),
            "registry_version",
        ),
        "source": deepcopy(template.get("source")),
        "level": template.get("level"),
        "device_type": template.get("device_type"),
        "risk_level": template.get("risk_level"),
        "fault_mechanism": fault_mechanism,
        "parameters": checked_parameters,
        "parameter_rules_snapshot": deepcopy(dict(rules)),
        "prompt": prompt,
        "generation_constraints": {
            "approved_template": True,
            "approved_fault_mechanism": True,
            "parameters_in_range": True,
        },
        "review_status": "pending_expert_review",
    }
    candidate["candidate_fingerprint"] = _candidate_fingerprint(candidate)
    return candidate


def screen_case_candidate(
    candidate: Mapping[str, Any],
    *,
    existing_fingerprints: set[str],
    training_prompts: Sequence[str],
    judge_review: Mapping[str, Any],
) -> dict[str, Any]:
    screened = deepcopy(dict(candidate))
    fingerprint = screened.get("candidate_fingerprint")
    duplicate = fingerprint in existing_fingerprints
    structure_consistent = _candidate_structure_consistent(screened)
    numeric_consistent = _candidate_numeric_consistent(screened)
    training_similarity = _maximum_prompt_similarity(
        str(screened.get("prompt", "")),
        training_prompts,
    )
    too_similar = training_similarity >= 0.9
    screened["quality_checks"] = {
        "structure_consistent": structure_consistent,
        "numeric_consistent": numeric_consistent,
        "duplicate": duplicate,
        "training_similarity": training_similarity,
        "similarity_method": "normalized_token_jaccard",
        "training_prompt_count": len(training_prompts),
        "training_similarity_passed": not too_similar,
    }
    screened["judge_review"] = normalize_judge_review(judge_review)
    screened["review_status"] = (
        "rejected_by_deterministic_checks"
        if duplicate
        or too_similar
        or not structure_consistent
        or not numeric_consistent
        else "pending_expert_review"
    )
    return screened


def _approved_template(
    registry: Mapping[str, Any],
    template_id: str,
) -> dict[str, Any]:
    templates = registry.get("templates")
    if not isinstance(templates, list):
        raise ValueError("模板注册表缺少 templates")
    matches = [
        template
        for template in templates
        if isinstance(template, Mapping)
        and template.get("template_id") == template_id
    ]
    if len(matches) != 1:
        raise ValueError(f"模板不存在或不唯一：{template_id}")
    template = dict(matches[0])
    if template.get("status") != "approved":
        raise ValueError(f"模板尚未批准：{template_id}")
    for field in ("level", "device_type", "risk_level", "source"):
        if field not in template:
            raise ValueError(f"模板缺少字段：{field}")
    return template


def _validate_parameter(
    name: str,
    value: Any,
    rule_value: Any,
) -> Any:
    if not isinstance(rule_value, Mapping):
        raise ValueError(f"{name} 的参数规则必须是对象")
    expected_type = rule_value.get("type")
    if expected_type == "string":
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} 必须是非空字符串")
    elif expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} 必须是整数")
    elif expected_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} 必须是数值")
    else:
        raise ValueError(f"{name} 的类型规则不受支持：{expected_type}")
    allowed = rule_value.get("allowed")
    if allowed is not None:
        if not isinstance(allowed, list) or value not in allowed:
            raise ValueError(f"{name} 不在批准枚举范围内")
    if "min" in rule_value and value < rule_value["min"]:
        raise ValueError(f"{name} 低于批准下限")
    if "max" in rule_value and value > rule_value["max"]:
        raise ValueError(f"{name} 高于批准上限")
    return value


def _fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _candidate_fingerprint(value: Mapping[str, Any]) -> str:
    return _fingerprint(
        {
            key: item
            for key, item in value.items()
            if key != "candidate_fingerprint"
        }
    )


def _candidate_structure_consistent(value: Mapping[str, Any]) -> bool:
    required = {
        "schema_version",
        "candidate_id",
        "candidate_version",
        "case_version",
        "answer_version",
        "template_id",
        "registry_version",
        "source",
        "level",
        "device_type",
        "risk_level",
        "fault_mechanism",
        "parameters",
        "parameter_rules_snapshot",
        "prompt",
        "generation_constraints",
        "review_status",
        "candidate_fingerprint",
    }
    if not required.issubset(value):
        return False
    constraints = value.get("generation_constraints")
    if not isinstance(constraints, Mapping) or any(
        constraints.get(field) is not True
        for field in (
            "approved_template",
            "approved_fault_mechanism",
            "parameters_in_range",
        )
    ):
        return False
    return value.get("candidate_fingerprint") == _candidate_fingerprint(value)


def _candidate_numeric_consistent(value: Mapping[str, Any]) -> bool:
    parameters = value.get("parameters")
    rules = value.get("parameter_rules_snapshot")
    if not isinstance(parameters, Mapping) or not isinstance(rules, Mapping):
        return False
    if set(parameters) != set(rules):
        return False
    try:
        for name, rule in rules.items():
            _validate_parameter(str(name), parameters[name], rule)
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _maximum_prompt_similarity(
    prompt: str,
    training_prompts: Sequence[str],
) -> float:
    if isinstance(training_prompts, (str, bytes)):
        raise TypeError("training_prompts 必须是字符串数组")
    if any(not isinstance(item, str) for item in training_prompts):
        raise TypeError("training_prompts 必须是字符串数组")
    prompt_tokens = _tokens(prompt)
    similarities = [
        _jaccard(prompt_tokens, _tokens(training_prompt))
        for training_prompt in training_prompts
    ]
    return round(max(similarities, default=0.0), 6)


def _tokens(value: str) -> set[str]:
    return set(
        re.findall(
            r"[a-z0-9_.-]+|[\u4e00-\u9fff]",
            value.casefold(),
        )
    )


def _jaccard(first: set[str], second: set[str]) -> float:
    if not first and not second:
        return 1.0
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()


__all__ = ["generate_case_candidate", "screen_case_candidate"]
