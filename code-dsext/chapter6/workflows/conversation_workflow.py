from __future__ import annotations

from dataclasses import replace
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
from schemas.models import AnalysisTask, Evidence, WorkflowConfig, WorkflowRun
from tools.industrial_tools import ReadOnlyQualityTools
from workflows.common import (
    attach_supplemental_evidence,
    build_workflow_run,
    order_hypotheses,
    pending_data_decision,
    required_evidence_is_ready,
    supplement_messages,
)


class ConversationQualityWorkflow:
    def __init__(
        self,
        data_dir: Path,
        config: WorkflowConfig | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.config = config or WorkflowConfig()

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
        trace = ["parse_task", "group_chat_start"]
        reports = []

        for specialist in specialists:
            trace.append(f"turn_{specialist.name}")
            report = specialist.investigate(task)
            report = replace(
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
            reports.append(report)
            messages.append(report.message)
            for item in report.evidence:
                evidence_by_id[item.evidence_id] = item

        trace.append("coordinator_summary")
        hypotheses, merge_message = coordinator.merge(task, reports)
        messages.append(merge_message)
        evidence = tuple(sorted(evidence_by_id.values(), key=lambda item: item.evidence_id))
        trace.append("reviewer_turn")
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
            trace.append("reviewer_turn_supplemental")
            reviews, missing_data, review_message = reviewer.review(task, hypotheses, evidence)
            messages.append(review_message)
            hypotheses = order_hypotheses(hypotheses, reviews)

        score_by_id = {item.hypothesis_id: item.total_score for item in reviews}
        top = next((item for item in hypotheses if item.status == "candidate"), None)
        top_score = score_by_id.get(top.hypothesis_id, 0.0) if top else 0.0
        ready = (
            top_score >= self.config.review_threshold
            and required_evidence_is_ready(top, evidence)
        )
        if not ready:
            if not supplemental_evidence:
                trace.extend(["supplemental_collection", "supplemental_no_new_data"])
                messages.extend(supplement_messages(task, missing_data, ()))
            else:
                trace.append("supplemental_evidence_insufficient")
            trace.extend(["review_stop", "group_chat_end"])
            return build_workflow_run(
                framework="AutoGen-style 顺序对话链",
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
                tool_call_count=tools.tool_call_count,
                rounds=1,
            )

        trace.extend(["draft_report_turn", "human_turn"])
        plan = coordinator.build_experiment_plan(top)
        human = HumanSupervisorAgent()
        plan, decision, human_message = human.decide(task, plan, human_decision)
        messages.append(human_message)
        status = {
            "approve": "completed",
            "defer": "awaiting_human",
            "reject": "interrupted",
        }[decision.decision]
        trace.append("group_chat_end")
        return build_workflow_run(
            framework="AutoGen-style 顺序对话链",
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
            tool_call_count=tools.tool_call_count,
            rounds=1,
        )
