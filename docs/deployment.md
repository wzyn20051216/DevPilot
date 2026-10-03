# DevPilot 部署与任务队列运维

本文档对应技术手册 10.2.5（API 与 Sandbox Worker 分离）与 10.2.6
（独立 Docker Host / Kubernetes Job / Firecracker 强化隔离），说明
三种执行模式、租约/心跳/幂等语义、独立 Docker Host 配置及演进路径。

## 1. 三种模式对比

`TASK_QUEUE_BACKEND` 决定任务如何从「入队」走到「执行」：

| 模式 | 职责划分 | 适用场景 | 故障语义 |
|------|----------|----------|----------|
| `inline` | API 进程内起后台线程直接执行，无队列 | 本地开发、单机演示 | Worker 即 API，进程退出即中断；靠 `mark_incomplete_as_interrupted` 标记孤儿任务 |
| `sqlite` | API 只入队 `task_queue` 表，独立 Worker 进程领取执行 | 单机/小规模生产、答辩 Demo，零额外依赖 | Worker 崩溃 → 租约到期自动重投；SQLite 单写者，跨机共享需注意锁 |
| `redis` | 同上，队列落在 Redis | 多机、多 Worker 高吞吐 | 同上，且支持多客户端并发领取、分布式部署 |

关键差异：`inline` 里「执行」是 API 的副作用；`sqlite`/`redis` 里「执行」
被移到 Worker，API 变薄、可独立扩容，且任务状态与队列状态分离可观测。

## 2. 租约 + 心跳 + 幂等语义

### 2.1 租约（lease）

Worker `claim` 时写入 `lease_expires_at = now + task_worker_lease_seconds`
（默认 300s）。任务只有在 `status='queued'`，或 `status='claimed'` 但
**租约已过期**时，才会被再次领取。Worker 崩溃后，超过一个租约周期，任务
自动回到队列，由其它 Worker 接手。

### 2.2 心跳（heartbeat）

存活 Worker 每 `task_worker_heartbeat_seconds`（默认 30s）对在飞任务续租，
把 `lease_expires_at` 前移。心跳间隔必须**显著小于**租约时长，否则长任务
会被误判为崩溃而重复执行。

### 2.3 幂等（idempotency）

- **重复入队防护**：`task_queue.task_id` 有 `UNIQUE` 约束，同一任务重复
  `enqueue` 只会得到 `False`，不会产生重复队列项。
- **重复执行防护**：Worker 领取到队列项后，还要调用
  `claim_status(task_id, {"awaiting_approval","interrupted"}, "running")`
  在 `tasks` 表上做原子状态迁移。这一步串行化「队列领取」与「业务状态」，
  防止同一任务被 API 与 Worker、或多个 Worker 重复执行。
- **失败重试上限**：`attempts` 每次 `claim` 自增；失败且未达 `max_attempts`
  时回 `queued` 重试，达到上限则进 `dead`（死信）终止。

### 2.4 故障恢复矩阵

| 故障场景 | 结果 |
|----------|------|
| Worker 进程崩溃（任务执行中） | 租约到期 → `reclaim_expired` 置回 `queued` → 其它 Worker 领取重跑 |
| Worker 心跳线程卡死 | 同上，租约不再续期，到期后重投 |
| 重复入队同一任务 | `task_id` 唯一约束 → 返回 False，不产生重复 |
| 同一任务被并发领取 | `claim` 单语句原子领取，只有一个 Worker 拿到 |
| 任务状态不允许执行（已被执行/取消） | `claim_status` 返回 False → `complete(False)` 归还队列，靠 attempts 上限兜底 |
| 反复失败 | `attempts` 递增，达 `max_attempts` 后进 `dead` 死信 |

## 3. 独立 Docker Host 配置

Worker 负责运行 Sandbox，需要访问 Docker。生产环境不把宿主机的
`/var/run/docker.sock` 挂进 Worker，而是指向一台独立 Docker Host：

```bash
# 方式一：环境变量（Worker 进程读取 DOCKER_HOST）
export DOCKER_HOST=tcp://docker-host.local:2375
# 启用 TLS（推荐）
export DOCKER_TLS_VERIFY=1
export DOCKER_CERT_PATH=/etc/docker/certs
```

```bash
# 方式二：docker context（本地/运维机切换）
docker context create prod \
  --docker "host=tcp://docker-host.local:2375,ca=...,cert=...,key=..."
docker context use prod
```

Worker 的 `DOCKER_HOST` 最终通过环境变量注入（见 K8s `worker-deployment.yaml`
与 `configmap.yaml` 的占位）。独立 Docker Host 隔离了 API 与执行环境，是
10.2.6 的第一阶段。

## 4. K8s Job 方案与 Firecracker 演进

- **K8s Job（每任务一 Job）**：把「常驻 Worker 轮询」替换为「一任务一
  Job」。Job 自带 `backoffLimit`/`parallelism`，每个任务独立 Pod 与资源
  配额，故障爆炸半径更小；代价是冷启动延迟与额外的 Job 编排。适合任务间
  有强隔离要求的场景。
- **Firecracker 强化隔离**：进一步把执行单元换成 microVM（约 125ms 启动、
  内核级隔离），缩小可信计算基，抵御容器逃逸。可经支持 Firecracker 的
  Docker 运行时或 containerd snapshotter 接入，是 10.2.6 的最终形态。

详见 `deploy/kubernetes/README.md`。

## 5. 本地试运行与验证

```bash
# 启动 API + frontend + Worker（Worker 由 profile 按需启用）
docker compose --profile worker up

# 只起 Worker 加后端
docker compose --profile worker up backend worker
```

验证步骤：

1. 创建计划：`POST /api/tasks/plan`，得到 `awaiting_approval` 任务；
2. 触发执行：`POST /api/tasks/{task_id}/execute`（`TASK_QUEUE_BACKEND=sqlite`
   时 API 只入队，Worker 领取执行）；
3. 观察 Worker 日志出现 `claim` → `service.start` → `complete`；
4. 查询 `GET /api/tasks/{task_id}` 确认任务最终 `completed`/`failed`；
5. 查询队列统计：连入 SQLite 后 `SELECT status, COUNT(*) FROM task_queue GROUP BY status;`
   应看到 `done` 或 `dead`；
6. 模拟崩溃：强杀 Worker 容器，等待超过一个租约周期后重启，任务应被重新
   领取执行（验证租约重投）。
