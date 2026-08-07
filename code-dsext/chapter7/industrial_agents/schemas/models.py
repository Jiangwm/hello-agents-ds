"""工业数据分析智能体的纯标准库数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from industrial_agents.exceptions import ConfigurationError


def utc_now() -> datetime:
    """返回带时区信息的 UTC 当前时间。"""
    return datetime.now(timezone.utc)


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区信息")
    return value.astimezone(timezone.utc)


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")


def _to_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ValueError("字符串列表不能包含空值")
    return result


def _to_frozenset(
    values: frozenset[str] | set[str] | tuple[str, ...] | list[str],
) -> frozenset[str]:
    result = frozenset(values)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ValueError("字符串集合不能包含空值")
    return result


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return _utc_timestamp(value).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


class MessageRole(str, Enum):
    """对话消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RiskLevel(str, Enum):
    """工具或结论的风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunStatus(str, Enum):
    """任务或工具运行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ApprovalContext:
    approval_id: str
    approver: str
    reason: str
    approved_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _required_text(self.approval_id, "approval_id")
        _required_text(self.approver, "approver")
        _required_text(self.reason, "reason")
        object.__setattr__(self, "approved_at", _utc_timestamp(self.approved_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "approver": self.approver,
            "reason": self.reason,
            "approved_at": self.approved_at.isoformat(),
        }


@dataclass(frozen=True)
class IndustrialMessage:
    """携带工业范围与证据引用的模型消息。"""

    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=utc_now)
    equipment_scope: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc_timestamp(self.timestamp))
        object.__setattr__(self, "equipment_scope", _to_tuple(self.equipment_scope))
        object.__setattr__(self, "evidence_ids", _to_tuple(self.evidence_ids))
        tool_calls = tuple(self.tool_calls)
        if any(not isinstance(call, ToolCall) for call in tool_calls):
            raise ValueError("tool_calls 必须全部为 ToolCall")
        if not self.content.strip() and not tool_calls:
            raise ValueError("消息必须包含内容或工具调用")
        object.__setattr__(self, "tool_calls", tool_calls)
        if self.name is not None:
            _required_text(self.name, "name")
        if self.tool_call_id is not None:
            _required_text(self.tool_call_id, "tool_call_id")
        if self.role == MessageRole.TOOL and self.tool_call_id is None:
            raise ValueError("tool 消息必须包含 tool_call_id")
        if self.tool_call_id is not None and self.role != MessageRole.TOOL:
            raise ValueError("仅 tool 消息可包含 tool_call_id")

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "equipment_scope": list(self.equipment_scope),
            "evidence_ids": list(self.evidence_ids),
            "name": self.name,
            "tool_call_id": self.tool_call_id,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }


@dataclass(frozen=True)
class IndustrialAgentConfig:
    """工业智能体的模型、边界和资源上限配置。"""

    model: str = "mock-industrial-v1"
    max_rounds: int = 5
    tool_allowlist: frozenset[str] = frozenset()
    data_root: Path | str = Path(".")
    field_allowlist: frozenset[str] | None = None
    max_input_rows: int = 5_000
    max_result_rows: int = 100
    timeout_seconds: float = 30.0
    export_directory: Path | str = "exports"

    def __post_init__(self) -> None:
        _required_text(self.model, "model")
        if not 1 <= self.max_rounds <= 100:
            raise ConfigurationError("max_rounds 必须在 1 到 100 之间")
        if self.max_input_rows < 1:
            raise ConfigurationError("max_input_rows 必须大于 0")
        if self.max_result_rows < 1:
            raise ConfigurationError("max_result_rows 必须大于 0")
        if self.max_result_rows > self.max_input_rows:
            raise ConfigurationError("max_result_rows 不能大于 max_input_rows")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("timeout_seconds 必须大于 0")

        data_root = Path(self.data_root).expanduser().resolve()
        if not data_root.is_dir():
            raise ConfigurationError(f"data_root 不存在或不是目录：{data_root}")
        object.__setattr__(self, "data_root", data_root)
        export_path = Path(self.export_directory).expanduser()
        if not export_path.is_absolute():
            export_path = data_root / export_path
        export_path = export_path.resolve()
        try:
            export_path.relative_to(data_root)
        except ValueError as exc:
            raise ConfigurationError(
                "export_directory 必须位于 data_root 内："
                f"{export_path}"
            ) from exc
        object.__setattr__(self, "export_directory", export_path)
        object.__setattr__(self, "tool_allowlist", _to_frozenset(self.tool_allowlist))
        if self.field_allowlist is not None:
            object.__setattr__(
                self,
                "field_allowlist",
                _to_frozenset(self.field_allowlist),
            )


@dataclass(frozen=True)
class IndustrialTask:
    """一次只读工业数据分析任务。"""

    task_id: str
    objective: str
    equipment_scope: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    inputs: Mapping[str, object] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _required_text(self.task_id, "task_id")
        _required_text(self.objective, "objective")
        object.__setattr__(self, "equipment_scope", _to_tuple(self.equipment_scope))
        object.__setattr__(self, "data_sources", _to_tuple(self.data_sources))
        if not isinstance(self.inputs, Mapping):
            raise ValueError("inputs 必须是映射")
        object.__setattr__(self, "inputs", dict(self.inputs))
        object.__setattr__(self, "constraints", _to_tuple(self.constraints))
        object.__setattr__(self, "created_at", _utc_timestamp(self.created_at))


@dataclass(frozen=True)
class ToolCall:
    """模型请求执行的结构化工具调用。"""

    call_id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)
    requested_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _required_text(self.call_id, "call_id")
        _required_text(self.name, "name")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("arguments 必须是映射")
        object.__setattr__(self, "arguments", dict(self.arguments))
        object.__setattr__(self, "requested_at", _utc_timestamp(self.requested_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": _json_value(self.arguments),
            "requested_at": self.requested_at.isoformat(),
        }


@dataclass(frozen=True)
class LLMResponse:
    """统一的模型响应，支持文本和结构化工具调用。"""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    provider: str | None = None
    model: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    received_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("content 必须是字符串")
        tool_calls = tuple(self.tool_calls)
        if not self.content.strip() and not tool_calls:
            raise ValueError("响应必须包含文本或工具调用")
        if any(not isinstance(call, ToolCall) for call in tool_calls):
            raise ValueError("tool_calls 必须全部为 ToolCall")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata 必须是映射")
        object.__setattr__(self, "tool_calls", tool_calls)
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "received_at", _utc_timestamp(self.received_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "provider": self.provider,
            "model": self.model,
            "metadata": _json_value(self.metadata),
            "received_at": self.received_at.isoformat(),
        }


@dataclass(frozen=True)
class Evidence:
    """可审计、可回溯的一条数据分析证据。"""

    evidence_id: str
    source: str
    summary: str
    data_range: str
    details: Mapping[str, object] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _required_text(self.evidence_id, "evidence_id")
        _required_text(self.source, "source")
        _required_text(self.summary, "summary")
        _required_text(self.data_range, "data_range")
        if not isinstance(self.details, Mapping):
            raise ValueError("details 必须是映射")
        object.__setattr__(self, "details", dict(self.details))
        object.__setattr__(self, "created_at", _utc_timestamp(self.created_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "summary": self.summary,
            "data_range": self.data_range,
            "details": _json_value(self.details),
            "risk_level": self.risk_level.value,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def claim(self) -> str:
        return self.summary


@dataclass(frozen=True)
class ToolResult:
    """受控工具调用的结果与其产生的证据。"""

    output: Mapping[str, object]
    evidence: tuple[Evidence, ...] = ()
    truncated: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.output, Mapping) or not isinstance(self.metadata, Mapping):
            raise ValueError("output 和 metadata 必须是映射")
        evidence = tuple(self.evidence)
        if any(not isinstance(item, Evidence) for item in evidence):
            raise ValueError("evidence 必须全部为 Evidence")
        object.__setattr__(self, "output", dict(self.output))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True)
class AuditRecord:
    """记录可复核的模型、工具或审批事件。"""

    call_id: str
    tool_name: str
    status: RunStatus
    started_at: datetime
    duration_ms: int
    input_summary: str
    evidence_ids: tuple[str, ...] = ()
    approval_id: str | None = None
    approver: str | None = None
    approved_at: datetime | None = None
    approval_reason: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.call_id, "call_id")
        _required_text(self.tool_name, "tool_name")
        _required_text(self.input_summary, "input_summary")
        if self.duration_ms < 0:
            raise ValueError("duration_ms 不能小于 0")
        if self.status == RunStatus.FAILED:
            if not self.error_type or not self.error_message:
                raise ValueError("失败审计记录必须包含错误类型和错误信息")
        elif self.error_type is not None or self.error_message is not None:
            raise ValueError("仅失败审计记录可包含错误信息")
        approval_fields = (
            self.approval_id,
            self.approver,
            self.approved_at,
            self.approval_reason,
        )
        if any(value is not None for value in approval_fields):
            if any(value is None for value in approval_fields):
                raise ValueError("审批审计字段必须完整提供")
            _required_text(str(self.approval_id), "approval_id")
            _required_text(str(self.approver), "approver")
            _required_text(str(self.approval_reason), "approval_reason")
            object.__setattr__(
                self,
                "approved_at",
                _utc_timestamp(self.approved_at),
            )
        object.__setattr__(self, "started_at", _utc_timestamp(self.started_at))
        object.__setattr__(self, "evidence_ids", _to_tuple(self.evidence_ids))


@dataclass(frozen=True)
class AnalysisResult:
    """最终分析结论，必须随结论提供可追溯证据。"""

    task_id: str
    status: RunStatus
    conclusion: str
    evidence: tuple[Evidence, ...]
    confidence: float
    limitations: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    audit_records: tuple[AuditRecord, ...] = ()
    messages: tuple[IndustrialMessage, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _required_text(self.task_id, "task_id")
        _required_text(self.conclusion, "conclusion")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 0 到 1 之间")
        evidence = tuple(self.evidence)
        audit_records = tuple(self.audit_records)
        messages = tuple(self.messages)
        if any(not isinstance(item, Evidence) for item in evidence):
            raise ValueError("evidence 必须全部为 Evidence")
        if any(not isinstance(item, AuditRecord) for item in audit_records):
            raise ValueError("audit_records 必须全部为 AuditRecord")
        if any(not isinstance(item, IndustrialMessage) for item in messages):
            raise ValueError("messages 必须全部为 IndustrialMessage")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata 必须是映射")
        if self.status == RunStatus.COMPLETED and not evidence:
            raise ValueError("完成的分析结果必须包含证据")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "limitations", _to_tuple(self.limitations))
        object.__setattr__(self, "next_steps", _to_tuple(self.next_steps))
        object.__setattr__(self, "audit_records", audit_records)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "generated_at", _utc_timestamp(self.generated_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "conclusion": self.conclusion,
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence": self.confidence,
            "limitations": list(self.limitations),
            "next_steps": list(self.next_steps),
            "audit_records": [
                {
                    "call_id": item.call_id,
                    "tool_name": item.tool_name,
                    "status": item.status.value,
                    "started_at": item.started_at.isoformat(),
                    "duration_ms": item.duration_ms,
                    "input_summary": item.input_summary,
                    "evidence_ids": list(item.evidence_ids),
                    "approval_id": item.approval_id,
                    "approver": item.approver,
                    "approved_at": (
                        item.approved_at.isoformat()
                        if item.approved_at is not None
                        else None
                    ),
                    "approval_reason": item.approval_reason,
                    "error_type": item.error_type,
                    "error_message": item.error_message,
                }
                for item in self.audit_records
            ],
            "messages": [item.to_dict() for item in self.messages],
            "metadata": _json_value(self.metadata),
            "generated_at": self.generated_at.isoformat(),
        }

    def to_markdown(self) -> str:
        """生成明确列出每个证据 ID 的中文 Markdown 报告。"""
        evidence_lines = [
            f"- **证据 ID：{item.evidence_id}**：{item.summary}（来源：{item.source}；"
            f"数据范围：{item.data_range}）"
            for item in self.evidence
        ]
        limitation_lines = [f"- {item}" for item in self.limitations] or ["- 无"]
        next_step_lines = [f"- {item}" for item in self.next_steps] or ["- 无"]
        return "\n".join(
            [
                "# 工业数据分析结果",
                "",
                f"- 任务 ID：{self.task_id}",
                f"- 状态：{self.status.value}",
                f"- 置信度：{self.confidence:.2f}",
                "",
                "## 结论",
                self.conclusion,
                "",
                "## 证据（按 ID 可追溯）",
                *(evidence_lines if evidence_lines else ["- 无证据"]),
                "",
                "## 限制",
                *limitation_lines,
                "",
                "## 建议下一步",
                *next_step_lines,
            ]
        )
