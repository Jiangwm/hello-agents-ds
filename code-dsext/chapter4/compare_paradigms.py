from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Sequence

from industrial_tools import RCAOutcome, build_demo_request
from plan_solve_rca_agent import PlanSolveRCAAgent
from react_rca_agent import ReActRCAAgent
from reflection_report_agent import ReflectionReportAgent


@dataclass(frozen=True)
class ParadigmSummary:
    case_id: str
    paradigm: str
    status: str
    tool_calls: int
    reused_evidence: int
    completeness: float
    auditability: float
    result: str


def compare_paradigms(
    scenario: str = "default",
) -> tuple[tuple[ParadigmSummary, ...], tuple[RCAOutcome, RCAOutcome], str]:
    request = build_demo_request(scenario)
    react = ReActRCAAgent().run(request, scenario=scenario)
    plan_solve = PlanSolveRCAAgent().run(request, scenario=scenario)
    reflection = ReflectionReportAgent().run(request, plan_solve)
    summaries = (
        ParadigmSummary(
            request.case_id,
            "ReAct",
            react.status,
            react.tool_call_count,
            0,
            react.evidence_completeness,
            react.auditability,
            "逐轮观察后生成候选排序",
        ),
        ParadigmSummary(
            request.case_id,
            "Plan-and-Solve",
            plan_solve.status,
            plan_solve.tool_call_count,
            0,
            plan_solve.evidence_completeness,
            plan_solve.auditability,
            "按显式计划保留每步状态",
        ),
        ParadigmSummary(
            request.case_id,
            "Reflection",
            reflection.status,
            0,
            len(plan_solve.evidence),
            plan_solve.evidence_completeness,
            plan_solve.auditability,
            "复用不可变证据并保留修订差异",
        ),
    )
    return summaries, (react, plan_solve), reflection.revised_report


def render_comparison(summaries: Sequence[ParadigmSummary]) -> str:
    lines = [
        "| 案例 | 范式 | 状态 | 新增工具调用 | 复用证据 | 证据完整度 | 可审计率 | 结果特征 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {item.case_id} | {item.paradigm} | {item.status} | "
        f"{item.tool_calls} | {item.reused_evidence} | "
        f"{item.completeness:.0%} | "
        f"{item.auditability:.0%} | {item.result} |"
        for item in summaries
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="横向比较三种生产异常 RCA 范式")
    parser.add_argument(
        "--scenario",
        choices=("default", "tool-failure", "insufficient"),
        default="default",
    )
    parser.add_argument("--show-reports", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summaries, outcomes, reflected_report = compare_paradigms(args.scenario)
    print(render_comparison(summaries))
    if args.show_reports:
        for outcome in outcomes:
            print()
            print(outcome.report)
        print()
        print(reflected_report)
    return 0 if all(item.status == "completed" for item in outcomes) else 2


if __name__ == "__main__":
    raise SystemExit(main())
