from colorama import Fore
from camel.societies import RolePlaying
from camel.utils import print_text_animated
from camel.models import ModelFactory
from camel.types import ModelPlatformType
from dotenv import load_dotenv
import os

load_dotenv()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL_ID") or os.getenv("LLM_MODEL")

if not all([LLM_API_KEY, LLM_BASE_URL, LLM_MODEL]):
    raise ValueError("请在 .env 中配置 LLM_MODEL_ID、LLM_API_KEY、LLM_BASE_URL")

def _use_reasoning_if_content_empty(result):
    """智谱等思考模型可能只填 reasoning_content，CAMEL 会把空 content 当成无回复。"""
    for choice in getattr(result, "choices", None) or []:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        content = getattr(message, "content", None)
        if content and str(content).strip():
            continue
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            message.content = reasoning
    return result


# glm-5.3 / glm-5.3-flash 强制思考，不能传 disabled，只能用 low / high / max
_model_id = LLM_MODEL.lower()
_always_think = "glm-5.3" in _model_id
_extra_body = (
    {
        "thinking": {"type": "enabled", "clear_thinking": False},
        "reasoning_effort": "low",
    }
    if _always_think
    else {
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
)

# 使用 OpenAI 兼容接口，与课程统一环境变量一致（智谱/百炼/中转均可）
model = ModelFactory.create(
    model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
    model_type=LLM_MODEL,
    url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    model_config_dict={
        "temperature": 0.7,
        "max_tokens": 8192,
        "extra_body": _extra_body,
    },
)
_original_run = model._run
model._run = lambda messages, response_format=None, tools=None: _use_reasoning_if_content_empty(
    _original_run(messages, response_format, tools)
)

# 定义协作任务
task_prompt = """
创作一本关于"拖延症心理学"的短篇电子书，目标读者是对心理学感兴趣的普通大众。
要求：
1. 内容科学严谨，基于实证研究
2. 语言通俗易懂，避免过多专业术语
3. 包含实用的改善建议和案例分析
4. 篇幅控制在8000-10000字
5. 结构清晰，包含引言、核心章节和总结
"""

print(Fore.YELLOW + f"协作任务:\n{task_prompt}\n")

# 初始化角色扮演会话
role_play_session = RolePlaying(
    assistant_role_name="心理学家",
    user_role_name="作家",
    task_prompt=task_prompt,
    model=model,
    with_task_specify=False,
    output_language="中文",
)

print(Fore.CYAN + f"具体任务描述:\n{role_play_session.task_prompt}\n")

# 开始协作对话
chat_turn_limit, n = 30, 0
input_msg = role_play_session.init_chat()

while n < chat_turn_limit:
    n += 1
    try:
        assistant_response, user_response = role_play_session.step(input_msg)
    except ValueError as exc:
        print(Fore.RED + f"本轮对话失败: {exc}")
        print(Fore.RED + "常见原因：思考模型把正文写在 reasoning_content，或 max_tokens 在思考阶段耗尽。")
        break

    if not user_response.msgs or not assistant_response.msgs:
        print(Fore.RED + "本轮模型返回了空回复，已停止。可尝试更换非思考模型或提高 max_tokens。")
        break

    print_text_animated(Fore.BLUE + f"作家:\n\n{user_response.msg.content}\n")
    print_text_animated(Fore.GREEN + f"心理学家:\n\n{assistant_response.msg.content}\n")

    # 检查任务完成标志
    if "CAMEL_TASK_DONE" in user_response.msg.content:
        print(Fore.MAGENTA + "✅ 电子书创作完成！")
        break

    input_msg = assistant_response.msg

print(Fore.YELLOW + f"总共进行了 {n} 轮协作对话")