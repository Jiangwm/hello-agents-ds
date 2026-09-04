from __future__ import annotations

from dataclasses import dataclass
from typing import Final, assert_never

from ..models.common import TodoStatus
from ..models.research import Budget, ResearchScope, TodoItem


@dataclass(frozen=True, slots=True)
class _TodoSpec:
    key: str
    title: str
    question: str
    domains: tuple[str, ...]
    tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    source_quality: str
    falsification_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanningValidationError(ValueError):
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


_TODO_SPECS: Final = (
    _TodoSpec(
        "anomaly_scope",
        "异常范围",
        "异常从何时开始，影响哪些资产、批次与指标？",
        ("quality", "process"),
        ("query_quality_window", "summarize_anomaly_scope"),
        (),
        "去标识化一手质量记录与确定性统计",
        ("扩大时间窗后异常不再显著", "异常未与指定资产或批次共现"),
    ),
    _TodoSpec(
        "normal_control",
        "正常对照",
        "同口径正常窗口与异常窗口有哪些稳定差异？",
        ("quality", "process"),
        ("select_normal_control", "compare_windows"),
        ("anomaly_scope",),
        "同源同口径正常窗口",
        ("更换匹配对照后差异消失", "对照窗口存在同类异常"),
    ),
    _TodoSpec(
        "process_drift",
        "工艺漂移",
        "异常前后工艺参数是否存在可复现漂移？",
        ("process",),
        ("query_process_timeseries", "detect_process_drift"),
        ("normal_control",),
        "校验后的过程时序与规则计算",
        ("漂移早于或晚于异常且无时序关联", "正常对照出现同等漂移"),
    ),
    _TodoSpec(
        "equipment_maintenance",
        "设备维护",
        "设备事件或维护活动是否与异常窗口重合？",
        ("equipment", "maintenance"),
        ("query_equipment_events", "query_maintenance_events"),
        ("normal_control",),
        "设备事件与已关闭维护工单",
        ("事件时间与异常不重合", "同类维护后未出现异常"),
    ),
    _TodoSpec(
        "material_change",
        "物料变化",
        "物料批次或供应变化是否与异常批次一致？",
        ("material", "quality"),
        ("query_material_lots", "compare_material_cohorts"),
        ("normal_control",),
        "去标识化物料批次与检验记录",
        ("相同物料在正常批次表现稳定", "异常跨越多个无共同属性的物料批次"),
    ),
    _TodoSpec(
        "historical_cases",
        "历史案例",
        "历史案例是否提供可验证的相似机制与反例？",
        ("documents",),
        ("search_local_documents", "extract_case_evidence"),
        ("anomaly_scope",),
        "本地受控文档与可定位引用",
        ("案例条件与当前范围不匹配", "案例仅有相关性描述而无验证记录"),
    ),
    _TodoSpec(
        "root_cause_validation",
        "候选根因验证",
        "哪些候选根因经交叉证据与反证检查后仍成立？",
        ("quality", "process", "equipment", "maintenance", "material", "documents"),
        ("validate_candidate_causes", "assemble_evidence_chain"),
        ("process_drift", "equipment_maintenance", "material_change", "historical_cases"),
        "跨域证据链与人工批准的验证结果",
        ("任一关键证据无法复现", "替代解释具有更强时序与机制证据"),
    ),
)


class PlanningService:
    def plan(
        self, run_id: str, scope: ResearchScope, budget: Budget
    ) -> tuple[TodoItem, ...]:
        if not run_id.strip():
            raise PlanningValidationError(
                "planning.blank_run_id", "run_id must not be blank"
            )
        scope_domains = ",".join(scope.data_domains) or "unspecified"
        budget_limits = (
            f"calls={budget.max_tool_calls},rows={budget.max_rows_scanned},"
            f"cost={budget.max_cost},elapsed_ms={budget.max_elapsed_ms}"
        )
        ids = {spec.key: f"{run_id}:todo:{spec.key}" for spec in _TODO_SPECS}
        return tuple(
            TodoItem(
                todo_id=ids[spec.key],
                title=spec.title,
                question=spec.question,
                data_scope=(
                    f"scope={scope.scope_id}; domains={','.join(spec.domains)}; "
                    f"declared_domains={scope_domains}; budget={budget_limits}"
                ),
                required_tools=spec.tools,
                status=TodoStatus.PENDING,
                outcome=None,
                dependencies=tuple(ids[key] for key in spec.dependencies),
                evidence_ids=(),
                negative_results=(),
                source_quality=spec.source_quality,
                candidate_conclusion=None,
                confidence=None,
                falsification_conditions=spec.falsification_conditions,
                started_at=None,
                completed_at=None,
                cost_units=0,
                failure_reason=None,
            )
            for spec in _TODO_SPECS
        )

    def ready_todos(self, todos: tuple[TodoItem, ...]) -> tuple[TodoItem, ...]:
        completed: set[str] = set()
        for item in todos:
            match item.status:
                case TodoStatus.COMPLETED:
                    completed.add(item.todo_id)
                case (
                    TodoStatus.PENDING
                    | TodoStatus.RUNNING
                    | TodoStatus.BLOCKED
                    | TodoStatus.AWAITING_HUMAN
                ):
                    pass
                case unreachable:
                    assert_never(unreachable)
        ready: list[TodoItem] = []
        for item in todos:
            match item.status:
                case TodoStatus.PENDING:
                    if set(item.dependencies) <= completed:
                        ready.append(item)
                case (
                    TodoStatus.RUNNING
                    | TodoStatus.BLOCKED
                    | TodoStatus.AWAITING_HUMAN
                    | TodoStatus.COMPLETED
                ):
                    pass
                case unreachable:
                    assert_never(unreachable)
        return tuple(ready)
