from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from schemas.models import (
    AgentMessage,
    AnalysisTask,
    Evidence,
    ExperimentPlan,
    HumanDecision,
    Hypothesis,
    MissingDataRequest,
    ReviewScore,
    WorkflowRun,
)


def order_hypotheses(
    hypotheses: Sequence[Hypothesis],
    reviews: Sequence[ReviewScore],
) -> tuple[Hypothesis, ...]:
    score_by_id = {
        item.hypothesis_id: item.total_score
        for item in reviews
    }
    return tuple(
        sorted(
            hypotheses,
            key=lambda item: (
                item.status == "excluded",
                -score_by_id.get(item.hypothesis_id, 0.0),
                item.hypothesis_id,
            ),
        )
    )


def pending_data_decision() -> HumanDecision:
    return HumanDecision(
        decision="defer",
        approver_role="人工质量负责人",
        scope="补充取数后再审",
        note="证据评分未达阈值，未生成或批准验证实验计划。",
    )


def required_evidence_is_ready(
    hypothesis: Hypothesis | None,
    evidence: Sequence[Evidence],
) -> bool:
    if hypothesis is None:
        return False
    evidence_by_id = {item.evidence_id: item for item in evidence}
    return bool(hypothesis.evidence_ids) and all(
        evidence_by_id.get(evidence_id) is not None
        and evidence_by_id[evidence_id].status == "success"
        for evidence_id in hypothesis.evidence_ids
    )


def supplement_messages(
    task: AnalysisTask,
    requests: Sequence[MissingDataRequest],
    supplemental_evidence: Sequence[Evidence],
) -> tuple[AgentMessage, ...]:
    messages: list[AgentMessage] = []
    if requests:
        messages.append(
            AgentMessage(
                task_id=task.task_id,
                sender="coordinator",
                recipient="data_domain_owner",
                evidence_ids=(),
                claim=f"已发起 {len(requests)} 项补数请求。",
                confidence=0.0,
                open_questions=tuple(item.description for item in requests),
                message_type="supplement_request",
            )
        )
    if supplemental_evidence:
        messages.append(
            AgentMessage(
                task_id=task.task_id,
                sender="data_domain_owner",
                recipient="quality_reviewer",
                evidence_ids=tuple(
                    item.evidence_id for item in supplemental_evidence
                ),
                claim=f"已提交 {len(supplemental_evidence)} 条补充证据供复审。",
                confidence=1.0,
                open_questions=(),
                message_type="supplement_evidence",
            )
        )
    return tuple(messages)


def attach_supplemental_evidence(
    hypotheses: Sequence[Hypothesis],
    supplemental_evidence: Sequence[Evidence],
) -> tuple[Hypothesis, ...]:
    if not supplemental_evidence:
        return tuple(hypotheses)
    attached: list[Hypothesis] = []
    for hypothesis in hypotheses:
        evidence_ids = list(hypothesis.evidence_ids)
        for item in supplemental_evidence:
            related_ids = item.metrics.get("related_hypothesis_ids", ())
            if not isinstance(related_ids, (list, tuple, set)):
                related_ids = ()
            if not related_ids or hypothesis.hypothesis_id in related_ids:
                evidence_ids.append(item.evidence_id)
        attached.append(
            replace(
                hypothesis,
                evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            )
        )
    return tuple(attached)


def build_workflow_run(
    framework: str,
    status: str,
    task: AnalysisTask,
    messages: Sequence[AgentMessage],
    evidence: Sequence[Evidence],
    hypotheses: Sequence[Hypothesis],
    reviews: Sequence[ReviewScore],
    missing_data: Sequence[MissingDataRequest],
    experiment_plan: ExperimentPlan | None,
    human_decision: HumanDecision,
    trace: Sequence[str],
    tool_call_count: int,
    rounds: int,
) -> WorkflowRun:
    from workflows.reporting import render_report

    report = render_report(
        framework=framework,
        status=status,
        task=task,
        messages=messages,
        evidence=evidence,
        hypotheses=hypotheses,
        reviews=reviews,
        missing_data=missing_data,
        experiment_plan=experiment_plan,
        human_decision=human_decision,
        trace=trace,
        tool_call_count=tool_call_count,
        rounds=rounds,
    )
    return WorkflowRun(
        framework=("state-graph" if "状态图" in framework else "conversation"),
        status=status,
        task=task,
        messages=tuple(messages),
        evidence=tuple(evidence),
        hypotheses=tuple(hypotheses),
        reviews=tuple(reviews),
        missing_data=tuple(missing_data),
        experiment_plan=experiment_plan,
        human_decision=human_decision,
        trace=tuple(trace),
        tool_call_count=tool_call_count,
        rounds=rounds,
        report=report,
    )
