"""! @brief 隔离评测子进程：真实 API/Worker 配合可控运行器，不调用付费模型。"""

import argparse
import os
import sys
import time
from pathlib import Path


def main():
    """! @brief 先覆盖环境配置，再导入应用，杜绝接触演示数据库和凭据。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["api", "worker"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18015)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update({"APP_ENV": "test", "DATABASE_BACKEND": "sqlite",
                       "DATABASE_PATH": str(args.database.resolve()), "TASK_QUEUE_BACKEND": "sqlite",
                       "TASK_WORKER_MAX_CONCURRENCY": str(args.concurrency),
                       "TASK_WORKER_POLL_SECONDS": "0.01", "TASK_WORKER_LEASE_SECONDS": "30",
                       "TASK_WORKER_HEARTBEAT_SECONDS": "5", "TASK_QUEUE_MAX_DEPTH": "1000",
                       "API_KEYS": "engineering-local-test", "ALLOWED_REPO_ROOTS": str(args.database.parent.resolve()),
                       "LLM_API_KEY": "", "LOG_LEVEL": "ERROR", "AGENT_CHECKPOINT_ENABLED": "false"})
    from backend.src.models.agent_state import AgentEvent

    class ControlledRunner:
        """! @brief 50ms 可控工作负载；故障任务仅用于验证执行链语义。"""

        def __init__(self, task, cancel_check):
            self.task = task
            self.cancel_check = cancel_check

        def execute_stream(self, **kwargs):
            """! @brief 在真实持久化、续租和取消路径中产生有界事件。"""
            yield AgentEvent(type="start", agent="coder", message="controlled start",
                             data={"started_monotonic": time.monotonic()})
            if self.task.question == "fail":
                raise RuntimeError("injected runner failure")
            seconds = 120 if self.task.question in {"hold", "cancel"} else 0.05
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if self.cancel_check():
                    yield AgentEvent(type="cancelled", agent="coder", message="controlled cancellation")
                    return
                time.sleep(0.005)
            yield AgentEvent(type="final", agent="coder", message="controlled complete")

    if args.mode == "api":
        import uvicorn
        from backend.src.main import app
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="error")
    else:
        from backend.src import worker
        worker._worker_runner_factory = ControlledRunner
        sys.argv = [sys.argv[0], "--once"] if args.once else [sys.argv[0]]
        worker.main()


if __name__ == "__main__":
    main()
