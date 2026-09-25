from pathlib import Path


# 遍历时跳过这些目录：要么是版本控制/依赖/缓存，要么是 IDE 配置，
# 它们体积大、内容与业务无关，读进来只会浪费上下文。
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
}


def resolve_safe_path(base_path: Path, relative_path: str = ".") -> Path:
    """安全地解析相对路径，防止路径遍历攻击（../../ 逃出仓库根目录）。

    Args:
        base_path: 基础目录的 Path 对象（通常是仓库根目录）。
        relative_path: 用户/LLM 提供的相对路径。

    Returns:
        解析后的绝对路径（保证在 base_path 之内）。

    Raises:
        ValueError: 解析后的路径不在基础目录下（检测到路径遍历）。
    """
    # 将基础目录和相对路径都解析成绝对路径（resolve 会规范化 .. 和符号链接）
    base_path = base_path.resolve()
    resolved_path = (base_path / relative_path).resolve()

    try:
        # 检查解析后的路径是否还在 base_path 之下；
        # relative_to 抛 ValueError 就说明越界了（发生了路径遍历）。
        _ = resolved_path.relative_to(base_path)
    except ValueError:
        raise ValueError("Attempted path traversal detected.")

    return resolved_path


def list_files(repo_path: str, max_files: int = 200) -> list[str]:
    """列出指定目录下的所有文件路径（跳过忽略目录），最多返回 max_files 个。

    Args:
        repo_path: 目标目录路径。
        max_files: 最大返回文件数量。

    Returns:
        文件路径列表（相对 repo_path 的 POSIX 风格路径，如 src/main.py）。

    Raises:
        ValueError: repo_path 不是有效目录时。
    """
    base_path = Path(repo_path).resolve()
    if not base_path.is_dir():
        raise ValueError(f"{repo_path} is not a valid directory.")

    files: list[str] = []
    # rglob("*") 递归遍历目录下所有文件 + 文件夹
    for path in base_path.rglob("*"):
        # 只要路径任意一层目录名命中忽略名单，就跳过整个路径
        if any(part in IGNORED_DIRS for part in path.parts):
            continue

        if not path.is_file():
            continue

        # 转成相对路径，且统一用 POSIX 分隔符（as_posix），跨平台一致
        relative = path.relative_to(base_path)
        files.append(relative.as_posix())

        if len(files) >= max_files:
            break

    return files

def read_files(
    repo_path: str | Path,
    file_path: str,
    max_size: int = 1024 * 1024,    # 磁盘最大字节，默认 1MB
    max_chars: int = 8000,          # 解码后最大字符数
) -> str:
    """读取仓库中指定文件的内容，带安全校验和大小/字符截断。

    file_path 是相对于 repo_path 的相对路径。
    安全措施：路径防遍历、检查是否为普通文件、限制字节大小和字符数。

    Args:
        repo_path: 仓库根目录路径。
        file_path: 相对于仓库根目录的文件路径。
        max_size: 允许读取的最大字节数（超限直接拒绝，不加载进内存）。
        max_chars: 解码后最大字符数（超长则截断并追加提示）。

    Returns:
        文件文本内容。

    Raises:
        ValueError: 目标不是文件、或超过字节上限时。
        RuntimeError: 读取过程中发生 OSError 时。
    """
    base_path = Path(repo_path).resolve()
    safe_file_path = resolve_safe_path(base_path, file_path)

    # is_file() 已隐含「文件存在且是普通文件」两层含义
    if not safe_file_path.is_file():
        raise ValueError(f"{file_path} is not a valid file.")

    # 读之前先看磁盘字节大小，超大直接拒绝，避免把巨文件整个加载进内存
    file_stat = safe_file_path.stat()
    if file_stat.st_size > max_size:
        raise ValueError(
            f"{file_path} exceeds the maximum allowed size of {max_size} bytes."
        )

    try:
        # errors="ignore"：遇到非法 utf-8 字节直接跳过，避免读二进制文件崩溃
        content = safe_file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise RuntimeError(f"读取文件失败: {exc}") from exc

    # 按字符截断，超长时追加提示文本，防止撑爆 LLM 上下文
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n...[文件内容过长，已截断]"

    return content


def search_code(repo_path: str, keyword: str, max_results: int = 50) -> list[dict[str, object]]:
    """在指定目录下搜索包含关键字的代码文件，返回匹配文件及命中行。

    Args:
        repo_path: 目标目录路径。
        keyword: 搜索关键字。
        max_results: 最大返回结果数量。

    Returns:
        list[dict]: 每个元素是 {"file_path": 相对路径, "matching_lines": [命中行, ...]}。

    Raises:
        ValueError: repo_path 不是有效目录时。
    """
    base_path = Path(repo_path).resolve()
    if not base_path.is_dir():
        raise ValueError(f"{repo_path} is not a valid directory.")

    results: list[dict[str, object]] = []
    # 只在这些常见代码/配置扩展名里搜，避免读二进制文件浪费时间
    allowed_extensions = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".cpp",
        ".c",
        ".h",
        ".go",
        ".rs",
        ".md",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
    }
    for path in base_path.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue

        if not path.is_file():
            continue
        # suffix 是带点的后缀名（如 ".py"），lower 统一小写比对
        if path.suffix.lower() not in allowed_extensions:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue  # 读不了的文件直接跳过

        # 逐行找出包含 keyword 的行
        matching_lines = [
            line for line in content.splitlines() if keyword in line
        ]

        if matching_lines:
            results.append({
                "file_path": str(path.relative_to(base_path)),
                "matching_lines": matching_lines,
            })

        if len(results) >= max_results:
            break

    return results
