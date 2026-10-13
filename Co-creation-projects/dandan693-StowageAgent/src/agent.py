"""
ReAct 循环（Reason 想 + Act 做）。

ReAct = Reason（想）+ Act（做）。
它跟普通聊天的区别只有一句话：**普通聊天是"问一句答一句"，
ReAct 是"想一步、做一步、看结果、再想下一步"，能循环好几轮。**

这个循环的流程图如下，本文件就是它的代码版：

    ┌──────────────────────────────────────┐
    │  问题                                 │
    │    ↓                                 │
    │  思考（Thought）  ← 模型自己想        │
    │    ↓                                 │
    │  行动（Action）   ← 模型点名要哪个工具 │
    │    ↓                                 │
    │  观察（Observation）← 我们执行工具    │←─┐
    │    ↓                                 │  │ 没做完
    │  做完了吗？ ──没完───────────────────┼──┘
    │    │完                                │
    │    ↓                                 │
    │  最终答案（Final Answer）             │
    └──────────────────────────────────────┘

⚠️ 这里故意**没用任何框架**，全是手写的。因为 ReAct 的骨架就这么点东西，
   看懂这几十行，一个智能体内部在干什么就清楚了。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .context import ContextBuilder
from .llm import LLM
from .memory import Memory
from .tools import ToolRegistry

# ----------------------------------------------------------------------
# 系统提示词 —— 这封信决定了 Agent 的性格和纪律
# ----------------------------------------------------------------------
REACT_SYSTEM_PROMPT = """你是一名集装箱船配载助理，服务于码头配载员。

你的能力边界：
- 你可以调用下面列出的工具来获取准确结果。
- 你不知道的规则，要先用工具查，**绝对不要凭印象编造**。
- 如果工具查不到、资料里也没有，就直接回答"资料里没有提到"，不要猜。

可用工具：
{tools}

请严格按下面两种格式之一回答，不要加任何多余的话：

格式 A（还需要用工具时）：
思考：<你为什么需要这个工具>
行动：<工具名，必须是上面列出的名字之一>
行动输入：<给这个工具的输入，纯文本一行>

格式 B（已经能回答了）：
思考：<你的推理>
最终答案：<给用户的回答，中文，简洁，需要时引用资料编号>

铁律：
1. 一次只调用一个工具。
2. "行动："后面只能写工具名本身，不能带括号、不能带别的话。
3. **凡是涉及配载业务规则、图纸规则、文件读写约定这类问题，不管你自己觉不觉得知道答案，
   都必须先调用【知识库检索】把资料拿到手，再回答。**
   没检索过就直接说"资料里没有提到"，是错的。
4. 只在"已经检索过、工具也返回了结果"之后，才可以直接给最终答案。
   这时候还反复调用工具就是浪费。
5. 涉及具体数字（贝位、层号、坐标）时，必须以工具返回的结果为准，不要自己心算。
"""


class ReActAgent:
    """最小可用的 ReAct 智能体。"""

    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        memory: Memory | None = None,
        system_prompt: str | None = None,
        max_steps: int = 5,
        verbose: bool = True,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.memory = memory or Memory()
        self.max_steps = max_steps
        self.verbose = verbose

        self.system_prompt = (system_prompt or REACT_SYSTEM_PROMPT).format(
            tools=registry.describe()
        )
        self.context = ContextBuilder(self.system_prompt)

    # ------------------------------------------------------------------
    def run(self, question: str) -> Dict[str, Any]:
        """跑完一轮完整的思考-行动循环。

        返回 {'answer': 最终答案, 'steps': [(思考, 行动, 观察), …], 'ok': 是否正常结束}
        """
        steps: List[Tuple[str, str, str]] = []
        self.memory.add("user", question)

        for i in range(1, self.max_steps + 1):
            if self.verbose:
                print(f"\n── 第 {i} 轮 ──")

            reply = self._think(question, steps)
            thought, action, action_input, final = self._parse(reply)

            # 情况一：模型觉得可以收工了
            if final:
                if self.verbose:
                    print(f"  💭 思考：{thought}")
                    print(f"  ✅ 最终答案：{final}")
                self.memory.add("assistant", final)
                return {"answer": final, "steps": steps, "ok": True}

            # 情况二：模型要调工具
            if action:
                if self.verbose:
                    print(f"  💭 思考：{thought}")
                    print(f"  🔧 行动：{action}")
                    print(f"  📥 输入：{action_input}")
                observation = self._use_tool(action, action_input)
                if self.verbose:
                    preview = observation if len(observation) < 300 else observation[:300] + " …"
                    print(f"  👀 观察：{preview}")
                steps.append((thought, action, observation))
                continue

            # 情况三：格式没按规矩来，提醒它一次
            if self.verbose:
                print(f"  ⚠️ 格式不对，提醒模型重来。原始回复：{reply[:120]}")
            steps.append((thought, "", "（你的上一条回复不符合格式，请严格按 思考/行动/行动输入，或 思考/最终答案 来写）"))

        # 转 max_steps 轮还没收工，兜底
        fallback = "抱歉，我试了几种办法都没能得到确切答案。建议补充一下具体船名或图纸信息。"
        self.memory.add("assistant", fallback)
        return {"answer": fallback, "steps": steps, "ok": False}

    # ------------------------------------------------------------------
    def _think(self, question: str, steps: List[Tuple[str, str, str]]) -> str:
        """把当前状态拼成一封信，让模型回一句。"""
        scratchpad = ContextBuilder.build_agent_scratchpad(steps)
        user_block = f"问题：{question}"
        if scratchpad:
            user_block += f"\n\n你已经走过的步骤：\n{scratchpad}\n\n请继续。"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_block},
        ]
        return self.llm.chat(messages, temperature=0.0)

    # ------------------------------------------------------------------
    @staticmethod
    def _parse(reply: str) -> Tuple[str, str, str, str]:
        """从模型的一坨文字里，抠出"思考 / 行动 / 行动输入 / 最终答案"。

        解析这件事看起来很土，但它是 Agent 最脆弱的一环——
        **模型只要少写一个冒号，整个循环就会卡住**。所以这里写得尽量宽松。
        """
        text = (reply or "").replace("\r\n", "\n").strip()

        def grab(label: str, stop: List[str]) -> str:
            """从 `label：` 后面一直取到下一个标签之前。"""
            pattern = rf"{label}\s*[:：]\s*(.*?)(?=\n\s*(?:{'|'.join(stop)})\s*[:：]|$)"
            match = re.search(pattern, text, re.S)
            return match.group(1).strip() if match else ""

        labels = ["思考", "行动", "行动输入", "最终答案", "观察"]
        thought = grab("思考", labels)
        final = grab("最终答案", labels)

        if final:
            return thought, "", "", final

        action = grab("行动", labels)
        action_input = grab("行动输入", labels)

        # 有些模型会把工具名和输入写在一行，例如 "行动：贝位计算器 01+03"
        # 这种情况下面会找不到"行动输入"，就把工具名后面剩下的字当输入
        if action and not action_input:
            parts = action.split(maxsplit=1)
            if len(parts) == 2:
                action, action_input = parts[0], parts[1]

        # 去掉模型爱加的各种装饰
        action = re.sub(r"[`*\"'（）()]", "", action).strip()
        action_input = re.sub(r"^[`*\"']|[`*\"']$", "", action_input).strip()
        return thought, action, action_input, ""

    # ------------------------------------------------------------------
    def _use_tool(self, name: str, query: str) -> str:
        """执行工具，并把任何异常变成一句"人话"回喂给模型。"""
        tool = self.registry.get(name)
        if tool is None:
            return (
                f"没有叫 '{name}' 的工具。可用工具只有：{', '.join(self.registry.names)}"
            )
        try:
            return tool.run(query)
        except Exception as exc:  # noqa: BLE001 - 工具报错也要让循环继续
            return f"工具 '{name}' 执行出错：{exc}"
