"""
性能评估 → 给自己的 Agent 打分。

评估方法论有很多（BFCL 测工具调用、GAIA 测通用能力……）。
对小项目来说，抓住一个核心就够：

    **把"感觉它挺好"变成"跑 20 个用例，过了 17 个"。**

本文件做了两层评估，是很典型的"分层测试"思路：

  第一层：工具自检（离线、免费、秒出结果）
    不调用大模型，直接测工具函数的输入输出。
    判定标准是**确定的**——算错了就是算错了，没有"大概对"。
    → 代码逻辑一旦改动，先跑这层，它是最快的安全网。

  第二层：端到端评估（需要联网调大模型）
    真的把问题丢给 Agent，看它的回答有没有答到点子上。
    因为大模型的回答每次可能不一样，所以判定不能要求"一字不差"，
    而是"必须包含这几个关键词"。
    → 这层的分数天然会抖动，跑一次 88%、再跑一次 94% 都正常。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List


def _normalize(text: str) -> str:
    """把空白字符全去掉，再比对关键词。

    为什么需要这一步：大模型写中文时常常在数字和单位之间插空格，
    "40尺箱" 会被写成 "40 尺箱"，"0.4 秒" 会被写成 "0.4秒"。
    如果断言死抠字面，就会出现**回答其实完全正确、却被判成失败**的假失败。

    这是个真实的教训：端到端评估里的"假失败"比"漏报"更消耗信任，
    每次误报都要人工去看一遍，跑多了就没人信这套测试了。
    """
    return re.sub(r"\s+", "", text)


def _hit(answer: str, keywords: List[str], exclude: List[str] | None = None) -> bool:
    """回答里是否包含全部关键词（忽略空格差异），且不含排除词。"""
    body = _normalize(answer)
    if not all(_normalize(kw) in body for kw in keywords):
        return False
    return not any(_normalize(kw) in body for kw in (exclude or []))


# ======================================================================
# 第一层：工具自检（不花钱、可重复、结果确定）
# ======================================================================
def check_tools(registry, case_file: str | Path) -> Dict[str, Any]:
    cases = json.loads(Path(case_file).read_text(encoding="utf-8"))["工具自检"]

    results: List[Dict[str, Any]] = []
    for case in cases:
        tool = registry.get(case["工具"])
        if tool is None:
            results.append({**case, "通过": False, "实际输出": f"找不到工具 {case['工具']}"})
            continue
        try:
            output = tool.run(case["输入"])
        except Exception as exc:  # noqa: BLE001
            output = f"抛异常：{exc}"

        ok = _hit(output, case["必须包含"], case.get("不能包含"))
        results.append({**case, "通过": ok, "实际输出": output})

    passed = sum(1 for r in results if r["通过"])
    return {"总数": len(results), "通过": passed, "明细": results}


# ======================================================================
# 第二层：端到端评估（要调大模型）
# ======================================================================
def evaluate_agent(
    ask: Callable[[str], Dict[str, Any]],
    case_file: str | Path,
    verbose: bool = True,
) -> Dict[str, Any]:
    """`ask` 是一个"问一句、返回结果字典"的函数（通常是 agent.run）。"""
    cases = json.loads(Path(case_file).read_text(encoding="utf-8"))["端到端"]

    results: List[Dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        started = time.time()
        try:
            out = ask(case["问题"])
            answer = out.get("answer", "")
            n_steps = len(out.get("steps", []))
        except Exception as exc:  # noqa: BLE001
            answer, n_steps = f"跑挂了：{exc}", 0
        elapsed = time.time() - started

        ok = _hit(answer, case["必须包含"])
        results.append(
            {
                "问题": case["问题"],
                "分类": case.get("分类", "未分类"),
                "通过": ok,
                "回答": answer,
                "步数": n_steps,
                "耗时": round(elapsed, 2),
            }
        )

        if verbose:
            print(f"  [{i}/{len(cases)}] {'✅' if ok else '❌'} {case['问题'][:36]}…")

    passed = sum(1 for r in results if r["通过"])
    total_time = sum(r["耗时"] for r in results)

    # 按分类汇总，找出哪一类最弱 —— 这才是评估真正的用处
    by_category: Dict[str, List[bool]] = {}
    for r in results:
        by_category.setdefault(r["分类"], []).append(r["通过"])

    return {
        "总数": len(results),
        "通过": passed,
        "准确率": round(passed / len(results) * 100, 1) if results else 0.0,
        "平均耗时": round(total_time / len(results), 2) if results else 0.0,
        "分类准确率": {
            k: round(sum(v) / len(v) * 100, 1) for k, v in by_category.items()
        },
        "明细": results,
    }


# ======================================================================
# 打印报告
# ======================================================================
def print_tool_report(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 56)
    print("第一层：工具自检（不调用大模型）")
    print("=" * 56)
    for r in report["明细"]:
        print(f"  {'✅' if r['通过'] else '❌'} {r['案例']}")
        print(f"      输入：{r['输入']}")
        if not r["通过"]:
            print(f"      期望包含：{r['必须包含']}")
            print(f"      实际输出：{r['实际输出'].replace(chr(10), ' | ')[:160]}")
    print(f"\n  结果：{report['通过']} / {report['总数']} 通过")


def print_agent_report(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 56)
    print("第二层：端到端评估（调用了大模型）")
    print("=" * 56)
    for r in report["明细"]:
        flag = "✅" if r["通过"] else "❌"
        print(f"\n{flag} [{r['分类']}] {r['问题']}")
        print(f"   回答：{r['回答'].replace(chr(10), ' ')[:200]}")
        print(f"   用了 {r['步数']} 步，耗时 {r['耗时']} 秒")

    print("\n" + "-" * 56)
    print(f"  总准确率：{report['准确率']}%  ({report['通过']}/{report['总数']})")
    print(f"  平均耗时：{report['平均耗时']} 秒/题")
    print("  分类准确率：")
    for name, acc in report["分类准确率"].items():
        print(f"    {name:<12} {acc}%")
