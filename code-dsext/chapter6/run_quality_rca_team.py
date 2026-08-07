from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from schemas.models import AnalysisTask, WorkflowConfig, build_demo_task
from workflows.conversation_workflow import ConversationQualityWorkflow
from workflows.state_graph_workflow import StateGraphQualityWorkflow


CHAPTER_DIR = Path(__file__).resolve().parent
DATA_DIR = CHAPTER_DIR / "data"


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(1, f"参数错误：{message}\n")


def _batch_ids(value: str) -> tuple[str, ...]:
    batch_ids = tuple(
        item.strip() for item in value.split(",") if item.strip()
    )
    if not batch_ids:
        raise argparse.ArgumentTypeError("批次列表不能为空")
    return batch_ids


def build_parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(
        description="运行多智能体质量根因分析团队",
    )
    parser.add_argument(
        "--framework",
        choices=("state-graph", "conversation"),
        default="state-graph",
    )
    parser.add_argument(
        "--scenario",
        choices=("default", "insufficient"),
        default="default",
    )
    parser.add_argument("--normal-batches", type=_batch_ids)
    parser.add_argument("--abnormal-batches", type=_batch_ids)
    parser.add_argument(
        "--focus",
        default="产品尺寸均值上移且一次合格率下降",
    )
    parser.add_argument(
        "--human-decision",
        choices=("approve", "defer", "reject"),
        default="defer",
    )
    parser.add_argument("--review-threshold", type=float, default=0.72)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--min-new-evidence", type=int, default=1)
    parser.add_argument(
        "--output",
        choices=("markdown", "json"),
        default="markdown",
    )
    return parser


def _task_from_args(args: argparse.Namespace) -> AnalysisTask:
    if bool(args.normal_batches) != bool(args.abnormal_batches):
        raise ValueError("自定义分析必须同时提供正常组和异常组")
    if args.normal_batches:
        return AnalysisTask(
            task_id="QRA-CUSTOM",
            focus=args.focus,
            normal_batch_ids=args.normal_batches,
            abnormal_batch_ids=args.abnormal_batches,
            scenario="custom",
        )
    return build_demo_task(args.scenario)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        task = _task_from_args(args)
        config = WorkflowConfig(
            review_threshold=args.review_threshold,
            max_rounds=args.max_rounds,
            max_tool_calls=args.max_tool_calls,
            min_new_evidence=args.min_new_evidence,
        )
        workflow_class = (
            StateGraphQualityWorkflow
            if args.framework == "state-graph"
            else ConversationQualityWorkflow
        )
        result = workflow_class(DATA_DIR, config).run(
            task,
            human_decision=args.human_decision,
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"运行失败：{error}\n")
    if args.output == "json":
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(result.report)
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
