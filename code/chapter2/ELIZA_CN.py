# -*- coding: utf-8 -*-
"""
迷你版 ELIZA —— 中文语料版
==========================

本文件由同目录的 ELIZA.py（英文版）改写而来。整体设计遵循「对话逻辑与交互框架不变，
只替换语料与语言处理层」的原则：规则库的“有序匹配 + 首个命中即返回 + 捕获组代入模板 +
代词转换 + 随机挑选回复”这套骨架完全保留。

相对于英文版，改动集中在三处：

1. 规则匹配（正则中文化）
   - 英文依赖空格分词，故可用 `.* mother .*`；中文无空格，这类规则改为
     「分词后按词元匹配关键词」（见 RULES 中的 keywords 字段）。关键词判定落在
     词元粒度而非字符子串上，命中结果会随分词结果自然变化，接入真实分词器
     （如 jieba 词表中的“母亲节”“丈母娘”等独立词条）时比 `.*母亲.*`
     这种字符级子串匹配更贴近语言事实。
   - 问号统一写成 `[?？]`，兼容半角 `?` 与全角 `？`。
   - 动词/句式的正则按中文语序重写，例如 `I need (.*)` -> `我(?:想|要|需要)(.+)`。

2. 代词转换（swap_pronouns）
   - 英文用 `phrase.lower().split()` 切词后查表；中文无空格，改为
     「最长优先的单遍正则替换」。单遍替换不会回扫已替换文本，因此避免了
     英文版那种隐患：先 我->你、再 你->我 会把结果又翻回去。

3. 中文分词与关键词匹配
   - 优先使用 jieba；若环境未安装 jieba，则自动回退到内置的
     **正向最大匹配（Forward Maximum Matching, FMM）+ 领域词典** 实现。
     因此本文件零第三方依赖也能直接运行。
   - 分词结果同时用于：a) 关键词规则的命中判定；b) 调试模式下展示语言处理过程。

运行方式：
    python ELIZA_CN.py            # 正常对话
    python ELIZA_CN.py --debug    # 额外打印分词、关键词、命中规则，便于教学观察
"""

import re
import random
import sys


# =============================================================================
# 1. 中文分词层
# =============================================================================

# 领域词典：用于内置 FMM 分词器，同时也作为关键词抽取的候选词表。
# 词典越贴合语料，切分越准；这里只收录与本章对话语料相关的核心词。
LEXICON = {
    # 人称代词 / 物主代词
    "我", "你", "您", "我们", "你们", "咱们", "我的", "你的", "自己",
    # 家庭成员（关键词规则命中用）
    "母亲", "妈妈", "娘", "父亲", "爸爸", "爸", "父母", "家人", "家庭",
    # 高频动词 / 能愿动词
    "需要", "想要", "希望", "觉得", "认为", "知道", "明白", "喜欢", "讨厌",
    "陪", "帮", "帮助", "说", "聊", "睡", "睡觉", "做", "想", "要",
    # 情绪 / 状态
    "难过", "开心", "高兴", "焦虑", "害怕", "担心", "孤独", "生气", "累",
    "紧张", "迷茫", "委屈", "痛苦",
    # 常见名词
    "朋友", "工作", "学习", "考试", "生活", "问题", "事情", "时候",
    "现在", "今天", "以后", "未来", "天气", "最近", "关系", "照顾",
    # 副词 / 连词 / 疑问词
    "为什么", "怎么", "怎么样", "什么", "多久", "因为", "所以", "但是", "如果",
    "也许", "真的", "一直", "总是", "经常", "有时候", "非常", "特别",
    "很", "太", "都", "还", "不错", "没有",
}

# 抽取关键词时过滤掉的虚词
STOPWORDS = {"我", "你", "您", "的", "了", "是", "很", "太", "都", "还", "说", "想", "要"}

MAX_WORD_LEN = max(len(w) for w in LEXICON)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")   # 单个汉字
_ASCII_RE = re.compile(r"[A-Za-z0-9]+")    # 英文单词 / 数字串


def _fmm_segment(text):
    """
    正向最大匹配（FMM）分词。

    从左到右扫描：在当前位置优先尝试词典中最长的词，命中即切出；
    未命中则退化为单字（OOV 处理）。英文/数字整段保留，标点单独成元。
    """
    tokens = []
    i, n = 0, len(text)
    while i < n:
        # 英文单词 / 数字串整体切出，避免被逐字符打散
        m = _ASCII_RE.match(text, i)
        if m:
            tokens.append(m.group())
            i = m.end()
            continue

        # 非中文（标点、空白以外的符号）单独成元
        if not _CJK_RE.match(text[i]):
            if not text[i].isspace():
                tokens.append(text[i])
            i += 1
            continue

        # 正向最大匹配：从最长候选长度依次向下尝试
        for length in range(min(MAX_WORD_LEN, n - i), 1, -1):
            if text[i:i + length] in LEXICON:
                tokens.append(text[i:i + length])
                i += length
                break
        else:
            # 词典未覆盖，退化为单字
            tokens.append(text[i])
            i += 1
    return tokens


try:  # 优先使用 jieba（若已安装），获得更通用的分词效果
    import jieba

    jieba.setLogLevel(60)  # 关闭 jieba 的初始化日志输出

    def segment(text):
        """jieba 分词：过滤掉纯空白元。"""
        return [word for word in jieba.lcut(text) if word.strip()]

    SEGMENTER_NAME = "jieba"
except Exception:  # 未安装 jieba 时回退到内置 FMM，保证零依赖可运行

    def segment(text):
        """内置 FMM 分词（回退方案）。"""
        return _fmm_segment(text)

    SEGMENTER_NAME = "FMM（内置词典）"


def extract_keywords(text):
    """抽取文本中命中领域词典的实词，供关键词匹配 / 调试观察使用。"""
    return [t for t in segment(text) if t in LEXICON and t not in STOPWORDS]


# =============================================================================
# 2. 中文语料与规则库
# =============================================================================
# 说明：
#   - 有 "pattern" 的规则走正则捕获（对应英文版写法）；
#   - 有 "keywords" 的规则走「分词后的词元匹配」（中文无空格场景的替代方案）；
#   - 列表顺序即优先级，首个命中即返回；最后一条为兜底规则。

RULES = [
    {
        "name": "需求表达",
        # 对应英文 `I need (.*)`
        "pattern": r"我(?:想|要|需要)(.+)",
        "responses": [
            "你为什么需要{0}？",
            "得到{0}真的对你有帮助吗？",
            "你确定你需要{0}吗？",
        ],
    },
    {
        "name": "反问-你为什么",
        # 对应英文 `Why don't you (.*)\?`
        "pattern": r"你为什么不(.+?)[?？]",
        "responses": [
            "你真的觉得我不会{0}吗？",
            "也许我最终会{0}的。",
            "你真的想让我{0}吗？",
        ],
    },
    {
        "name": "反问-我为什么不能",
        # 对应英文 `Why can't I (.*)\?`
        "pattern": r"我为什么不能(.+?)[?？]",
        "responses": [
            "你觉得你应该能够{0}吗？",
            "如果你能{0}，你会做什么？",
            "我不知道——你为什么不能{0}呢？",
        ],
    },
    {
        "name": "自我陈述",
        # 对应英文 `I am (.*)`
        "pattern": r"我是(.+)",
        "responses": [
            "你是因为{0}才来找我的吗？",
            "你{0}多久了？",
            "对于{0}的感觉，你想多聊聊吗？",
        ],
    },
    {
        "name": "关键词-母亲",
        # 对应英文 `.* mother .*`，中文改用分词词元匹配
        "keywords": ["母亲", "妈妈", "娘"],
        "responses": [
            "多跟我说说你的母亲吧。",
            "你和母亲的关系怎么样？",
            "你对母亲有什么感受？",
        ],
    },
    {
        "name": "关键词-父亲",
        # 对应英文 `.* father .*`
        "keywords": ["父亲", "爸爸", "爸"],
        "responses": [
            "多跟我说说你的父亲吧。",
            "父亲让你有什么感受？",
            "父亲教会了你什么？",
        ],
    },
    {
        "name": "兜底",
        # 对应英文 `.*`
        "pattern": r".*",
        "responses": [
            "请继续说下去。",
            "我们换个话题吧……跟我说说你的家人。",
            "你能就这一点再展开说说吗？",
        ],
    },
]


# =============================================================================
# 3. 代词转换层
# =============================================================================

# 中文第一/第二人称及物主代词互换表
PRONOUN_SWAP = {
    "我": "你",
    "你": "我",
    "您": "我",
    "我们": "你们",
    "你们": "我们",
    "咱们": "你们",
    "我的": "你的",
    "你的": "我的",
    "我自己": "你自己",
    "你自己": "我自己",
}

# 最长优先构造单个替换正则；re.sub 为单遍替换，不会回扫已替换内容，
# 因此不会出现「我 -> 你 -> 我」的级联回翻问题。
_SWAP_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(PRONOUN_SWAP, key=len, reverse=True))
)


def swap_pronouns(phrase):
    """
    对输入短语中的代词进行第一/第二人称转换（单遍、最长优先）。
    """
    if not phrase:
        return phrase
    return _SWAP_RE.sub(lambda m: PRONOUN_SWAP[m.group(0)], phrase)


# =============================================================================
# 4. 响应生成层
# =============================================================================

def _match_rule(user_input):
    """
    按规则顺序匹配输入，返回 (命中规则 or None, 捕获内容, 分词结果)。

    保持英文版语义：首个命中即返回；只有第 1 个捕获组会被使用。
    """
    tokens = segment(user_input)
    token_set = set(tokens)

    for rule in RULES:
        if "pattern" in rule:
            match = re.search(rule["pattern"], user_input, re.IGNORECASE)
            if match:
                captured = match.group(1) if match.groups() else ""
                return rule, captured, tokens
        elif "keywords" in rule:
            # 词元级匹配：只有分词结果中出现了完整关键词才算命中
            if token_set & set(rule["keywords"]):
                return rule, "", tokens

    return None, "", tokens


def respond(user_input):
    """
    根据规则库生成响应（与英文版 respond 行为一致）。
    """
    rule, captured, _ = _match_rule(user_input)
    if rule is None:
        # 理论上不会走到这里（兜底规则 `.*` 必然命中），保留防御性分支
        return random.choice(RULES[-1]["responses"])

    # 进行代词转换，再从模板中随机选择一个并格式化
    swapped_group = swap_pronouns(captured)
    return random.choice(rule["responses"]).format(swapped_group)


# =============================================================================
# 5. 交互层（主聊天循环）
# =============================================================================

QUIT_WORDS = {"quit", "exit", "bye", "退出", "再见", "拜拜", "结束"}


if __name__ == "__main__":
    debug = "--debug" in sys.argv

    print("心理咨询师：你好！今天有什么想聊的吗？")
    if debug:
        print(f"[调试] 当前分词器：{SEGMENTER_NAME}")

    while True:
        user_input = input("你：").strip()
        if user_input.lower() in QUIT_WORDS:
            print("心理咨询师：再见，很高兴和你聊天。")
            break

        if debug:
            rule, captured, tokens = _match_rule(user_input)
            print(f"[调试] 分词结果：{tokens}")
            print(f"[调试] 抽取关键词：{extract_keywords(user_input)}")
            print(f"[调试] 命中规则：{rule['name'] if rule else '无'}"
                  f"｜捕获组：{captured!r}｜代词转换：{swap_pronouns(captured)!r}")

        print(f"心理咨询师：{respond(user_input)}")
