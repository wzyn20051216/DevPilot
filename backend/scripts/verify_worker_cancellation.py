"""! @brief 用真实本机 API 与独立 Worker 验证取消后无重复领取，不调用模型。"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx


def wait_until(predicate, timeout=90):
    """! @brief 状态观察有截止时间，超时保留日志而不是默默通过。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("cancellation verification exceeded deadline")


def main():
    """! @brief 单次和持续 Worker 分别取消一次，并检查正常任务仍可完成。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18018)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / "isolated.db"
    os.environ.update({"APP_ENV": "test", "DATABASE_BACKEND": "sqlite",
                       "DATABASE_PATH": str(database.resolve()), "TASK_QUEUE_BACKEND": "sqlite",
                       "TASK_QUEUE_MAX_DEPTH": "1000", "LLM_API_KEY": "",
                       "API_KEYS": "engineering-local-test", "ALLOWED_REPO_ROOTS": str(args.output.resolve())})
    from backend.src.database.connection import init_database
    from backend.src.database.task_repository import TaskRepository
    from backend.src.services.task_queue import SQLTaskQueue
    init_database()
    repo, queue = TaskRepository(), SQLTaskQueue()
    root = Path(__file__).resolve().parents[2]
    processes, streams = [], []
    report = {"status": "running", "paid_model_calls": 0, "cases": []}

    def spawn(mode, once=False):
        """! @brief 全部子进程指向本次数据库与既有可控负载探针。"""
        stream = (args.output / f"{mode}-{len(processes)}.log").open("w", encoding="utf-8")
        streams.append(stream)
        command = [sys.executable, "-u", "-m", "backend.scripts.engineering_probe", mode,
                   "--database", str(database.resolve()), "--port", str(args.port)]
        if once:
            command.append("--once")
        process = subprocess.Popen(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def stop(process):
        """! @brief 仅清理本脚本创建的子进程。"""
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)

    base = f"http://127.0.0.1:{args.port}"
    headers = {"X-API-Key": "engineering-local-test"}
    try:
        api = spawn("api")

        def ready():
            """! @brief 就绪检查不调用模型，也不重试已经退出的进程。"""
            if api.poll() is not None:
                raise RuntimeError("isolated API exited")
            try:
                return httpx.get(base + "/healthz", timeout=.5).status_code == 200
            except httpx.HTTPError:
                return False

        wait_until(ready)
        for once in (True, False):
            task = repo.create_task(repo_path=str(args.output.resolve()), question="cancel", plan=[])
            queue.enqueue(task.id, "cancel")
            worker = spawn("worker", once)
            wait_until(lambda: repo.get_task(task.id).status == "running" and bool(repo.get_events(task.id)))
            started = time.perf_counter()
            response = httpx.post(base + f"/api/tasks/{task.id}/cancel", headers=headers, timeout=10)
            assert response.status_code == 200
            wait_until(lambda: repo.get_task(task.id).status == "cancelled" and queue.get(task.id).status == "dead")
            elapsed = time.perf_counter() - started
            entry = queue.get(task.id)
            assert entry.attempts == 1 and entry.last_error == "任务已取消"
            assert entry.claimed_by is None and entry.lease_expires_at is None
            assert queue.claim("verification-observer") is None
            if once:
                worker.wait(timeout=10)
                assert worker.returncode == 0
            normal = repo.create_task(repo_path=str(args.output.resolve()), question="normal", plan=[])
            queue.enqueue(normal.id, "normal")
            if once:
                worker = spawn("worker", True)
            wait_until(lambda: repo.get_task(normal.id).status == "completed" and queue.get(normal.id).status == "done")
            if once:
                worker.wait(timeout=10)
                assert worker.returncode == 0
            else:
                stop(worker)
            events = repo.get_events(task.id)
            starts = sum(e["type"] == "start" for e in events)
            cancellations = sum(e["type"] == "cancelled" for e in events)
            assert starts == cancellations == 1
            assert queue.get(task.id).attempts == 1 and queue.claim("after-normal") is None
            report["cases"].append({"worker_mode": "once" if once else "continuous", "task_status": "cancelled",
                                    "queue_status": "dead", "attempts": 1, "start_events": starts,
                                    "cancelled_events": cancellations, "extra_claims": 0,
                                    "normal_task_status": "completed", "normal_queue_status": "done",
                                    "cancel_to_terminal_seconds": elapsed, "passed": True})
            print(f"CANCEL {'once' if once else 'continuous'}: cancelled/dead, attempts=1, normal task completed", flush=True)
        report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for process in reversed(processes):
            stop(process)
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    main()
