from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from typing import Sequence

from agents.team import (
    CoordinatorAgent,
    DataAnalysisAgent,
    EquipmentAgent,
    HumanSupervisorAgent,
    ProcessAgent,
    QualityReviewAgent,
)
from schemas.models import (
    AnalysisTask,
    Evidence,
    WorkflowConfig,
    WorkflowRun,
)
from tools.industrial_tools import ReadOnlyQualityTools
from workflows.common import (
    attach_supplemental_evidence,
    build_workflow_run,
    order_hypotheses,
    pending_data_decision,
    required_evidence_is_ready,
    supplement_messages,
)


class StateGraphQualityWorkflow:
    def __init__(
        self,
        data_dir: Path,
        config: WorkflowConfig | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.config = config or WorkflowConfig()
        self._paused_runs: dict[str, WorkflowRun] = {}

    def run(
        self,
        task: AnalysisTask,
        human_decision: str = "defer",
        supplemental_evidence: Sequence[Evidence] = (),
    ) -> WorkflowRun:
        tools = ReadOnlyQualityTools(
            data_dir=self.data_dir,
            max_calls=self.config.max_tool_calls,
        )
        coordinator = CoordinatorAgent()
        specialists = (
            DataAnalysisAgent(tools),
            ProcessAgent(tools),
            EquipmentAgent(tools),
        )
        reviewer = QualityReviewAgent()
        messages = list(coordinator.dispatch(task))
        evidence_by_id: dict[str, Evidence] = {}
        trace = ["parse_task", "dispatch", "parallel_investigation"]

        with ThreadPoolExecutor(max_workers=3) as executor:
            reports = tuple(
                executor.submit(agent.investigate, task)
                for agent in specialists
            )
            reports = tuple(item.result() for item in reports)
        reports = tuple(
            replace(
                report,
                message=replace(
                    report.message,
                    evidence_ids=tuple(
                        item.evidence_id
                        for item in report.evidence
                        if item is not None
                    ),
                ),
                evidence=tuple(item for item in report.evidence if item is not None),
            )
            for report in reports
        )
        for report in reports:
            messages.append(report.message)
            for item in report.evidence:
                evidence_by_id[item.evidence_id] = item

        trace.append("merge_hypotheses")
        hypotheses, merge_message = coordinator.merge(task, reports)
        messages.append(merge_message)
        evidence = tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id))
        trace.append("quality_review")
        reviews, missing_data, review_message = reviewer.review(task, hypotheses, evidence)
        messages.append(review_message)
        hypotheses = order_hypotheses(hypotheses, reviews)

        if supplemental_evidence:
            trace.extend(["supplemental_collection", "supplemental_evidence_received"])
            messages.extend(supplement_messages(task, missing_data, supplemental_evidence))
            for item in supplemental_evidence:
                evidence_by_id[item.evidence_id] = item
            evidence = tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id))
            hypotheses = attach_supplemental_evidence(hypotheses, supplemental_evidence)
            trace.append("quality_review_supplemental")
            reviews, missing_data, review_message = reviewer.review(task, hypotheses, evidence)
            messages.append(review_message)
            hypotheses = order_hypotheses(hypotheses, reviews)

        result = self._route_after_review(
            task=task,
            coordinator=coordinator,
            reviewer=reviewer,
            human_decision=human_decision,
            messages=messages,
            evidence=tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id)),
            hypotheses=hypotheses,
            reviews=reviews,
            missing_data=missing_data,
            trace=trace,
            tool_call_count=tools.tool_call_count,
            rounds=1,
            supplemental_was_supplied=bool(supplemental_evidence),
        )
        return self._remember_if_paused(result)

    def checkpoint(self, task_id: str) -> dict[str, object]:
        run = self._paused_runs[task_id]
        return {"version": 1, "task_id": task_id, "run": asdict(run)}

    def export_checkpoint(self, task_id: str) -> str:
        return json.dumps(self.checkpoint(task_id), ensure_ascii=False)

    def resume(
        self,
        task_id: str,
        human_decision: str = "defer",
        supplemental_evidence: Sequence[Evidence] = (),
    ) -> WorkflowRun:
        if task_id not in self._paused_runs:
            raise ValueError(f"不存在可恢复任务：{task_id}")
        previous = self._paused_runs[task_id]
        if previous.status == "awaiting_human":
            if human_decision not in {"approve", "reject"}:
                raise ValueError("恢复人工审批只能为 approve 或 reject")
            plan = previous.experiment_plan
            if plan is None:
                raise ValueError("人工审批节点缺少验证计划")
            human = HumanSupervisorAgent()
            plan, decision, message = human.decide(
                previous.task,
                plan,
                human_decision,
            )
            status = "completed" if decision.decision == "approve" else "interrupted"
            trace = list(previous.trace) + ["resume_human_gate", "done"]
            result = build_workflow_run(
                framework="LangGraph-style 显式状态图",
                status=status,
                task=previous.task,
                messages=(*previous.messages, message),
                evidence=previous.evidence,
                hypotheses=previous.hypotheses,
                reviews=previous.reviews,
                missing_data=(),
                experiment_plan=plan,
                human_decision=decision,
                trace=trace,
                tool_call_count=previous.tool_call_count,
                rounds=previous.rounds,
            )
            self._paused_runs.pop(task_id, None)
            return result
        if previous.status != "needs_more_data":
            raise ValueError(f"任务状态不可恢复：{previous.status}")
        if not supplemental_evidence:
            return previous

        reviewer = QualityReviewAgent()
        coordinator = CoordinatorAgent()
        evidence_by_id = {item.evidence_id: item for item in previous.evidence}
        for item in supplemental_evidence:
            evidence_by_id[item.evidence_id] = item
        evidence = tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id))
        hypotheses = attach_supplemental_evidence(
            previous.hypotheses,
            supplemental_evidence,
        )
        trace = list(previous.trace[:-1]) + [
            "resume_supplemental_collection",
            "resume_supplemental_evidence_received",
            "quality_review_supplemental",
        ]
        messages = list(previous.messages)
        messages.extend(
            supplement_messages(previous.task, previous.missing_data, supplemental_evidence)
        )
        reviews, missing_data, review_message = reviewer.review(
            previous.task,
            hypotheses,
            evidence,
        )
        messages.append(review_message)
        hypotheses = order_hypotheses(hypotheses, reviews)
        result = self._route_after_review(
            task=previous.task,
            coordinator=coordinator,
            reviewer=reviewer,
            human_decision=human_decision,
            messages=messages,
            evidence=evidence,
            hypotheses=hypotheses,
            reviews=reviews,
            missing_data=missing_data,
            trace=trace,
            tool_call_count=previous.tool_call_count,
            rounds=previous.rounds + 1,
            supplemental_was_supplied=True,
        )
        return self._remember_if_paused(result)

    def _route_after_review(
        self,
        task: AnalysisTask,
        coordinator: CoordinatorAgent,
        reviewer: QualityReviewAgent,
        human_decision: str,
        messages: list,
        evidence: tuple[Evidence, ...],
        hypotheses: tuple,
        reviews: tuple,
        missing_data: tuple,
        trace: list[str],
        tool_call_count: int,
        rounds: int,
        supplemental_was_supplied: bool,
    ) -> WorkflowRun:
        score_by_id = {item.hypothesis_id: item.total_score for item in reviews}
        top = next((item for item in hypotheses if item.status == "candidate"), None)
        top_score = score_by_id.get(top.hypothesis_id, 0.0) if top else 0.0
        ready = (
            top_score >= self.config.review_threshold
            and required_evidence_is_ready(top, evidence)
        )
        if not ready:
            if "supplemental_collection" not in trace and not supplemental_was_supplied:
                trace.append("supplemental_collection")
                messages.extend(supplement_messages(task, missing_data, ()))
            if supplemental_was_supplied:
                trace.append("supplemental_evidence_insufficient")
            else:
                trace.append("supplemental_no_new_data")
            trace.extend(["minimum_new_evidence_stop", "done"])
            return build_workflow_run(
                framework="LangGraph-style 显式状态图",
                status="needs_more_data",
                task=task,
                messages=messages,
                evidence=evidence,
                hypotheses=hypotheses,
                reviews=reviews,
                missing_data=missing_data,
                experiment_plan=None,
                human_decision=pending_data_decision(),
                trace=trace,
                tool_call_count=tool_call_count,
                rounds=rounds + int(not supplemental_was_supplied),
            )

        trace.extend(["draft_report", "human_gate"])
        plan = coordinator.build_experiment_plan(top)
        human = HumanSupervisorAgent()
        plan, decision, human_message = human.decide(task, plan, human_decision)
        messages.append(human_message)
        status = {
            "approve": "completed",
            "defer": "awaiting_human",
            "reject": "interrupted",
        }[decision.decision]
        trace.append("done")
        return build_workflow_run(
            framework="LangGraph-style 显式状态图",
            status=status,
            task=task,
            messages=messages,
            evidence=evidence,
            hypotheses=hypotheses,
            reviews=reviews,
            missing_data=(),
            experiment_plan=plan,
            human_decision=decision,
            trace=trace,
            tool_call_count=tool_call_count,
            rounds=rounds,
        )

    def _remember_if_paused(self, result: WorkflowRun) -> WorkflowRun:
        if result.status in {"awaiting_human", "needs_more_data"}:
            self._paused_runs[result.task.task_id] = result
        return result
