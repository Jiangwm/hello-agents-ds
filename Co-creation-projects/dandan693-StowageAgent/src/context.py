"""
上下文工程 → 往模型的脑子里"塞什么、塞多少、怎么摆"。

有句话很关键：**模型的智商是固定的，你能改变的只有上下文。**
同一个模型，喂给它的提示词不一样，表现能差出十万八千里。

这个文件只干一件事：把散落各处的东西拼成一封给模型的信：

    系统提示词（你是谁、有什么工具、守什么规矩）
      + 长期笔记（跨对话该记住的事）
      + 检索到的资料（RAG 查回来的规则原文）
      + 最近几轮对话（短期记忆）
      + 这次的问题
    = 最终发给模型的 messages

另外顺手做了"裁长度"：模型能读的字数是有限的（叫"上下文窗口"），
塞太多了会被截断或者直接报错，所以超预算时先把老对话丢掉。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

#: 给"历史对话"留的字数预算。超了就丢最早的。
DEFAULT_HISTORY_BUDGET = 2000


class ContextBuilder:
    def __init__(
        self,
        system_prompt: str = "",
        history_budget: int = DEFAULT_HISTORY_BUDGET,
    ) -> None:
        self.system_prompt = system_prompt
        self.history_budget = history_budget

    # ------------------------------------------------------------------
    @staticmethod
    def trim_history(
        history: List[Dict[str, str]],
        budget: int = DEFAULT_HISTORY_BUDGET,
    ) -> List[Dict[str, str]]:
        """从最新的一轮往前数，攒够 budget 个字就停。"""
        kept: List[Dict[str, str]] = []
        used = 0
        for msg in reversed(history):
            size = len(msg.get("content", ""))
            if used + size > budget and kept:
                break
            kept.append(msg)
            used += size
        return list(reversed(kept))

    # ------------------------------------------------------------------
    def build_api_messages(
        self,
        question: str,
        history: List[Dict[str, str]] | None = None,
        extra_system: str = "",
    ) -> List[Dict[str, str]]:
        """拼出一封"普通问答"的信（不涉及工具调用）。

        给 --no-llm 之外的纯问答场景用，比如让模型直接读资料回答。
        """
        system = self.system_prompt + ("\n\n" + extra_system if extra_system else "")
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend(self.trim_history(history or [], self.history_budget))
        messages.append({"role": "user", "content": question})
        return messages

    # ------------------------------------------------------------------
    @staticmethod
    def build_agent_scratchpad(
        steps: List[Tuple[str, str, str]],
    ) -> str:
        """把已经走过的步骤拼成一段"草稿纸"，回喂给模型。

        steps 里每一项是 (思考, 行动, 观察结果)。
        有了这段草稿纸，模型才知道自己刚才干过什么、别重复做。
        """
        if not steps:
            return ""
        lines: List[str] = []
        for thought, action, observation in steps:
            if thought:
                lines.append(f"思考：{thought}")
            if action:
                lines.append(f"行动：{action}")
            if observation:
                lines.append(f"观察：{observation}")
        return "\n".join(lines)
