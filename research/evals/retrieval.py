"""! @brief Hybrid Code RAG 的文件级检索评测。"""

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

from backend.src.rag.code_index import CodeIndex
from .dataset import PROJECT_ROOT, get_fixture_path, load_benchmark_cases

RETRIEVAL_EVAL_ROOT = PROJECT_ROOT / "artifacts" / "retrieval_evals"


def score_retrieval(
    returned_files: list[str],
    relevant_files: list[str],
) -> tuple[float, float]:
    """! @brief 计算文件级 Recall@K 和 reciprocal rank。"""

    relevant = set(relevant_files)
    unique_returned = list(dict.fromkeys(returned_files))
    recall = len(relevant.intersection(unique_returned)) / len(relevant)
    first_rank = next(
        (rank for rank, path in enumerate(unique_returned, start=1) if path in relevant),
        None,
    )
    return recall, 0.0 if first_rank is None else 1.0 / first_rank


def run_retrieval_evaluation(top_k: int = 5, run_id: str | None = None) -> Path:
    """! @brief 在全部带标注用例上测量 Recall@K、MRR 和端到端延迟。"""

    if top_k <= 0:
        raise ValueError("top_k 必须为正整数")
    actual_run_id = run_id or uuid4().hex
    run_dir = RETRIEVAL_EVAL_ROOT / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    build_seconds: list[float] = []

    for case in load_benchmark_cases():
        if not case.retrieval_queries:
            continue
        workspace = run_dir / "workspaces" / case.id
        shutil.copytree(
            get_fixture_path(case),
            workspace,
            ignore=shutil.ignore_patterns(".git*", ".devpilot", "__pycache__", ".pytest_cache"),
        )
        index = CodeIndex(str(workspace))
        started = perf_counter()
        stats = index.build()
        build_elapsed = perf_counter() - started
        build_seconds.append(build_elapsed)

        for item in case.retrieval_queries:
            started = perf_counter()
            results = index.hybrid_search(item.query, top_k=top_k)
            latency = perf_counter() - started
            files = [str(result["file_path"]) for result in results]
            recall, reciprocal_rank = score_retrieval(files, item.relevant_files)
            rows.append(
                {
                    "case_id": case.id,
                    "query": item.query,
                    "relevant_files": item.relevant_files,
                    "returned_files": files,
                    "recall_at_k": recall,
                    "reciprocal_rank": reciprocal_rank,
                    "latency_seconds": round(latency, 6),
                    "index_chunks": stats["chunks"],
                    "build_seconds": round(build_elapsed, 6),
                }
            )

    report = {
        "run_id": actual_run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "top_k": top_k,
        "queries": len(rows),
        "recall_at_k": mean(float(row["recall_at_k"]) for row in rows) if rows else 0.0,
        "mrr": mean(float(row["reciprocal_rank"]) for row in rows) if rows else 0.0,
        "avg_query_seconds": (
            mean(float(row["latency_seconds"]) for row in rows) if rows else 0.0
        ),
        "avg_build_seconds": mean(build_seconds) if build_seconds else 0.0,
        "rows": rows,
    }
    output = run_dir / "report.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def main() -> None:
    """! @brief 命令行入口。"""

    parser = argparse.ArgumentParser(description="评测 DevPilot Hybrid Code RAG")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    output = run_retrieval_evaluation(top_k=args.top_k)
    print(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
