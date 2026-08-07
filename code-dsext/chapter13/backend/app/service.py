from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .agents import (
    EnergyDataValidationAgent,
    EnergyForecastAgent,
    EnergyReportAgent,
    ScheduleOptimizationAgent,
    ScheduleReviewAgent,
)
from .engine import DeterministicEnergyEngine
from .mcp import ReadOnlyEnergyMCPClient
from .models import (
    ApprovalRecord,
    ApprovalRequest,
    ChangeRecord,
    DataSummary,
    EnergyBaseline,
    EnergyCurve,
    EnergyDataMetadata,
    EnergyPlan,
    OperationConstraint,
    PlanningRequest,
    PlanEditRequest,
    ProductionTask,
    RecommendationEvidence,
    TariffPeriod,
)


def _content_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EnergyPlanningAssistant:
    model_version = "energy-engine-1.0"

    def __init__(self, mcp_client: ReadOnlyEnergyMCPClient | None = None):
        sample_data = Path(__file__).resolve().parents[2] / "sample_data"
        self.mcp = mcp_client or ReadOnlyEnergyMCPClient(sample_data)
        self.engine = DeterministicEnergyEngine()
        self.data_agent = EnergyDataValidationAgent(self.engine)
        self.forecast_agent = EnergyForecastAgent()
        self.optimization_agent = ScheduleOptimizationAgent()
        self.review_agent = ScheduleReviewAgent(self.engine)
        self.report_agent = EnergyReportAgent()
        self.plans: dict[str, EnergyPlan] = {}
        self._contexts: dict[str, dict] = {}

    def create_plan(self, request: PlanningRequest) -> EnergyPlan:
        reads = {
            name: self.mcp.read(name)
            for name in (
                "metadata",
                "tariff_profiles",
                "tasks",
                "constraints",
                "energy_baselines",
            )
        }
        tariffs = [
            TariffPeriod.model_validate(item)
            for profile in reads["tariff_profiles"].payload
            if profile["profile_id"] == request.tariff_profile_id
            for item in profile["periods"]
        ]
        if not tariffs:
            raise ValueError(f"unknown tariff profile: {request.tariff_profile_id}")
        tasks = [
            ProductionTask.model_validate(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"line_id", "planning_date"}
                }
            )
            for item in reads["tasks"].payload
            if item["line_id"] == request.line_id
            and item["planning_date"] == request.planning_date.isoformat()
        ]
        if not tasks:
            raise ValueError(
                "no tasks for line and planning date: "
                f"{request.line_id}/{request.planning_date.isoformat()}"
            )
        for task in tasks:
            if task.task_id in request.locked_task_ids:
                task.locked = True
        constraints = [
            OperationConstraint.model_validate(item)
            for item in reads["constraints"].payload
        ] + deepcopy(request.constraints)
        baselines = [
            EnergyBaseline.model_validate(item)
            for item in reads["energy_baselines"].payload
            if item["task_id"] in {task.task_id for task in tasks}
        ]
        metadata = EnergyDataMetadata.model_validate(reads["metadata"].payload)
        data_issues = self.data_agent.validate(
            request,
            tasks,
            tariffs,
            baselines,
            constraints,
            metadata,
        )
        tasks, baselines = self.forecast_agent.apply_baselines(
            tasks,
            baselines,
            request.output_target_units,
        )
        relative_error_percent = round(
            max(
                (item.upper_power_kw - item.lower_power_kw)
                / (2 * item.expected_power_kw)
                * 100
                for item in baselines
            ),
            2,
        )
        baseline_versions = ",".join(
            sorted({item.model_version for item in baselines})
        )
        request_hash = _content_hash(request.model_dump(mode="json"))
        evidence = RecommendationEvidence(
            tool_call_ids=[item.tool_call_id for item in reads.values()],
            content_hashes=[
                *[item.content_hash for item in reads.values()],
                request_hash,
            ],
            source_window=reads["metadata"].source_window,
            data_version=reads["metadata"].data_version,
            model_version=f"{baseline_versions}+{self.model_version}",
            assumptions=[
                "按 30 分钟时间片进行成本和峰值复算",
                "任务在单个时间片内按恒定功率估算",
                "任务功率与计划产量按标称批量线性缩放",
                (
                    "任务不得跨越已选班次边界："
                    + "、".join(
                        {
                            "day": "白班 06:00-18:00",
                            "night": "夜班 18:00-次日 06:00",
                        }[shift]
                        for shift in request.shifts
                    )
                ),
                "仅使用脱敏离线教学数据",
            ],
            estimation_error=(
                "功率基线置信区间的最大相对半宽为 "
                f"±{relative_error_percent}%"
            ),
            reviewed=False,
        )
        baseline = self.review_agent.review(
            "baseline",
            "当前排产基线",
            "baseline",
            tasks,
            tariffs,
            constraints,
            request.shifts,
            request.output_target_units,
            evidence,
            baselines,
        )
        generated = self.optimization_agent.generate(
            tasks,
            tariffs,
            constraints,
            request.shifts,
        )
        names = {"cost": "成本优先", "peak": "削峰优先", "balanced": "均衡方案"}
        candidates = [
            self.review_agent.review(
                f"option-{strategy}",
                names[strategy],
                strategy,
                option_tasks,
                tariffs,
                constraints,
                request.shifts,
                request.output_target_units,
                evidence,
                baselines,
            )
            for strategy, option_tasks in generated.items()
        ]
        plan = EnergyPlan(
            plan_id=str(uuid.uuid4()),
            request=request,
            effective_constraints=deepcopy(constraints),
            baseline=baseline,
            candidates=candidates,
            recommended_option_id=self.report_agent.recommend(request, candidates),
            progress=[
                "data",
                "validation",
                "forecast",
                "optimization",
                "review",
                "report",
            ],
            data_summary=DataSummary(
                data_version=evidence.data_version,
                model_version=evidence.model_version,
                source_window=evidence.source_window,
                status="degraded" if data_issues else "ready",
                issues=data_issues,
                task_count=len(tasks),
                baseline_count=len(baselines),
            ),
            assumptions=list(evidence.assumptions),
            estimation_error_percent=relative_error_percent,
            evidence=[evidence.model_copy(update={"reviewed": True})],
        )
        self.plans[plan.plan_id] = plan
        self._contexts[plan.plan_id] = {
            "tariffs": tariffs,
            "constraints": constraints,
            "baselines": baselines,
        }
        return plan

    def get_plan(self, plan_id: str) -> EnergyPlan:
        try:
            return self.plans[plan_id]
        except KeyError as exc:
            raise KeyError(f"plan not found: {plan_id}") from exc

    def get_curve(
        self,
        plan_id: str,
        option_id: str | None = None,
    ) -> EnergyCurve:
        plan = self.get_plan(plan_id)
        selected_id = option_id or plan.recommended_option_id
        options = [plan.baseline, *plan.candidates]
        try:
            return next(
                option.curve
                for option in options
                if option.option_id == selected_id
            )
        except StopIteration as exc:
            raise ValueError(f"option not found: {selected_id}") from exc

    def edit_plan(self, plan_id: str, edit: PlanEditRequest) -> EnergyPlan:
        plan = self.get_plan(plan_id)
        try:
            option_index = next(
                index
                for index, option in enumerate(plan.candidates)
                if option.option_id == edit.option_id
            )
        except StopIteration as exc:
            raise ValueError(f"option not found: {edit.option_id}") from exc
        option = plan.candidates[option_index]
        edited_tasks = deepcopy(option.tasks)
        task_by_id = {task.task_id: task for task in edited_tasks}
        if len({change.task_id for change in edit.changes}) != len(edit.changes):
            raise ValueError("each task may appear only once per edit request")
        staged_records: list[tuple[str, dict[str, int | bool], dict[str, int | bool]]] = []
        for change in edit.changes:
            task = task_by_id.get(change.task_id)
            if task is None:
                raise ValueError(f"task not found: {change.task_id}")
            before = {
                "start_minute": task.start_minute,
                "locked": task.locked,
            }
            if (
                change.start_minute is not None
                and change.start_minute != task.start_minute
            ):
                if task.locked or not task.movable:
                    raise ValueError("locked or fixed task cannot be moved")
                if (
                    change.start_minute < task.earliest_start_minute
                    or change.start_minute + task.duration_minutes
                    > task.latest_end_minute
                ):
                    raise ValueError("edited task is outside its movable window")
                task.start_minute = change.start_minute
            if change.locked is not None:
                if task.locked and not change.locked:
                    raise ValueError("a locked task cannot be unlocked")
                task.locked = change.locked
            after = {
                "start_minute": task.start_minute,
                "locked": task.locked,
            }
            if before == after:
                raise ValueError(f"edit does not change task: {change.task_id}")
            staged_records.append((change.task_id, before, after))
        context = self._contexts[plan_id]
        if option.evidence is None:
            raise ValueError("option is missing review evidence")
        reviewed = self.review_agent.review(
            option.option_id,
            option.name,
            option.strategy,
            edited_tasks,
            context["tariffs"],
            context["constraints"],
            plan.request.shifts,
            plan.request.output_target_units,
            option.evidence,
            context["baselines"],
        )
        if not reviewed.feasible:
            messages = "; ".join(item.message for item in reviewed.violations)
            raise ValueError(f"edit violates constraints: {messages}")
        plan.candidates[option_index] = reviewed
        changed_at = datetime.now(timezone.utc).isoformat()
        for task_id, before, after in staged_records:
            change_hash = _content_hash(
                {
                    "plan_id": plan_id,
                    "option_id": edit.option_id,
                    "task_id": task_id,
                    "before": before,
                    "after": after,
                    "actor": edit.actor,
                    "reason": edit.reason,
                }
            )
            plan.change_history.append(
                ChangeRecord(
                    change_id=f"CHG-{change_hash[:16]}",
                    content_hash=change_hash,
                    changed_at=changed_at,
                    option_id=edit.option_id,
                    task_id=task_id,
                    before=before,
                    after=after,
                    actor=edit.actor,
                    reason=edit.reason,
                )
            )
        plan.recommended_option_id = edit.option_id
        plan.recomputed_task_ids = [task_id for task_id, _, _ in staged_records]
        plan.approval_status = "pending_approval"
        plan.approval = None
        return plan

    def approve_plan(
        self, plan_id: str, approval: ApprovalRequest
    ) -> EnergyPlan:
        plan = self.get_plan(plan_id)
        recommended = next(
            option
            for option in plan.candidates
            if option.option_id == plan.recommended_option_id
        )
        if (
            not recommended.feasible
            or recommended.evidence is None
            or not recommended.evidence.reviewed
        ):
            raise ValueError("only a feasible reviewed plan can be approved")
        approval_hash = _content_hash(
            {
                "plan_id": plan_id,
                "recommended_option": recommended.model_dump(mode="json"),
                "change_hashes": [
                    item.content_hash for item in plan.change_history
                ],
            }
        )
        plan.approval_status = "approved"
        plan.approval = ApprovalRecord(
            approval_id=f"APR-{approval_hash[:16]}",
            input_hash=approval_hash,
            approver=approval.approver,
            reason=approval.reason,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )
        return plan

    def export_plan(
        self, plan_id: str, destination: Path | str | None = None
    ) -> dict:
        plan = self.get_plan(plan_id)
        if plan.approval_status != "approved" or plan.approval is None:
            raise PermissionError("plan must be approved before export")
        recommended = next(
            option
            for option in plan.candidates
            if option.option_id == plan.recommended_option_id
        )
        if recommended.evidence is None:
            raise ValueError("approved option is missing review evidence")
        package = {
            "plan": recommended.model_dump(mode="json"),
            "data_version": recommended.evidence.data_version,
            "model_version": recommended.evidence.model_version,
            "constraints": [
                item.model_dump(mode="json")
                for item in plan.effective_constraints
            ],
            "risks": [
                "estimate uncertainty remains",
                "human approval does not authorize production control",
            ],
            "estimation_error": recommended.evidence.estimation_error,
            "audit": {
                "request": plan.request.model_dump(mode="json"),
                "changes": [
                    item.model_dump(mode="json") for item in plan.change_history
                ],
                "approval": plan.approval.model_dump(mode="json"),
            },
            "read_only": True,
            "production_control": "prohibited",
        }
        if destination is not None:
            output_path = Path(destination)
            if output_path.suffix.lower() != ".json":
                raise ValueError("export destination must be a JSON file")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return package
