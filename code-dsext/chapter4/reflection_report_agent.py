from __future__ import annotations

import argparse
import difflib
import re
from dataclasses import dataclass
from typing import Sequence

from industrial_tools import (
    RCAOutcome,
    RCARequest,
    ToolObservation,
    build_demo_request,
    parse_batch_ids,
    render_trace,
)
from plan_solve_rca_agent import PlanSolveRCAAgent


@dataclass(frozen=True)
class ReviewItem:
    criterion: str
    status: str
    detail: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryRecord:
    record_type: str
    content: str


@dataclass(frozen=True)
class ReflectionOutcome:
    status: str
    original_report: str
    revised_report: str
    review_items: tuple[ReviewItem, ...]
    diff: str
    memory: tuple[MemoryRecord, ...]
    evidence: tuple[ToolObservation, ...]


class ReflectionMemory:
    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []

    def add(self, record_type: str, content: str) -> None:
        self.records.append(MemoryRecord(record_type, content))


class ReportReviewer:
    def review(
        self,
        request: RCARequest,
        candidate: RCAOutcome,
    ) -> tuple[ReviewItem, ...]:
        items: list[ReviewItem] = []
        valid_evidence_ids = {item.evidence_id for item in candidate.evidence}
        cited_ids = set(re.findall(r"\[(EV-\d{3})\]", candidate.report))
        missing_ids = sorted(valid_evidence_ids - cited_ids)
        unknown_ids = sorted(cited_ids - valid_evidence_ids)
        if missing_ids or unknown_ids:
            items.append(
                ReviewItem(
                    "数据引用",
                    "revise",
                    f"未引用：{missing_ids or '无'}；未知引用：{unknown_ids or '无'}。",
                )
            )
        else:
            items.append(
                ReviewItem(
                    "数据引用",
                    "pass",
                    "每条工具证据均可由报告中的证据 ID 定位。",
                    tuple(sorted(valid_evidence_ids)),
                )
            )

        comparison = next(
            (
                item
                for item in candidate.evidence
                if item.tool_name == "compare_time_windows"
                and item.status == "success"
            ),
            None,
        )
        items.append(
            ReviewItem(
                "基线与批次",
                "pass" if comparison else "revise",
                (
                    "正常组、异常组与可比产品/产线均已显式记录。"
                    if comparison
                    else "缺少成功的正常组—异常组对比，不能形成根因排序。"
                ),
                (comparison.evidence_id,) if comparison else (),
            )
        )

        causal_patterns = ("根因已确认", "唯一根因", "确定由", "直接导致")
        violations = [
            pattern for pattern in causal_patterns if pattern in candidate.report
        ]
        items.append(
            ReviewItem(
                "因果措辞",
                "revise" if violations else "pass",
                (
                    f"发现过度因果措辞：{', '.join(violations)}。"
                    if violations
                    else "候选项保持为数据支持的假设，没有把相关性写成因果。"
                ),
            )
        )

        profile_ids: list[str] = []
        event_types: set[str] = set()
        for observation in candidate.evidence:
            if (
                observation.tool_name != "get_batch_profile"
                or observation.status != "success"
            ):
                continue
            profile_ids.append(observation.evidence_id)
            events = observation.data.get("events", ())
            if isinstance(events, tuple):
                event_types.update(
                    str(event.get("event_type", ""))
                    for event in events
                    if isinstance(event, dict)
                )
        has_confounding = {
            "material_change",
            "recipe_version",
        }.issubset(event_types)
        items.append(
            ReviewItem(
                "遗漏变量与混杂因素",
                "revise" if has_confounding else "pass",
                (
                    "物料批次与程序版本在异常组同时变化；排序必须明确为排查优先级，"
                    "并要求单变量对照。"
                    if has_confounding
                    else "当前事件证据未显示两个已知因素同时变化。"
                ),
                tuple(profile_ids),
            )
        )

        safe = all(
            phrase in candidate.report
            for phrase in ("不写入 PLC", "人工审批", "不把相关性")
        )
        items.append(
            ReviewItem(
                "安全边界",
                "pass" if safe else "revise",
                (
                    "只读边界、人工审批和相关性限制均已声明。"
                    if safe
                    else "报告缺少完整的只读、人工审批或因果限制声明。"
                ),
            )
        )

        expected_batches = set(request.normal_batch_ids + request.abnormal_batch_ids)
        report_batches = set(re.findall(r"\bBATCH-[A-Z0-9-]+\b", candidate.report))
        unexpected = sorted(report_batches - expected_batches)
        items.append(
            ReviewItem(
                "批次引用",
                "revise" if unexpected else "pass",
                (
                    f"报告含非分析组批次：{', '.join(unexpected)}；"
                    "它只能作为失败调用记录，不能支撑根因。"
                    if unexpected
                    else "用于结论的批次引用均属于本次分析组。"
                ),
            )
        )
        return tuple(items)


class ReflectionReportAgent:
    def __init__(self, reviewer: ReportReviewer | None = None) -> None:
        self.reviewer = reviewer or ReportReviewer()

    def run(
        self,
        request: RCARequest,
        candidate: RCAOutcome,
    ) -> ReflectionOutcome:
        memory = ReflectionMemory()
        memory.add("execution", candidate.report)
        review_items = self.reviewer.review(request, candidate)
        review_text = "\n".join(
            f"- [{item.status}] {item.criterion}：{item.detail}"
            for item in review_items
        )
        memory.add("reflection", review_text)
        revised_report = self._revise(candidate.report, review_items)
        memory.add("revision", revised_report)
        diff = "\n".join(
            difflib.unified_diff(
                candidate.report.splitlines(),
                revised_report.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        status = (
            "revised"
            if any(item.status == "revise" for item in review_items)
            else "accepted"
        )
        return ReflectionOutcome(
            status,
            candidate.report,
            revised_report,
            review_items,
            diff,
            tuple(memory.records),
            candidate.evidence,
        )

    def _revise(
        self,
        report: str,
        review_items: Sequence[ReviewItem],
    ) -> str:
        revised = report
        replacements = {
            "根因已确认": "仍待验证的候选根因",
            "唯一根因": "当前优先排查候选",
            "确定由": "数据提示可能与",
            "直接导致": "与异常同期出现",
        }
        for source, target in replacements.items():
            revised = revised.replace(source, target)

        revisions = [
            item
            for item in review_items
            if item.status == "revise"
        ]
        lines = [
            "### Reflection 复核修订",
            "",
            "- [尚无证据] 以下候选排序只代表排查优先级，不是因果概率。",
            "- [尚无证据] 混杂因素必须通过单变量对照实验进一步区分。",
        ]
        lines.extend(f"- {item.detail}" for item in revisions)
        block = "\n".join(lines) + "\n\n"
        marker = "## 5. 建议补充采集的数据"
        if marker in revised:
            revised = revised.replace(marker, block + marker, 1)
        else:
            revised += "\n\n" + block.rstrip()
        return revised


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行生产异常根因报告 Reflection 复核智能体",
    )
    parser.add_argument(
        "--scenario",
        choices=("confounded", "default", "tool-failure", "insufficient"),
        default="confounded",
    )
    parser.add_argument("--normal-batches", help="逗号分隔的正常批次")
    parser.add_argument("--abnormal-batches", help="逗号分隔的异常批次")
    parser.add_argument("--focus", default="产品一次合格率突然下降")
    parser.add_argument("--show-original", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
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
        candidate = PlanSolveRCAAgent().run(
            request,
            scenario=args.scenario,
        )
        outcome = ReflectionReportAgent().run(request, candidate)
    except (OSError, ValueError) as error:
        parser.exit(1, f"运行失败：{error}\n")
    if args.show_trace:
        print(render_trace(candidate.trace))
        print()
    if args.show_original:
        print(outcome.original_report)
        print()
    print("# Reflection 评审结果")
    for item in outcome.review_items:
        print(f"- [{item.status}] {item.criterion}：{item.detail}")
    print()
    print(outcome.revised_report)
    if args.show_diff:
        print()
        print("# Reflection 前后差异")
        print(outcome.diff or "无差异")
    return 0 if candidate.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
