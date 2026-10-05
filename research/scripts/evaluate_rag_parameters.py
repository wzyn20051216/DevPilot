"""! @brief 使用本地真实 embedding 比较 RAG 参数，分离选参与验证查询。"""

import argparse
import csv
import hashlib
import itertools
import json
import os
import platform
import random
import shutil
import time
from pathlib import Path
from statistics import mean
from unittest.mock import patch

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.src.rag import code_index
from backend.src.rag.chunker import calculate_source_fingerprint, iter_indexable_files
from backend.src.rag.embedder import MODEL_NAME, embed_query, embed_texts
from research.evals.retrieval import score_retrieval

ROOT = Path(__file__).resolve().parents[2]
CHUNKING = [
    {"name": "ast80", "python_strategy": "ast", "chunk_lines": 80, "overlap": 15},
    {"name": "window40", "python_strategy": "window", "chunk_lines": 40, "overlap": 15},
    {"name": "window80", "python_strategy": "window", "chunk_lines": 80, "overlap": 15},
    {"name": "window160", "python_strategy": "window", "chunk_lines": 160, "overlap": 15},
    {"name": "window80_no_overlap", "python_strategy": "window", "chunk_lines": 80, "overlap": 0},
    {"name": "window80_overlap30", "python_strategy": "window", "chunk_lines": 80, "overlap": 30},
]


def write_json(path, value):
    """! @brief 原子保存阶段结果，中断时也保留完整已完成记录。"""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def aggregate(rows):
    """! @brief 文件级宏平均；Precision 分母为实际返回数。"""
    return {"queries": len(rows), **{key: mean(r[key] for r in rows)
            for key in ("recall", "reciprocal_rank", "precision", "context_chars")}}


def main():
    """! @brief 跑完整网格，选参仅使用 development，延迟另做真实查询测量。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / "backend/src")
    parser.add_argument("--labels", type=Path, default=ROOT / "research/benchmarks/retrieval_engineering.json")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    labels_path = args.labels.resolve()
    labels = json.loads(labels_path.read_text(encoding="utf-8"))["queries"]
    corpus = args.output / "corpus"
    corpus.mkdir()
    source = args.corpus.resolve()
    for path in iter_indexable_files(source):
        target = corpus / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for query in labels:
        assert all((corpus / name).is_file() for name in query["relevant_files"])
    report = {"status": "building", "dataset_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
              "corpus_sha256": calculate_source_fingerprint(corpus),
              "source_files": len(list(iter_indexable_files(corpus))),
              "model": MODEL_NAME, "model_revision_not_pinned": True,
              "platform": platform.platform(), "llm_calls": 0,
              "selection_rule": "development 的 Recall@5、MRR@5 降序，再按上下文长度升序；validation 不参与选参",
              "timing_note": "网格缓存真实查询向量和每路前40候选，重复调用产品融合逻辑；独立 latency 测量不使用这些缓存，包含查询编码与 BM25 构建，不含索引构建",
              "builds": [], "configs": []}
    write_json(args.output / "report.json", report)
    started = time.perf_counter()
    vectors = embed_texts([q["query"] for q in labels])
    report["model_initialization_and_batch_query_encoding_seconds"] = time.perf_counter() - started
    cached_vectors = {q["query"]: vector for q, vector in zip(labels, vectors, strict=True)}
    indexes = {}
    grid_rows = []
    settings = list(itertools.product([1, 3, 5, 10], [10, 20, 40], [10, 60], [0.0, 0.25, 0.5, 0.75, 1.0]))
    # 固定随机次序降低固定参数顺序与机器热状态的耦合，不改变检索排名。
    random.Random(20261005).shuffle(settings)
    for chunking in CHUNKING:
        index = code_index.CodeIndex(str(corpus))
        options = {k: v for k, v in chunking.items() if k != "name"}
        started = time.perf_counter()
        stats = index.build(**options)
        report["builds"].append({**chunking, **stats, "seconds": time.perf_counter() - started})
        indexes[chunking["name"]] = index
        with patch.object(code_index, "embed_query", lambda query: cached_vectors[query]):
            # 最大候选排名的前缀等价于较小 candidate_k；网格只需重算融合。
            vector_ranks = {q["query"]: index.vector_search(q["query"], top_k=40) for q in labels}
            bm25_ranks = {q["query"]: index.bm25_search(q["query"], top_k=40) for q in labels}
        with (patch.object(index, "vector_search", lambda query, top_k: vector_ranks[query][:top_k]),
              patch.object(index, "bm25_search", lambda query, top_k: bm25_ranks[query][:top_k])):
            for top_k, candidate_k, rrf_k, weight in settings:
                config = {"chunking": chunking["name"], "top_k": top_k,
                          "candidate_k": candidate_k, "rrf_k": rrf_k, "vector_weight": weight}
                cfg_rows = []
                for query in labels:
                    results = index.hybrid_search(query["query"], top_k=top_k,
                                                  candidate_k=candidate_k, rrf_k=rrf_k, vector_weight=weight)
                    files = [r["file_path"] for r in results]
                    recall, rr = score_retrieval(files, query["relevant_files"])
                    hits = len(set(files).intersection(query["relevant_files"]))
                    row = {**config, "query_id": query["id"], "split": query["split"],
                           "recall": recall, "reciprocal_rank": rr,
                           "precision": hits / len(files) if files else 0,
                           "context_chars": sum(len(r["content"]) for r in results),
                           "returned_files": ";".join(files)}
                    cfg_rows.append(row)
                    grid_rows.append(row)
                report["configs"].append({**config, **{split: aggregate([r for r in cfg_rows if r["split"] == split])
                                                     for split in ("development", "validation")}})
        write_json(args.output / "report.json", report)
        print(f"RAG {chunking['name']}: {stats['chunks']} chunks; grid complete", flush=True)
    eligible = [c for c in report["configs"] if c["top_k"] == 5]
    selected = sorted(eligible, key=lambda c: (-c["development"]["recall"],
                                             -c["development"]["reciprocal_rank"],
                                             c["development"]["context_chars"],
                                             c["chunking"], c["candidate_k"], c["rrf_k"], c["vector_weight"]))[0]
    report["selected"] = selected
    default = next(c for c in eligible if c["chunking"] == "ast80" and c["candidate_k"] == 20
                   and c["rrf_k"] == 60 and c["vector_weight"] == 0.5)
    report["default"] = default
    latency = []
    for name, config in (("default", default), ("selected", selected)):
        index = indexes[config["chunking"]]
        opts = {k: config[k] for k in ("top_k", "candidate_k", "rrf_k", "vector_weight")}
        index.hybrid_search(labels[0]["query"], **opts)  # 预热，不计测量。
        for repeat in range(3):
            queries = list(labels)
            random.Random(101 + repeat).shuffle(queries)
            for query in queries:
                started = time.perf_counter()
                results = index.hybrid_search(query["query"], **opts)
                seconds = time.perf_counter() - started
                recall, rr = score_retrieval([r["file_path"] for r in results], query["relevant_files"])
                latency.append({"config": name, "repeat": repeat + 1, "query_id": query["id"],
                                "seconds": seconds, "recall": recall, "reciprocal_rank": rr})
    report["latency"] = latency
    report["status"] = "completed"
    report["corpus_unchanged"] = calculate_source_fingerprint(corpus) == report["corpus_sha256"]
    write_json(args.output / "report.json", report)
    with (args.output / "grid.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(grid_rows[0]))
        writer.writeheader()
        writer.writerows(grid_rows)
    print(json.dumps({"status": "completed", "configs": len(report["configs"]),
                      "selected": selected, "default": default}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
