"""! @brief Benchmark 数据集加载器。"""

import json
from hashlib import sha256
from pathlib import Path

from .models import BenchmarkCase

# 本文件位于 backend/src/evals，向上两级正好是 backend。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmarks"
CASE_DIR = BENCHMARK_ROOT / "cases"
REPO_DIR = BENCHMARK_ROOT / "repos"


def load_case(case_id: str) -> BenchmarkCase:
    """! @brief 按 ID 加载并校验一条 Benchmark。

    @param case_id 不含 .json 后缀的用例 ID。
    @return 经过 Pydantic 校验的 BenchmarkCase。
    @throws FileNotFoundError 用例文件或对应 fixture 仓库不存在。
    """

    case_path = CASE_DIR / f"{case_id}.json"
    if not case_path.is_file():
        raise FileNotFoundError(f"Benchmark 用例不存在：{case_path}")

    raw = json.loads(case_path.read_text(encoding="utf-8"))
    case = BenchmarkCase.model_validate(raw)
    fixture_path = REPO_DIR / case.repo_fixture
    if not fixture_path.is_dir():
        raise FileNotFoundError(f"Benchmark 仓库不存在：{fixture_path}")
    return case


def load_cases() -> list[BenchmarkCase]:
    """! @brief 加载 cases 目录中的全部用例，并按 ID 排序。"""

    return [load_case(path.stem) for path in sorted(CASE_DIR.glob("*.json"))]


def load_benchmark_cases() -> list[BenchmarkCase]:
    """! @brief 兼容实验教程命名，加载全部 Benchmark 用例。

    `load_cases` 是项目原有的简洁接口；保留该函数作为语义更明确的别名，
    让正式实验代码和交互式分析代码都不需要复制目录扫描逻辑。
    """

    return load_cases()


def get_fixture_path(case: BenchmarkCase) -> Path:
    """! @brief 返回用例对应的只读基准仓库路径。"""

    return REPO_DIR / case.repo_fixture


def calculate_dataset_fingerprint() -> str:
    """! @brief 计算 case 定义和 fixture 源码的稳定 SHA-256 指纹。

    指纹写入实验配置后，可以证明两次运行使用的是同一版数据集。生成文件、
    缓存和嵌套 Git 元数据不会参与计算，避免本地执行测试改变指纹。

    @return 64 位十六进制 SHA-256 字符串。
    """

    digest = sha256()
    paths = [*CASE_DIR.glob("*.json"), *REPO_DIR.rglob("*")]
    for path in sorted(item for item in paths if item.is_file()):
        relative = path.relative_to(BENCHMARK_ROOT)
        if relative == Path("audit_report.json") or any(
            part in {"__pycache__", ".pytest_cache", ".git", ".git-local-backup"}
            for part in relative.parts
        ) or path.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
