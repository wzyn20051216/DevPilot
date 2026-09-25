from openai import OpenAI

from .config import settings


def create_client() -> OpenAI:
    """创建 OpenAI 兼容的客户端实例。

    用 settings 里的 api_key / base_url 初始化，
    因为 base_url 可指向 DeepSeek/Qwen 等兼容网关，所以叫「OpenAI 兼容」。

    Returns:
        配置好的 OpenAI 客户端。
    """
    settings.validate_llm()

    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def chat_once(message: str) -> str:
    """执行一次最基础的 LLM 单轮对话（不带工具调用）。

    仅供 /api/chat 这类简单问答接口使用；
    多智能体流程走的是 BaseToolAgent 里的 tool-calling 循环，不经过这里。

    Args:
        message: 用户输入的内容。

    Returns:
        LLM 的文本回复（若模型返回空则回退为 ""）。
    """
    client = create_client()

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 DevPilot，一个专业的软件研发智能助手。"
                    "你擅长 Python、代码分析、调试和软件工程。"
                ),
            },
            {
                "role": "user",
                "content": message,
            },
        ],
        temperature=0.2,
    )

    content = response.choices[0].message.content

    return content or ""
