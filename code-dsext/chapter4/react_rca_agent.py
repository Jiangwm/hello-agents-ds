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
class ReActAction:
    thought: str
    tool_name: str
    tool_input: Mapping[str, object]


class OfflineReActPolicy:
    def next_action(
        self,
        request: RCARequest,
        evidence: Sequence[ToolObservation],
        scenario: str,
    ) -> ReActAction:
        if scenario == "tool-failure" and not evidence:
            return ReActAction(
                "先探测用户给出的可疑批次，确认它是否存在。",
                "get_batch_profile",
                {"batch_id": "BATCH-NOT-FOUND"},
            )

        profiled_batches = {
            str(item.data["batch"]["batch_id"])
            for item in evidence
            if item.tool_name == "get_batch_profile"
            and item.status == "success"
            and isinstance(item.data.get("batch"), dict)
        }
        for batch_id in request.abnormal_batch_ids:
            if batch_id not in profiled_batches:
                return ReActAction(
                    f"读取异常批次 {batch_id}，核查缺陷与相邻事件。",
                    "get_batch_profile",
                    {"batch_id": batch_id},
                )

        if not any(item.tool_name == "compare_time_windows" for item in evidence):
            return ReActAction(
                "异常批次已定位，比较正常组与异常组的一次合格率。",
                "compare_time_windows",
                {
                    "normal_batch_ids": request.normal_batch_ids,
                    "abnormal_batch_ids": request.abnormal_batch_ids,
                },
            )

        if not any(item.tool_name == "detect_parameter_shift" for item in evidence):
            return ReActAction(
                "质量差异已量化，筛查同一批次窗口内的工艺参数漂移。",
                "detect_parameter_shift",
                {
                    "normal_batch_ids": request.normal_batch_ids,
                    "abnormal_batch_ids": request.abnormal_batch_ids,
                },
            )

        if not any(item.tool_name == "lookup_process_spec" for item in evidence):
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
            if comparison and shift:
                shifts = shift.data.get("shifts", ())
                if isinstance(shifts, tuple) and shifts:
                    top = shifts[0]
                    if isinstance(top, dict):
                        return ReActAction(
                            "核对最显著漂移参数的适用工艺规范。",
                            "lookup_process_spec",
                            {
                                "product_id": comparison.data["product_id"],
                                "parameter": top["parameter"],
                            },
                        )

        return ReActAction(
            "当前可取得的证据已完成，保留未知项并生成报告。",
            "Finish",
            {},
        )


class ReActRCAAgent:
    def __init__(
        self,
        policy: OfflineReActPolicy | None = None,
        max_steps: int = 8,
    ) -> None:
        if not 1 <= max_steps <= 8:
            raise ValueError("max_steps 必须在 1 到 8 之间")
        self.policy = policy or OfflineReActPolicy()
        self.max_steps = max_steps

    def run(
        self,
        request: RCARequest,
        scenario: str = "default",
    ) -> RCAOutcome:
        executor = IndustrialToolExecutor(max_calls=self.max_steps)
        trace: list[ActionTrace] = []
        for round_number in range(1, self.max_steps + 1):
            action = self.policy.next_action(
                request,
                tuple(executor.observations),
                scenario,
            )
            if action.tool_name == "Finish":
                trace.append(
                    ActionTrace(
                        round_number,
                        action.thought,
                        "Finish",
                        "停止工具调用，使用已记录证据生成报告。",
                        "复用以上全部证据范围",
                        "-",
                        "finish",
                    )
                )
                break
            observation = executor.execute(action.tool_name, **action.tool_input)
            trace.append(
                ActionTrace(
                    round_number,
                    action.thought,
                    f"{action.tool_name}[{dict(action.tool_input)}]",
                    observation.summary,
                    observation.data_range,
                    observation.evidence_id,
                    observation.status,
                )
            )
        else:
            trace.append(
                ActionTrace(
                    self.max_steps + 1,
                    "已达到有界执行上限。",
                    "Finish",
                    "不再调用工具，按当前证据生成不完整报告。",
                    "复用当前已取得的证据范围",
                    "-",
                    "blocked",
                )
            )

        evidence = tuple(executor.observations)
        report, status, completeness, auditability = build_rca_report(
            request,
            evidence,
            "ReAct 探索式取证",
        )
        return RCAOutcome(
            "ReAct",
            status,
            report,
            tuple(trace),
            evidence,
            len(evidence),
            completeness,
            auditability,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行生产异常根因分析 ReAct 智能体",
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
        outcome = ReActRCAAgent(max_steps=args.max_steps).run(
            request,
            scenario=args.scenario,
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"运行失败：{error}\n")
    if args.show_trace:
        print(render_trace(outcome.trace))
        print()
    print(outcome.report)
    return 0 if outcome.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
