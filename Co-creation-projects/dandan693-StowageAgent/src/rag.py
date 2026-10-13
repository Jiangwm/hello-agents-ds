"""
RAG（检索增强生成）→ 把知识库塞进大模型的脑子。

一句话理解 RAG：**开卷考试**。
不给资料，模型只能凭记忆瞎答（容易编）；先翻资料再答，就靠谱多了。

RAG 一共四步，本文件每一步都单独写成了一个小方法，方便你看清楚：

    ① 分块 chunk()      —— 把一大篇资料切成一段一段（本项目的切法极简：一个空行 = 一段）
    ② 建索引 index()    —— 给每一段算一个"指纹"
    ③ 检索 search()     —— 把问题也算成指纹，比一比谁最像
    ④ 拼上下文          —— 由 context.py 负责，把命中的资料贴到问题前面

两种"指纹"算法：

    tfidf  —— 数"字"出现得多不多。不用下载模型、秒开（关键词匹配）
    vector —— 把整段话压成一串数字，比距离。能看懂"意思"而不只是字面，
              （语义向量）。首次要下载约 400MB 模型。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


class Retriever:
    """知识库检索器。"""

    def __init__(self, kb_path: str | Path, mode: str = "tfidf") -> None:
        self.kb_path = Path(kb_path)
        self.mode = mode
        self.chunks: List[str] = []
        self._vectors = None  # tfidf 模式下是一个矩阵；vector 模式下是 numpy 数组
        self._model = None

        if not self.kb_path.exists():
            raise FileNotFoundError(f"找不到知识库文件：{self.kb_path}")

        self.chunks = self.chunk(self.kb_path.read_text(encoding="utf-8"))
        if not self.chunks:
            raise ValueError(f"知识库 {self.kb_path} 是空的")

    # ------------------------------------------------------------------
    # 第一步：分块
    # ------------------------------------------------------------------
    @staticmethod
    def chunk(text: str) -> List[str]:
        """把整篇资料切成一段一段。

        真实项目里分块是很讲究的（按标题切、按字数切、带重叠……），
        这里用最朴素的办法：**一个空行隔开的就是一段**。
        好处是：你想让机器人多懂一件事，只要在 txt 里加一段就行，不用改代码。
        """
        blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n")]
        return [b for b in blocks if b and not b.startswith("#")]

    # ------------------------------------------------------------------
    # 第二步：建索引
    # ------------------------------------------------------------------
    def index(self) -> None:
        if self.mode == "tfidf":
            self._build_tfidf()
        elif self.mode == "vector":
            self._build_vector()
        else:
            raise ValueError(f"不认识的检索模式：{self.mode}（只支持 tfidf / vector）")

    def _build_tfidf(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            analyzer="char",  # 中文没有空格，按"字"切比按"词"切省事且够用
            ngram_range=(1, 2),  # 兼顾单字和两字词
        )
        self._vectors = self._vectorizer.fit_transform(self.chunks)

    def _build_vector(self) -> None:
        import os

        # 走国内镜像，否则下模型会卡住
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from sentence_transformers import SentenceTransformer

        # 中文任务一定要用中文模型。用多语言通用模型的话，
        # "配载图上哪些东西不是箱位"这种问题会被答成别的（实测踩过坑）
        self._model = SentenceTransformer("shibing624/text2vec-base-chinese")
        self._vectors = self._model.encode(self.chunks, normalize_embeddings=True)

    # ------------------------------------------------------------------
    # 第三步：检索
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 3) -> List[Tuple[str, float]]:
        """返回 [(资料段落, 相关度分数), …]，分数越高越相关。"""
        if self._vectors is None:
            self.index()

        if self.mode == "tfidf":
            from sklearn.metrics.pairwise import cosine_similarity

            q = self._vectorizer.transform([query])
            scores = cosine_similarity(q, self._vectors)[0]
        else:
            q = self._model.encode([query], normalize_embeddings=True)[0]
            scores = self._vectors @ q

        import numpy as np

        order = np.argsort(scores)[::-1][:top_k]
        return [(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]
