"""
把"跟大模型说话"这件事封装好。

三个要点：

  提示工程  →  messages 里 system（定人设）/ user（用户说）/ assistant（模型说）
                     三种角色。角色不同，模型的态度就不同。
  单次请求  → 一次请求返回一段文本。本文件默认用"非流式"（好解析），
                        另留了 stream() 做打字机效果。
  抑制幻觉  →  temperature 默认 0。温度越低，回答越稳定、越可复现，
                     这样评估脚本跑出来的分数才有意义。

只要你的模型服务兼容 OpenAI 接口（DeepSeek / 通义 / 智谱 / 本地 vLLM 都兼容），
这个类就能直接用。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Iterator, List

from dotenv import load_dotenv

# ----------------------------------------------------------------------
# 读取 .env 的位置
#   1) 优先读本项目根目录的 .env（下完代码复制 .env.example 就在这里）
#   2) 本项目目录没有，再按 dotenv 默认行为从当前目录逐级往上找
# 先找到先算数，已经存在的环境变量不会被覆盖。
# ----------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = PROJECT_ROOT / ".env"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
else:
    load_dotenv()


class LLMError(RuntimeError):
    """大模型调用失败时抛这个，方便上层区分"是模型挂了"还是"是代码写错了"。"""


class LLM:
    """一个极简的大模型客户端。

    用法::

        llm = LLM()
        print(llm.chat([{"role": "user", "content": "你好"}]))
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retry: int = 2,
    ) -> None:
        # 优先用调用方传进来的参数；没传就去 .env 里找
        self.model = model or os.getenv("LLM_MODEL_ID")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.timeout = timeout or float(os.getenv("LLM_TIMEOUT", "60"))
        self.max_retry = max_retry

        missing = [
            name
            for name, value in (
                ("LLM_MODEL_ID", self.model),
                ("LLM_API_KEY", self.api_key),
                ("LLM_BASE_URL", self.base_url),
            )
            if not value
        ]
        if missing:
            raise LLMError(
                "缺少配置：" + "、".join(missing) + "\n"
                "请把 .env.example 复制成 .env（放在这里即可："
                f"{PROJECT_ROOT}），然后填上你自己的模型信息。"
            )

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("没装 openai 库。请先执行：pip install -r requirements.txt") from exc

        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    # ------------------------------------------------------------------
    # 主方法
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_retry: int | None = None,
    ) -> str:
        """发一轮对话，返回模型的完整回复文本。

        失败会自动重试（网络抖动很常见），重试 max_retry 次还不行就抛 LLMError。
        """
        retry = self.max_retry if max_retry is None else max_retry
        last_error: Exception | None = None

        for attempt in range(retry + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                )
                if not response.choices:
                    raise LLMError("模型返回了空结果")
                return response.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001 - 网络/限流/超时都归到这里
                last_error = exc
                if attempt < retry:
                    wait = 1.5 * (attempt + 1)
                    print(f"  [提示] 模型调用失败（{type(exc).__name__}），{wait:.1f} 秒后重试…")
                    time.sleep(wait)

        raise LLMError(f"连续 {retry + 1} 次调用模型都失败：{last_error}")

    def stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
    ) -> Iterator[str]:
        """流式输出，一个字一个字地吐，适合做命令行里的打字机效果。

        注意：流式返回的是一堆碎片，要自己拼起来（"".join），
        所以需要"整段文本"来做解析的场景（比如 Agent 解析"行动："）
        请用 chat()，别用这个。
        """
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
