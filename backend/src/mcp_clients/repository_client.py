"""! @brief Repository MCP Client。

本模块封装 Repository MCP Server 的客户端调用逻辑。
客户端通过 stdio 拉起独立的 Repository MCP Server 子进程，
让 DevPilot 主进程和仓库工具进程彻底分离。
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp import (
    Client,
    StdioServerParameters,
)

from openai.types.chat import (
    ChatCompletionFunctionToolParam,
)


BACKEND_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)


def _create_stdio_server(
    repo_path: str,
) -> StdioServerParameters:
    """! @brief 创建 Repository MCP Server 的 stdio 启动参数。

    @param repo_path 目标代码仓库根目录，会通过环境变量传给 MCP 子进程。
    @return 可直接传给 MCP Client 的 stdio server 参数。
    """

    # 子进程需要继承当前代理、PATH 等运行环境，所以从 os.environ 复制；
    # 只在副本里追加仓库路径，避免污染 DevPilot 主进程的全局环境变量。
    env = dict(
        os.environ
    )
    env["DEVPILOT_REPO_PATH"] = repo_path
    if "PYTHONIOENCODING" not in env:
        env["PYTHONIOENCODING"] = "utf-8"

    return StdioServerParameters(
        # 使用当前解释器可确保 MCP 子进程复用同一个 uv/venv 环境，能够导入
        # 主进程已经安装的 mcp、Pydantic 和项目依赖。
        command=sys.executable,
        args=[
            "-m",
            "src.mcp_servers.repository_stdio",
        ],
        env=env,
        # `python -m src...` 需要从 backend 根目录启动，src 才是可导入包。
        cwd=BACKEND_ROOT,
    )


async def list_repository_tools(
    repo_path: str,
) -> list[
    ChatCompletionFunctionToolParam
]:
    """! @brief 从 Repository MCP Server 动态发现 tools。

    @param repo_path 目标代码仓库根目录。
    @return 转换为 OpenAI function tool 格式的工具定义列表。
    """

    server = _create_stdio_server(
        repo_path
    )

    definitions: list[
        ChatCompletionFunctionToolParam
    ] = []

    async with Client(server) as client:

        result = await client.list_tools()

        for tool in result.tools:

            definition: (
                ChatCompletionFunctionToolParam
            ) = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (
                        tool.description
                        or ""
                    ),
                    "parameters": (
                        tool.input_schema
                    ),
                },
            }

            definitions.append(
                definition
            )

    return definitions

async def call_repository_tool(
    repo_path: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """! @brief 通过 MCP 调用 Repository tool。

    @param repo_path 目标代码仓库根目录。
    @param tool_name MCP tool 名称。
    @param arguments MCP tool 参数。
    @return tool 执行结果；优先返回 structured_content。
    """

    server = _create_stdio_server(
        repo_path
    )

    async with Client(server) as client:

        result = await client.call_tool(
            tool_name,
            arguments,
        )

        # 高层 MCP Server 通常提供结构化结果
        # structured_content 是 MCP 的机器可读结果，优先级高于下方文本块。
        structured = (
            result.structured_content
        )

        if structured is not None:

            # MCP 对简单返回值有时会包装：
            # {"result": ...}
            if (
                isinstance(
                    structured,
                    dict,
                )
                and set(
                    structured.keys()
                ) == {"result"}
            ):
                # 某些 MCP SDK 会把工具原始返回值统一包成 {"result": value}；
                # 解一层后，上层仍能拿到与本地工具一致的数据形态。
                return structured["result"]

            return structured

        # 兜底：读取文本 content
        text_parts: list[str] = []

        for block in result.content:

            text = getattr(
                block,
                "text",
                None,
            )

            if isinstance(text, str):
                text_parts.append(text)

        return "\n".join(
            text_parts
        )

def list_repository_tools_sync(
    repo_path: str,
) -> list[
    ChatCompletionFunctionToolParam
]:
    """! @brief 同步调用 MCP Tool Discovery。

    供 Python REPL、测试脚本或同步业务代码直接调用。

    @param repo_path 目标代码仓库根目录。
    @return OpenAI function tool 格式的工具定义列表。
    """

    # 当前调用方是同步 Agent/FastAPI 路由，因此在这里建立并关闭一次事件循环。
    # 不应在已经运行的 async 事件循环内部直接调用这个同步包装函数。
    return asyncio.run(
        list_repository_tools(
            repo_path
        )
    )


def call_repository_tool_sync(
    repo_path: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """! @brief 同步调用 MCP tool。

    @param repo_path 目标代码仓库根目录。
    @param tool_name MCP tool 名称。
    @param arguments MCP tool 参数。
    @return tool 执行结果。
    """

    # 与 discovery 的同步包装相同：一次调用对应一次独立 MCP 会话和子进程。
    return asyncio.run(
        call_repository_tool(
            repo_path=repo_path,
            tool_name=tool_name,
            arguments=arguments,
        )
    )
