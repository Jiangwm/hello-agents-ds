from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


CHAPTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHAPTER_DIR))

from industrial_agents.agents import (
    FunctionCallIndustrialAgent,
    PlanSolveIndustrialAgent,
    ReActIndustrialAgent,
    ReflectionIndustrialAgent,
    SummaryAgent,
)
from industrial_agents.exceptions import IndustrialAgentError
from industrial_agents.llms import MockLLMAdapter
from industrial_agents.schemas import (
    IndustrialAgentConfig,
    IndustrialTask,
    LLMResponse,
    RunStatus,
    ToolCall,
)
from industrial_agents.tools import create_default_tool_registry


READ_ONLY_TOOLS = frozenset(
    {
        "dataset_profile",
        "trend_analysis",
        "anomaly_detection",
        "specification_lookup",
    }
)


def build_config() -> IndustrialAgentConfig:
    return IndustrialAgentConfig(
        data_root=CHAPTER_DIR / "data",
        tool_allowlist=READ_ONLY_TOOLS,
        max_rounds=5,
        max_input_rows=1_000,
        max_result_rows=20,
        timeout_seconds=10.0,
    )


def build_task() -> IndustrialTask:
    return IndustrialTask(
        task_id="CH7-DEMO-E101",
        objective="分析 E-101 振动升高与质量评分下降的离线样例",
        equipment_scope=("E-101",),
        data_sources=("industrial_timeseries.csv",),
        inputs={"file_path": "industrial_timeseries.csv"},
        constraints=(
            "只读分析",
            "所有数字引用工具证据",
            "相关性不得表述为因果性",
        ),
    )


def build_agent(mode: str, config: IndustrialAgentConfig):
    registry = create_default_tool_registry()
    if mode == "summary":
        return SummaryAgent(
            "工业摘要智能体",
            MockLLMAdapter(
                ["数据画像显示 E-101 存在需进一步核查的局部波动。"]
            ),
            registry,
            config,
        )
    if mode == "react":
        return ReActIndustrialAgent(
            "工业 ReAct 智能体",
            MockLLMAdapter(
                [
                    (
                        '{"action":{"tool":"dataset_profile","arguments":'
                        '{"file_path":"industrial_timeseries.csv"}}}'
                    ),
                    (
                        '{"action":{"tool":"anomaly_detection","arguments":'
                        '{"file_path":"industrial_timeseries.csv",'
                        '"field":"vibration_mm_s","equipment_id":"E-101",'
                        '"z_threshold":1.5}}}'
                    ),
                    (
                        '{"finish":"已发现振动异常候选；这只是统计共现，'
                        '需要设备与质量负责人复核。"}'
                    ),
                ]
            ),
            registry,
            config,
        )
    if mode == "plan":
        plan = (
            '{"plan":['
            '{"id":"S1","description":"计算分组滚动趋势",'
            '"tool":"trend_analysis","arguments":{'
            '"file_path":"industrial_timeseries.csv",'
            '"field":"vibration_mm_s","frequency":"hour",'
            '"rolling_window":2,"group_by":"equipment_id"}},'
            '{"id":"S2","description":"检索规范、字典与案例",'
            '"tool":"specification_lookup","arguments":'
            '{"query":"vibration_mm_s"}}]}'
        )
        return PlanSolveIndustrialAgent(
            "工业计划执行智能体",
            MockLLMAdapter(
                [
                    plan,
                    (
                        "分组趋势和本地知识共同提示 E-101 振动时段需要复核；"
                        "当前证据不能确认最终根因。"
                    ),
                ]
            ),
            registry,
            config,
        )
    if mode == "reflection":
        draft = SummaryAgent(
            "工业摘要智能体",
            MockLLMAdapter(["草稿：E-101 的异常由振动升高导致。"]),
            registry,
            config,
        )
        return ReflectionIndustrialAgent(
            "工业反思智能体",
            MockLLMAdapter(
                [
                    (
                        "复核结论：样例只支持振动升高与质量评分下降在时间上共现；"
                        "根因仍未知，需人工验证。"
                    )
                ]
            ),
            draft,
        )
    if mode == "function":
        calls = (
            ToolCall(
                call_id="call-profile",
                name="dataset_profile",
                arguments={"file_path": "industrial_timeseries.csv"},
            ),
            ToolCall(
                call_id="call-specification",
                name="specification_lookup",
                arguments={"query": "vibration_mm_s"},
            ),
        )
        return FunctionCallIndustrialAgent(
            "工业函数调用智能体",
            MockLLMAdapter(
                [
                    LLMResponse(tool_calls=calls),
                    (
                        "结构化工具结果已取得；异常候选与教学规范需要人工联合复核。"
                    ),
                ]
            ),
            registry,
            config,
        )
    raise ValueError(f"不支持的模式：{mode}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线运行工业数据分析智能体框架示例"
    )
    parser.add_argument(
        "--mode",
        choices=("all", "summary", "react", "plan", "reflection", "function"),
        default="all",
    )
    parser.add_argument("--show-audit", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = build_config()
    task = build_task()
    modes = (
        ("summary", "react", "plan", "reflection", "function")
        if args.mode == "all"
        else (args.mode,)
    )
    statuses: list[RunStatus] = []
    try:
        for mode in modes:
            result = build_agent(mode, config).run(task)
            statuses.append(result.status)
            print(f"\n{'=' * 72}\n{mode.upper()}\n{'=' * 72}")
            print(result.to_markdown())
            if args.show_audit:
                print("\n## 工具审计")
                for record in result.audit_records:
                    evidence = ",".join(record.evidence_ids) or "-"
                    print(
                        f"- {record.call_id} | {record.tool_name} | "
                        f"{record.status.value} | {evidence}"
                    )
    except (IndustrialAgentError, OSError, ValueError) as error:
        print(f"运行失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0 if all(status == RunStatus.COMPLETED for status in statuses) else 2


if __name__ == "__main__":
    raise SystemExit(main())
