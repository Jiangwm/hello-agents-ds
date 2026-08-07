from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from embedding_retrieval import ClassificationResult
from embedding_retrieval import LOG_DATA_PATH
from embedding_retrieval import MaintenanceLog
from embedding_retrieval import MaintenanceLogIndex
from embedding_retrieval import SearchResult
from embedding_retrieval import load_logs
from embedding_retrieval import split_logs
from llm_event_extraction import ExtractionAudit
from llm_event_extraction import MaintenanceEvent
from llm_event_extraction import audit_extraction
from llm_event_extraction import extract_offline_event


@dataclass(frozen=True)
class SplitSummary:
    strategy: str
    training_count: int
    validation_count: int
    training_window: str
    validation_window: str
    source_event_overlap: int


@dataclass(frozen=True)
class QueryAnalysis:
    query: str
    split: SplitSummary
    classification: ClassificationResult
    search_results: tuple[SearchResult, ...]
    structured_event: MaintenanceEvent
    extraction_audit: ExtractionAudit


@dataclass(frozen=True)
class ValidationCase:
    log_id: str
    expected_category: str
    predicted_category: str
    correct: bool
    top_result_ids: tuple[str, ...]


@dataclass(frozen=True)
class CategoryMetric:
    category: str
    total: int
    correct: int
    accuracy: float


@dataclass(frozen=True)
class ValidationSummary:
    total: int
    correct: int
    accuracy: float
    category_metrics: tuple[CategoryMetric, ...]
    error_cases: tuple[ValidationCase, ...]


def _time_window(logs: Sequence[MaintenanceLog]) -> str:
    times = sorted(log.event_time for log in logs)
    return (
        f"{times[0].isoformat(timespec='minutes')}"
        f" ～ {times[-1].isoformat(timespec='minutes')}"
    )


class MaintenanceLogSystem:
    def __init__(
        self,
        training_logs: Sequence[MaintenanceLog],
        validation_logs: Sequence[MaintenanceLog],
        split_strategy: str,
    ) -> None:
        self.training_logs = tuple(training_logs)
        self.validation_logs = tuple(validation_logs)
        self.split_strategy = split_strategy
        self.index = MaintenanceLogIndex(self.training_logs)
        overlap = {
            log.source_event_id for log in self.training_logs
        } & {
            log.source_event_id for log in self.validation_logs
        }
        self.split_summary = SplitSummary(
            strategy=split_strategy,
            training_count=len(self.training_logs),
            validation_count=len(self.validation_logs),
            training_window=_time_window(self.training_logs),
            validation_window=_time_window(self.validation_logs),
            source_event_overlap=len(overlap),
        )

    @classmethod
    def from_dataset(
        cls,
        path: Path = LOG_DATA_PATH,
        split_strategy: str = "time",
        validation_ratio: float = 0.2,
    ) -> MaintenanceLogSystem:
        training, validation = split_logs(
            load_logs(path),
            strategy=split_strategy,
            validation_ratio=validation_ratio,
        )
        return cls(training, validation, split_strategy)

    def analyze(self, query: str, top_k: int = 5) -> QueryAnalysis:
        search_results = self.index.search(query, top_k=top_k)
        classification = self.index.classify(query, neighbors=top_k)
        structured_event = extract_offline_event(query)
        extraction_payload = asdict(structured_event)
        extraction_audit = audit_extraction(query, extraction_payload)
        return QueryAnalysis(
            query=query,
            split=self.split_summary,
            classification=classification,
            search_results=search_results,
            structured_event=structured_event,
            extraction_audit=extraction_audit,
        )

    def validate(self) -> ValidationSummary:
        cases = []
        per_category = Counter()
        correct_per_category = Counter()
        for log in self.validation_logs:
            query = f"{log.equipment_type} {log.raw_text}"
            classification = self.index.classify(query)
            results = self.index.search(query)
            correct = classification.category == log.fault_category
            per_category[log.fault_category] += 1
            correct_per_category[log.fault_category] += int(correct)
            cases.append(
                ValidationCase(
                    log_id=log.log_id,
                    expected_category=log.fault_category,
                    predicted_category=classification.category,
                    correct=correct,
                    top_result_ids=tuple(result.log_id for result in results),
                )
            )
        correct_count = sum(case.correct for case in cases)
        category_metrics = tuple(
            CategoryMetric(
                category=category,
                total=total,
                correct=correct_per_category[category],
                accuracy=round(correct_per_category[category] / total, 6),
            )
            for category, total in sorted(per_category.items())
        )
        return ValidationSummary(
            total=len(cases),
            correct=correct_count,
            accuracy=round(correct_count / len(cases), 6),
            category_metrics=category_metrics,
            error_cases=tuple(case for case in cases if not case.correct),
        )

    def annotation_template(
        self,
        query: str,
        top_k: int = 5,
    ) -> dict[str, object]:
        results = self.index.search(query, top_k=top_k)
        return {
            "query": query,
            "label_guide": {
                "relevance": ["相关", "部分相关", "不相关"],
                "error_type": [
                    "文字相似但原因不同",
                    "措辞不同但故障相同",
                    "设备不一致",
                    "其他",
                ],
            },
            "judgments": [
                {
                    **asdict(result),
                    "relevance": "",
                    "error_type": "",
                    "annotator": "",
                    "notes": "",
                }
                for result in results
            ],
        }

    @staticmethod
    def format_markdown(analysis: QueryAnalysis) -> str:
        certainty_labels = {
            "fact": "事实",
            "speculation": "推测",
            "unknown": "未知",
        }
        event = analysis.structured_event
        field_rows = (
            ("设备", event.equipment),
            ("现象", event.phenomenon),
            ("候选原因", event.candidate_cause),
            ("处理措施", event.action_taken),
        )
        lines = [
            "# 维修日志语义检索与归类报告",
            "",
            "## 数据切分",
            "",
            f"- 策略：{analysis.split.strategy}",
            f"- 训练集：{analysis.split.training_count} 条，"
            f"{analysis.split.training_window}",
            f"- 验证集：{analysis.split.validation_count} 条，"
            f"{analysis.split.validation_window}",
            f"- 同源事件重叠：{analysis.split.source_event_overlap}",
            "",
            "## 事实与证据",
            "",
            f"- 查询原文：{analysis.query}",
        ]
        for label, field in field_rows:
            value = field.value or "未提及"
            evidence = field.evidence or "无"
            certainty = certainty_labels[field.certainty]
            lines.append(
                f"- {label}：{value}；证据：`{evidence}`；状态：{certainty}"
            )
        for uncertainty in event.uncertainties:
            lines.append(f"- 未知项：{uncertainty}")

        lines.extend(
            (
                "",
                "## 故障归类",
                "",
                f"- 候选类别：{analysis.classification.category}",
                f"- 近邻投票置信度：{analysis.classification.confidence:.3f}",
                "- 性质：基于相似案例的待复核假设，不是根因结论。",
                "",
                f"## 相似案例 Top-{len(analysis.search_results)}",
                "",
                "| 排名 | 分数 | 日志 ID | 类别 | 原文 |",
                "| ---: | ---: | --- | --- | --- |",
            )
        )
        for rank, result in enumerate(analysis.search_results, start=1):
            text = result.raw_text.replace("|", "\\|")
            lines.append(
                f"| {rank} | {result.score:.4f} | `{result.log_id}` | "
                f"{result.fault_category} | {text} |"
            )

        audit_status = "通过" if analysis.extraction_audit.passed else "未通过"
        lines.extend(
            (
                "",
                "## 幻觉审计",
                "",
                f"- 证据约束：{audit_status}",
            )
        )
        if analysis.extraction_audit.issues:
            for issue in analysis.extraction_audit.issues:
                lines.append(
                    f"- {issue.severity} / {issue.field}：{issue.reason}"
                )
        else:
            lines.append("- 未发现脱离原文的事实字段。")
        lines.extend(
            (
                "",
                "## 安全边界",
                "",
                "- 系统仅用于离线教学、历史案例检索与故障类别预归类。",
                "- 结果不替代设备工程师、正式检修规程或安全联锁。",
                "- 系统不连接 PLC、MES、DCS，也不写入生产参数。",
            )
        )
        return "\n".join(lines)

    @staticmethod
    def format_validation(summary: ValidationSummary) -> str:
        lines = [
            "# 验证集归类与误差分析",
            "",
            f"- 样本数：{summary.total}",
            f"- 正确数：{summary.correct}",
            f"- 总体准确率：{summary.accuracy:.3f}",
            "",
            "## 分类别结果",
            "",
            "| 类别 | 正确/总数 | 准确率 |",
            "| --- | ---: | ---: |",
        ]
        for metric in summary.category_metrics:
            lines.append(
                f"| {metric.category} | {metric.correct}/{metric.total} | "
                f"{metric.accuracy:.3f} |"
            )
        lines.extend(("", "## 错误案例", ""))
        if not summary.error_cases:
            lines.append("- 当前脱敏样例未出现类别错误，仍需人工相关性标注。")
        for case in summary.error_cases:
            lines.append(
                f"- `{case.log_id}`：期望 {case.expected_category}，"
                f"预测 {case.predicted_category}；近邻 "
                f"{', '.join(case.top_result_ids)}"
            )
        lines.extend(
            (
                "",
                "准确率只描述该脱敏样例，不替代 Top-5 人工相关性和错误类型审查。",
            )
        )
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="设备维修日志语义检索与归类系统")
    parser.add_argument(
        "--split",
        choices=("time", "equipment"),
        default="time",
        help="训练/验证切分方式",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    query_parser = subparsers.add_parser("query", help="检索、归类并审计一条日志")
    query_parser.add_argument("text", help="维修日志或查询描述")
    query_parser.add_argument("--top-k", type=int, default=5)
    query_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )

    validate_parser = subparsers.add_parser("validate", help="评估时间留出集")
    validate_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )

    annotation_parser = subparsers.add_parser(
        "annotation-template",
        help="输出 Top-K 人工相关性标注模板",
    )
    annotation_parser.add_argument("text", help="待标注查询")
    annotation_parser.add_argument("--top-k", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        system = MaintenanceLogSystem.from_dataset(split_strategy=args.split)
        if args.command == "query":
            analysis = system.analyze(args.text, args.top_k)
            if args.format == "json":
                print(json.dumps(asdict(analysis), ensure_ascii=False, indent=2))
            else:
                print(system.format_markdown(analysis))
        elif args.command == "validate":
            summary = system.validate()
            if args.format == "json":
                print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
            else:
                print(system.format_validation(summary))
        else:
            template = system.annotation_template(args.text, args.top_k)
            print(json.dumps(template, ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
