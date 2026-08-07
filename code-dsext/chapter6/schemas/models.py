from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


EVIDENCE_STATUSES = {"success", "insufficient", "blocked", "error"}
HYPOTHESIS_STATUSES = {"candidate", "excluded"}
HUMAN_DECISIONS = {"approve", "defer", "reject"}


class FrozenMapping(Mapping[str, object]):
    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, object]) -> dict[str, object]:
        return _json_value(self)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} 不能为空")


def _require_confidence(value: float, field_name: str = "confidence") -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} 必须在 0 到 1 之间")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return FrozenMapping({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, frozenset):
        return [_json_value(item) for item in sorted(value, key=repr)]
    return value


def validate_evidence_payload(
    *,
    status: str,
    query: str,
    data_range: str,
    source_files: tuple[str, ...],
    data: Mapping[str, object],
    error: str | None,
) -> None:
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"未知证据状态：{status}")
    if not isinstance(data, Mapping):
        raise ValueError("data 必须是映射")
    if error is not None and not error.strip():
        raise ValueError("error 不能为空字符串")
    if status == "error" and error is None:
        raise ValueError("error 状态必须提供 error")
    if status != "error" and error is not None:
        raise ValueError("仅 error 状态可提供 error")
    if status in {"success", "insufficient"}:
        _require_text(query, "query")
        _require_text(data_range, "data_range")
        if not source_files or any(not item.strip() for item in source_files):
            raise ValueError("success 或 insufficient 证据必须提供 source_files")


@dataclass(frozen=True)
class AnalysisTask:
    task_id: str
    focus: str
    normal_batch_ids: tuple[str, ...]
    abnormal_batch_ids: tuple[str, ...]
    scenario: str = "default"

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.focus, "focus")
        if not self.normal_batch_ids or not self.abnormal_batch_ids:
            raise ValueError("正常组和异常组批次均不能为空")
        if set(self.normal_batch_ids) & set(self.abnormal_batch_ids):
            raise ValueError("正常组和异常组批次不能重叠")


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_agent: str
    tool_name: str
    status: str
    claim: str
    query: str
    data_range: str
    source_files: tuple[str, ...]
    data: Mapping[str, object]
    error: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_text(self.source_agent, "source_agent")
        _require_text(self.tool_name, "tool_name")
        _require_text(self.claim, "claim")
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
        object.__setattr__(self, "data", _freeze(self.data))
        object.__setattr__(self, "limitations", limitations)

    @property
    def metrics(self) -> Mapping[str, object]:
        return self.data

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "source_agent": self.source_agent,
            "tool_name": self.tool_name,
            "status": self.status,
            "claim": self.claim,
            "query": self.query,
            "data_range": self.data_range,
            "source_files": list(self.source_files),
            "data": _json_value(self.data),
            "error": self.error,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ToolExecution:
    evidence: Evidence
    is_new: bool


@dataclass(frozen=True)
class AgentMessage:
    task_id: str
    sender: str
    recipient: str
    evidence_ids: tuple[str, ...]
    claim: str
    confidence: float
    open_questions: tuple[str, ...]
    message_type: str = "finding"

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.sender, "sender")
        _require_text(self.recipient, "recipient")
        _require_text(self.claim, "claim")
        _require_confidence(self.confidence)


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    title: str
    claim: str
    owners: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    confidence: float
    open_questions: tuple[str, ...]
    falsification_test: str
    status: str = "candidate"

    def __post_init__(self) -> None:
        _require_text(self.hypothesis_id, "hypothesis_id")
        _require_text(self.title, "title")
        _require_text(self.claim, "claim")
        _require_text(self.falsification_test, "falsification_test")
        _require_confidence(self.confidence)
        if self.status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"未知假设状态：{self.status}")
        if not self.evidence_ids:
            raise ValueError("候选假设必须引用至少一个 evidence_id")


@dataclass(frozen=True)
class ReviewScore:
    hypothesis_id: str
    evidence_completeness: float
    temporal_consistency: float
    falsifiability: float
    total_score: float
    evidence_ids: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("evidence_completeness", self.evidence_completeness),
            ("temporal_consistency", self.temporal_consistency),
            ("falsifiability", self.falsifiability),
            ("total_score", self.total_score),
        ):
            _require_confidence(value, field_name)
        if not self.evidence_ids:
            raise ValueError("审核评分必须引用证据，不能以投票替代证据")


@dataclass(frozen=True)
class MissingDataRequest:
    request_id: str
    description: str
    reason: str
    related_hypothesis_ids: tuple[str, ...]
    required_approval: str


@dataclass(frozen=True)
class ExperimentPlan:
    plan_id: str
    hypothesis_id: str
    evidence_ids: tuple[str, ...]
    objective: str
    steps: tuple[str, ...]
    success_criteria: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    approved: bool = False


@dataclass(frozen=True)
class HumanDecision:
    decision: str
    approver_role: str
    scope: str
    note: str

    def __post_init__(self) -> None:
        if self.decision not in HUMAN_DECISIONS:
            raise ValueError(f"未知人工决策：{self.decision}")
        _require_text(self.approver_role, "approver_role")
        _require_text(self.scope, "scope")


@dataclass(frozen=True)
class AgentReport:
    message: AgentMessage
    evidence: tuple[Evidence, ...]
    hypotheses: tuple[Hypothesis, ...]
    new_evidence_count: int


@dataclass(frozen=True)
class WorkflowConfig:
    review_threshold: float = 0.72
    max_rounds: int = 3
    max_tool_calls: int = 12
    min_new_evidence: int = 1

    def __post_init__(self) -> None:
        _require_confidence(self.review_threshold, "review_threshold")
        if not 1 <= self.max_rounds <= 10:
            raise ValueError("max_rounds 必须在 1 到 10 之间")
        if not 1 <= self.max_tool_calls <= 50:
            raise ValueError("max_tool_calls 必须在 1 到 50 之间")
        if not 0 <= self.min_new_evidence <= 10:
            raise ValueError("min_new_evidence 必须在 0 到 10 之间")


@dataclass(frozen=True)
class WorkflowRun:
    framework: str
    status: str
    task: AnalysisTask
    messages: tuple[AgentMessage, ...]
    evidence: tuple[Evidence, ...]
    hypotheses: tuple[Hypothesis, ...]
    reviews: tuple[ReviewScore, ...]
    missing_data: tuple[MissingDataRequest, ...]
    experiment_plan: ExperimentPlan | None
    human_decision: HumanDecision
    trace: tuple[str, ...]
    tool_call_count: int
    rounds: int
    report: str


def build_demo_task(scenario: str = "default") -> AnalysisTask:
    if scenario == "default":
        return AnalysisTask(
            task_id="QRA-P600-20260726",
            focus="P-600 产品尺寸均值上移且一次合格率下降",
            normal_batch_ids=("BATCH-N001", "BATCH-N002"),
            abnormal_batch_ids=("BATCH-A001", "BATCH-A002"),
            scenario=scenario,
        )
    if scenario == "insufficient":
        return AnalysisTask(
            task_id="QRA-P601-20260727",
            focus="P-601 产品尺寸均值疑似漂移但样本不足",
            normal_batch_ids=("BATCH-N003",),
            abnormal_batch_ids=("BATCH-A003",),
            scenario=scenario,
        )
    raise ValueError(f"未知演示场景：{scenario}")
