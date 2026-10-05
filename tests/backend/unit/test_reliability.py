"""外部服务、Agent 循环和 Sandbox 的故障注入测试。"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from pytest import MonkeyPatch

from backend.src import llm_client
from backend.src.agents import base_tool_agent
from backend.src.agents.base_tool_agent import BaseToolAgent
from backend.src.agents.orchestrator import DevPilotOrchestrator
from backend.src.exceptions import ExternalServiceError
from backend.src.mcp_clients import repository_client
from backend.src.models.agent_state import AgentEvent
from backend.src.sandbox import docker_runner
from backend.src.tools import test_tool
from backend.src.tools import command_tool


def _response(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
) -> SimpleNamespace:
    """构造 BaseToolAgent 需要的最小 OpenAI 兼容响应。"""

    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


def _tool_call(
    arguments: str = "{}",
    name: str = "read_file",
) -> SimpleNamespace:
    """构造一个 function tool call。"""

    return SimpleNamespace(
        id="call-1",
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


def test_openai_client_uses_configured_limits(monkeypatch: MonkeyPatch) -> None:
    """客户端构造必须真正传入超时和重试设置。"""

    captured: dict[str, Any] = {}

    def fake_openai(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm_client, "OpenAI", fake_openai)
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_client.settings, "llm_model", "test-model")
    monkeypatch.setattr(llm_client.settings, "llm_timeout_seconds", 12.5)
    monkeypatch.setattr(llm_client.settings, "llm_max_retries", 4)

    _ = llm_client.create_client()

    assert captured["timeout"] == 12.5
    assert captured["max_retries"] == 4


def _build_agent(monkeypatch: MonkeyPatch, outcomes: list[Any], max_iterations: int = 2) -> BaseToolAgent:
    """创建不联网、不启动 MCP 的基础 Agent。"""

    client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(outcomes)))
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: [])
    return BaseToolAgent(
        repo_path=".",
        name="coder",
        system_prompt="test",
        allowed_tools={"read_file"},
        max_iterations=max_iterations,
    )


def test_agent_turns_llm_failure_into_error_event(monkeypatch: MonkeyPatch) -> None:
    """LLM 异常应结束为可观测 error 事件，而不是逃出生成器。"""

    agent = _build_agent(monkeypatch, [TimeoutError("model timeout")])
    events = list(agent.run_stream("question"))

    assert [event.type for event in events] == ["start", "thinking", "error"]
    assert events[-1].message == "model timeout"


def test_agent_can_recover_after_tool_failure(monkeypatch: MonkeyPatch) -> None:
    """工具异常应作为 observation 返回模型，使下一轮仍可正常完成。"""

    agent = _build_agent(
        monkeypatch,
        [_response(tool_calls=[_tool_call("not-json")]), _response(content="recovered")],
    )

    def fail_tool(**_: Any) -> Any:
        raise OSError("tool unavailable")

    monkeypatch.setattr(base_tool_agent, "execute_tool", fail_tool)
    events = list(agent.run_stream("question"))

    assert events[-1].type == "final"
    assert events[-1].message == "recovered"
    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_result.data["arguments"] == {}
    assert "tool unavailable" in str(tool_result.data["result_preview"])


def test_agent_forces_final_answer_after_tool_iteration_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    """工具轮数耗尽后应禁用工具并请求一次最终回答。"""

    agent = _build_agent(
        monkeypatch,
        [
            _response(tool_calls=[_tool_call()]),
            _response(content="finalized"),
        ],
        max_iterations=1,
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: {"ok": True})
    events = list(agent.run_stream("question"))

    assert events[-1].type == "final"
    assert events[-1].message == "finalized"
    completions = cast(Any, agent.client.chat.completions)
    assert completions.requests[-1]["tool_choice"] == "none"
    assert events[-1].data["usage"]["total_tokens"] == 10


def test_context_compaction_keeps_complete_recent_tool_exchange(
    monkeypatch: MonkeyPatch,
) -> None:
    """旧回合可压缩，但最近 assistant/tool 配对必须保持完整。"""

    monkeypatch.setattr(base_tool_agent.settings, "agent_recent_messages", 4)
    monkeypatch.setattr(
        base_tool_agent.settings,
        "agent_history_summary_max_chars",
        1_000,
    )
    messages: list[Any] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "old",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "old", "content": "old result"},
        {"role": "user", "content": "continue"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "recent",
                    "type": "function",
                    "function": {"name": "run_test", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "recent", "content": "1 passed"},
        {"role": "user", "content": "summarize"},
    ]
    state = base_tool_agent.AgentState(repo_path=".", question="fix")

    compacted, changed = BaseToolAgent._compact_history(messages, state)

    assert changed is True
    assert len(str(compacted[2]["content"])) <= 1_000
    assert compacted[0:2] == messages[0:2]
    assert "read_file" in str(compacted[2]["content"])
    recent_assistant = next(
        message
        for message in compacted
        if message.get("role") == "assistant"
    )
    assert recent_assistant["tool_calls"][0]["id"] == "recent"
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "recent"
        for message in compacted
    )

    state.modified_files = ["src/" + "x" * 2_000 + ".py"]
    compacted_with_long_fact, changed = BaseToolAgent._compact_history(messages, state)
    assert changed is True
    assert len(str(compacted_with_long_fact[2]["content"])) == 1_000


def test_agent_forces_write_tool_after_edit_deadline(
    monkeypatch: MonkeyPatch,
) -> None:
    """定位超时且尚无改动时，下一轮必须收敛到写入工具。"""

    replace_definition = {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "replace",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    monkeypatch.setattr(
        base_tool_agent,
        "get_tool_definitions",
        lambda **_: [replace_definition],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_FakeCompletions(
                [
                    _response(tool_calls=[_tool_call(name="replace_in_file")]),
                    _response(content="done"),
                ]
            )
        )
    )
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)
    monkeypatch.setattr(
        base_tool_agent,
        "execute_tool",
        lambda **_: {"changed": True, "file_path": "src/a.py"},
    )
    agent = BaseToolAgent(
        repo_path=".",
        name="coder",
        system_prompt="test",
        allowed_tools={"replace_in_file"},
        max_iterations=2,
        edit_deadline=1,
    )

    events = list(agent.run_stream("fix"))

    assert events[-1].type == "final"
    assert client.chat.completions.requests[0]["tool_choice"] == "auto"
    assert [
        item["function"]["name"]
        for item in client.chat.completions.requests[0]["tools"]
    ] == ["replace_in_file"]
    assert client.chat.completions.requests[1]["tool_choice"] == "auto"
    assert events[-1].data["tool_calls"][0]["succeeded"] is True


def test_agent_rejects_text_only_completion_before_required_edit(
    monkeypatch: MonkeyPatch,
) -> None:
    """到编辑阶段后，纯文字建议不能被当作任务完成。"""

    definition = {
        "type": "function",
        "function": {"name": "replace_in_file", "parameters": {"type": "object"}},
    }
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: [definition])
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_FakeCompletions(
                [
                    _response(content="建议修改"),
                    _response(tool_calls=[_tool_call(name="replace_in_file")]),
                    _response(content="done"),
                ]
            )
        )
    )
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)
    monkeypatch.setattr(
        base_tool_agent,
        "execute_tool",
        lambda **_: {"changed": True, "file_path": "src/a.py"},
    )
    agent = BaseToolAgent(
        repo_path=".",
        name="coder",
        system_prompt="test",
        allowed_tools={"replace_in_file"},
        max_iterations=3,
        edit_deadline=1,
    )

    events = list(agent.run_stream("fix"))

    assert events[-1].type == "final"
    assert events[-1].message.startswith("机器验证：修改后未执行 run_test")
    assert events[-1].message.endswith("done")
    assert len([event for event in events if event.type == "tool_call"]) == 1


def test_run_test_event_exposes_structured_report(
    monkeypatch: MonkeyPatch,
) -> None:
    """run_test 事件应携带可供 Orchestrator 兜底的测试事实。"""

    agent = _build_agent(
        monkeypatch,
        [
            _response(tool_calls=[_tool_call(name="run_test")]),
            _response(content='{"passed": true}'),
        ],
    )
    agent.allowed_tools = {"run_test"}
    monkeypatch.setattr(
        base_tool_agent,
        "execute_tool",
        lambda **_: {
            "passed": True,
            "returncode": 0,
            "stdout": "x" * 5_000,
            "stderr": "",
            "timed_out": False,
        },
    )

    events = list(agent.run_stream("test"))
    tool_result = next(event for event in events if event.type == "tool_result")

    assert tool_result.data["test_report"]["passed"] is True
    assert tool_result.data["test_report"]["stdout"] == "x" * 4_000


def test_agent_stops_tools_after_modified_code_passes_full_suite(
    monkeypatch: MonkeyPatch,
) -> None:
    """源码已修改且全量测试通过后，应直接收尾，避免继续消耗工具轮次。"""

    definitions = [
        {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
        for name in ("replace_in_file", "run_test")
    ]
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: definitions)
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_FakeCompletions(
                [
                    _response(tool_calls=[_tool_call(name="replace_in_file")]),
                    _response(tool_calls=[_tool_call(name="run_test")]),
                    _response(content="all done"),
                ]
            )
        )
    )
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)

    def fake_execute(tool_name: str, **_: Any) -> dict[str, Any]:
        if tool_name == "replace_in_file":
            return {"changed": True, "file_path": "src/a.py"}
        return {
            "passed": True,
            "returncode": 0,
            "stdout": "1 passed",
            "stderr": "",
            "timed_out": False,
        }

    monkeypatch.setattr(base_tool_agent, "execute_tool", fake_execute)
    agent = BaseToolAgent(
        repo_path=".",
        name="coder",
        system_prompt="test",
        allowed_tools={"replace_in_file", "run_test"},
        max_iterations=5,
    )

    events = list(agent.run_stream("fix"))

    assert events[-1].message.startswith("机器验证：修改后 run_test 完整测试套件通过")
    assert events[-1].message.endswith("all done")
    assert client.chat.completions.requests[-1]["tool_choice"] == "none"
    assert len(client.chat.completions.requests) == 3


def test_tester_falls_back_to_run_test_result() -> None:
    """最终 JSON 损坏时，Orchestrator 应使用 run_test 的真实结果。"""

    class FakeTester:
        def run_stream(self, _: str) -> Any:
            yield AgentEvent(
                type="tool_result",
                agent="tester",
                message="run_test 执行完成",
                data={
                    "tool": "run_test",
                    "test_report": {
                        "passed": True,
                        "summary": "pytest returncode=0",
                        "stdout": "1 passed",
                        "stderr": "",
                    },
                },
            )
            yield AgentEvent(
                type="final",
                agent="tester",
                message="{not valid json",
            )

    orchestrator = cast(
        DevPilotOrchestrator,
        cast(object, SimpleNamespace(tester=FakeTester())),
    )
    _, report = DevPilotOrchestrator._run_tester(orchestrator, "test")

    assert report is not None
    assert report.passed is True
    assert report.stdout == "1 passed"


def test_tester_machine_result_overrides_conflicting_llm_claim() -> None:
    """LLM 的合法 JSON 也不能把真实测试失败改写成通过。"""

    class ConflictingTester:
        def run_stream(self, question: str):
            yield AgentEvent(
                type="tool_result",
                agent="tester",
                data={
                    "test_report": {
                        "passed": False,
                        "summary": "pytest returncode=1",
                        "stdout": "1 failed",
                        "stderr": "",
                    }
                },
            )
            yield AgentEvent(
                type="final",
                agent="tester",
                message='{"passed": true, "summary": "全部通过"}',
            )

    orchestrator = object.__new__(DevPilotOrchestrator)
    orchestrator.tester = ConflictingTester()

    _, report = orchestrator._run_tester("验证修改")

    assert report is not None
    assert report.passed is False
    assert report.stdout == "1 failed"


def test_tester_without_run_test_cannot_claim_success() -> None:
    """没有机器测试结果时，模型自报成功应降级为未通过。"""

    class ClaimOnlyTester:
        def run_stream(self, question: str):
            yield AgentEvent(
                type="final",
                agent="tester",
                message='{"passed": true, "summary": "看起来没问题"}',
            )

    orchestrator = object.__new__(DevPilotOrchestrator)
    orchestrator.tester = ClaimOnlyTester()

    _, report = orchestrator._run_tester("验证修改")

    assert report is not None
    assert report.passed is False
    assert "未调用 run_test" in report.summary


def test_sandbox_timeout_is_returned_as_structured_result(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Docker 超时应返回 timed_out，供 Agent 决定是否修复或终止。"""

    monkeypatch.setattr(docker_runner.shutil, "which", lambda _: "docker")

    def timeout(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(docker_runner.subprocess, "run", timeout)
    result = docker_runner.run_in_sandbox(
        repo_path=str(tmp_path),
        argv=["pytest", "-q"],
        timeout=1,
    )

    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert "超过 1 秒" in result["stderr"]


@pytest.mark.parametrize("cleanup_returncode", [0, 1])
def test_sandbox_timeout_cleans_only_its_named_container(tmp_path, monkeypatch, cleanup_returncode):
    """! @brief 超时应删除本次容器；清理失败不得报告已终止。"""
    commands = []
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _: "docker")

    def execute(command, **kwargs):
        commands.append(command)
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, cleanup_returncode, "", "")

    monkeypatch.setattr(docker_runner.subprocess, "run", execute)
    result = docker_runner.run_in_sandbox(str(tmp_path), ["python", "-c", "pass"], timeout=1)
    container_name = commands[0][commands[0].index("--name") + 1]
    assert commands[1] == ["docker", "rm", "-f", container_name]
    assert result["container_cleanup_confirmed"] is (cleanup_returncode == 0)
    assert ("清理未确认" in result["stderr"]) is (cleanup_returncode != 0)


def test_trusted_sandbox_profile_is_scoped_and_quotes_argv(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """可信评测配置应只在上下文内切换镜像，并安全传递测试参数。"""

    commands: list[list[str]] = []
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _: "docker")

    def completed(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(docker_runner.subprocess, "run", completed)
    profile = docker_runner.SandboxProfile(
        image="trusted/eval:image",
        command_prefix=("source /activate",),
        mount_target="/testbed",
    )
    with docker_runner.use_sandbox_profile(profile):
        docker_runner.run_in_sandbox(
            repo_path=str(tmp_path),
            argv=["pytest", "tests/a file.py"],
        )
    docker_runner.run_in_sandbox(repo_path=str(tmp_path), argv=["pytest"])

    assert "trusted/eval:image" in commands[0]
    assert commands[0][-3:-1] == ["/bin/bash", "-lc"]
    assert "'tests/a file.py'" in commands[0][-1]
    assert "/testbed" in commands[0]
    assert docker_runner.SANDBOX_IMAGE in commands[1]


def test_run_test_splits_pytest_target_arguments(monkeypatch: MonkeyPatch) -> None:
    """pytest 文件和 -k 表达式应作为独立 argv 传入沙箱。"""

    captured: dict[str, Any] = {}

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}

    monkeypatch.setattr(test_tool, "run_in_sandbox", fake_sandbox)
    result = test_tool.run_tests(".", 'tests/test_a.py -k "alpha or beta"')

    assert captured["argv"][-3:] == ["tests/test_a.py", "-k", "alpha or beta"]
    assert result["passed"] is True


def test_run_test_preserves_exact_target_list(monkeypatch: MonkeyPatch) -> None:
    """内部裁判传入的参数化 pytest 节点不得被 shlex 再次拆分。"""

    captured: dict[str, Any] = {}

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}

    monkeypatch.setattr(test_tool, "run_in_sandbox", fake_sandbox)
    node = "tests/test_a.py::test_value[hello world]"
    test_tool.run_tests(".", targets=[node])

    assert captured["argv"][-1] == node


def test_python_probe_runs_only_inside_sandbox(monkeypatch: MonkeyPatch) -> None:
    """运行时 Python 探针应原样交给隔离沙箱，不在宿主机执行。"""

    captured: dict[str, Any] = {}

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"returncode": 0, "timed_out": False, "stdout": "value", "stderr": ""}

    monkeypatch.setattr(command_tool, "run_in_sandbox", fake_sandbox)
    result = command_tool.run_command(".", ["python", "-c", "print(object())"])

    assert captured["argv"][0] == "python"
    assert result["stdout"] == "value"


@pytest.mark.asyncio
async def test_repository_mcp_timeout_becomes_domain_error(
    monkeypatch: MonkeyPatch,
) -> None:
    """MCP 子进程失去响应时应在配置期限内返回稳定业务异常。"""

    class SlowClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def list_tools(self) -> Any:
            await asyncio.sleep(1)
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(repository_client, "Client", lambda _: SlowClient())
    monkeypatch.setattr(repository_client.settings, "mcp_timeout_seconds", 0.01)

    with pytest.raises(ExternalServiceError, match="工具发现超过"):
        await repository_client.list_repository_tools(".")
