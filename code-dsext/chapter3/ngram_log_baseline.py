from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Iterable
from typing import Sequence

from embedding_retrieval import MaintenanceLog
from embedding_retrieval import load_logs
from tokenizer_inspection import industrial_tokenize


KEYWORD_TOKEN_SPLITS = {
    "温度高": ("温度", "高"),
    "温度升高": ("温度", "高"),
    "温度偏高": ("温度", "高"),
    "振动升高": ("振动", "高"),
    "电流偏高": ("电流", "高"),
    "压力偏低": ("压力", "低"),
    "压力下降": ("压力", "下降"),
    "温度波动": ("温度", "波动"),
    "尺寸偏大": ("尺寸", "大"),
}


@dataclass(frozen=True)
class NGramSummary:
    order: int
    total: int
    unique: int
    top_items: tuple[tuple[tuple[str, ...], int], ...]


class NGramAnalyzer:
    def __init__(
        self,
        token_sequences: Sequence[Sequence[str]],
    ) -> None:
        self.token_sequences = tuple(
            tuple(token for token in sequence if token)
            for sequence in token_sequences
        )
        self._counts = {
            order: Counter(
                ngram
                for sequence in self.token_sequences
                for ngram in self._ngrams(sequence, order)
            )
            for order in (1, 2, 3)
        }

    @classmethod
    def from_token_sequences(
        cls,
        token_sequences: Sequence[Sequence[str]],
    ) -> NGramAnalyzer:
        return cls(token_sequences)

    @classmethod
    def from_logs(cls, logs: Sequence[MaintenanceLog]) -> NGramAnalyzer:
        sequences = []
        for log in logs:
            tokens = [log.equipment_type]
            for keyword in log.keywords:
                tokens.extend(_keyword_tokens(keyword))
            sequences.append(tuple(tokens))
        return cls(sequences)

    @staticmethod
    def _ngrams(
        tokens: Sequence[str],
        order: int,
    ) -> Iterable[tuple[str, ...]]:
        for index in range(len(tokens) - order + 1):
            yield tuple(tokens[index:index + order])

    def top_ngrams(
        self,
        order: int,
        limit: int = 10,
    ) -> tuple[tuple[tuple[str, ...], int], ...]:
        if order not in self._counts:
            raise ValueError("order 仅支持 1、2、3")
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        return tuple(
            sorted(
                self._counts[order].items(),
                key=lambda item: (-item[1], item[0]),
            )[:limit]
        )

    def conditional_probability(
        self,
        context: Sequence[str],
        next_token: str,
    ) -> float:
        order = len(context) + 1
        if order not in self._counts:
            raise ValueError("仅支持一阶或二阶上下文")
        context_tuple = tuple(context)
        denominator = sum(
            count
            for ngram, count in self._counts[order].items()
            if ngram[:-1] == context_tuple
        )
        if denominator == 0:
            return 0.0
        numerator = self._counts[order][context_tuple + (next_token,)]
        return numerator / denominator

    def summarize(self, order: int, limit: int = 10) -> NGramSummary:
        counts = self._counts[order]
        return NGramSummary(
            order=order,
            total=sum(counts.values()),
            unique=len(counts),
            top_items=self.top_ngrams(order, limit),
        )


def _keyword_tokens(keyword: str) -> tuple[str, ...]:
    if keyword in KEYWORD_TOKEN_SPLITS:
        return KEYWORD_TOKEN_SPLITS[keyword]
    tokens = industrial_tokenize(keyword)
    return tokens or (keyword,)


def _pattern_explanation(ngram: Sequence[str]) -> str:
    if any(any(character.isdigit() for character in token) for token in ngram):
        return "型号或测量值与相邻线索反复共现，便于定位同类记录。"
    if any("/" in token or "mm" in token for token in ngram):
        return "测量值和工业单位构成固定局部表达，分词时不应拆散。"
    if any(token in {"轴承", "主轴", "电机", "叶轮"} for token in ngram):
        return "部件与现象形成局部故障短语，但共现不等于根因。"
    return "相邻关键词形成稳定短语，仍需回看原文确认语义。"


def format_report(analyzer: NGramAnalyzer, limit: int = 10) -> str:
    lines = ["# 维修日志 N-gram 基线", ""]
    labels = {1: "unigram", 2: "bigram", 3: "trigram"}
    for order in (1, 2, 3):
        summary = analyzer.summarize(order, limit)
        lines.extend(
            (
                f"## {labels[order]}",
                "",
                f"- 总数：{summary.total}",
                f"- 不重复数：{summary.unique}",
            )
        )
        for ngram, count in summary.top_items:
            lines.append(f"- `{' → '.join(ngram)}`：{count}")
        lines.append("")
    lines.extend(("## 上下文概率比较", ""))
    for context, next_token in (
        (("轴承", "温度"), "高"),
        (("电机", "温度"), "高"),
    ):
        probability = analyzer.conditional_probability(context, next_token)
        lines.append(
            f"- P({next_token} | {' → '.join(context)}) = {probability:.3f}"
        )
    lines.extend(("", "## 三个高频共现模式", ""))
    for ngram, count in analyzer.top_ngrams(2, limit=3):
        lines.append(
            f"- `{' → '.join(ngram)}`（{count} 次）："
            f"{_pattern_explanation(ngram)}"
        )
    lines.extend(("",))
    lines.extend(
        (
            "## 局限",
            "",
            "- 稀疏：三元组合数量增长快，低频上下文难以可靠估计。",
            "- 未登录词：新型号、新缩写和新故障词没有历史计数。",
            "- 长距离依赖：局部 N-gram 无法稳定连接相隔较远的原因与措施。",
            "- Transformer 通过注意力建模远距离关联，但仍需证据审计。",
        )
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统计维修日志 N-gram 共现模式")
    parser.add_argument("--top", type=int, default=10, help="每阶显示数量")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analyzer = NGramAnalyzer.from_logs(load_logs())
        print(format_report(analyzer, args.top))
    except (OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
