"""
记忆机制 → 让 Agent 不是"聊完就忘"。

这里实现两层记忆：

    短期记忆（工作记忆）  —— 这次对话说过什么。就是 messages 列表，有长度上限，
                             太长了要丢掉最早的（否则 token 会爆）。
    长期记忆（笔记）      —— 跨对话要记住的事。写进一个 md 文件，下次还在。

一句话记住区别：短期记忆是"刚才聊到哪了"，长期记忆是"这个用户有什么习惯"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List


class Memory:
    def __init__(
        self,
        note_path: str | Path | None = None,
        max_messages: int = 12,
    ) -> None:
        #: 短期记忆：一串 {"role": ..., "content": ...}
        self.messages: List[Dict[str, str]] = []
        self.max_messages = max_messages

        self.note_path = Path(note_path) if note_path else None
        #: 长期记忆：一串字符串（每条就是一条笔记）
        self.notes: List[str] = []
        if self.note_path and self.note_path.exists():
            self.notes = [
                line.strip("- ").strip()
                for line in self.note_path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("-")
            ]

    # ------------------------------------------------------------------
    # 短期记忆
    # ------------------------------------------------------------------
    def add(self, role: str, content: str) -> None:
        """记一轮对话。role 是 'user' 或 'assistant'。"""
        self.messages.append({"role": role, "content": content})
        self._forget_old()

    def _forget_old(self) -> None:
        """超出上限就把最早的对话丢掉。

        这是最粗暴的做法（"上下文工程"会做得更聪明：
        把老对话压缩成摘要，而不是直接扔掉）。但对小项目够用。
        """
        if len(self.messages) > self.max_messages:
            overflow = len(self.messages) - self.max_messages
            self.messages = self.messages[overflow:]

    def recent(self, n: int | None = None) -> List[Dict[str, str]]:
        return self.messages[-n:] if n else list(self.messages)

    # ------------------------------------------------------------------
    # 长期记忆
    # ------------------------------------------------------------------
    def add_note(self, text: str) -> None:
        """记一条长期笔记，并立刻落盘。"""
        self.notes.append(text)
        if self.note_path:
            self.note_path.parent.mkdir(parents=True, exist_ok=True)
            body = "# 配载智能体 · 长期笔记\n\n" + "\n".join(f"- {n}" for n in self.notes) + "\n"
            self.note_path.write_text(body, encoding="utf-8")

    def recall(self, keyword: str = "") -> List[str]:
        """按关键词翻笔记；不给关键词就返回全部。"""
        if not keyword:
            return list(self.notes)
        return [n for n in self.notes if keyword in n]

    def clear(self) -> None:
        self.messages.clear()
