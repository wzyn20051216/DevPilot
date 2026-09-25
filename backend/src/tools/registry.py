"""! @brief Agent 工具注册与分发总台。

本模块只保留本地高风险/执行类工具定义，仓库只读检索类工具统一从
Repository MCP Server 动态发现。这样后续 MCP tool 增减时，Agent 不需要
在这里重复维护一份静态 schema。
"""
from typing import Any

from openai.types.chat import ChatCompletionFunctionToolParam

from ..mcp_clients.repository_client import (
    call_repository_tool_sync,
    list_repository_tools_sync,
)
from .command_tool import run_command
from .test_tool import run_tests as run_test
from .write_tool import write_file


MCP_REPOSITORY_TOOLS: set[str] = {
    "list_files",
    "read_file",
    "search_code",
    "retrieve_code",
    "git_diff",
}


WRITE_FILE_DEFINITION: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "修改代码仓库中的已有文本文件。"
            "只有在充分阅读和理解目标文件之后才能使用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "相对于仓库根目录的文件路径",
                },
                "content": {
                    "type": "string",
                    "description": "修改后的完整文件内容",
                },
            },
            "required": [
                "file_path",
                "content",
            ],
            "additionalProperties": False,
        },
    },
}


RUN_TEST_DEFINITION: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "run_test",
        "description": (
            "运行代码仓库的 pytest 测试。"
            "代码修改完成后必须尽可能执行测试验证。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": [
                        "string",
                        "null",
                    ],
                    "description": "可选 pytest 测试目标",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


RUN_COMMAND_DEFINITION: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "执行受限的代码质量命令。"
            "目前只允许 pytest、ruff、mypy。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                    "description": (
                        "命令及参数数组，例如 ['ruff', 'check', 'src']"
                    ),
                },
            },
            "required": [
                "argv",
            ],
            "additionalProperties": False,
        },
    },
}


LOCAL_TOOL_DEFINITIONS: list[ChatCompletionFunctionToolParam] = [
    WRITE_FILE_DEFINITION,
    RUN_TEST_DEFINITION,
    RUN_COMMAND_DEFINITION,
]


def _definition_name(
    definition: ChatCompletionFunctionToolParam,
) -> str:
    """! @brief 读取 OpenAI function tool 定义中的工具名。

    @param definition 单个 function tool 定义。
    @return 工具名称。
    """

    return definition["function"]["name"]


def get_tool_definitions(
    repo_path: str,
    allowed_tools: set[str],
) -> list[ChatCompletionFunctionToolParam]:
    """! @brief 返回某个 Agent 被允许使用的工具定义列表。

    先通过 MCP 对仓库工具做 discovery，再追加本地执行类工具。最终只保留
    `allowed_tools` 白名单中的工具，避免把当前 Agent 不该使用的能力暴露给 LLM。

    @param repo_path 目标代码仓库根目录，用于连接 Repository MCP Server。
    @param allowed_tools 当前 Agent 允许使用的工具名集合。
    @return 过滤后的 OpenAI function tool 定义列表。
    """

    definitions: list[ChatCompletionFunctionToolParam] = []

    # 仓库读取工具以 MCP Server 的实时 schema 为准，避免客户端静态定义
    # 与服务端参数发生漂移；本地写入/执行工具仍由本进程固定维护。
    mcp_tools = list_repository_tools_sync(
        repo_path
    )

    for tool in mcp_tools:
        if _definition_name(tool) in allowed_tools:
            definitions.append(tool)

    for tool in LOCAL_TOOL_DEFINITIONS:
        if _definition_name(tool) in allowed_tools:
            definitions.append(tool)

    return definitions


def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    repo_path: str,
    allow_tools: set[str] | None = None,
) -> Any:
    """! @brief 根据工具名称和参数分发执行对应工具。

    仓库只读检索类工具走 MCP；写文件、运行测试、运行受限命令仍保留在本地，
    方便继续做安全限制和 sandbox 控制。

    @param tool_name 工具名称。
    @param arguments LLM function-call 传入的参数。
    @param repo_path 目标代码仓库根目录。
    @param allow_tools 可选工具白名单；传入后非白名单工具会被拒绝。
    @return 工具执行结果。
    @raise PermissionError 当前 Agent 无权调用工具时抛出。
    @raise ValueError 未知工具名时抛出。
    """

    # 定义阶段的过滤只限制“模型看见什么”；执行阶段必须再次校验，防止
    # 手工构造调用或模型异常返回绕过工具权限边界。
    if allow_tools is not None and tool_name not in allow_tools:
        raise PermissionError(
            f"当前 agent 无权调用这个工具: {tool_name}"
        )

    # 只读仓库能力跨 stdio 交给独立 MCP 进程；有副作用的本地工具继续
    # 在下方显式分发，便于分别实施路径校验、命令白名单和 sandbox。
    if tool_name in MCP_REPOSITORY_TOOLS:
        return call_repository_tool_sync(
            repo_path=repo_path,
            tool_name=tool_name,
            arguments=arguments,
        )

    if tool_name == "write_file":
        return write_file(
            repo_path=repo_path,
            file_path=arguments["file_path"],
            content=arguments["content"],
        )

    if tool_name == "run_test":
        return run_test(
            repo_path=repo_path,
            target=arguments.get("target"),
        )

    if tool_name == "run_command":
        return run_command(
            repo_path=repo_path,
            argv=arguments["argv"],
        )

    raise ValueError(
        f"Unknown tool name: {tool_name}"
    )
