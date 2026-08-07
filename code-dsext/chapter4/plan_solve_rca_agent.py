from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Mapping, Sequence

from industrial_tools import (
    ActionTrace,
    IndustrialToolExecutor,
    RCAOutcome,
    RCARequest,
    ToolObservation,
    build_demo_request,
    build_rca_report,
    parse_batch_ids,
    render_trace,
)


@dataclass(frozen=True)
class PlanStep:
    step_number: int
    name: str
    tool_name: str
    tool_input: Mapping[str, object]


class RCAPlanner:
    def plan(self, request: RCARequest, scenario: str) -> tuple[PlanStep, ...]:
        steps: list[PlanStep] = []
        if scenario == "tool-failure":
            steps.append(
                PlanStep(
                    1,
                    "验证外部给定的可疑批次",
                    "get_batch_profile",
                    {"batch_id": "BATCH-NOT-FOUND"},
                )
            )
        for batch_id in request.abnormal_batch_ids:
            steps.append(
                PlanStep(
                    len(steps) + 1,
                    f"读取异常批次 {batch_id} 的质量与事件画像",
                    "get_batch_profile",
                    {"batch_id": batch_id},
                )
            )
        steps.extend(
            [
                PlanStep(
                    len(steps) + 1,
                    "构造正常组与异常组并比较一次合格率",
                    "compare_time_windows",
                    {
                        "normal_batch_ids": request.normal_batch_ids,
                        "abnormal_batch_ids": request.abnormal_batch_ids,
                    },
                ),
                PlanStep(
                    len(steps) + 2,
                    "筛查工艺参数漂移",
                    "detect_parameter_shift",
                    {
                        "normal_batch_ids": request.normal_batch_ids,
                        "abnormal_batch_ids": request.abnormal_batch_ids,
                    },
                ),
                PlanStep(
                    len(steps) + 3,
                    "核对排名第一的漂移参数规范",
                    "lookup_process_spec",
                    {"product_id": "$product_id", "parameter": "$top_parameter"},
                ),
                PlanStep(
                    len(steps) + 4,
                    "基于证据形成候选根因排序与反证",
                    "synthesize_candidates",
                    {},
                ),
            ]
        )
        return tuple(steps)


class PlanExecutor:
    def __init__(self, max_steps: int = 8) -> None:
        if not 1 <= max_steps <= 8:
            raise ValueError("max_steps 必须在 1 到 8 之间")
        self.max_steps = max_steps

    def execute(
        self,
        plan: Sequence[PlanStep],
    ) -> tuple[tuple[ToolObservation, ...], tuple[ActionTrace, ...]]:
        executor = IndustrialToolExecutor(max_calls=self.max_steps)
        trace: list[ActionTrace] = []
        for step in plan[: self.max_steps]:
            if step.tool_name == "synthesize_candidates":
                supporting_ids = tuple(
                    item.evidence_id
                    for item in executor.observations
                    if item.tool_name
                    in {
                        "compare_time_windows",
                        "detect_parameter_shift",
                        "lookup_process_spec",
                    }
                    and item.status in {"success", "insufficient"}
                )
                synthesis_status = "success" if supporting_ids else "blocked"
                trace.append(
                    ActionTrace(
                        step.step_number,
                        step.name,
                        "synthesize_candidates[{}]",
                        (
                            "已基于不可变工具证据形成候选排序与反证。"
                            if supporting_ids
                            else "缺少上游证据，无法形成候选排序。"
                        ),
                        (
                            f"复用证据 {','.join(supporting_ids)} 的数据范围"
                            if supporting_ids
                            else "未取得数据"
                        ),
                        ",".join(supporting_ids) or "-",
                        synthesis_status,
                    )
                )
                continue
            resolved_input = self._resolve_input(step.tool_input, executor.observations)
            if resolved_input is None:
                trace.append(
                    ActionTrace(
                        step.step_number,
                        step.name,
                        f"{step.tool_name}[{dict(step.tool_input)}]",
                        "依赖的上游证据不可用，步骤明确标记为 blocked。",
                        "未取得数据",
                        "-",
                        "blocked",
                    )
                )
                continue
            observation = executor.execute(step.tool_name, **resolved_input)
            trace.append(
                ActionTrace(
                    step.step_number,
                    step.name,
                    f"{step.tool_name}[{resolved_input}]",
                    observation.summary,
                    observation.data_range,
                    observation.evidence_id,
                    observation.status,
                )
            )
        if len(plan) > self.max_steps:
            trace.append(
                ActionTrace(
                    self.max_steps + 1,
                    "计划超过有界执行上限。",
                    "Finish",
                    f"剩余 {len(plan) - self.max_steps} 个步骤未执行并已记录。",
                    "复用当前已取得的证据范围",
                    "-",
                    "blocked",
                )
            )
        return tuple(executor.observations), tuple(trace)

    def _resolve_input(
        self,
        tool_input: Mapping[str, object],
        evidence: Sequence[ToolObservation],
    ) -> dict[str, object] | None:
        if "$product_id" not in tool_input.values():
            return dict(tool_input)
        comparison = next(
            (
                item
                for item in reversed(evidence)
                if item.tool_name == "compare_time_windows"
                and item.status == "success"
            ),
            None,
        )
        shift = next(
            (
                item
                for item in reversed(evidence)
                if item.tool_name == "detect_parameter_shift"
                and item.status in {"success", "insufficient"}
            ),
            None,
        )
        if comparison is None or shift is None:
            return None
        shifts = shift.data.get("shifts", ())
        if not isinstance(shifts, tuple) or not shifts:
            return None
        top = shifts[0]
        if not isinstance(top, dict):
            return None
        return {
            "product_id": comparison.data["product_id"],
            "parameter": top["parameter"],
        }


class PlanSolveRCAAgent:
    def __init__(
        self,
        planner: RCAPlanner | None = None,
        executor: PlanExecutor | None = None,
    ) -> None:
        self.planner = planner or RCAPlanner()
        self.executor = executor or PlanExecutor()

    def run(
        self,
        request: RCARequest,
        scenario: str = "default",
    ) -> RCAOutcome:
        plan = self.planner.plan(request, scenario)
        evidence, trace = self.executor.execute(plan)
        synthesis_completed = any(
            item.action == "synthesize_candidates[{}]"
            and item.status == "success"
            for item in trace
        )
        report, status, completeness, auditability = build_rca_report(
            request,
            evidence,
            "Plan-and-Solve 结构化排查",
            candidate_synthesis_completed=synthesis_completed,
        )
        return RCAOutcome(
            "Plan-and-Solve",
            status,
            report,
            trace,
            evidence,
            len(evidence),
            completeness,
            auditability,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行生产异常根因分析 Plan-and-Solve 智能体",
    )
    parser.add_argument(
        "--scenario",
        choices=("default", "tool-failure", "insufficient"),
        default="default",
    )
    parser.add_argument("--normal-batches", help="逗号分隔的正常批次")
    parser.add_argument("--abnormal-batches", help="逗号分隔的异常批次")
    parser.add_argument("--focus", default="产品一次合格率突然下降")
    parser.add_argument("--max-steps", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--show-trace", action="store_true")
    return parser


def _request_from_args(args: argparse.Namespace) -> RCARequest:
    if bool(args.normal_batches) != bool(args.abnormal_batches):
        raise ValueError("自定义分析必须同时提供正常组和异常组")
    if args.normal_batches:
        return RCARequest(
            parse_batch_ids(args.normal_batches),
            parse_batch_ids(args.abnormal_batches),
            args.focus,
        )
    return build_demo_request(args.scenario)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = _request_from_args(args)
        outcome = PlanSolveRCAAgent(
            executor=PlanExecutor(max_steps=args.max_steps)
        ).run(request, scenario=args.scenario)
    except (OSError, ValueError) as error:
        parser.exit(1, f"运行失败：{error}\n")
    if args.show_trace:
        print(render_trace(outcome.trace))
        print()
    print(outcome.report)
    return 0 if outcome.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
