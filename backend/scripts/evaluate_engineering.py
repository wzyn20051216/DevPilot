"""! @brief 对隔离的真实 HTTP/Worker 执行负载、崩溃、取消、重试与安全探针。"""

import argparse
import asyncio
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean

import httpx

ROOT = Path(__file__).resolve().parents[2]


def percentile(values, fraction):
    """! @brief 最近秩百分位，避免小样本插值误读。"""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def windows_process_tree(pid):
    """! @brief 包含虚拟环境启动器的子进程，避免把启动器误当应用资源。"""
    if os.name != "nt":
        return [pid]
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            has_entry = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    selected = {pid}
    while True:
        expanded = selected | {child for child, parent in parents.items() if parent in selected}
        if expanded == selected:
            return sorted(selected)
        selected = expanded


def windows_resources(pid):
    """! @brief 汇总本次启动的完整进程树，资源不包含负载客户端。"""
    values = [_windows_single_process_resources(child) for child in windows_process_tree(pid)]
    return {"cpu_seconds": sum(v["cpu_seconds"] for v in values) if os.name == "nt" else None,
            "rss_bytes": sum(v["rss_bytes"] for v in values) if os.name == "nt" else None,
            "process_ids": windows_process_tree(pid)}


def _windows_single_process_resources(pid):
    """! @brief 从系统读取指定子进程 CPU 累计时间与工作集，不安装新依赖。"""
    if os.name != "nt":
        return {"cpu_seconds": None, "rss_bytes": None}
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *[(key, ctypes.c_size_t) for key in ("PeakWorkingSetSize", "WorkingSetSize",
                       "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                       "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        stamps = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(s) for s in stamps]):
            raise ctypes.WinError(ctypes.get_last_error())
        ticks = sum((s.dwHighDateTime << 32) + s.dwLowDateTime for s in stamps[2:])
        return {"cpu_seconds": ticks / 10_000_000, "rss_bytes": counters.WorkingSetSize}
    finally:
        kernel.CloseHandle(handle)


def wait_until(predicate, timeout=90):
    """! @brief 所有就绪与状态等待有期限，失败必须保留。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError("isolated engineering probe exceeded deadline")


def save(path, value):
    """! @brief 每阶段立即落盘，异常也保留部分结果。"""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    """! @brief 三类工程评估只操作本次专用数据库，模型使用本地故障端点。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18015)
    parser.add_argument("--performance-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / "isolated.db"
    os.environ.update({"APP_ENV": "test", "DATABASE_BACKEND": "sqlite", "DATABASE_PATH": str(database.resolve()),
                       "TASK_QUEUE_BACKEND": "sqlite", "TASK_WORKER_LEASE_SECONDS": "30",
                       "TASK_WORKER_HEARTBEAT_SECONDS": "5", "TASK_MAX_ATTEMPTS": "3",
                       "TASK_QUEUE_MAX_DEPTH": "1000", "API_KEYS": "engineering-local-test",
                       "ALLOWED_REPO_ROOTS": str(args.output.resolve()), "LLM_API_KEY": "",
                       "AGENT_CHECKPOINT_ENABLED": "false"})
    from backend.src.config import settings
    from backend.src.database.connection import init_database
    from backend.src.database.task_repository import TaskRepository
    from backend.src.models.agent_state import AgentEvent
    from backend.src.services.task_queue import SQLTaskQueue
    from backend.src.worker import make_fence_check
    from backend.src import llm_client
    init_database()
    repo, queue = TaskRepository(), SQLTaskQueue()
    base = f"http://127.0.0.1:{args.port}"
    headers = {"X-API-Key": "engineering-local-test"}
    processes = []
    streams = []
    report = {"status": "running", "llm_paid_calls": 0, "performance": [], "reliability": [], "security": [],
              "latency_clock": "perf_counter", "latency_clock_resolution_seconds": time.get_clock_info("perf_counter").resolution,
              "scope": "Windows 本机单 API + 独立 Worker + SQLite，真实 HTTP，运行器为可控 50ms stub；不代表 LLM/修复吞吐或多机验收",
              "cpu_definition": "各进程 CPU 秒 / 墙钟秒 ×100%，以单核为100%；内存为采样工作集",
              "resources_incomplete_if_non_windows": os.name != "nt"}
    report_path = args.output / "report.json"

    def spawn(mode, concurrency=2, once=False):
        """! @brief 记录子进程 PID，仅终止本次创建的进程。"""
        stream = (args.output / f"{mode}-{len(processes)}.log").open("w", encoding="utf-8")
        streams.append(stream)
        argv = [sys.executable, "-u", "-m", "backend.scripts.engineering_probe", mode,
                "--database", str(database.resolve()), "--port", str(args.port),
                "--concurrency", str(concurrency)]
        if once:
            argv.append("--once")
        process = subprocess.Popen(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def stop(process):
        """! @brief 硬终止只用于真实崩溃注入或清理隔离进程。"""
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)

    def task(question="normal"):
        return repo.create_task(repo_path=str(args.output.resolve()), question=question, plan=[])

    async def load_phase(api, worker, concurrency, count, method, urls):
        """! @brief 有界闭环负载；记录每条响应，P95 不隐藏错误。"""
        semaphore = asyncio.Semaphore(concurrency)
        results = []
        samples = []
        submitted = {}
        pids = [api.pid] + ([worker.pid] if worker else [])
        start_resources = {pid: windows_resources(pid) for pid in pids}
        running = True

        async def sample():
            while running:
                samples.append({str(pid): windows_resources(pid) for pid in pids})
                await asyncio.sleep(0.05)

        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60,
                                     limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)) as client:
            async def request(index):
                async with semaphore:
                    started = time.perf_counter()
                    submitted[urls[index]] = time.monotonic()
                    try:
                        response = await client.request(method, urls[index])
                        if method == "GET":
                            valid = response.status_code == 200 and len(response.json()["events"]) == 20
                        else:
                            valid = response.status_code == 200 and "controlled complete" in response.text
                        results.append({"seconds": time.perf_counter() - started, "status": response.status_code, "valid": valid})
                    except Exception as exc:
                        results.append({"seconds": time.perf_counter() - started, "status": "exception", "valid": False,
                                        "error_type": type(exc).__name__})
            monitor = asyncio.create_task(sample())
            started = time.perf_counter()
            try:
                await asyncio.gather(*[request(i) for i in range(count)])
            finally:
                running = False
                await monitor
            elapsed = time.perf_counter() - started
        resources = {}
        for pid in pids:
            end = windows_resources(pid)
            cpu_start = start_resources[pid]["cpu_seconds"]
            resources[str(pid)] = {"cpu_single_core_pct": ((end["cpu_seconds"] - cpu_start) / elapsed * 100)
                                   if cpu_start is not None else None,
                                   "rss_peak_mib": max(s[str(pid)]["rss_bytes"] or 0 for s in samples) / 1048576}
        times = [r["seconds"] for r in results]
        return {"concurrency": concurrency, "requests": count, "seconds": elapsed,
                "requests_per_second": count / elapsed,
                "successful_requests_per_second": sum(r["valid"] for r in results) / elapsed,
                "p50_ms": percentile(times, 0.5) * 1000, "p95_ms": percentile(times, 0.95) * 1000,
                "p99_ms": percentile(times, 0.99) * 1000, "errors": sum(not r["valid"] for r in results),
                "statuses": dict(Counter(str(r["status"]) for r in results)), "resources": resources,
                "raw_responses": results,
                "resource_process_ids": {str(pid): start_resources[pid]["process_ids"] for pid in pids}}, submitted

    try:
        api = spawn("api")
        def ready():
            if api.poll() is not None:
                raise RuntimeError("isolated API exited; see local log")
            try:
                return httpx.get(base + "/healthz", timeout=0.5).status_code == 200
            except httpx.HTTPError:
                return False
        wait_until(ready)
        populated = task()
        repo.set_status(populated.id, "completed")
        for i in range(20):
            repo.add_event(populated.id, i + 1, AgentEvent(type="thinking", agent="coder", message="x" * 512))
        with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
            for _ in range(10):
                assert client.get(f"/api/tasks/{populated.id}").status_code == 200
        for repeat in range(1, 4):
            for concurrency in ([1, 4, 16, 32] if repeat % 2 else [32, 16, 4, 1]):
                phase, _ = asyncio.run(load_phase(api, None, concurrency, 240, "GET", [f"/api/tasks/{populated.id}"] * 240))
                phase.update({"workload": "task_detail_20_events", "repeat": repeat})
                report["performance"].append(phase)
                save(report_path, report)
                print(f"READ c={concurrency} repeat={repeat} p95={phase['p95_ms']:.1f}ms errors={phase['errors']}", flush=True)
        for workers in [1, 2, 4]:
            worker = spawn("worker", workers)
            # 以持久化任务 start 事件就绪，避免把进程导入计入性能样本。
            warm = task()
            queue.enqueue(warm.id, "warm")
            wait_until(lambda: repo.get_task(warm.id).status == "completed")
            wait_until(lambda: queue.get(warm.id).status == "done")
            for repeat in range(1, 4):
                tasks = [task() for _ in range(48)]
                urls = [f"/api/tasks/{t.id}/execute" for t in tasks]
                phase, submitted = asyncio.run(load_phase(api, worker, 16, len(tasks), "POST", urls))
                waits = []
                for t, url in zip(tasks, urls, strict=True):
                    events = repo.get_events(t.id)
                    starts = [e for e in events if e["type"] == "start"]
                    if len(starts) != 1 or repo.get_task(t.id).status != "completed":
                        raise AssertionError("duplicate or unfinished controlled task")
                    waits.append(starts[0]["data"]["started_monotonic"] - submitted[url])
                    wait_until(lambda t=t: queue.get(t.id).status == "done")
                phase.update({"workload": "execute_50ms_stub_sse", "worker_concurrency": workers, "repeat": repeat,
                              "request_to_runner_start_mean_ms": mean(waits) * 1000,
                              "request_to_runner_start_p95_ms": percentile(waits, 0.95) * 1000})
                report["performance"].append(phase)
                save(report_path, report)
                print(f"QUEUE workers={workers} repeat={repeat} p95={phase['p95_ms']:.1f}ms errors={phase['errors']}", flush=True)
            stop(worker)

        if args.performance_only:
            report["status"] = "completed"
            print("PERFORMANCE probes complete", flush=True)
            return

        # 真正终止正在执行的 Worker；自然等待租约过期，不篡改到期时间。
        held = task("hold")
        queue.enqueue(held.id, "crash")
        crashing = spawn("worker", once=True)
        wait_until(lambda: repo.get_task(held.id).status == "running" and bool(repo.get_events(held.id)))
        entry = queue.get(held.id)
        killed_at = time.monotonic()
        stop(crashing)
        assert queue.claim("competitor-before-expiry") is None
        assert repo.get_task(held.id).status == "running"
        wait_until(lambda: queue.reclaim_expired() == 1, timeout=40)
        isolated_after = time.monotonic() - killed_at
        assert queue.get(held.id).status == "dead" and repo.get_task(held.id).status == "interrupted"
        assert not queue.is_current(held.id, entry.claimed_by, entry.fence_token)
        # 确认旧进程已结束后显式恢复；这里将负载改成可结束的测试任务。
        from backend.src.database.connection import get_connection
        with get_connection() as conn:
            conn.execute("UPDATE tasks SET question = ? WHERE id = ?", ("normal", held.id))
        assert queue.reset(held.id, "explicit-resume")
        resuming = spawn("worker", once=True)
        resuming.wait(timeout=90)
        assert resuming.returncode == 0 and repo.get_task(held.id).status == "completed"
        assert queue.get(held.id).status == "done"
        report["reliability"].append({"scenario": "real_worker_process_kill_and_explicit_resume", "passed": True,
                                     "quarantine_seconds_after_kill": isolated_after, "lease_seconds": 30,
                                     "automatic_takeover": False, "final_status": "completed"})
        save(report_path, report)
        print("FAULT actual Worker kill, lease quarantine, explicit resume passed", flush=True)

        failed = task("fail")
        queue.enqueue(failed.id, "retry")
        for _ in range(3):
            worker = spawn("worker", once=True)
            worker.wait(timeout=90)
            assert worker.returncode == 0
        assert queue.get(failed.id).status == "dead" and queue.get(failed.id).attempts == 3
        assert len([e for e in repo.get_events(failed.id) if e["type"] == "start"]) == 3
        report["reliability"].append({"scenario": "bounded_runner_failure_retries", "passed": True, "attempts": 3})

        cancelled = task("cancel")
        queue.enqueue(cancelled.id, "cancel")
        worker = spawn("worker", once=True)
        wait_until(lambda: repo.get_task(cancelled.id).status == "running")
        cancel_started = time.monotonic()
        assert httpx.post(base + f"/api/tasks/{cancelled.id}/cancel", headers=headers).status_code == 200
        worker.wait(timeout=90)
        assert repo.get_task(cancelled.id).status == "cancelled"
        assert len([e for e in repo.get_events(cancelled.id) if e["type"] == "cancelled"]) == 1
        assert queue.get(cancelled.id).status == "dead"
        assert queue.get(cancelled.id).attempts == 1
        assert queue.claim("after-cancellation") is None
        report["reliability"].append({"scenario": "cross_process_api_cancel", "passed": True,
                                     "cancel_to_worker_exit_seconds": time.monotonic() - cancel_started,
                                     "queue_status_after_cancel": queue.get(cancelled.id).status,
                                     "queue_cleanup_gap": queue.get(cancelled.id).status == "queued"})
        class BrokenStorage:
            def is_current(self, *args):
                raise ConnectionError("injected unavailable storage")
        assert make_fence_check(BrokenStorage(), "isolated", "worker", 1)() is False
        report["reliability"].append({"scenario": "unavailable_storage_fence_fails_closed", "passed": True,
                                     "injection": "in-process storage exception; not a network partition test"})

        with httpx.Client(base_url=base, timeout=10) as client:
            protected = [("GET", f"/api/tasks/{populated.id}"), ("POST", f"/api/tasks/{populated.id}/execute"),
                         ("GET", f"/api/tasks/{populated.id}/events"), ("POST", f"/api/tasks/{populated.id}/cancel")]
            for method, url in protected:
                for label, auth in [("missing", {}), ("invalid", {"Authorization": "Bearer invalid"})]:
                    response = client.request(method, url, headers=auth)
                    assert response.status_code == 401
                    report["security"].append({"scenario": f"unauthorized_{label}_{method}_{url.rsplit('/', 1)[-1]}",
                                               "status": response.status_code, "passed": True})
            assert client.get("/healthz").status_code == 200
            assert client.get("/api/auth/check", headers=headers).status_code == 200
            for path in (str(ROOT), str(args.output / ".." / "..")):
                response = client.post("/api/tasks/plan", headers=headers, json={"repo_path": path, "question": "fix"})
                assert response.status_code == 422
            report["security"].append({"scenario": "outside_whitelist_and_parent_traversal", "passed": True})

        # 对 SDK 发真实 HTTP 故障，不接触外部供应商或账户。
        received = {"count": 0, "status": 429, "delay": 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                received["count"] += 1
                time.sleep(received["delay"])
                status = 200 if received["status"] == 429 and received["count"] > 2 else received["status"]
                payload = ({"id": "local", "object": "chat.completion", "created": 0, "model": "local-flash-stub",
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
                           if status == 200 else {"error": {"message": "injected transient failure", "type": "rate_limit_error"}})
                content = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("retry-after-ms", "1")
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings.llm_api_key = "local-test-only"
            settings.llm_base_url = f"http://127.0.0.1:{server.server_port}/v1"
            settings.llm_model = "deepseek-v4-flash"
            settings.llm_max_retries = 2
            with llm_client.create_client() as model:
                assert model.chat.completions.create(model=settings.llm_model, messages=[{"role": "user", "content": "test"}]).choices[0].message.content == "ok"
            assert received["count"] == 3
            report["reliability"].append({"scenario": "sdk_real_http_429_then_success", "passed": True, "requests": 3})
            received.update(count=0, status=401)
            with llm_client.create_client() as model:
                try:
                    model.chat.completions.create(model=settings.llm_model, messages=[{"role": "user", "content": "test"}])
                except Exception as exc:
                    assert getattr(exc, "status_code", None) == 401
                else:
                    raise AssertionError("401 must fail")
            assert received["count"] == 1
            report["reliability"].append({"scenario": "sdk_permanent_401_not_retried", "passed": True, "requests": 1})
            received.update(count=0, status=200, delay=0.5)
            settings.llm_timeout_seconds = 0.1
            settings.llm_max_retries = 0
            started = time.monotonic()
            with llm_client.create_client() as model:
                try:
                    model.chat.completions.create(model=settings.llm_model, messages=[{"role": "user", "content": "test"}])
                except Exception as exc:
                    assert type(exc).__name__ == "APITimeoutError"
                else:
                    raise AssertionError("slow local model endpoint must time out")
            assert received["count"] == 1
            report["reliability"].append({"scenario": "sdk_real_http_timeout", "passed": True,
                                         "elapsed_seconds": time.monotonic() - started, "requests": 1})
        finally:
            server.shutdown()
            server.server_close()
        report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        save(report_path, report)
        for process in reversed(processes):
            stop(process)
        for stream in streams:
            stream.close()
    print("ENGINEERING probes complete; no paid LLM calls", flush=True)


if __name__ == "__main__":
    main()
