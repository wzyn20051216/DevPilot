"""! @brief 核对原始证据，生成可提交的四项工程评估摘要。"""

import argparse
import csv
import hashlib
import json
import math
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path


def read(path):
    """! @brief 读取 UTF-8 原始报告。"""
    return json.loads(path.read_text(encoding="utf-8"))


def percentile(values, fraction):
    """! @brief 与负载脚本一致的最近秩百分位。"""
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def write_csv(path, rows):
    """! @brief 写入无模型对话、无凭据的结构化指标。"""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    """! @brief 公开摘要必须与完整网格、逐条响应和 JUnit 计数一致。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.run_root
    rag = read(root / "rag_cached/report.json")
    performance = read(root / "system_performance/report.json")
    faults = read(root / "system_final/report.json")
    assert all(r["status"] == "completed" for r in (rag, performance, faults))
    assert len(rag["configs"]) == 720 and len(performance["performance"]) == 21
    assert rag["corpus_unchanged"] and rag["llm_calls"] == performance["llm_paid_calls"] == faults["llm_paid_calls"] == 0
    assert len({tuple(c[k] for k in ("chunking", "top_k", "candidate_k", "rrf_k", "vector_weight")) for c in rag["configs"]}) == 720
    grid = list(csv.DictReader((root / "rag_cached/grid.csv").open(encoding="utf-8", newline="")))
    assert len(grid) == 720 * 32
    configs = []
    for c in rag["configs"]:
        row = {k: c[k] for k in ("chunking", "top_k", "candidate_k", "rrf_k", "vector_weight")}
        for split in ("development", "validation"):
            row.update({f"{split}_{key}": value for key, value in c[split].items()})
        configs.append(row)
    write_csv(args.output / "rag_parameters_2026-10-05.csv", configs)
    queries = []
    for label in ("default", "selected"):
        config = rag[label]
        matching = [r for r in grid if all(str(config[k]) == r[k]
                    for k in ("chunking", "top_k", "candidate_k", "rrf_k", "vector_weight"))]
        assert len(matching) == 32
        for split in ("development", "validation"):
            for metric in ("recall", "reciprocal_rank", "precision", "context_chars"):
                assert math.isclose(statistics.mean(float(r[metric]) for r in matching if r["split"] == split), config[split][metric])
        queries.extend({"configuration": label, **r} for r in matching)
    write_csv(args.output / "rag_queries_2026-10-05.csv", queries)
    latency = {}
    for label in ("default", "selected"):
        rows = [r for r in rag["latency"] if r["config"] == label]
        assert len(rows) == 96
        times = [r["seconds"] * 1000 for r in rows]
        latency[label] = {"samples": len(times), "mean_ms": statistics.mean(times), "p95_ms": percentile(times, .95)}
    phases = []
    aggregates = []
    for r in performance["performance"]:
        assert r["requests"] == len(r["raw_responses"])
        assert r["errors"] == sum(not x["valid"] for x in r["raw_responses"])
        resources = list(r["resources"].values())
        phases.append({k: r.get(k) for k in ("workload", "repeat", "concurrency", "worker_concurrency", "requests", "seconds",
                                           "requests_per_second", "p50_ms", "p95_ms", "p99_ms", "errors")}
                      | {"api_cpu_single_core_pct": resources[0]["cpu_single_core_pct"],
                         "api_rss_peak_mib": resources[0]["rss_peak_mib"],
                         "worker_cpu_single_core_pct": resources[1]["cpu_single_core_pct"] if len(resources) > 1 else None,
                         "worker_rss_peak_mib": resources[1]["rss_peak_mib"] if len(resources) > 1 else None,
                         "request_to_runner_start_mean_ms": r.get("request_to_runner_start_mean_ms")})
    write_csv(args.output / "service_performance_2026-10-05.csv", phases)
    for workload in sorted({r["workload"] for r in performance["performance"]}):
        for level in sorted({r.get("worker_concurrency", r["concurrency"]) for r in performance["performance"] if r["workload"] == workload}):
            rows = [r for r in performance["performance"] if r["workload"] == workload and r.get("worker_concurrency", r["concurrency"]) == level]
            responses = [x for r in rows for x in r["raw_responses"]]
            times = [x["seconds"] * 1000 for x in responses]
            aggregates.append({"workload": workload, "concurrency_level": level, "repeats": len(rows), "requests": len(responses),
                               "requests_per_second": len(responses) / sum(r["seconds"] for r in rows),
                               "p50_ms": percentile(times, .5), "p95_ms": percentile(times, .95), "p99_ms": percentile(times, .99),
                               "repeat_p95_min_ms": min(r["p95_ms"] for r in rows), "repeat_p95_max_ms": max(r["p95_ms"] for r in rows),
                               "errors": sum(r["errors"] for r in rows),
                               "api_cpu_single_core_pct": sum(list(r["resources"].values())[0]["cpu_single_core_pct"] * r["seconds"] for r in rows) / sum(r["seconds"] for r in rows),
                               "api_rss_peak_mib": max(list(r["resources"].values())[0]["rss_peak_mib"] for r in rows),
                               "worker_rss_peak_mib": max((list(r["resources"].values())[1]["rss_peak_mib"] if len(r["resources"]) > 1 else 0) for r in rows),
                               "request_to_runner_start_mean_ms": statistics.mean(r["request_to_runner_start_mean_ms"] for r in rows) if workload.startswith("execute") else None})
    tree = ET.parse(root / "backend-final.xml")
    attrs = next(tree.getroot().iter("testsuite")).attrib
    tests = {key: int(attrs[key]) for key in ("tests", "failures", "errors", "skipped")}
    tests["passed"] = tests["tests"] - tests["failures"] - tests["errors"] - tests["skipped"]
    assert tests["passed"] == tests["tests"] and tests["passed"] >= 210
    assert tests["failures"] == tests["errors"] == tests["skipped"] == 0
    before, after = read(root / "security/before_fix.json"), read(root / "security/after_fix.json")
    assert before["containers_after_timeout"] == 1 and after["containers_after_timeout"] == 0
    assert after["timeout"]["container_cleanup_confirmed"]
    labels_path = Path(__file__).resolve().parents[1] / "benchmarks/retrieval_engineering.json"
    assert hashlib.sha256(labels_path.read_bytes()).hexdigest() == rag["dataset_sha256"]
    summary = {"status": "completed", "date": "2026-10-05", "paid_model_calls": 0,
               "rag": {k: rag[k] for k in ("corpus_sha256", "dataset_sha256", "source_files", "model", "model_revision_not_pinned",
                                           "selection_rule", "timing_note", "builds", "default", "selected")},
               "service": {"scope": performance["scope"], "latency_clock": performance["latency_clock"],
                           "latency_clock_resolution_seconds": performance["latency_clock_resolution_seconds"],
                           "cpu_definition": performance["cpu_definition"], "total_measured_requests": sum(r["requests"] for r in aggregates),
                           "aggregates": aggregates},
               "reliability": faults["reliability"], "security_http_probes": faults["security"],
               "sandbox": {"readonly": before["readonly_and_network"]["readonly"], "network_disabled": before["readonly_and_network"]["network_disabled"],
                           "timeout_running_containers_before_fix": 1, "timeout_running_containers_after_fix": 0, "cleanup_confirmed": True},
               "regression": {"backend": tests, "frontend_tests_passed": 8, "frontend_build_passed": True,
                              "frontend_build_warning": "评测视图 chunk 超过 500kB，构建仍成功"},
               "excluded_attempts": {"initial_full_suite": "207通过、1失败；测试假配置被宿主环境变量覆盖，已修复后复跑",
                                     "initial_service_resources": "仅采到虚拟环境启动器，资源值无效；最终统计完整进程树",
                                     "initial_grid": "480组完整记录保留；补充控制变量并缓存候选排名后另跑720组，源码快照指纹不同，不混合结果"}}
    summary["rag"]["latency"] = latency
    summary["rag"]["configurations"] = 720
    summary["rag"]["queries_development"] = summary["rag"]["queries_validation"] = 16
    summary["rag"]["grid_query_results"] = len(grid)
    summary["rag"]["validation_context_change_pct"] = (rag["selected"]["validation"]["context_chars"] / rag["default"]["validation"]["context_chars"] - 1) * 100
    (args.output / "engineering_evaluation_2026-10-05.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "completed", "rag": summary["rag"]["selected"], "service": aggregates, "tests": tests}, ensure_ascii=False))


if __name__ == "__main__":
    main()
