"""! @brief Evals 数据模型。

模型只描述稳定的评测输入和输出，不包含 Agent 执行逻辑，便于后续把同一份
结果用于命令行报告、API 查询或论文实验统计。
"""

from typing import Literal

from pydantic import BaseModel, Field

from backend.src.models.evaluation import EvaluationResult, EvaluationVariant

BenchmarkDifficulty = Literal["easy", "medium", "hard"]
BenchmarkCategory = Literal[
    "bugfix",
    "feature",
    "refactor",
    "validation",
    "configuration",
    "cross_module",
]
BenchmarkLanguage = Literal["python", "typescript", "javascript", "java"]


class RetrievalQuery(BaseModel):
    """! @brief 一条带文件级相关性标注的代码检索查询。"""

    query: str = Field(min_length=1)
    relevant_files: list[str] = Field(min_length=1)


class BenchmarkCase(BaseModel):
    """! @brief 一条可重复执行的基准测试用例。"""

    id: str = Field(min_length=1, description="用例唯一标识")
    title: str = Field(min_length=1, description="用例标题")
    description: str = Field(default="", description="用例背景说明")
    difficulty: BenchmarkDifficulty = Field(description="用例难度等级")
    category: BenchmarkCategory = Field(description="用例任务类别")
    tags: list[str] = Field(
        default_factory=list,
        description="用于更细粒度实验切片的标签",
    )
    repo_fixture: str = Field(min_length=1, description="benchmarks/repos 下的仓库目录名")
    task: str = Field(min_length=1, description="发送给 Agent 的开发任务")
    language: BenchmarkLanguage = Field(
        default="python",
        description="用例实现语言，决定独立验证走 pytest 还是 polyglot 命令",
    )
    verification_command: list[str] | None = Field(
        default=None,
        description=(
            "独立验证的完整 argv；None 时按 python 走 pytest 现有路径，"
            "非 None 时在 polyglot 沙箱中直接执行该命令"
        ),
    )
    verification_target: str | None = Field(
        default=None,
        description="独立验证时传给 pytest 的目标路径",
    )
    timeout_seconds: int = Field(default=120, gt=0, description="独立验证超时秒数")
    retrieval_queries: list[RetrievalQuery] = Field(
        default_factory=list,
        description="用于 Recall@K/MRR 的代码检索标注",
    )
