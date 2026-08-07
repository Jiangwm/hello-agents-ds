from __future__ import annotations

from typing import Sequence

from schemas.models import (
    AgentMessage,
    AnalysisTask,
    Evidence,
    ExperimentPlan,
    HumanDecision,
    Hypothesis,
    MissingDataRequest,
    ReviewScore,
)


def render_report(
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
) -> str:
    review_by_id = {item.hypothesis_id: item for item in reviews}
    candidate_hypotheses = [
        item for item in hypotheses if item.status == "candidate"
    ]
    excluded_hypotheses = [
        item for item in hypotheses if item.status == "excluded"
    ]
    lines = [
        "# 多智能体质量根因分析报告",
        "",
        f"- 任务：`{task.task_id}`",
        f"- 场景：{task.focus}",
        f"- 主流程：{framework}",
        f"- 状态：`{status}`",
        f"- 协作轮次：{rounds}",
        f"- 只读工具调用：{tool_call_count}",
        "",
        "## 1. 分组与影响",
        "",
        f"- 正常组：{', '.join(task.normal_batch_ids)}",
        f"- 异常组：{', '.join(task.abnormal_batch_ids)}",
        "",
        "## 2. 已确认事实与证据",
        "",
    ]
    for item in evidence:
        fact_label = "已确认事实" if item.status == "success" else "证据不足"
        lines.append(
            f"- [{fact_label}] [{item.evidence_id}] {item.claim} "
            f"范围：{item.data_range}；来源：{', '.join(item.source_files) or '无'}。"
        )
        for limitation in item.limitations:
            lines.append(f"  - 限制：{limitation}")

    lines.extend(["", "## 3. 候选根因与证据图", ""])
    if not candidate_hypotheses:
        lines.append("- 尚无满足结构化证据契约的候选根因。")
    for rank, hypothesis in enumerate(candidate_hypotheses, start=1):
        review = review_by_id[hypothesis.hypothesis_id]
        lines.extend(
            [
                (
                    f"{rank}. **{hypothesis.title}**（证据评分 "
                    f"{review.total_score:.3f}，智能体置信度 "
                    f"{hypothesis.confidence:.3f}）"
                ),
                f"   - 数据支持的假设：{hypothesis.claim}",
                f"   - 证据：{', '.join(hypothesis.evidence_ids)}",
                f"   - 可反证检查：{hypothesis.falsification_test}",
            ]
        )
        for evidence_id in hypothesis.evidence_ids:
            lines.append(
                f"   - 证据图：`{evidence_id}` → `{hypothesis.hypothesis_id}`"
            )

    lines.extend(["", "## 4. 被排除假设与反证", ""])
    if not excluded_hypotheses:
        lines.append("- 当前没有达到排除条件的假设。")
    for hypothesis in excluded_hypotheses:
        review = review_by_id[hypothesis.hypothesis_id]
        lines.append(
            f"- **{hypothesis.title}**：{hypothesis.claim} "
            f"反证：{', '.join(hypothesis.counter_evidence_ids) or '尚无'}；"
            f"证据评分 {review.total_score:.3f}。"
        )

    lines.extend(["", "## 5. 缺失数据与补充采集请求", ""])
    for item in missing_data:
        lines.append(
            f"- `{item.request_id}` {item.description} 原因：{item.reason} "
            f"审批：{item.required_approval}。"
        )

    lines.extend(["", "## 6. 验证实验与人工审批", ""])
    if experiment_plan is None:
        lines.append("- 证据评分未达阈值，未生成验证实验计划。")
    else:
        lines.extend(
            [
                f"- 计划：`{experiment_plan.plan_id}`",
                f"- 目标：{experiment_plan.objective}",
                (
                    "- 审批状态："
                    + ("已批准" if experiment_plan.approved else "未批准")
                ),
                "- 步骤：",
            ]
        )
        lines.extend(
            f"  {index}. {step}"
            for index, step in enumerate(experiment_plan.steps, start=1)
        )
        lines.append("- 成功判据：")
        lines.extend(
            f"  - {criterion}"
            for criterion in experiment_plan.success_criteria
        )
        lines.append("- 安全约束：")
        lines.extend(
            f"  - {constraint}"
            for constraint in experiment_plan.safety_constraints
        )
    lines.extend(
        [
            f"- 人工决策：`{human_decision.decision}`",
            f"- 决策范围：{human_decision.scope}",
            f"- 说明：{human_decision.note}",
            "",
            "## 7. 完整消息轨迹",
            "",
        ]
    )
    for index, message in enumerate(messages, start=1):
        lines.extend(
            [
                (
                    f"{index}. `{message.sender}` → `{message.recipient}` "
                    f"(`{message.message_type}`)"
                ),
                f"   - task_id：`{message.task_id}`",
                (
                    "   - evidence_ids："
                    + (", ".join(message.evidence_ids) or "[]")
                ),
                f"   - claim：{message.claim}",
                f"   - confidence：{message.confidence:.3f}",
                (
                    "   - open_questions："
                    + ("；".join(message.open_questions) or "[]")
                ),
            ]
        )

    lines.extend(["", "## 8. 工具调用与状态轨迹", ""])
    for item in evidence:
        lines.append(
            f"- `{item.evidence_id}` `{item.tool_name}` status=`{item.status}`；"
            f"query=`{item.query}`。"
        )
    lines.append(f"- 节点轨迹：{' → '.join(trace)}")

    lines.extend(
        [
            "",
            "## 9. 安全与结论边界",
            "",
            "- 系统只读本目录内的脱敏教学数据，不写入 PLC、MES 或 DCS。",
            "- 候选排序是排查优先级，不是因果概率；投票不能替代证据。",
            "- 验证实验必须由人工负责人审批，最终根因需由质量、工艺和设备负责人会签。",
            "- 当前报告不把相关性写成因果，也不作为生产控制、报警设定或 EHS 依据。",
        ]
    )
    return "\n".join(lines)
