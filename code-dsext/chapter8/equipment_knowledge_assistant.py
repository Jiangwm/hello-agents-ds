from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from assistant import EquipmentKnowledgeSystem
from evaluations import run_evaluation
from memory import ApprovalContext, LongTermMemoryStore, WorkingMemory
from schemas import AssistantResponse


CHAPTER_DIR = Path(__file__).resolve().parent
DATA_DIR = CHAPTER_DIR / "data"


def _response_payload(response: AssistantResponse) -> dict[str, object]:
    return asdict(response)


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (frozenset, set)):
        return sorted(value)
    raise TypeError(f"无法序列化类型：{type(value).__name__}")


def _print_json(payload: object) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )


def _print_response(response: AssistantResponse, output_format: str) -> None:
    if output_format == "json":
        _print_json(_response_payload(response))
        return
    print(f"状态：{response.status}")
    print(response.answer)
    if response.citations:
        print("\n引用：")
        for citation in response.citations:
            location = (
                f"p.{citation.page}"
                if citation.page is not None
                else "无页码"
            )
            print(
                f"- {citation.document_id} v{citation.version} "
                f"{location} ¶{citation.paragraph_id} "
                f"(score={citation.score:.3f})"
            )
    if response.needed_information:
        print("\n需要补充：")
        for item in response.needed_information:
            print(f"- {item}")
    if response.limitations:
        print("\n边界：")
        for item in response.limitations:
            print(f"- {item}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线设备知识与维修记忆助手",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    ask_parser = subparsers.add_parser("ask", help="检索并生成带引用回答")
    ask_parser.add_argument("query")
    ask_parser.add_argument("--equipment-id", required=True)
    ask_parser.add_argument(
        "--role",
        default="equipment_engineer",
    )
    ask_parser.add_argument("--top-k", type=int, default=4)
    ask_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    evaluation_parser = subparsers.add_parser(
        "evaluate",
        help="运行固定离线质量评估",
    )
    evaluation_parser.add_argument(
        "--cases",
        type=Path,
        default=DATA_DIR / "evaluation_cases.json",
    )
    evaluation_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    remember_parser = subparsers.add_parser(
        "remember",
        help="生成候选记忆，并在显式确认后持久化",
    )
    remember_parser.add_argument("--session-id", required=True)
    remember_parser.add_argument("--equipment-id", required=True)
    remember_parser.add_argument("--alert", required=True)
    remember_parser.add_argument(
        "--evidence-id",
        action="append",
        default=[],
    )
    remember_parser.add_argument("--hypothesis")
    remember_parser.add_argument(
        "--hypothesis-state",
        choices=("open", "excluded", "confirmed"),
        default="open",
    )
    remember_parser.add_argument(
        "--next-step",
        action="append",
        default=[],
    )
    remember_parser.add_argument("--retention-days", type=int, default=180)
    remember_parser.add_argument("--store", type=Path, required=True)
    remember_parser.add_argument("--confirm", action="store_true")
    remember_parser.add_argument("--approval-id")
    remember_parser.add_argument("--approver")
    remember_parser.add_argument(
        "--approver-role",
        default="equipment_engineer",
    )
    remember_parser.add_argument("--reason")
    remember_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    forget_parser = subparsers.add_parser(
        "forget",
        help="经数据治理审批删除指定设备的长期记忆",
    )
    forget_parser.add_argument("--equipment-id", required=True)
    forget_parser.add_argument("--store", type=Path, required=True)
    forget_parser.add_argument("--approval-id", required=True)
    forget_parser.add_argument("--approver", required=True)
    forget_parser.add_argument(
        "--approver-role",
        default="data_steward",
    )
    forget_parser.add_argument("--reason", required=True)
    forget_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    demo_parser = subparsers.add_parser(
        "demo",
        help="运行规格中的四类典型任务",
    )
    demo_parser.add_argument("--equipment-id", default="CNC-03")
    demo_parser.add_argument("--role", default="equipment_engineer")
    demo_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            report = run_evaluation(DATA_DIR, args.cases)
            if args.format == "json":
                _print_json(report)
            else:
                for name, value in report.items():
                    print(f"{name}: {value}")
            return 0
        if args.command == "remember":
            working = WorkingMemory(
                session_id=args.session_id,
                equipment_id=args.equipment_id,
                alert=args.alert,
            )
            for evidence_id in args.evidence_id:
                working.add_evidence(evidence_id)
            if args.hypothesis:
                working.add_hypothesis(
                    args.hypothesis,
                    state=args.hypothesis_state,
                )
            for next_step in args.next_step:
                working.add_next_step(next_step)
            candidate = working.create_candidate(
                retention_days=args.retention_days
            )
            if not args.confirm:
                payload = {
                    "status": "requires_confirmation",
                    "candidate": asdict(candidate),
                }
                if args.format == "json":
                    _print_json(payload)
                else:
                    print("候选记忆尚未持久化，需要用户显式确认。")
                    print(f"candidate_id: {candidate.candidate_id}")
                return 2
            if not all((args.approval_id, args.approver, args.reason)):
                raise ValueError(
                    "确认写入时必须提供 approval-id、approver 和 reason"
                )
            approval = ApprovalContext(
                approval_id=args.approval_id,
                approver=args.approver,
                approver_role=args.approver_role,
                reason=args.reason,
                approved_at=datetime.now(timezone.utc),
            )
            memory = LongTermMemoryStore(args.store).confirm(
                candidate,
                approval,
            )
            payload = {"status": "stored", "memory": asdict(memory)}
            if args.format == "json":
                _print_json(payload)
            else:
                print(f"记忆已确认并保存：{memory.memory_id}")
            return 0
        if args.command == "forget":
            approval = ApprovalContext(
                approval_id=args.approval_id,
                approver=args.approver,
                approver_role=args.approver_role,
                reason=args.reason,
                approved_at=datetime.now(timezone.utc),
            )
            deleted_count = LongTermMemoryStore(
                args.store
            ).delete_equipment_memories(
                equipment_id=args.equipment_id,
                approval=approval,
            )
            payload = {
                "status": "completed",
                "deleted_count": deleted_count,
                "equipment_id": args.equipment_id,
                "approval_id": approval.approval_id,
            }
            if args.format == "json":
                _print_json(payload)
            else:
                print(
                    f"删除请求已执行，移除 {deleted_count} 条长期记忆。"
                )
            return 0
        if args.command == "demo":
            system = EquipmentKnowledgeSystem.from_data_dir(DATA_DIR)
            working = WorkingMemory(
                session_id="CASE-CNC03-DEMO",
                equipment_id=args.equipment_id,
                alert="主轴过热和风机转速波动",
            )
            working.add_evidence("WO-CNC03-20260718")
            working.add_hypothesis("冷却滤网再次堵塞", state="open")
            working.add_hypothesis("润滑油位不足", state="excluded")
            tasks = (
                "查找 CNC-03 主轴过热的官方检查顺序",
                "总结这台设备过去三个月主轴过热相似告警及已验证原因",
                "比较当前主轴过热和风机转速波动与最近两次工单的共同点",
                "列出仍未确认的假设，不要复述已排除项",
            )
            runs = []
            for task in tasks:
                response = system.ask(
                    task,
                    equipment_id=args.equipment_id,
                    role=args.role,
                    top_k=2 if "共同点" in task else 4,
                    working_memory=(
                        working if "未确认的假设" in task else None
                    ),
                )
                runs.append(
                    {
                        "task": task,
                        "response": _response_payload(response),
                    }
                )
            completed = all(
                run["response"]["status"] == "answered"
                for run in runs
            )
            payload = {
                "status": "completed" if completed else "partial",
                "runs": runs,
            }
            if args.format == "json":
                _print_json(payload)
            else:
                for run in runs:
                    print(f"\n任务：{run['task']}")
                    response_payload = run["response"]
                    print(f"状态：{response_payload['status']}")
                    print(response_payload["answer"])
            return 0 if completed else 2
        system = EquipmentKnowledgeSystem.from_data_dir(DATA_DIR)
        response = system.ask(
            args.query,
            equipment_id=args.equipment_id,
            role=args.role,
            top_k=args.top_k,
        )
        _print_response(response, args.format)
        return 0 if response.status == "answered" else 2
    except (OSError, ValueError) as error:
        print(f"执行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
