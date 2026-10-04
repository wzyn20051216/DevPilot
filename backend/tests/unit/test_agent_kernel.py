"""! @brief Agent 内核改进的离线单元测试。

覆盖技术手册中的几项内核改进：
- 工具观测去重（降 Token）；
- 长观测摘要增强（空行折叠 + 原始长度标记）；
- Prompt Cache 命中/未命中 Token 统计；
- protocol_probe 运行时协议探针工具；
- 上下文断点恢复的检查点钩子。

全部测试离线运行：不联网、不调真实 LLM、不使用真实 Docker。
"""
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from backend.src.agents import base_tool_agent
from backend.src.agents.base_tool_agent import BaseToolAgent
from backend.src.evals import runner
from backend.src.tools import protocol_probe


def _response(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    prompt_cache_hit: int = 0,
    prompt_cache_miss: int = 0,
    reasoning_content: str | None = None,
) -> SimpleNamespace:
    """构造 BaseToolAgent 需要的最小 OpenAI 兼容响应。"""

    message = SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=reasoning_content
    )
    usage = SimpleNamespace(
        prompt_tokens=3,
        completion_tokens=2,
        total_tokens=5,
        prompt_cache_hit_tokens=prompt_cache_hit,
        prompt_cache_miss_tokens=prompt_cache_miss,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


def _tool_call(
    arguments: str = "{}",
    name: str = "read_file",
    call_id: str = "call-1",
) -> SimpleNamespace:
    """构造一个 function tool call。"""

    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _FakeCompletions:
    """按顺序返回响应或抛出异常的 completions stub。"""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = iter(outcomes)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _build_agent(
    monkeypatch: MonkeyPatch,
    outcomes: list[Any],
    max_iterations: int = 3,
    **kwargs: Any,
) -> tuple[BaseToolAgent, Any]:
    """创建不联网、不启动 MCP 的基础 Agent，并返回其 fake client。"""

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(outcomes))
    )
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: [])
    return (
        BaseToolAgent(
            repo_path=".",
            name="coder",
            system_prompt="test",
            allowed_tools={"read_file"},
            max_iterations=max_iterations,
            **kwargs,
        ),
        client,
    )


def _tool_messages(client: Any) -> list[dict[str, Any]]:
    """取出最后一轮 LLM 请求中的 tool 消息列表。"""

    return [
        message
        for message in client.chat.completions.requests[-1]["messages"]
        if message.get("role") == "tool"
    ]


# ---------------------------------------------------------------
# A. 工具观测去重
# ---------------------------------------------------------------

def test_observation_dedupe_read_file(monkeypatch: MonkeyPatch) -> None:
    """连续两次相同 read_file 时，第二次应改写为紧凑指针。"""

    content = "line\n" * 500
    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c1")]),
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c2")]),
        _response(content="done"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: content)

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    tool_messages = _tool_messages(client)
    assert len(tool_messages) == 2
    assert "[观测去重]" not in tool_messages[0]["content"]
    assert "[观测去重]" in tool_messages[1]["content"]
    assert "文件内容未变化" in tool_messages[1]["content"]
    # 指针必须显著短于首次全量观测。
    assert len(tool_messages[1]["content"]) * 3 < len(tool_messages[0]["content"])


def test_observation_dedupe_content_change_returns_full(
    monkeypatch: MonkeyPatch,
) -> None:
    """文件内容变化时应全量返回，不去重。"""

    first = "v1" * 800
    second = "v2" * 800
    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c1")]),
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c2")]),
        _response(content="done"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    results = iter([first, second])
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: next(results))

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    tool_messages = _tool_messages(client)
    assert "[观测去重]" not in tool_messages[1]["content"]
    assert "v2v2" in tool_messages[1]["content"]


def test_observation_dedupe_disabled(monkeypatch: MonkeyPatch) -> None:
    """agent_dedupe_observations=False 时不去重。"""

    monkeypatch.setattr(
        base_tool_agent.settings, "agent_dedupe_observations", False
    )
    content = "x" * 3000
    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c1")]),
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c2")]),
        _response(content="done"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: content)

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    tool_messages = _tool_messages(client)
    assert len(tool_messages) == 2
    assert "[观测去重]" not in tool_messages[0]["content"]
    assert "[观测去重]" not in tool_messages[1]["content"]


def test_long_observation_summary_notes_original_length(
    monkeypatch: MonkeyPatch,
) -> None:
    """超长观测截断时应在标记中注明原始长度。"""

    content = "line\n" * 6000  # 超过默认 10000 字符上限
    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c1")]),
        _response(content="done"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: content)

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    tool_messages = _tool_messages(client)
    assert len(tool_messages) == 1
    assert "工具结果已截断，原始" in tool_messages[0]["content"]


# ---------------------------------------------------------------
# C. Prompt Cache 命中统计
# ---------------------------------------------------------------

def test_prompt_cache_usage_accumulated(monkeypatch: MonkeyPatch) -> None:
    """usage 中的 cache hit/miss 应累加进 state 与 final 事件，且能被汇总。"""

    outcomes = [
        _response(
            tool_calls=[_tool_call(name="run_command")],
            prompt_cache_hit=10,
            prompt_cache_miss=20,
        ),
        _response(content="done", prompt_cache_hit=5, prompt_cache_miss=6),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: {"ok": True})

    events = list(agent.run_stream("q"))

    final = events[-1]
    assert final.type == "final"
    usage = final.data["usage"]
    assert usage["prompt_cache_hit_tokens"] == 15
    assert usage["prompt_cache_miss_tokens"] == 26

    hit, miss = runner.collect_cache_usage(events)
    assert hit == 15
    assert miss == 26


@pytest.mark.parametrize(
    ("tool_results", "expected_notice"),
    [
        ([{"file_path": "a.py", "changed": True}], "修改后未执行 run_test"),
        (
            [
                {"file_path": "a.py", "changed": True},
                {"passed": False, "returncode": 1, "stdout": "failed", "stderr": ""},
            ],
            "最近一次 run_test 未通过",
        ),
    ],
)
def test_final_answer_uses_latest_machine_test_evidence(
    monkeypatch: MonkeyPatch,
    tool_results: list[dict[str, Any]],
    expected_notice: str,
) -> None:
    """模型声称通过时，最终输出仍需揭示未验证或失败的最新代码状态。"""

    tool_names = [
        "run_test" if "passed" in result else "replace_in_file"
        for result in tool_results
    ]
    outcomes = [
        _response(tool_calls=[_tool_call(name=name, call_id=f"call-{index}")])
        for index, name in enumerate(tool_names)
    ] + [_response(content="所有测试已通过")]
    agent, _ = _build_agent(monkeypatch, outcomes, max_iterations=len(outcomes))
    results = iter(tool_results)
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: next(results))

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    assert events[-1].message.startswith(f"机器验证：{expected_notice}")
    assert "所有测试已通过" in events[-1].message


def test_later_failed_test_clears_prior_full_suite_success(
    monkeypatch: MonkeyPatch,
) -> None:
    """同一工具回合先通过再失败时，不得提前强制收尾。"""

    outcomes = [
        _response(tool_calls=[_tool_call(name="replace_in_file", call_id="edit")]),
        _response(
            tool_calls=[
                _tool_call(name="run_test", call_id="pass"),
                _tool_call(name="run_test", call_id="fail"),
            ]
        ),
        _response(content="已结束"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    results = iter(
        [
            {"file_path": "a.py", "changed": True},
            {"passed": True, "returncode": 0},
            {"passed": False, "returncode": 1},
        ]
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: next(results))

    events = list(agent.run_stream("q"))

    assert client.chat.completions.requests[-1]["tool_choice"] == "auto"
    assert events[-1].message.startswith("机器验证：最近一次 run_test 未通过")


def test_edit_after_test_in_same_turn_invalidates_pass(
    monkeypatch: MonkeyPatch,
) -> None:
    """同一回合测试通过后又写文件，最终不能继承旧测试结论。"""

    outcomes = [
        _response(tool_calls=[_tool_call(name="replace_in_file", call_id="first-edit")]),
        _response(
            tool_calls=[
                _tool_call(name="run_test", call_id="test"),
                _tool_call(name="replace_in_file", call_id="second-edit"),
            ]
        ),
        _response(content="已结束"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    results = iter(
        [
            {"file_path": "a.py", "changed": True},
            {"passed": True, "returncode": 0},
            {"file_path": "a.py", "changed": True},
        ]
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: next(results))

    events = list(agent.run_stream("q"))

    assert client.chat.completions.requests[-1]["tool_choice"] == "auto"
    assert events[-1].message.startswith("机器验证：修改后未执行 run_test")


def test_edit_deadline_rejects_unadvertised_search_tool(
    monkeypatch: MonkeyPatch,
) -> None:
    """只开放写工具的轮次，模型返回搜索调用也不得实际执行。"""

    definitions = [
        {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
        for name in ("search_code", "replace_in_file")
    ]
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: definitions)
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_FakeCompletions(
                [
                    _response(tool_calls=[_tool_call(name="search_code", call_id="search")]),
                    _response(tool_calls=[_tool_call(name="replace_in_file", call_id="edit")]),
                    _response(content="done"),
                ]
            )
        )
    )
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)
    executed: list[str] = []

    def fake_execute(tool_name: str, **_: Any) -> dict[str, Any]:
        executed.append(tool_name)
        return {"changed": True, "file_path": "a.py"}

    monkeypatch.setattr(base_tool_agent, "execute_tool", fake_execute)
    agent = BaseToolAgent(
        repo_path=".",
        name="coder",
        system_prompt="test",
        allowed_tools={"search_code", "replace_in_file"},
        max_iterations=3,
        edit_deadline=1,
    )

    events = list(agent.run_stream("fix"))

    assert executed == ["replace_in_file"]
    assert client.chat.completions.requests[0]["tools"] == [definitions[1]]
    denied = next(event for event in events if event.type == "tool_result")
    assert denied.data["succeeded"] is False
    assert "本轮不允许调用工具" in denied.data["result_preview"]


# ---------------------------------------------------------------
# D. protocol_probe 工具
# ---------------------------------------------------------------

def test_protocol_probe_ok(monkeypatch: MonkeyPatch) -> None:
    """正常结束的探针应判定为 ok，且交给沙箱执行。"""

    captured: dict[str, Any] = {}

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"returncode": 0, "timed_out": False, "stdout": "__PROBE_OK__\n", "stderr": ""}

    monkeypatch.setattr(protocol_probe, "run_in_sandbox", fake_sandbox)

    result = protocol_probe.probe_runtime(
        ".", [{"name": "p1", "code": "print(1)"}]
    )

    assert result["all_passed"] is True
    assert result["probes"][0]["ok"] is True
    assert captured["argv"][0] == "python"
    assert captured["argv"][1] == "-c"
    assert "compile(" in captured["argv"][2]
    assert captured["timeout"] == 30


def test_protocol_probe_fail(monkeypatch: MonkeyPatch) -> None:
    """抛异常的探针应判定为失败并带出异常文本。"""

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        return {
            "returncode": 1,
            "timed_out": False,
            "stdout": "__PROBE_FAIL__: ValueError bad value\n",
            "stderr": "",
        }

    monkeypatch.setattr(protocol_probe, "run_in_sandbox", fake_sandbox)

    result = protocol_probe.probe_runtime(
        ".", [{"name": "p1", "code": "raise ValueError('bad value')"}]
    )

    assert result["all_passed"] is False
    assert result["probes"][0]["ok"] is False
    assert result["probes"][0]["exception"] == "ValueError bad value"


def test_protocol_probe_expect_exception(monkeypatch: MonkeyPatch) -> None:
    """expect_exception 匹配/不匹配两个分支都应正确判定。"""

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        return {
            "returncode": 1,
            "timed_out": False,
            "stdout": "__PROBE_FAIL__: ValueError bad value\n",
            "stderr": "",
        }

    monkeypatch.setattr(protocol_probe, "run_in_sandbox", fake_sandbox)

    matched = protocol_probe.probe_runtime(
        ".", [{"name": "p1", "code": "x", "expect_exception": "bad value"}]
    )
    assert matched["probes"][0]["ok"] is True
    assert matched["all_passed"] is True

    mismatched = protocol_probe.probe_runtime(
        ".", [{"name": "p2", "code": "x", "expect_exception": "KeyError"}]
    )
    assert mismatched["probes"][0]["ok"] is False
    assert mismatched["all_passed"] is False


def test_protocol_probe_too_many_raises(monkeypatch: MonkeyPatch) -> None:
    """探针数量超过 8 时应抛 ValueError。"""

    monkeypatch.setattr(
        protocol_probe,
        "run_in_sandbox",
        lambda **_: {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""},
    )
    probes = [{"name": f"p{i}", "code": "pass"} for i in range(9)]

    with pytest.raises(ValueError, match="8"):
        protocol_probe.probe_runtime(".", probes)


def test_protocol_probe_code_too_long_raises() -> None:
    """单个探针源码超过 4000 字符时应抛 ValueError。"""

    with pytest.raises(ValueError, match="4000"):
        protocol_probe.probe_runtime(".", [{"name": "p", "code": "x" * 4001}])


# ---------------------------------------------------------------
# F. 检查点钩子与断点恢复
# ---------------------------------------------------------------

def test_checkpoint_callback_called_each_iteration(monkeypatch: MonkeyPatch) -> None:
    """每轮工具执行后都应调用检查点，且消息列表单调增长。"""

    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c1")]),
        _response(tool_calls=[_tool_call('{"file_path": "b.py"}', call_id="c2")]),
        _response(content="done"),
    ]
    snapshots: list[tuple[str, int]] = []

    def callback(name: str, messages: list[dict[str, Any]]) -> None:
        assert isinstance(messages, list)
        snapshots.append((name, len(messages)))

    agent, _ = _build_agent(
        monkeypatch, outcomes, checkpoint_callback=callback
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: "data")

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    assert snapshots == [("coder", 4), ("coder", 6)]


def test_checkpoint_callback_exception_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """检查点回调抛异常不应打断主循环。"""

    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path": "a.py"}', call_id="c1")]),
        _response(content="done"),
    ]

    def callback(name: str, messages: list[dict[str, Any]]) -> None:
        raise RuntimeError("checkpoint broken")

    agent, _ = _build_agent(
        monkeypatch, outcomes, checkpoint_callback=callback
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: "data")

    events = list(agent.run_stream("q"))

    assert events[-1].type == "final"
    assert events[-1].message == "done"


def test_initial_messages_injected(monkeypatch: MonkeyPatch) -> None:
    """注入 initial_messages 后，首条 LLM 请求应收到它们。"""

    initial = [
        {"role": "system", "content": "custom system"},
        {"role": "user", "content": "custom question"},
    ]
    outcomes = [_response(content="done")]
    agent, client = _build_agent(monkeypatch, outcomes, initial_messages=initial)

    events = list(agent.run_stream("ignored question"))

    assert events[-1].type == "final"
    first_messages = client.chat.completions.requests[0]["messages"]
    assert first_messages[0] == initial[0]
    assert first_messages[1] == initial[1]


def test_restored_context_consumed_once_and_new_feedback_used(monkeypatch):
    """! @brief 恢复后下一轮修复必须使用新问题，不能再次套用旧快照。"""
    initial = [{"role": "system", "content": "sys"}, {"role": "user", "content": "old"}]
    agent, client = _build_agent(monkeypatch, [_response(content="ok"), _response(content="ok")], initial_messages=initial)
    list(agent.run_stream("resume"))
    assert client.chat.completions.requests[0]["messages"][2]["content"] == "resume"
    list(agent.run_stream("new failure feedback"))
    messages = client.chat.completions.requests[1]["messages"]
    assert messages[1]["content"] == "new failure feedback"
    assert initial == [{"role": "system", "content": "sys"}, {"role": "user", "content": "old"}]


def test_deepseek_thinking_tool_turn_preserves_reasoning(monkeypatch: MonkeyPatch) -> None:
    """! @brief DeepSeek 工具回合需回传 reasoning_content，且不向事件公开。"""
    monkeypatch.setattr(base_tool_agent.settings, "llm_model", "deepseek-v4-pro")
    monkeypatch.setattr(base_tool_agent.settings, "llm_reasoning_effort", "max")
    outcomes = [
        _response(tool_calls=[_tool_call('{"file_path":"a.py"}', call_id="c1")], reasoning_content="private reasoning"),
        _response(content="done", reasoning_content="private final"),
    ]
    agent, client = _build_agent(monkeypatch, outcomes)
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: "data")
    events = list(agent.run_stream("q"))
    requests = client.chat.completions.requests
    assert requests[0]["reasoning_effort"] == "max"
    assert requests[0]["extra_body"]["thinking"]["type"] == "enabled"
    assert requests[1]["messages"][2]["reasoning_content"] == "private reasoning"
    assert "private reasoning" not in repr(events)
