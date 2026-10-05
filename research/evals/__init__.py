"""! @brief DevPilot 离线评测模块。

该包负责加载固定 Benchmark、创建隔离工作区、运行四种 Agent 变体，
并把结果持久化到 SQLite，避免把“感觉更好”当成工程结论。
"""

from .models import BenchmarkCase, EvaluationResult, EvaluationVariant

__all__ = ["BenchmarkCase", "EvaluationResult", "EvaluationVariant"]
