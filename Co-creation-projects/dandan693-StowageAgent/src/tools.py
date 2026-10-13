"""
工具系统 → 这是全项目最值钱的部分。

工具就是"智能体的手"。大模型只会写字，不会算数、不会查表。
你把一个能力包装成工具，它就能调用。

工具的三件套（缺一不可）：
    name         名字     —— 大模型用它来"点名"
    description  说明书   —— 大模型靠这段文字决定"什么时候该用它"
    run()        干活的   —— 真正的逻辑，输入文本、输出文本

⚠️ 关键认知：大模型看不见 run() 里的代码，它只看 description。
   所以 description 写得好不好，直接决定工具会不会被用对。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ======================================================================
# 底层规则 —— 这些是集装箱配载的行业知识，跟 AI 无关
# ======================================================================

# 40 尺箱占两个相邻的 20 尺小贝位，图纸上把中间那个偶数号叫"大贝"
# 例：01 + 03 → 02 ；05 + 07 → 06 ；09 + 11 → 10
PAIR_MAP: Dict[int, int] = {1: 2, 5: 6, 9: 10, 13: 14, 17: 18, 21: 22, 25: 26}
REVERSE_PAIR_MAP: Dict[int, Tuple[int, int]] = {v: (k, k + 2) for k, v in PAIR_MAP.items()}

# 层号：02–08 在船舱里；82 及以上（82/84/86/88）在甲板上
HOLD_TIER_MAX = 8  # 舱内层号上限（含）
DECK_TIER_MIN = 82  # 舱面层号下限（含）


def merge_bay(small_bay: int) -> int:
    """小贝号 → 大贝号。

    奇数小贝：01→02、05→06、09→10（两个小贝合成一个大贝）
    已经是偶数（本来就是大贝号）：原样返回
    """
    if small_bay % 2 == 0:
        return small_bay
    if small_bay in PAIR_MAP:
        return PAIR_MAP[small_bay]
    # 表里没列的，按"大贝 = 前一个小贝 + 1"的规律推
    return small_bay + 1


def expand_bay(big_bay: int) -> Tuple[int, int]:
    """大贝号 → 组成它的两个小贝号。例：02 → (01, 03)"""
    if big_bay % 2 != 0:
        return (big_bay, big_bay)  # 奇数本来就是小贝，拆不出两个
    if big_bay in REVERSE_PAIR_MAP:
        return REVERSE_PAIR_MAP[big_bay]
    return (big_bay - 1, big_bay + 1)


def tier_location(tier: int) -> str:
    """层号 → 这个箱子在船上哪个位置。"""
    if 1 <= tier <= HOLD_TIER_MAX:
        return "舱内"
    if tier >= DECK_TIER_MIN:
        return "舱面（甲板）"
    return "未知（不在常见层号区间）"


def format_coord(bay: int, pos: int, tier: int) -> str:
    """三个数字 → 六位坐标，例如 (1, 6, 82) → '010682'。

    ⚠️ 一定要补前导零！02 写成 2，坐标就废了。
    """
    return f"{bay:02d}{pos:02d}{tier:02d}"


def parse_coord(coord: str) -> Tuple[int, int, int]:
    """六位坐标 → (贝位, 排位, 层号)，例如 '010682' → (1, 6, 82)。"""
    text = coord.strip()
    if not re.fullmatch(r"\d{6}", text):
        raise ValueError(f"'{coord}' 不是合法的六位坐标（应为 6 位数字，如 010682）")
    return int(text[0:2]), int(text[2:4]), int(text[4:6])


# ======================================================================
# 工具基类
# ======================================================================
class BaseTool:
    name: str = "tool"
    description: str = "工具说明"

    def run(self, query: str) -> str:  # pragma: no cover - 由子类实现
        raise NotImplementedError


# ======================================================================
# 工具 1：贝位计算器
# ======================================================================
class BayTool(BaseTool):
    name = "贝位计算器"
    description = (
        "计算集装箱船贝位（Bay）。"
        "输入形如 '01+03' 或 '01,03' 的两个小贝，返回合成后的大贝号（40尺箱用）；"
        "输入单个偶数大贝，如 '02'，返回它的两个小贝号；"
        "输入单个奇数小贝，如 '05'，原样返回（20尺箱写自己的小贝号）。"
    )

    def run(self, query: str) -> str:
        text = query.strip().replace("，", ",").replace(" ", "")
        if not text:
            return "错误：没有输入贝位"

        # 情况一：两个小贝相加
        if "+" in text or "," in text:
            parts = re.split(r"[+,]", text)
            nums = [int(p) for p in parts if p.strip().isdigit()]
            if len(nums) != 2:
                return f"错误：应该给两个小贝号，例如 '01+03'，你给的是 '{query}'"
            a, b = sorted(nums)
            if a % 2 == 0 or b % 2 == 0:
                return f"错误：{a:02d} 和 {b:02d} 里有偶数。成对的小贝必须是两个奇数（如 01+03）"
            if b - a != 2:
                return f"错误：{a:02d} 和 {b:02d} 不相邻，不是一对小贝（相邻的两个奇数才成对）"
            return (
                f"{a:02d} + {b:02d} → 大贝 {merge_bay(a):02d}\n"
                f"说明：40尺箱写大贝号 {merge_bay(a):02d}；20尺箱写各自的小贝号 {a:02d} / {b:02d}"
            )

        # 情况二：单个贝位
        if not text.isdigit():
            return f"错误：'{query}' 不是数字"
        num = int(text)
        if num % 2 == 0:
            left, right = expand_bay(num)
            return (
                f"大贝 {num:02d} → 由小贝 {left:02d} 和 {right:02d} 合成\n"
                f"说明：这个贝位上的 40 尺箱写 {num:02d}，20 尺箱分别写 {left:02d} / {right:02d}"
            )
        return (
            f"{num:02d} 是小贝号（奇数），保持原样\n"
            f"说明：20 尺箱写小贝号 {num:02d}；如果这里装 40 尺箱，应写成大贝号 {merge_bay(num):02d}"
        )


# ======================================================================
# 工具 2：坐标解析器
# ======================================================================
class CoordTool(BaseTool):
    name = "坐标解析器"
    description = (
        "处理六位箱位坐标（贝位2位 + 排位2位 + 层号2位）。"
        "输入六位数字如 '010682'，返回它拆开的贝位、排位、层号，以及箱子在舱内还是甲板上；"
        "也可以输入三个用逗号隔开的数字如 '1,6,82' 来反向拼出六位坐标。"
    )

    def run(self, query: str) -> str:
        text = query.strip().replace("，", ",").replace(" ", "")

        # 反向：三个数字 → 六位码
        if "," in text:
            parts = text.split(",")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                return f"错误：应该给三个数字，例如 '1,6,82'，你给的是 '{query}'"
            bay, pos, tier = (int(p) for p in parts)
            return (
                f"贝位{bay:02d} + 排位{pos:02d} + 层号{tier:02d} → 坐标 {format_coord(bay, pos, tier)}\n"
                f"位置：{tier_location(tier)}"
            )

        # 正向：六位码 → 三要素
        try:
            bay, pos, tier = parse_coord(text)
        except ValueError as exc:
            return f"错误：{exc}"

        side = ""
        if pos % 2 == 1:
            side = "（奇数排位，通常在中线右侧 / 右舷方向）"
        elif pos != 0:
            side = "（偶数排位，通常在中线左侧 / 左舷方向）"

        return (
            f"坐标 {text} 拆解：\n"
            f"  贝位 Bay = {bay:02d}\n"
            f"  排位 Pos = {pos:02d}{side}\n"
            f"  层号 Tier = {tier:02d} → {tier_location(tier)}"
        )


# ======================================================================
# 工具 3：箱位校验器（这条最像"测试"）
# ======================================================================
class StowageCheckTool(BaseTool):
    name = "箱位校验器"
    description = (
        "校验一批箱位坐标是否合规。"
        "输入多行或逗号隔开的坐标（可写成 'HE 010682' 带箱型，也可只写 '010682'），"
        "返回：格式错误、重复坐标、箱型与贝位是否匹配（40尺必须是偶数大贝、20尺必须是奇数小贝）、"
        "以及各箱型的数量统计。"
    )

    #: 看名字就能判断尺寸的箱型（大写=40尺，含小写字母=20尺）
    @staticmethod
    def _size_of(mark: str) -> str:
        if not mark:
            return "未知"
        if mark.endswith("+") or mark in {"HE", "X", "F", "NE", "*"}:
            return "40尺"
        if mark.isupper():
            return "40尺"
        return "20尺"

    def run(self, query: str) -> str:
        rows: List[Tuple[str, str]] = []
        for raw in re.split(r"[\n,，;；]+", query):
            item = raw.strip()
            if not item:
                continue
            m = re.search(r"(\d{6})", item)
            if not m:
                rows.append(("", item))
                continue
            mark = item[: m.start()].strip()
            rows.append((mark, m.group(1)))

        if not rows:
            return "错误：没有解析到任何坐标"

        errors: List[str] = []
        marks: Dict[str, int] = {}
        seen: Dict[str, int] = {}
        for idx, (mark, coord) in enumerate(rows, 1):
            if not coord:
                errors.append(f"第 {idx} 行 '{mark}' 里找不到六位坐标")
                continue
            seen[coord] = seen.get(coord, 0) + 1
            bay, pos, tier = parse_coord(coord)
            size = self._size_of(mark)
            if size == "40尺" and bay % 2 != 0:
                errors.append(f"'{mark} {coord}'：40尺箱应写偶数大贝号，但贝位是 {bay:02d}")
            if size == "20尺" and bay % 2 == 0:
                errors.append(f"'{mark} {coord}'：20尺箱应写奇数小贝号，但贝位是 {bay:02d}")
            if not (1 <= tier <= HOLD_TIER_MAX or tier >= DECK_TIER_MIN):
                errors.append(f"'{coord}'：层号 {tier:02d} 不在常见区间（舱内 02-08 / 舱面 82 以上）")
            key = mark or "（未标注箱型）"
            marks[key] = marks.get(key, 0) + 1

        dup = [c for c, n in seen.items() if n > 1]

        lines = [f"共收到 {len(rows)} 个箱位，去重后 {len(seen)} 个"]
        lines.append("箱型统计：" + "  ".join(f"{k}={v}" for k, v in sorted(marks.items())))
        if dup:
            lines.append("⚠️ 重复坐标：" + "、".join(sorted(dup)))
        if errors:
            lines.append(f"⚠️ 发现 {len(errors)} 处问题：")
            lines.extend("  - " + e for e in errors[:20])
        else:
            lines.append("✅ 未发现格式或贝位问题")
        return "\n".join(lines)


# ======================================================================
# 工具 4：知识库检索（内部调 RAG）
# ======================================================================
class KnowledgeTool(BaseTool):
    name = "知识库检索"
    description = (
        "在配载规则知识库里查资料。"
        "当你需要确认某条业务规则（贝位怎么合成、层号怎么分舱内舱面、"
        "箱型标记怎么写、图纸有哪几种排版、坐标为什么要补前导零等）时使用。"
        "输入一段自然语言问题，返回最相关的几段规则原文。"
    )

    def __init__(self, retriever, top_k: int = 3) -> None:
        self.retriever = retriever
        self.top_k = top_k

    def run(self, query: str) -> str:
        hits = self.retriever.search(query, top_k=self.top_k)
        if not hits:
            return "知识库里没有找到相关内容。"
        parts = []
        for i, (text, score) in enumerate(hits, 1):
            parts.append(f"【资料{i}｜相关度 {score:.3f}】\n{text}")
        return "\n\n".join(parts)


# ======================================================================
# 工具注册表
# ======================================================================
class ToolRegistry:
    """统一管理所有工具：注册、按名字取用、生成给大模型看的说明书。"""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> "ToolRegistry":
        self._tools[tool.name] = tool
        return self

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name.strip())

    @property
    def names(self) -> List[str]:
        return list(self._tools)

    def describe(self) -> str:
        """拼出工具清单，这段文本会被塞进系统提示词里。"""
        return "\n".join(f"- {t.name}：{t.description}" for t in self._tools.values())
