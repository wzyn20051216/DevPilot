#把现有 CodeAgent 抽成基础 Agent
import hashlib
import json
import re
from collections.abc import Iterator
from time import perf_counter
from typing import Any, Callable

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
        edit_deadline: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
        checkpoint_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        initial_messages: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        # 注意：不要再写成 self.xxx = xxx, (带逗号变 tuple)
        # 同时给 self 属性加显式注解，避免 Pyright 把 self.name
        # 推成 str 而与 AgentRole 字面量不兼容。
        self.repo_path: str = repo_path
        self.name: AgentRole = name
        self.system_prompt: str = system_prompt
        self.allowed_tools: set[str] = set(allowed_tools)
        self.max_iterations: int = max_iterations
        self.edit_deadline = edit_deadline
        self.cancel_check = cancel_check or (lambda: False)
        # 上下文断点恢复钩子：默认 None，现有子类零改动即可工作。
        self.checkpoint_callback = checkpoint_callback
        # 恢复执行时的起始消息：默认 None，走现状的 system+user 构建路径。
        self.initial_messages = initial_messages
        self.client: Any = create_client()

    def _cancellation_event(self, state: AgentState) -> AgentEvent:
        """! @brief 构造统一的协作式取消事件。"""

        state.status = "failed"
        return AgentEvent(
            type="cancelled",
            agent=self.name,
            iteration=state.iteration,
            message=f"{self.name} 已停止：用户取消任务",
            data={
                "usage": self._usage_payload(state),
                "timing": {
                    "llm_seconds": state.llm_seconds,
                    "tool_seconds": state.tool_seconds,
                },
            },
        )

    @staticmethod
    def _usage_payload(state: AgentState) -> dict[str, Any]:
        """! @brief 构造终止事件中的累计 usage 数据（含 Prompt Cache 命中统计）。"""

        return {
            "prompt_tokens": state.prompt_tokens,
            "completion_tokens": state.completion_tokens,
            "total_tokens": state.total_tokens,
            "prompt_cache_hit_tokens": state.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": state.prompt_cache_miss_tokens,
        }

    @staticmethod
    def _answer_with_test_evidence(
        answer: str,
        *,
        state: AgentState,
        last_edit_call: int,
        last_test_call: int,
        last_test_targeted: bool,
    ) -> str:
        """! @brief 用工具执行记录约束修改后的测试结论。"""

        if last_edit_call == 0:
            return answer
        if last_test_call <= last_edit_call or state.test_report is None:
            notice = "机器验证：修改后未执行 run_test，尚不能确认测试通过。"
        elif not state.test_report.passed:
            notice = f"机器验证：最近一次 run_test 未通过（{state.test_report.summary}）。"
        elif last_test_targeted:
            notice = "机器验证：目标测试通过，尚未确认完整测试套件通过。"
        else:
            notice = "机器验证：修改后 run_test 完整测试套件通过。"
        return f"{notice}\n\n{answer}"

    @staticmethod
    def _llm_completion_options() -> dict[str, Any]:
        """! @brief 仅对显式启用的 DeepSeek 思考模式附加请求参数。"""

        if not settings.llm_model.startswith("deepseek-") or not settings.llm_reasoning_effort:
            return {}
        return {
            "reasoning_effort": settings.llm_reasoning_effort,
            "extra_body": {"thinking": {"type": "enabled"}},
        }

    @staticmethod
    def _accumulate_usage(state: AgentState, response: Any) -> None:
        """! @brief 将单次 LLM 请求的 Token 用量累加到 Agent 状态。"""

        usage = getattr(response, "usage", None)
        if usage is None:
            return
        state.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        state.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        state.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
        # DeepSeek 等供应商会在 usage 里带 Prompt Cache 命中/未命中 Token；
        # 用 getattr 容错，不支持的网关保持 0。
        state.prompt_cache_hit_tokens += int(
            getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        )
        state.prompt_cache_miss_tokens += int(
            getattr(usage, "prompt_cache_miss_tokens", 0) or 0
        )

    @staticmethod
    def _compact_history(
        messages: list[ChatCompletionMessageParam],
        state: AgentState,
    ) -> tuple[list[ChatCompletionMessageParam], bool]:
        """! @brief 压缩旧工具回合，并保留最近完整的协议消息。

        system 与原始 user 消息始终保留。裁剪点若落在 tool 消息中，会向前
        回退到对应 assistant tool_calls，防止产生没有调用方的孤立结果。
        """

        recent = settings.agent_recent_messages
        if len(messages) <= recent + 3:
            return messages, False

        tail_start = max(2, len(messages) - recent)
        while tail_start > 2 and messages[tail_start].get("role") == "tool":
            tail_start -= 1
        if tail_start <= 2:
            return messages, False

        removed = messages[2:tail_start]
        lines: list[str] = []
        for raw_message in removed:
            message = dict(raw_message)
            role = str(message.get("role", "unknown"))
            if role == "assistant" and message.get("tool_calls"):
                calls = []
                for call in message.get("tool_calls", []):
                    function = dict(call).get("function", {})
                    name = str(dict(function).get("name", "unknown"))
                    arguments = str(dict(function).get("arguments", ""))[:300]
                    calls.append(f"{name}({arguments})")
                lines.append("调用工具：" + "; ".join(calls))
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            limit = 700 if role == "tool" else 400
            lines.append(f"{role}：{content[:limit]}")

        facts = ["以下是较早操作的压缩记录；最近完整回合仍保留在后文："]
        if state.modified_files:
            facts.append("已修改文件：" + ", ".join(map(str, state.modified_files)))
        if state.test_report is not None:
            facts.append(
                "最近测试："
                + ("通过；" if state.test_report.passed else "失败；")
                + state.test_report.summary
            )
        summary = "\n".join((*facts, *lines))
        max_chars = settings.agent_history_summary_max_chars
        if len(summary) > max_chars:
            prefix = "\n".join(facts) + "\n...[更早记录已省略]...\n"
            if len(prefix) >= max_chars:
                summary = prefix[:max_chars]
            else:
                summary = prefix + summary[-(max_chars - len(prefix)) :]

        compacted: list[ChatCompletionMessageParam] = [
            *messages[:2],
            {"role": "user", "content": summary},
            *messages[tail_start:],
        ]
        return compacted, True

    @staticmethod
    def _fold_blank_lines(text: str) -> str:
        """! @brief 把连续 3 个及以上换行折叠为一个空行，压缩纯空白体积。"""

        return re.sub(r"\n{3,}", "\n\n", text)

    @staticmethod
    def _dedupe_observation(
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        observation: str,
        iteration: int,
        observation_cache: dict[str, tuple[int, str]],
        read_file_hashes: dict[str, tuple[int, str]],
    ) -> str:
        """! @brief 对本次观测做去重，命中缓存时返回紧凑指针。

        去重 key = 工具名 + 规范化参数，保证只有"同一操作"才可能命中；值保存
        首次出现的轮次与观测原文。read_file 单独按文件内容 sha256 判定，因为
        读文件的语义是"内容是否变化"，用哈希既省内存又能给出更明确的提示。
        """

        key = f"{tool_name}|{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"

        if tool_name == "read_file":
            # read_file 返回的是文件文本（str），直接对内容做哈希；其它形态
            # 兜底用序列化后的观测文本，避免结构变化导致判空。
            content = result if isinstance(result, str) else observation
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            previous = read_file_hashes.get(key)
            if previous is not None and previous[1] == digest:
                return (
                    f"[观测去重] 文件内容未变化：与第 {previous[0]} 轮 read_file "
                    f"结果一致（约 {len(observation)} 字符已省略）。"
                )
            read_file_hashes[key] = (iteration, digest)
            return observation

        previous = observation_cache.get(key)
        if previous is not None and previous[1] == observation:
            return (
                f"[观测去重] 与第 {previous[0]} 轮工具 {tool_name} 的结果完全一致"
                f"（约 {len(observation)} 字符已省略）。如需完整内容请用不同参数重新调用。"
                f"\n\n{observation[:200]}"
            )
        observation_cache[key] = (iteration, observation)
        return observation

    def _emit_checkpoint(self, messages: list[ChatCompletionMessageParam]) -> None:
        """! @brief 尝试调用检查点回调；任何异常都静默跳过。

        检查点只是为"上下文断点恢复"预埋的观测钩子，绝不能打断 tool-calling
        主循环，因此这里吞掉所有异常而不是向上传播。
        """

        if self.checkpoint_callback is None:
            return
        try:
            # 通过 JSON 往返构造一个与主循环互不相干的深拷贝，防止回调方
            # 修改消息对象污染后续 LLM 请求。
            copies = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
            self.checkpoint_callback(self.name, copies)
        except Exception:  # noqa: BLE001
            pass

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
        if self.cancel_check():
            yield self._cancellation_event(state)
            return

        # -------------------------
        # 2. 构建 LLM 上下文
        # -------------------------
        if self.initial_messages is not None:
            # 断点恢复：以注入消息的副本为起点，保证首条 system、第二条 user
            # 与保存时一致；后续追加的新消息不会改动调用方持有的原列表。
            messages: list[ChatCompletionMessageParam] = list(self.initial_messages)
            # 检查点只消费一次；后续修复轮必须使用新的失败反馈。
            self.initial_messages = None
            messages.append({"role": "user", "content": question})
        else:
            messages = [
                {
                    "role": "system",
                    "content": self.system_prompt,
                },
                {
                    "role": "user",
                    "content": question,
                },
            ]
        # 本次运行内的观测去重缓存：key → (产生轮次, 观测原文/内容哈希)。
        # 只作用于写回 LLM 的 observation，持久化追踪（state.tool_calls /
        # SSE tool_result）不受影响。
        observation_cache: dict[str, tuple[int, str]] = {}
        read_file_hashes: dict[str, tuple[int, str]] = {}
        try:
            # 同一次 Agent 运行的工具 schema 不会变化。只发现一次可避免每轮
            # 重新启动 Repository MCP 子进程，也减少长任务中的固定延迟。
            tool_definitions = get_tool_definitions(
                repo_path=self.repo_path,
                allowed_tools=self.allowed_tools,
            )
            full_test_passed = False
            last_edit_call = 0
            last_test_call = 0
            last_test_targeted = False
            # -------------------------
            # 3. Agent Loop
            # -------------------------
            for iteration in range(1, self.max_iterations + 1):
                state.iteration = iteration
                if self.cancel_check():
                    yield self._cancellation_event(state)
                    return
                yield AgentEvent(
                    type="thinking",
                    agent=self.name,
                    iteration=iteration,
                    message=f"agent 正在第 {iteration} 轮分析",
                )

                messages, _ = self._compact_history(messages, state)

                # -------------------------
                # 4. 调用 LLM（按 allowed_tools 过滤）
                # -------------------------
                llm_started = perf_counter()
                must_edit = (
                    self.edit_deadline is not None
                    and iteration >= self.edit_deadline
                    and not state.modified_files
                )
                active_tool_definitions = tool_definitions
                tool_choice: str = "auto"
                if full_test_passed:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "代码已修改且完整测试套件通过。不得再调用工具，"
                                "请立即简要总结修改和验证结果。"
                            ),
                        }
                    )
                    tool_choice = "none"
                elif must_edit:
                    active_tool_definitions = [
                        definition
                        for definition in tool_definitions
                        if definition["function"]["name"]
                        in {"replace_in_file", "write_file"}
                    ]
                    if active_tool_definitions:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "定位预算已经结束。本轮必须立即调用写入工具"
                                    "实施当前最可信的最小修复；不得继续搜索或只输出建议。"
                                ),
                            }
                        )
                # 本轮实际开放的工具还要在执行层校验；仅缩小发送给模型的
                # schema 不足以阻止兼容网关返回旧工具调用。
                active_tool_names = {
                    definition["function"]["name"]
                    for definition in active_tool_definitions
                }
                response = self.client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    tools=active_tool_definitions,
                    tool_choice=tool_choice,
                    temperature=0.2,
                    **self._llm_completion_options(),
                )
                state.llm_seconds += perf_counter() - llm_started
                # Usage 属于本次 LLM 请求，不包含前几轮，所以每轮都要累加。
                # 使用 getattr 兼容不返回 usage 或字段为空的 OpenAI 兼容服务。
                self._accumulate_usage(state, response)
                if (
                    settings.agent_token_budget > 0
                    and state.total_tokens > settings.agent_token_budget
                ):
                    yield AgentEvent(
                        type="error",
                        agent=self.name,
                        iteration=iteration,
                        message=(
                            "Agent Token 预算已用尽："
                            f"{state.total_tokens}/{settings.agent_token_budget}"
                        ),
                        data={
                            "usage": self._usage_payload(state),
                            "timing": {
                                "llm_seconds": state.llm_seconds,
                                "tool_seconds": state.tool_seconds,
                            },
                        },
                    )
                    return
                message = response.choices[0].message

                # -------------------------
                # 5. 保存 assistant 消息
                # -------------------------
                assistant_message: ChatCompletionAssistantMessageParam = {
                    "role": "assistant",
                    "content": message.content,
                }
                # DeepSeek 思考模式与工具调用联用时，后续请求必须带回原始
                # reasoning_content；只在内部消息中保存，不写入 SSE/Trace。
                reasoning_content = getattr(message, "reasoning_content", None)
                if settings.llm_model.startswith("deepseek-") and reasoning_content is not None:
                    assistant_message["reasoning_content"] = reasoning_content  # type: ignore[typeddict-unknown-key]
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

                # DeepSeek thinking 模式不支持 tool_choice="required"。当编辑
                # 截止轮次已到而模型只给文字时，拒绝提前结束并继续下一轮；
                # 同时只暴露写工具，从协议层把行为收敛到真实代码修改。
                if must_edit and not message.tool_calls:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "当前任务尚未产生任何代码改动，不能结束。"
                                "请在下一轮调用可用的写入工具实施最小修复。"
                            ),
                        }
                    )
                    self._emit_checkpoint(messages)
                    continue

                # -------------------------
                # 6. 没有 Tool Call → Agent 任务完成
                # -------------------------
                if not message.tool_calls:
                    state.status = "completed"
                    state.answer = self._answer_with_test_evidence(
                        message.content or "",
                        state=state,
                        last_edit_call=last_edit_call,
                        last_test_call=last_test_call,
                        last_test_targeted=last_test_targeted,
                    )
                    yield AgentEvent(
                        type="final",
                        agent=self.name,
                        iteration=iteration,
                        message=state.answer,
                        data={
                            "answer": state.answer,
                            "iteration": iteration,
                            "tool_calls": [item.model_dump() for item in state.tool_calls],
                            "usage": self._usage_payload(state),
                            "timing": {
                                "llm_seconds": state.llm_seconds,
                                "tool_seconds": state.tool_seconds,
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
                    if self.cancel_check():
                        yield self._cancellation_event(state)
                        return
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
                    tool_started = perf_counter()
                    try:
                        if active_tool_names and tool_name not in active_tool_names:
                            raise PermissionError(f"本轮不允许调用工具 {tool_name}")
                        if tool_choice == "none":
                            raise PermissionError(f"本轮禁止调用任何工具：{tool_name}")
                        result = execute_tool(
                            tool_name=tool_name,
                            arguments=arguments,
                            repo_path=self.repo_path,
                            allow_tools=(
                                self.allowed_tools.intersection(active_tool_names)
                                if active_tool_names else self.allowed_tools
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001
                        # 工具异常也转换成 observation 回传给模型，使模型有机会
                        # 修正参数或选择其它工具，而不是立即终止整个 Agent。
                        result = {"error": str(exc)}
                    tool_elapsed = perf_counter() - tool_started
                    state.tool_seconds += tool_elapsed

                    # -------------------------
                    # 11. 更新 Agent State
                    # -------------------------
                    record = ToolCallRecord(
                        iteration=iteration,
                        tool=tool_name,
                        arguments=arguments,
                        result_preview=str(result)[:500],
                        elapsed_seconds=tool_elapsed,
                        succeeded=not (
                            isinstance(result, dict) and "error" in result
                        ),
                    )
                    # result_preview 只用于追踪和界面展示，因此限制长度；下方写回
                    # messages 的 observation 使用独立的可配置上限。
                    state.tool_calls.append(record)

                    # 11.1 记录已修改文件（write_file 工具）
                    if tool_name in {"write_file", "replace_in_file"} and isinstance(result, dict) and result.get("changed"):
                        modified_path = result.get("file_path")
                        if modified_path and modified_path not in state.modified_files:
                            state.modified_files.append(modified_path)
                        last_edit_call = len(state.tool_calls)
                        full_test_passed = False

                    # 11.2 记录测试报告（run_test 工具）
                    #     统一写进 state.test_report，不再使用不存在的
                    #     state.tests_passed（Pydantic v2 对未声明字段赋值会抛 ValueError）
                    if tool_name == "run_test" and isinstance(result, dict) and "passed" in result:
                        last_test_call = len(state.tool_calls)
                        last_test_targeted = bool(arguments.get("target"))
                        state.test_report = TesterOutput(
                            passed=bool(result.get("passed")),
                            summary=(
                                f"pytest returncode={result.get('returncode')}"
                                + ("（执行超时）" if result.get("timed_out") else "")
                            ),
                            stdout=str(result.get("stdout", "")),
                            stderr=str(result.get("stderr", "")),
                        )
                        full_test_passed = (
                            state.test_report.passed
                            and not last_test_targeted
                            and bool(state.modified_files)
                            and last_test_call > last_edit_call
                        )

                    # -------------------------
                    # 12. Tool Result Event
                    # -------------------------
                    event_data: dict[str, Any] = {
                        "tool": tool_name,
                        "arguments": arguments,
                        "result_preview": str(result)[:500],
                        "elapsed_seconds": round(tool_elapsed, 6),
                        "succeeded": not (
                            isinstance(result, dict) and "error" in result
                        ),
                    }
                    # Tester 的 run_test 返回值是可信的机器事实。将精简的
                    # 结构化报告放入事件，供 Orchestrator 在 LLM 最终 JSON
                    # 格式有瑕疵时兜底，不携带未截断的任意工具输出。
                    if tool_name == "run_test" and state.test_report is not None:
                        test_report_data = state.test_report.model_dump()
                        # SSE 事件只需要关键尾部用于展示和兜底，
                        # 避免把 Sandbox 允许的整段输出复制进事件流。
                        test_report_data["stdout"] = state.test_report.stdout[-4_000:]
                        test_report_data["stderr"] = state.test_report.stderr[-4_000:]
                        event_data["test_report"] = test_report_data

                    yield AgentEvent(
                        type="tool_result",
                        agent=self.name,
                        iteration=iteration,
                        message=f"工具 {tool_name} 执行完成",
                        data=event_data,
                    )

                    # -------------------------
                    # 13. Observation 返回给 LLM
                    # -------------------------
                    observation = json.dumps(result, ensure_ascii=False, default=str)
                    # 13.1 工具观测去重：相同工具+相同参数+相同结果时只给模型
                    #      一个紧凑指针，避免把整段结果反复塞进上下文。
                    if settings.agent_dedupe_observations:
                        observation = self._dedupe_observation(
                            tool_name=tool_name,
                            arguments=arguments,
                            result=result,
                            observation=observation,
                            iteration=iteration,
                            observation_cache=observation_cache,
                            read_file_hashes=read_file_hashes,
                        )
                    # 13.2 长观测摘要：保留 head+tail 截断，截断前折叠连续空行
                    #      并在标记中注明原始长度，让模型知道省略了多少内容。
                    if len(observation) > settings.tool_observation_max_chars:
                        original_len = len(observation)
                        tail_chars = min(2_000, settings.tool_observation_max_chars // 4)
                        head_chars = settings.tool_observation_max_chars - tail_chars
                        head = self._fold_blank_lines(observation[:head_chars])
                        tail = self._fold_blank_lines(observation[-tail_chars:])
                        observation = (
                            head
                            + f"\n...[工具结果已截断，原始 {original_len} 字符]...\n"
                            + tail
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            # default=str 让 Path、时间等非原生 JSON 对象也能作为
                            # 文本 observation 返回，不让序列化细节打断工具循环。
                            "content": observation,
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
                # 本轮消息（assistant + tool observation）已全部追加完成，
                # 进入下一轮前通知检查点钩子做断点持久化。
                self._emit_checkpoint(messages)
            # -------------------------
            # 14. 达到最大迭代次数
            # -------------------------
            # 工具轮数耗尽并不代表任务失败：最后一轮工具结果
            # 刚回填到上下文，模型还没获得根据它生成最终答案的机会。
            # 追加一次禁止工具的收尾请求，它不再扩大工具循环。
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "工具调用阶段已结束。现在不得再调用工具，"
                        "请立即按 system prompt 要求输出最终结果。"
                    ),
                }
            )
            messages, _ = self._compact_history(messages, state)
            llm_started = perf_counter()
            final_response = self.client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                tools=tool_definitions,
                tool_choice="none",
                temperature=0.0,
                **self._llm_completion_options(),
            )
            state.llm_seconds += perf_counter() - llm_started
            self._accumulate_usage(state, final_response)
            if (
                settings.agent_token_budget > 0
                and state.total_tokens > settings.agent_token_budget
            ):
                state.status = "failed"
                yield AgentEvent(
                    type="error",
                    agent=self.name,
                    iteration=state.iteration,
                    message=(
                        "Agent Token 预算已用尽："
                        f"{state.total_tokens}/{settings.agent_token_budget}"
                    ),
                    data={
                        "usage": self._usage_payload(state),
                        "timing": {
                            "llm_seconds": state.llm_seconds,
                            "tool_seconds": state.tool_seconds,
                        },
                    },
                )
                return
            final_message = final_response.choices[0].message
            if final_message.tool_calls or not final_message.content:
                state.status = "failed"
                yield AgentEvent(
                    type="error",
                    agent=self.name,
                    iteration=state.iteration,
                    message="agent 达到最大迭代次数且未产出最终结果",
                    data={
                        "iteration": state.iteration,
                        "tool_calls": [
                            item.model_dump() for item in state.tool_calls
                        ],
                        "usage": self._usage_payload(state),
                        "timing": {
                            "llm_seconds": state.llm_seconds,
                            "tool_seconds": state.tool_seconds,
                        },
                    },
                )
                return

            state.status = "completed"
            state.answer = self._answer_with_test_evidence(
                final_message.content,
                state=state,
                last_edit_call=last_edit_call,
                last_test_call=last_test_call,
                last_test_targeted=last_test_targeted,
            )
            yield AgentEvent(
                type="final",
                agent=self.name,
                iteration=state.iteration,
                message=state.answer,
                data={
                    "answer": state.answer,
                    "iteration": state.iteration,
                    "tool_calls": [item.model_dump() for item in state.tool_calls],
                    "usage": self._usage_payload(state),
                    "timing": {
                        "llm_seconds": state.llm_seconds,
                        "tool_seconds": state.tool_seconds,
                    },
                },
            )
            return

        # -------------------------
        # 15. Agent 本身发生异常
        # -------------------------
        except Exception as exc:  # noqa: BLE001
            state.status = "failed"
            yield AgentEvent(
                type="error",
                agent=self.name,
                iteration=state.iteration,
                message=str(exc),
                data={
                    "usage": self._usage_payload(state),
                    "timing": {
                        "llm_seconds": state.llm_seconds,
                        "tool_seconds": state.tool_seconds,
                    },
                },
            )
            return
