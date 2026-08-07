from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping


REVIEW_DECISIONS = frozenset({"approved", "modified", "rejected"})
JUDGE_DECISIONS = frozenset({"pass", "needs_revision", "reject"})


def apply_human_review(
    candidate: Mapping[str, Any],
    *,
    judge_review: Mapping[str, Any],
    decision: str,
    reviewer_id: str,
    rationale: str,
    reviewed_at: str,
    modifications: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"不支持的专家审核决定：{decision}")
    reviewer = _non_empty_text(reviewer_id, "reviewer_id")
    reason = _non_empty_text(rationale, "rationale")
    timestamp = _non_empty_text(reviewed_at, "reviewed_at")
    reviewed = deepcopy(dict(candidate))
    if modifications is not None:
        if not isinstance(modifications, Mapping):
            raise TypeError("modifications 必须是对象")
        reviewed.update(deepcopy(dict(modifications)))
    normalized_judge = normalize_judge_review(judge_review)
    reviewed["judge_review"] = normalized_judge
    final_status = "approved" if decision in {"approved", "modified"} else "rejected"
    judge_overridden = (
        final_status == "approved"
        and normalized_judge["decision"] != "pass"
    ) or (
        final_status == "rejected"
        and normalized_judge["decision"] == "pass"
    )
    reviewed["expert_review"] = {
        "status": final_status,
        "decision": decision,
        "reviewer_id": reviewer,
        "reviewed_at": timestamp,
        "rubric_version": "industrial-eval-review-1.0",
        "rationale": reason,
        "judge_overridden": judge_overridden,
    }
    reviewed["review_status"] = final_status
    reviewed["expert_review"]["input_hash"] = review_input_hash(reviewed)
    return reviewed


def validate_expert_review(record: Mapping[str, Any]) -> None:
    review = record.get("expert_review")
    if not isinstance(review, Mapping):
        raise ValueError("缺少 expert_review")
    status = review.get("status")
    if status not in {"approved", "rejected"}:
        raise ValueError("专家审核状态无效")
    for field in (
        "decision",
        "reviewer_id",
        "reviewed_at",
        "rubric_version",
        "rationale",
        "input_hash",
    ):
        _non_empty_text(review.get(field), f"expert_review.{field}")
    if type(review.get("judge_overridden")) is not bool:
        raise ValueError("expert_review.judge_overridden 必须是布尔值")
    expected_hash = review_input_hash(record)
    if review.get("input_hash") != expected_hash:
        raise ValueError("专家审核输入哈希不匹配")
    judge = record.get("judge_review")
    if not isinstance(judge, Mapping) or judge.get("advisory_only") is not True:
        raise ValueError("LLM Judge 记录必须保留且仅作建议")


def review_input_hash(record: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in deepcopy(dict(record)).items()
        if key != "expert_review"
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def normalize_judge_review(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("judge_review 必须是对象")
    decision = value.get("decision")
    if decision not in JUDGE_DECISIONS:
        raise ValueError(f"不支持的 Judge 决定：{decision}")
    score = value.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("judge_review.score 必须是数值")
    if not 0 <= float(score) <= 1:
        raise ValueError("judge_review.score 必须位于 0 到 1")
    return {
        "decision": decision,
        "score": float(score),
        "reason": _non_empty_text(value.get("reason"), "judge_review.reason"),
        "judge_id": _non_empty_text(
            value.get("judge_id"),
            "judge_review.judge_id",
        ),
        "advisory_only": True,
    }


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()


__all__ = [
    "apply_human_review",
    "normalize_judge_review",
    "review_input_hash",
    "validate_expert_review",
]
