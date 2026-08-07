from __future__ import annotations

from pathlib import Path

from schemas.models import build_demo_task
from workflows.conversation_workflow import ConversationQualityWorkflow
from workflows.state_graph_workflow import StateGraphQualityWorkflow


DATA_DIR = Path(__file__).resolve().parent / "data"


def _run_comparison():
    task = build_demo_task("default")
    graph = StateGraphQualityWorkflow(DATA_DIR).run(
        task,
        human_decision="approve",
    )
    conversation = ConversationQualityWorkflow(DATA_DIR).run(
        task,
        human_decision="approve",
    )
    return task, graph, conversation


def _comparison_passes(graph, conversation) -> bool:
    graph_top = next(
        (item.hypothesis_id for item in graph.hypotheses if item.status == "candidate"),
        None,
    )
    conversation_top = next(
        (item.hypothesis_id for item in conversation.hypotheses if item.status == "candidate"),
        None,
    )
    return (
        graph.status == "completed"
        and conversation.status == "completed"
        and graph_top is not None
        and graph_top == conversation_top
    )


def _render_comparison(task, graph, conversation) -> str:
    dimensions = (
        ("状态可见性", "显式节点与路由，可回放", "需从消息顺序重建"),
        ("并发", "三条调查线并行", "专业角色顺序发言"),
        ("恢复", "可从补数节点继续", "通常重放后续对话"),
        ("类型约束", "统一状态和消息 dataclass", "消息契约可用但状态较分散"),
        ("人工介入", "独立 human_gate 节点", "人工作为最后一轮角色"),
        ("调试成本", "节点多但定位直接", "实现简单但长对话定位较难"),
    )
    graph_top = next(
        (item for item in graph.hypotheses if item.status == "candidate"),
        None,
    )
    conversation_top = next(
        (item for item in conversation.hypotheses if item.status == "candidate"),
        None,
    )
    lines = [
        "# 同一质量案例的两种编排比较",
        "",
        f"- 任务：`{task.task_id}`",
        (
            f"- 共同最高候选：`{graph_top.hypothesis_id if graph_top else '无'}` / "
            f"`{conversation_top.hypothesis_id if conversation_top else '无'}`"
        ),
        "",
        "| 工程维度 | LangGraph-style 状态图 | AutoGen-style 对话链 |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {dimension} | {graph_value} | {conversation_value} |"
        for dimension, graph_value, conversation_value in dimensions
    )
    lines.extend(
        [
            "",
            "| 运行指标 | 状态图 | 对话链 |",
            "| --- | ---: | ---: |",
            (
                f"| 只读工具调用数 | {graph.tool_call_count} | "
                f"{conversation.tool_call_count} |"
            ),
            f"| 结构化消息数 | {len(graph.messages)} | {len(conversation.messages)} |",
            f"| 状态/轮次 | {graph.status}/{graph.rounds} | "
            f"{conversation.status}/{conversation.rounds} |",
            (
                f"| 最高证据评分 | {graph.reviews[0].total_score if graph.reviews else 0.0:.3f} | "
                f"{conversation.reviews[0].total_score if conversation.reviews else 0.0:.3f} |"
            ),
            "",
            "主实现选择状态图：工业质量分析更看重状态可见、失败恢复、人工中断和审计回放。",
            "对话链保留为同案例基线，用于展示角色对话直观但恢复与定位成本更高。",
        ]
    )
    return "\n".join(lines)


def compare_workflows() -> str:
    return _render_comparison(*_run_comparison())


def main() -> int:
    task, graph, conversation = _run_comparison()
    print(_render_comparison(task, graph, conversation))
    return 0 if _comparison_passes(graph, conversation) else 2


if __name__ == "__main__":
    raise SystemExit(main())
