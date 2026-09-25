#把现有 CodeAgent 抽成基础 Agent
import json
from typing import Any
from collections.abc import Iterator
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
)

from ..config import settings
from ..llm_client import create_client
from ..models.agent_state import (
    AgentEvent,
    AgentRole,
    AgentState,
    TesterOutput,
    ToolCallRecord,
)
from ..tools.registry import execute_tool, get_tool_definitions


class BaseToolAgent:
    """支持 tool calling 的基础 Agent。
    CodeAgent / Planner / Tester 等具体角色都基于它扩展。"""

    def __init__(
        self,
        repo_path: str,
        name: AgentRole,
        system_prompt: str,
        allowed_tools: set[str],
        max_iterations: int = 8,
    ) -> None:
        # 注意：不要再写成 self.xxx = xxx, (带逗号变 tuple)
        # 同时给 self 属性加显式注解，避免 Pyright 把 self.name
        # 推成 str 而与 AgentRole 字面量不兼容。
        self.repo_path: str = repo_path
        self.name: AgentRole = name
        self.system_prompt: str = system_prompt
        self.allowed_tools: set[str] = set(allowed_tools)
        self.max_iterations: int = max_iterations
        self.client: Any = create_client()

    def run_stream(self, question: str) -> Iterator[AgentEvent]:
        """流式执行 agent 的 tool-calling 循环，边跑边产出事件。

        这是所有子类 agent 共用的核心循环，整体分四步反复迭代：
        1. 把 system_prompt + 用户问题构建成消息列表；
        2. 调用 LLM（只暴露 `allowed_tools` 白名单里的工具定义）；
        3. 若 LLM 返回 tool_calls，则逐个执行工具、把结果作为 observation 回填消息；
        4. 若 LLM 不再要工具，则视为完成，产出 final 事件并结束。
        达到 `max_iterations` 仍未完成，或中途异常，则产出 error 事件。

        Args:
            question: 本次要交给 agent 处理的问题。

        Yields:
            AgentEvent: 依次为 start / thinking / tool_call / tool_result / final / error，
                供调用方（或 FastAPI 的 SSE）实时消费。
        """

        # -------------------------
        # 1. 创建 Agent State
        # -------------------------
        state = AgentState(
            repo_path=self.repo_path,
            question=question,
            answer="",
        )
        state.status = "running"
        yield AgentEvent(
            type="start",
            agent=self.name,
            message="DevPilot 开始执行代码仓库",
        )

        # -------------------------
        # 2. 构建 LLM 上下文
        # -------------------------
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": self.system_prompt,
            },
            {
                "role": "user",
                "content": question,
            },
        ]

        try:
            # -------------------------
            # 3. Agent Loop
            # -------------------------
            for iteration in range(1, self.max_iterations + 1):
                state.iteration = iteration
                yield AgentEvent(
                    type="thinking",
                    agent=self.name,
                    iteration=iteration,
                    message=f"agent 正在第 {iteration} 轮分析",
                )

                # -------------------------
                # 4. 调用 LLM（按 allowed_tools 过滤）
                # -------------------------
                response = self.client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    tools=get_tool_definitions(
                        repo_path=self.repo_path,
                        allowed_tools=self.allowed_tools,
                    ),
                    tool_choice="auto",
                    temperature=0.2,
                )
                # Usage 属于本次 LLM 请求，不包含前几轮，所以每轮都要累加。
                # 使用 getattr 兼容不返回 usage 或字段为空的 OpenAI 兼容服务。
                usage = getattr(response, "usage", None)
                if usage is not None:
                    state.prompt_tokens += int(
                        getattr(usage, "prompt_tokens", 0) or 0
                    )
                    state.completion_tokens += int(
                        getattr(usage, "completion_tokens", 0) or 0
                    )
                    state.total_tokens += int(
                        getattr(usage, "total_tokens", 0) or 0
                    )
                message = response.choices[0].message

                # -------------------------
                # 5. 保存 assistant 消息
                # -------------------------
                assistant_message: ChatCompletionAssistantMessageParam = {
                    "role": "assistant",
                    "content": message.content,
                }
                # OpenAI 的工具调用协议要求：工具结果消息之前，必须先保留
                # assistant 发出的 tool_calls（含 call id）。下一轮模型会依靠
                # 这个 id 把每条 observation 对回原来的工具请求。
                if message.tool_calls:
                    assistant_tool_calls: list[ChatCompletionMessageFunctionToolCallParam] = []
                    for tool_call in message.tool_calls:
                        if tool_call.type != "function":
                            continue
                        assistant_tool_calls.append(
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_call.function.arguments,
                                },
                            }
                        )
                    if assistant_tool_calls:
                        assistant_message["tool_calls"] = assistant_tool_calls
                messages.append(assistant_message)

                # -------------------------
                # 6. 没有 Tool Call → Agent 任务完成
                # -------------------------
                if not message.tool_calls:
                    state.status = "completed"
                    state.answer = message.content or ""
                    yield AgentEvent(
                        type="final",
                        agent=self.name,
                        iteration=iteration,
                        message=state.answer,
                        data={
                            "answer": state.answer,
                            "iteration": iteration,
                            "tool_calls": [item.model_dump() for item in state.tool_calls],
                            "usage": {
                                "prompt_tokens": state.prompt_tokens,
                                "completion_tokens": state.completion_tokens,
                                "total_tokens": state.total_tokens,
                            },
                        },
                    )
                    return

                # -------------------------
                # 7. 执行 Tool Calling
                # -------------------------
                handled_function_call = False
                for tool_call in message.tool_calls:
                    # 类型收窄
                    if tool_call.type != "function":
                        continue
                    handled_function_call = True
                    tool_name = tool_call.function.name

                    # -------------------------
                    # 8. 解析 Tool Arguments
                    # -------------------------
                    try:
                        # function.arguments 在接口层是 JSON 字符串，不是现成 dict。
                        # 解析失败时仍让工具层按“缺少参数”返回可理解的错误，
                        # 避免整条 Agent 流水线因一次模型格式错误直接中断。
                        arguments: dict[str, Any] = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                    # -------------------------
                    # 9. Tool Call 事件
                    # -------------------------
                    yield AgentEvent(
                        type="tool_call",
                        agent=self.name,
                        iteration=iteration,
                        message=f"准备调用工具：{tool_name}",
                        data={"tool": tool_name, "arguments": arguments},
                    )

                    # -------------------------
                    # 10. 真正执行 Tool
                    # -------------------------
                    try:
                        result = execute_tool(
                            tool_name=tool_name,
                            arguments=arguments,
                            repo_path=self.repo_path,
                            allow_tools=self.allowed_tools,
                        )
                    except Exception as exc:
                        # 工具异常也转换成 observation 回传给模型，使模型有机会
                        # 修正参数或选择其它工具，而不是立即终止整个 Agent。
                        result = {"error": str(exc)}

                    # -------------------------
                    # 11. 更新 Agent State
                    # -------------------------
                    record = ToolCallRecord(
                        iteration=iteration,
                        tool=tool_name,
                        arguments=arguments,
                        result_preview=str(result)[:500],
                    )
                    # result_preview 只用于追踪和界面展示，因此限制长度；下方写回
                    # messages 的工具结果仍然是完整 result，不会损失模型上下文。
                    state.tool_calls.append(record)

                    # 11.1 记录已修改文件（write_file 工具）
                    if tool_name == "write_file" and isinstance(result, dict) and result.get("changed"):
                        modified_path = result.get("file_path")
                        if modified_path and modified_path not in state.modified_files:
                            state.modified_files.append(modified_path)

                    # 11.2 记录测试报告（run_test 工具）
                    #     统一写进 state.test_report，不再使用不存在的
                    #     state.tests_passed（Pydantic v2 对未声明字段赋值会抛 ValueError）
                    if tool_name == "run_test" and isinstance(result, dict) and "passed" in result:
                        state.test_report = TesterOutput(
                            passed=bool(result.get("passed")),
                            summary=(
                                f"pytest returncode={result.get('returncode')}"
                                + ("（执行超时）" if result.get("timed_out") else "")
                            ),
                            stdout=str(result.get("stdout", "")),
                            stderr=str(result.get("stderr", "")),
                        )

                    # -------------------------
                    # 12. Tool Result Event
                    # -------------------------
                    yield AgentEvent(
                        type="tool_result",
                        agent=self.name,
                        iteration=iteration,
                        message=f"工具 {tool_name} 执行完成",
                        data={
                            "tool": tool_name,
                            "arguments": arguments,
                            "result_preview": str(result)[:500],
                        },
                    )

                    # -------------------------
                    # 13. Observation 返回给 LLM
                    # -------------------------
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            # default=str 让 Path、时间等非原生 JSON 对象也能作为
                            # 文本 observation 返回，不让序列化细节打断工具循环。
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )

                if not handled_function_call:
                    state.status = "failed"
                    yield AgentEvent(
                        type="error",
                        agent=self.name,
                        iteration=iteration,
                        message="模型返回了当前 devpilot 不支持的工具类型",
                    )
                    return
            # -------------------------
            # 14. 达到最大迭代次数
            # -------------------------
            else:
                # 这是 Python 的 for...else：只有循环自然耗尽、期间没有 return
                # 时才进入这里，表示模型连续请求工具却始终没有给出最终答案。
                state.status = "failed"
                yield AgentEvent(
                    type="error",
                    agent=self.name,
                    iteration=state.iteration,
                    message="agent 达到最大迭代次数，任务仍未正常完成",
                    data={
                        "iteration": state.iteration,
                        "tool_calls": [item.model_dump() for item in state.tool_calls],
                        "usage": {
                            "prompt_tokens": state.prompt_tokens,
                            "completion_tokens": state.completion_tokens,
                            "total_tokens": state.total_tokens,
                        },
                    },
                )
                return

        # -------------------------
        # 15. Agent 本身发生异常
        # -------------------------
        except Exception as exc:
            state.status = "failed"
            yield AgentEvent(
                type="error",
                agent=self.name,
                iteration=state.iteration,
                message=str(exc),
                data={
                    "usage": {
                        "prompt_tokens": state.prompt_tokens,
                        "completion_tokens": state.completion_tokens,
                        "total_tokens": state.total_tokens,
                    }
                },
            )
            return
