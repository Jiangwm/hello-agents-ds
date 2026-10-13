"""
配载智能体 · 命令行入口

把 src/ 里那七个模块串起来，变成一个能用的东西。

常用命令::

    python main.py tools                         # 看看有哪些工具
    python main.py ask "40尺箱写哪个贝号？"        # 问一个问题
    python main.py ask "..." --show-steps         # 顺便看它思考了几个来回
    python main.py ask "..." --no-llm             # 只用检索，不调大模型（免费、看原理）
    python main.py chat                           # 连续对话
    python main.py eval --offline                 # 工具自检（免费、秒出）
    python main.py eval --online                  # 端到端评估（要调大模型）
    python main.py eval --all                     # 两层都跑
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让 `python main.py` 在任意目录下执行都能找到 src 包
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import (  # noqa: E402
    BayTool,
    CoordTool,
    KnowledgeTool,
    LLM,
    LLMError,
    Memory,
    ReActAgent,
    Retriever,
    StowageCheckTool,
    ToolRegistry,
    check_tools,
    evaluate_agent,
)
from src.evaluate import print_agent_report, print_tool_report  # noqa: E402

KB_FILE = ROOT / "data" / "配载知识库.txt"
CASE_FILE = ROOT / "data" / "测试用例.json"
NOTE_FILE = ROOT / "outputs" / "长期笔记.md"


# ======================================================================
def build_registry(mode: str = "tfidf") -> ToolRegistry:
    """装好 4 个工具（工具系统）。"""
    print(f"⏳ 正在加载知识库并建索引（模式：{mode}）…")
    retriever = Retriever(KB_FILE, mode=mode)
    retriever.index()
    print(f"✅ 知识库已就绪，共 {len(retriever.chunks)} 段资料")

    registry = ToolRegistry()
    registry.register(BayTool())
    registry.register(CoordTool())
    registry.register(StowageCheckTool())
    registry.register(KnowledgeTool(retriever, top_k=3))
    return registry


def build_agent(args) -> ReActAgent:
    llm = LLM()
    print(f"✅ 已接入大模型：{llm.model}")
    registry = build_registry(args.mode)
    # chat / eval 子命令不一定带了 --show-steps 参数，用 getattr 兜一下默认值
    agent = ReActAgent(
        llm=llm,
        registry=registry,
        memory=Memory(note_path=NOTE_FILE),
        max_steps=getattr(args, "max_steps", 5),
        verbose=getattr(args, "show_steps", False),
    )
    return agent


# ======================================================================
# 命令一：列出工具
# ======================================================================
def cmd_tools(args) -> None:
    registry = build_registry(args.mode)
    print("\n可用工具：\n")
    for name in registry.names:
        tool = registry.get(name)
        print(f"  🔧 {name}")
        print(f"     {tool.description}\n")
    print("想试试？python main.py ask \"贝位 01 和 03 合成几号？\"")


# ======================================================================
# 命令二：问一句（--no-llm 时只做检索，看 RAG 的原理）
# ======================================================================
def cmd_ask(args) -> None:
    if args.no_llm:
        registry = build_registry(args.mode)
        hits = registry.get("知识库检索").run(args.question)
        print("\n" + "=" * 56)
        print("【检索结果】（这些资料会被贴到问题前面，一起发给大模型）")
        print("=" * 56)
        print(hits)
        print("\n" + "=" * 56)
        print("【实际会发给大模型的提示词长这样】")
        print("=" * 56)
        print("只准根据下面资料回答，资料里没有就说「没有提到」。\n")
        print(f"资料：\n{hits}\n")
        print(f"问题：{args.question}")
        return

    agent = build_agent(args)
    result = agent.run(args.question)

    print("\n" + "=" * 56)
    print("最终答案")
    print("=" * 56)
    print(result["answer"])
    print("\n" + "-" * 56)
    print(f"思考-行动轮数：{len(result['steps'])}  是否正常结束：{result['ok']}")


# ======================================================================
# 命令三：连续对话（演示"短期记忆"）
# ======================================================================
def cmd_chat(args) -> None:
    agent = build_agent(args)
    print("\n进入连续对话模式。它能记住你前面说过的话（短期记忆）。")
    print("输入 exit / quit 退出，输入 :clear 清空记忆。\n")

    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", ":q"}:
            break
        if question == ":clear":
            agent.memory.clear()
            print("（已清空短期记忆）")
            continue

        result = agent.run(question)
        print(f"\n助手 > {result['answer']}\n")
        print("-" * 56)


# ======================================================================
# 命令四：评估
# ======================================================================
def cmd_eval(args) -> None:
    report = {}

    if args.offline or args.all:
        registry = build_registry("tfidf")  # 离线自检固定用 tfidf，结果才稳定
        report["工具自检"] = check_tools(registry, CASE_FILE)
        print_tool_report(report["工具自检"])

    if args.online or args.all:
        agent = build_agent(args)
        print("\n开始端到端评估（每一题都要调一次大模型，慢是正常的）…\n")
        report["端到端"] = evaluate_agent(agent.run, CASE_FILE, verbose=True)
        print_agent_report(report["端到端"])

    # 报告做增量合并：只跑了一层时，不要把另一层的历史结果抹掉
    out = ROOT / "outputs" / "评估报告.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    merged = {}
    if out.exists():
        try:
            merged = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            merged = {}
    merged.update(report)
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📄 报告已保存：{out}（本次覆盖的层：{'、'.join(report)}）")


# ======================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="配载智能体 —— 集装箱船配载规则问答与坐标计算",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mode", default="tfidf", choices=["tfidf", "vector"],
                        help="检索模式：tfidf（默认，秒开）或 vector（语义向量，首次要下模型）")
    parser.add_argument("--max-steps", type=int, default=5, help="ReAct 最多循环几轮")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("tools", help="列出所有工具")

    p_ask = sub.add_parser("ask", help="问一个问题")
    p_ask.add_argument("question", help="你要问的话")
    p_ask.add_argument("--show-steps", action="store_true", help="打印每一轮的思考过程")
    p_ask.add_argument("--no-llm", action="store_true", help="只用检索，不调用大模型")

    sub.add_parser("chat", help="连续对话")

    p_eval = sub.add_parser("eval", help="跑评估")
    p_eval.add_argument("--offline", action="store_true", help="只跑工具自检（不花钱）")
    p_eval.add_argument("--online", action="store_true", help="只跑端到端评估")
    p_eval.add_argument("--all", action="store_true", help="两层都跑")
    p_eval.add_argument("--show-steps", action="store_true", help="端到端评估时打印思考过程")

    args = parser.parse_args()
    if args.command == "eval" and not (args.offline or args.online or args.all):
        args.offline = True  # 默认跑最便宜的那层

    try:
        {
            "tools": cmd_tools,
            "ask": cmd_ask,
            "chat": cmd_chat,
            "eval": cmd_eval,
        }[args.command](args)
    except LLMError as exc:
        print(f"\n❌ 大模型调用出问题：{exc}")
        print("   检查一下 .env 里的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID。")
        return 1
    except FileNotFoundError as exc:
        print(f"\n❌ 找不到文件：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
