from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Sequence


INDUSTRIAL_TOKEN_PATTERN = re.compile(
    r"[A-Za-z]+(?:-[A-Za-z0-9]+)+"
    r"|[XYZUVWxyzuvw]轴"
    r"|[A-Za-z](?:/[A-Za-z])+"
    r"|(?:mm|cm|m)/s"
    r"|\d+(?:\.\d+)?"
    r"|[A-Za-z]+"
    r"|[\u4e00-\u9fff]+",
    re.IGNORECASE,
)
NAIVE_TOKEN_PATTERN = re.compile(
    r"[A-Za-z]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]+"
)
MODEL_PATTERN = re.compile(r"[A-Za-z]+(?:-[A-Za-z0-9]+)+")
AXIS_PATTERN = re.compile(r"[XYZUVWxyzuvw]轴")
SLASH_ABBREVIATION_PATTERN = re.compile(
    r"[A-Za-z](?:/[A-Za-z])+"
)
UNIT_PATTERN = re.compile(r"(?:mm|cm|m)/s", re.IGNORECASE)


@dataclass(frozen=True)
class TokenizerInspection:
    raw_text: str
    naive_tokens: tuple[str, ...]
    industrial_tokens: tuple[str, ...]
    protected_tokens: tuple[str, ...]
    issues: tuple[str, ...]


def industrial_tokenize(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in INDUSTRIAL_TOKEN_PATTERN.finditer(text))


def inspect_tokenization(text: str) -> TokenizerInspection:
    naive_tokens = tuple(NAIVE_TOKEN_PATTERN.findall(text))
    industrial_tokens = industrial_tokenize(text)
    protected = []
    issues = []
    checks = (
        (MODEL_PATTERN, "设备型号被拆分"),
        (AXIS_PATTERN, "轴名称被拆分"),
        (SLASH_ABBREVIATION_PATTERN, "工业缩写被拆分"),
        (UNIT_PATTERN, "工业单位被拆分"),
    )
    for pattern, label in checks:
        for match in pattern.finditer(text):
            token = match.group(0)
            protected.append(token)
            if token not in naive_tokens:
                issues.append(f"{label}：{token}")
    return TokenizerInspection(
        raw_text=text,
        naive_tokens=naive_tokens,
        industrial_tokens=industrial_tokens,
        protected_tokens=tuple(protected),
        issues=tuple(issues),
    )


def format_report(inspection: TokenizerInspection) -> str:
    issue_lines = (
        [f"- {issue}" for issue in inspection.issues]
        if inspection.issues
        else ["- 未发现已知工业符号破坏"]
    )
    lines = [
        "# 工业维修日志分词检查",
        "",
        f"- 原文：{inspection.raw_text}",
        f"- 普通正则切分：`{' | '.join(inspection.naive_tokens)}`",
        f"- 工业保护切分：`{' | '.join(inspection.industrial_tokens)}`",
        "",
        "## 已记录问题",
        "",
        *issue_lines,
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="比较维修日志普通分词与工业保护分词")
    parser.add_argument(
        "text",
        nargs="?",
        default="CNC-01的X轴振动达到7.2 mm/s，PLC报警",
        help="待检查的维修日志",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(format_report(inspect_tokenization(args.text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
