# DevPilot Kubernetes 部署说明

> 本目录下的 YAML 均为**示例清单**，用于展示 API 与 Worker 分离后的部署形态，
> 落地到具体集群时必须按实际镜像仓库、存储、网络和权限策略调整。

## 1. 架构说明

DevPilot 生产形态把「对外 API」和「执行任务的 Sandbox Worker」拆成两类进程：

| 组件 | 职责 | 是否接触 Docker |
|------|------|-----------------|
| `api-deployment` | 对外 HTTP 接口、任务入队、SSE 观察、健康检查 | 否，**不挂载 docker.sock** |
| `worker-deployment` | 从队列领取任务并执行，运行 Sandbox | 是，通过 `DOCKER_HOST` 连**独立 Docker Host** |

核心原则：**API 进程不再直接调度 Docker**。API 只负责把任务写进持久化队列
（`task_queue` 表或 Redis），由 Worker 副本领取执行。这样：

- API 即使被频繁访问或扩容，也不会与耗时的构建/测试容器争抢资源；
- Worker 崩溃不会丢任务：租约到期后自动重投给其它 Worker；
- 攻击面收敛：API 容器无需 Docker 权限，隔离边界更清晰。

Worker 通过 `DOCKER_HOST` 环境变量连接**独立的 Docker Host**（可以是一台
裸金属、一台 VM 或 Docker-in-Docker 专用节点），而不是宿主节点上的 socket。

## 2. 部署步骤（示例）

```bash
# 1. 准备命名空间
kubectl create namespace devpilot

# 2. 先发布 ConfigMap（注意把 REDIS_URL / DOCKER_HOST 替换为真实地址）
kubectl apply -f configmap.yaml

# 3. 发布 API 与 Service
kubectl apply -f api-deployment.yaml
kubectl apply -f service.yaml

# 4. 发布 Worker（副本数按负载调整）
kubectl apply -f worker-deployment.yaml

# 5. 观察状态
kubectl -n devpilot get pods,svc
```

## 3. 为什么队列默认 SQLite，何时换 Redis

- **SQLite（默认）**：零额外依赖，`task_queue` 表和 `tasks` 表同库，事务一致、
  部署简单。适合单机、单 Worker 或中小规模，以及答辩/演示场景。
- **Redis**：多机、多 Worker 高吞吐时使用。SQLite 是单写者模型，跨主机共享
  文件系统会有锁竞争和一致性问题；Redis 天然支持多客户端并发领取，配合
  ZSET 优先级队列和 Lua 脚本能获得更可靠的分布式交付语义。

切换方式：`TASK_QUEUE_BACKEND=redis` + 配置 `REDIS_URL`。SQLite 后端要求
API 与 Worker 访问**同一个数据库文件**（持久卷/PVC 或共享文件系统）。

## 4. K8s Job（每任务一 Job）演进路径

当前 Worker 是**常驻进程 + 队列轮询**模型。对于「每任务一个容器」的更强隔离
诉求，可演进为 **Kubernetes Job** 模型：

1. API 入队后，由调度器（或事件驱动）为每个任务创建 `kind: Job`；
2. Job 内的 Pod 拉取任务上下文、执行、回写状态，完成后退出；
3. Job 天然支持重试（`backoffLimit`）与并发控制（`parallelism`），
   且每个任务拥有独立命名空间与资源配额，故障爆炸半径更小。

代价是需要额外的 Job 编排/生命周期管理，冷启动延迟也高于常驻 Worker。
建议在「任务之间有强隔离要求」或「执行环境差异大」时再引入。

## 5. Firecracker 强化隔离

当前 Sandbox 依赖 Docker 容器隔离。若要进一步缩小可信计算基（TCB）、
抵御容器逃逸，可把执行单元换成 **Firecracker microVM**：

- 每个任务在一个轻量虚拟机中运行，内核级隔离，启动约 125ms；
- 可通过 `DOCKER_HOST` 指向支持 Firecracker 的运行时（如 Fly Machines、
  AWS Lambda 的 Firecracker 运行时），或直接用 containerd 的 firecracker
  snapshotter；
- 这是技术手册 10.2.6 的目标形态，独立 Docker Host 是它之前的过渡阶段。

## 6. 注意事项

- 所有镜像 tag、`REDIS_URL`、`DOCKER_HOST` 均为占位，务必替换；
- SQLite 后端必须保证 API 与 Worker 挂载同一份数据（PVC）；
- `DOCKER_HOST` 指向的 Docker Host 需对 Worker 所在网络可达，且已开放
  对应端口（示例使用 `tcp://docker-host.local:2375`，生产应启用 TLS）。
