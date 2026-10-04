###写文件
import ast
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from .file_tool import resolve_safe_path

#保护文件集合
PROTECTED_FILES={
    ".env",
    ".env.local",
    ".env.production",
}

# 真实评测可按运行上下文额外保护测试文件；普通开发任务不启用此限制。
_write_guard: ContextVar[Callable[[str], bool] | None] = ContextVar(
    "devpilot_write_guard", default=None
)


@contextmanager
def use_write_guard(guard: Callable[[str], bool]) -> Iterator[None]:
    """! @brief 在当前运行上下文内禁止写入满足 guard 的相对路径。"""

    token = _write_guard.set(guard)
    try:
        yield
    finally:
        _write_guard.reset(token)


def _check_write_guard(repo_path: str, path: Path) -> None:
    """! @brief 在文件修改前执行当前评测写入边界。"""

    guard = _write_guard.get()
    relative_path = path.relative_to(Path(repo_path).resolve()).as_posix()
    if guard is not None and guard(relative_path):
        raise ValueError(f"真实评测禁止修改测试或测试配置: {relative_path}")


# 编辑语法守卫：参考 SWE-agent 的带 linter 编辑命令，拒绝会引入语法错误的
# Python 编辑，文件保持原样。默认关闭，由调用方按运行上下文显式开启，
# 便于真实评测中与旧行为做干净对照。
_syntax_guard: ContextVar[bool] = ContextVar("devpilot_syntax_guard", default=False)


@contextmanager
def use_syntax_guard(enabled: bool = True) -> Iterator[None]:
    """! @brief 在当前运行上下文内开启或关闭 Python 编辑语法守卫。"""

    token = _syntax_guard.set(enabled)
    try:
        yield
    finally:
        _syntax_guard.reset(token)


def _check_python_syntax(path: Path, old_content: str, content: str) -> None:
    """! @brief 新内容有语法错误、而原文件可解析时拒绝写入。

    原文件本身就无法被当前解释器解析（如旧语法文件）时不拦截，避免误伤。
    """

    if not _syntax_guard.get() or path.suffix != ".py":
        return
    try:
        ast.parse(content)
    except SyntaxError as exc:
        try:
            ast.parse(old_content)
        except SyntaxError:
            return
        lines = content.splitlines()
        line_no = exc.lineno or 0
        context = "\n".join(
            f"{number}: {lines[number - 1]}"
            for number in range(max(1, line_no - 2), min(len(lines), line_no + 2) + 1)
        )
        raise ValueError(
            f"编辑被拒绝：修改后第 {line_no} 行语法错误（{exc.msg}），文件未改动。\n"
            f"{context}\n请修正缩进/括号后重新提交编辑。"
        ) from exc


def replace_in_file(
    repo_path: str,
    file_path: str,
    old_text: str,
    new_text: str,
) -> dict[str, object]:
    """! @brief 用唯一文本锚点对已有文件做局部原子替换。

    @param repo_path 代码仓库根目录。
    @param file_path 相对仓库根目录的文件路径。
    @param old_text 必须在文件中恰好出现一次的原文本。
    @param new_text 替换后的文本。
    @return 修改状态和字符数摘要。
    @raise ValueError 目标受保护、锚点缺失或锚点不唯一时抛出。
    """

    path = resolve_safe_path(Path(repo_path), relative_path=file_path)
    _check_write_guard(repo_path, path)
    if path.name in PROTECTED_FILES or ".git" in path.parts or ".devpilot" in path.parts:
        raise ValueError(f"禁止操作受保护路径: {file_path}")
    if not path.is_file():
        raise FileNotFoundError(f"{file_path} 不是已有文件")
    if not old_text:
        raise ValueError("old_text 不能为空")
    content = path.read_text(encoding="utf-8", errors="strict")
    occurrences = content.count(old_text)
    if occurrences != 1:
        raise ValueError(
            f"old_text 必须恰好匹配一次，当前匹配 {occurrences} 次"
        )
    updated = content.replace(old_text, new_text, 1)
    return write_file(
        repo_path=repo_path,
        file_path=file_path,
        content=updated,
    )

def write_file(
     repo_path:str,
     file_path:str,
     content:str,
     max_chars:int =200_000,
    )-> dict[str,object]:
    """
    安全修改代码仓库中**已存在**的文本文件，使用原子写入避免文件损坏。

    Args:
        repo_path: 代码仓库根目录路径
        file_path: 相对于仓库根目录的目标文件相对路径
        content: 需要写入覆盖的新文件文本内容
        max_chars: 单次写入最大字符上限，防止超大文件写入

    Returns:
        dict: 返回执行结果字典，包含文件路径、是否变更、提示信息等

    Raises:
        ValueError: 修改受保护文件、.git目录、目标不是文件、内容超长时抛出
        FileNotFoundError: 文件不存在时抛出（本函数只允许修改已有文件）
    """
    #解析得到安全的绝对路径，做路径月结防护，防止跳出目录
    path=resolve_safe_path(Path(repo_path),relative_path=file_path)
    _check_write_guard(repo_path, path)
    #禁止修改敏感文件
    if path.name in PROTECTED_FILES:
        raise ValueError(f"禁止操作{path.name}{repo_path}")
    #禁止操作git版本控制目录
    if ".git" in path.parts or ".devpilot" in path.parts:
        raise ValueError(
            "禁止修改 .git 目录"
        )
    #检验目标路径是普通文件 不是文件夹
    if not path.is_file():
        raise ValueError(f"{file_path}不是目标文件")
    #检验写入内容长度 限制最大字节数
    if len(content)>max_chars:
        raise ValueError(f"{file_path}超出字符属于")
    #读取文件原始内容 编码忽略非法字符
    old_content=path.read_text(encoding="utf-8",errors="ignore")
    # 新内容和旧内容完全一致，无需写入，直接返回无变更结果
    if old_content == content:
        return {
            "file_path": file_path,
            "changed": False,
            "message": "文件内容没有变化",
        }
    _check_python_syntax(path, old_content, content)
    # ======================
    # 原子写入逻辑：先写同目录临时文件，全部写完再替换原文件
    # 好处：防止程序中途崩溃，造成原文件截断损坏
    # ======================
    # 在目标文件同级目录创建临时文件，生成文件描述符+临时文件路径
    fd,temp_path=tempfile.mkstemp(dir=str(path.parent),prefix=".devpilot_",suffix=".tmp") #pre是前缀 suf是后缀
    try:
        #通过文件描述符打开临时文件，写入新内容
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as f:
            f.write(content)
        # 原子替换：把临时文件直接覆盖替换真正的目标文件
        os.replace(
            temp_path,
            path,
        )
    except Exception:
        # 发生异常：清理残留临时文件，再向上抛出异常
        if os.path.exists(temp_path):
            os.remove(temp_path)#需要手动删除
        raise

    # 写入成功，返回变更信息
    return {
        "file_path": file_path,
        "changed": True,
        "old_chars": len(old_content),
        "new_chars": len(content),
        "message": "文件修改成功",
    }
