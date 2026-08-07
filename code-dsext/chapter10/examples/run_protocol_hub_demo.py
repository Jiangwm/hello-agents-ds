from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


CHAPTER_DIR = Path(__file__).resolve().parents[1]
if str(CHAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(CHAPTER_DIR))

from protocol_hub import AnalysisTask, HubResult, build_default_hub


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行离线工业数据协议协作中枢教学场景"
    )
    parser.add_argument("--request-id", default="REQ-CH10-DEMO-001")
    parser.add_argument("--task-id", default="TASK-CH10-DEMO-001")
    parser.add_argument("--tenant-id", default="tenant-a")
    parser.add_argument("--line-id", default="LINE-A")
    parser.add_argument("--equipment-id", default="EQ-101")
    parser.add_argument("--measurement", default="vibration_mm_s")
    parser.add_argument(
        "--start-time",
        default="2026-07-29T08:00:00+08:00",
    )
    parser.add_argument(
        "--end-time",
        default="2026-07-29T09:00:00+08:00",
    )
    parser.add_argument("--alarm-code", default="VIB_HIGH")
    parser.add_argument(
        "--access-token",
        default="demo-token-tenant-a",
        help="仅用于本地教学身份映射；不会写入输出或审计",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json"),
        default="summary",
    )
    return parser


def render_summary(result: HubResult) -> str:
    lines = [
        "工业数据协议协作中枢",
        f"状态: {result.status}",
        f"request_id: {result.request_id}",
        (
            "ANP 服务: "
            + str(result.selected_service.get("service_id", "未选择"))
        ),
        "A2A 链路: "
        + " -> ".join(message.sender for message in result.messages),
        "事实: "
        + json.dumps(
            result.report.get("facts", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "假设: "
        + json.dumps(
            result.report.get("hypotheses", []),
            ensure_ascii=False,
        ),
        "未知: "
        + json.dumps(
            result.report.get("unknowns", []),
            ensure_ascii=False,
        ),
        f"审计记录: {len(result.audit_records)} 条",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = AnalysisTask(
        task_id=args.task_id,
        request_id=args.request_id,
        tenant_id=args.tenant_id,
        line_id=args.line_id,
        equipment_id=args.equipment_id,
        measurement=args.measurement,
        start_time=args.start_time,
        end_time=args.end_time,
        alarm_code=args.alarm_code,
    )
    try:
        result = build_default_hub(CHAPTER_DIR).run(
            task,
            access_token=args.access_token,
        )
    except (KeyError, TypeError, ValueError) as error:
        print(f"配置或输入错误: {error}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(
            json.dumps(
                result.to_dict(),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_summary(result))
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
