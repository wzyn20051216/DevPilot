# DevPilot 部署与任务队列

本页描述当前实现。适用范围是可信本地环境与单机演示，尚未完成多机生产验收。API 当前无用户鉴权和仓库级授权，不应直接对公网开放。

## 执行模式

| 配置 | 执行位置 | 当前边界 |
|---|---|---|
| `inline`（默认） | API 内后台线程 | 单 API 进程；重启标记中断，SSE 断开不停止任务 |
| `sqlite` | 独立 Worker | API、Worker 必须访问同一 SQLite 文件和仓库路径；已覆盖基本并发领取和失败重试测试 |
| `redis` | 独立 Worker | 实验实现；弹出和更新非原子，未验收崩溃一致性，不适合依赖其自动故障切换 |

切换 Redis 只改变队列，任务、事件、检查点仍保存在 SQLite；并不会自动得到多机一致的状态存储。Redis Python 客户端不是默认依赖，试验前需另行安装与配置。

## 本地启动

在 `backend/.env` 设置 `TASK_QUEUE_BACKEND=sqlite`，API 和 Worker 要读取相同配置。然后执行：

```powershell
docker compose --profile worker up --build
```

仅启用 worker profile 不会自动把 API 从 inline 改为 sqlite。也可以在两个终端分别执行：

```powershell
.venv\Scripts\python.exe -m uvicorn backend.src.main:app
.venv\Scripts\python.exe -m backend.src.worker
```

确保预先构建沙箱镜像、Docker 可访问仓库目录，且两个进程访问同一任务库。`--once` 处理至多一项任务；运行期间同样续租和检查取消。

## 状态、重试和恢复

1. `/plan` 保存 `awaiting_approval`；`/execute` 只接受该状态。队列模式下它写入队列，由 Worker 原子领取并将任务改为 `running`。
2. 正常结束后队列进入 `done`。执行失败且次数未达上限时重新排队，Worker 允许失败任务在下一次 attempt 重新执行；已取消任务不会执行。
3. API 重启不会把独立 Worker 的 `running` 任务错误标成 `interrupted`。
4. SQLite 租约过期后，队列项隔离为 `dead`，运行任务变成 `interrupted`。**不会自动重投**：旧 Worker 可能只是暂时无法续租，仍在修改文件。
5. 确认旧 Worker 与其工具操作已经停止，检查仓库 Diff 后，调用 `/resume`。它仅接受 `interrupted`；加载角色消息后重新进入编排流程。工具可能重放，不能承诺恰好一次执行。
6. Worker 同时处理 SIGINT/SIGTERM；等待在途任务结束期间继续续租。耗尽退出等待时间仍不是外部工具已停止的证明。

租约默认 300 秒、心跳默认 30 秒。心跳必须显著短于租约。队列按 task_id 去重不等于工具副作用幂等；同一仓库的不同任务目前也没有互斥保护。

## 独立 Docker Host

代码可通过 `DOCKER_HOST` 连接 Docker daemon。远端 daemon 的 bind mount 路径必须存在于**远端主机**；Worker 本地有文件不代表远端可见。镜像也要在目标 daemon 上准备好。示意环境配置：

```powershell
$env:DOCKER_HOST = "tcp://docker-host.local:2376"
$env:DOCKER_TLS_VERIFY = "1"
$env:DOCKER_CERT_PATH = "C:\docker-certs"
```

地址、端口和证书路径必须与实际 daemon 配置匹配。这只是连接入口，项目未实现仓库自动同步、远端调度与整套生产安全配置。

## Kubernetes 与未来隔离

`deploy/kubernetes/` 是部署形态示例。需要自行准备镜像、Secret、PVC、网络与证书，并完成实测。当前没有每任务创建 Kubernetes Job 的调度器，也没有 Firecracker 执行器；二者都是待实现方向，不能仅通过改 DOCKER_HOST 接入任意 microVM 服务。

## 验证与已知限制

最小验收应覆盖：计划批准后执行、重复请求、取消、SSE 重连、API 重启不干扰 Worker、任务实际重试、租约丢失隔离、确认旧进程停止后的显式恢复。真实故障演练仍未完成；离线测试不能替代多进程崩溃和远程 Docker 验收。执行 fencing、背压、鉴权、统一状态存储和仓库互斥仍须补齐，详见 [项目复核](project-review.md)。
