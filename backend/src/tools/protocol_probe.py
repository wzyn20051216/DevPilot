"""! @brief 运行时协议探针工具。

让模型在修复迭代器、异常边界、兼容行为等"隐含协议"问题前，先把候选复现代码
放进隔离沙箱观察真实运行时行为。用机器可判的 stdout 标记代替"猜协议"，
避免模型对隐式兼容协议推断不稳导致的反复返工。
"""
from typing import Any

from ..sandbox.docker_runner import run_in_sandbox

# 单次调用允许的最大探针数量，防止模型把超大探针集一次性塞进沙箱。
_MAX_PROBES = 8
# 单个探针源码的最大字符数，超出即拒绝，避免撑爆命令行与容器。
_MAX_CODE_CHARS = 4000

# 探针包装模板：把用户源码编译进受控命名空间，用 try/except 捕获一切异常，
# 再打印机器可判的标记。容器 returncode 不一定可靠（编译错误/被信号终止等），
# 所以判定一律以 stdout 标记为准，而不是 returncode。
_WRAPPER_TEMPLATE = (
    "import sys, traceback\n"
    "try:\n"
    '    exec(compile(USER_CODE, "<probe>", "exec"), {"__name__": "__probe__"})\n'
    '    print("__PROBE_OK__")\n'
    "except BaseException as exc:\n"
    '    print("__PROBE_FAIL__:", type(exc).__name__, str(exc))\n'
)


def _wrap_code(code: str) -> str:
    """! @brief 把用户源码注入探针包装模板，产出可直接交给 python -c 的脚本。

    使用 repr 把源码转成字符串字面量，避免任何注入或引号转义问题；
    USER_CODE 占位符只出现在模板源码里，用户代码以字面量形式插入，互不干扰。
    """

    return _WRAPPER_TEMPLATE.replace("USER_CODE", repr(code))


def _run_probe(
    repo_path: str,
    name: str,
    code: str,
    expect_exception: str | None,
) -> dict[str, Any]:
    """! @brief 在沙箱中运行单个探针并解析 stdout 标记。"""

    wrapped = _wrap_code(code)
    outcome = run_in_sandbox(
        repo_path=repo_path,
        argv=["python", "-c", wrapped],
        timeout=30,
    )
    stdout = str(outcome.get("stdout", ""))
    exception = ""
    ok_marker = "__PROBE_OK__" in stdout
    fail_marker = "__PROBE_FAIL__:" in stdout

    if fail_marker:
        # 标记之后的内容即「异常类型 + 消息」。
        exception = stdout.split("__PROBE_FAIL__:", 1)[1].strip()

    if expect_exception:
        # 期望抛异常：只有真的抛了且异常文本包含给定子串才算通过。
        ok = fail_marker and str(expect_exception) in exception
    else:
        # 不期望异常：正常结束（出现 OK 标记且没有 FAIL 标记）才算通过。
        ok = ok_marker and not fail_marker

    if outcome.get("timed_out"):
        ok = False
        exception = exception or "timed_out"

    return {
        "name": name,
        "ok": ok,
        "exception": exception,
        "stdout_tail": stdout[-1_000:],
    }


def probe_runtime(
    repo_path: str,
    probes: list[dict[str, Any]],
) -> dict[str, Any]:
    """! @brief 在隔离沙箱中运行一组运行时协议探针。

    @param repo_path 目标代码仓库根目录。
    @param probes 探针列表，每个元素为
        ``{"name": str, "code": str, "expect_exception": str | None}``；
        ``expect_exception`` 可选，表示期望抛出的异常类型或消息子串。
    @return ``{"probes": [...], "all_passed": bool}``；每条结果为
        ``{"name", "ok", "exception", "stdout_tail"}``。
    @raise ValueError 探针数量超过 8 或单个源码超过 4000 字符时。
    """

    if len(probes) > _MAX_PROBES:
        raise ValueError(
            f"probes 数量不能超过 {_MAX_PROBES}，当前 {len(probes)}"
        )

    results: list[dict[str, Any]] = []
    for probe in probes:
        name = str(probe.get("name", "unnamed"))
        code = str(probe.get("code", ""))
        expect_exception = probe.get("expect_exception")
        if len(code) > _MAX_CODE_CHARS:
            raise ValueError(
                f"probe {name!r} 的 code 超过 {_MAX_CODE_CHARS} 字符"
            )
        results.append(
            _run_probe(
                repo_path=repo_path,
                name=name,
                code=code,
                expect_exception=str(expect_exception) if expect_exception else None,
            )
        )

    return {
        "probes": results,
        "all_passed": all(item["ok"] for item in results),
    }
