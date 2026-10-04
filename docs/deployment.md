# DevPilot 部署、鉴权与任务队列

面向开发与运维：本文用于本地启动、API/Worker 分离及状态迁移。已验证 SQLite、MySQL 8.4、Redis 7 的本机集成链路；真实多主机、网络分区和远端沙箱验收仍未完成。

## 配置与构建

业务服务配置统一写入 `backend/.env`，API 与 Worker 必须使用相同的数据库、队列、密钥及仓库根目录配置。Compose 根目录 `.env` 只管理宿主机工作区与可选 MySQL 容器密码。

```powershell
uv sync --frozen --extra mysql --extra redis
Copy-Item backend/.env.example backend/.env
# 编辑 backend/.env；production 必须设置 API_KEYS。
.venv/Scripts/python.exe -m uvicorn backend.src.main:app
# 队列模式在另一终端启动：
.venv/Scripts/python.exe -m backend.src.worker
```

| 配置 | 执行位置与状态存储 | 限制 |
|---|---|---|
| `TASK_QUEUE_BACKEND=inline` | API 内后台线程；状态可选 SQLite/MySQL | 单 API 进程；`TASK_INLINE_MAX_RUNNING=8`；0 不限制 |
| `TASK_QUEUE_BACKEND=sqlite` | 独立 Worker；SQL 队列随 `DATABASE_BACKEND` 使用 SQLite 或 MySQL | SQLite 需同机共享数据库文件；MySQL 使用行锁与 SKIP LOCKED 领取 |
| `TASK_QUEUE_BACKEND=redis` | 独立 Worker；Lua 队列；业务状态仍由 SQLite/MySQL 保存 | 单节点/Sentinel；不支持 Redis Cluster；跨机时选择 MySQL |

`TASK_QUEUE_MAX_DEPTH=100` 控制新入队与显式恢复，0 不限制。SQLite/Redis 的入队深度检查在写事务/Lua 内完成；MySQL 是软上限，并发入队可能短暂超额。已经在途的任务保留有限重试，因此回流可能使 queued 暂时超过上限。`TASK_WORKER_MAX_CONCURRENCY=2` 控制每个 Worker，inline 上限按单 API 进程计。容量不足返回 429 与 `Retry-After`，拒绝时不改变任务状态。

## 鉴权与目录授权

```dotenv
API_KEYS=replace-with-a-random-secret
ALLOWED_REPO_ROOTS=E:/desktop/projects
DATABASE_BACKEND=sqlite
TASK_QUEUE_BACKEND=sqlite
```

容器内白名单填写 `/workspace`，多根目录和多 Key 用逗号分隔。业务 API（含 SSE）接受 `Authorization: Bearer <key>` 或 `X-API-Key`；失败返回 401 与 `WWW-Authenticate: Bearer`。`/healthz`、`/readyz` 及 CORS 预检不需要 Key。development/test 未配置 Key 时兼容本地访问并打印提示，production 无 Key 拒绝启动。

前端首次发现 401 时弹出 Key 输入框，通过 `/api/auth/check` 验证后存入当前标签页的 sessionStorage。axios 与 fetch SSE 都带认证头；不会自动重放执行或发布请求，用户验证后重新确认操作。点击顶部钥匙图标可以更换或清除 Key。

请求模型会解析绝对路径、`..` 和符号链接，越过白名单返回 422；执行、恢复、Diff、发布和 Worker 构造运行器时再校验历史路径，拒绝返回 403。Key 是共享应用凭据，不提供用户身份、租户隔离或按 Key 分配不同仓库权限。根目录未配置时不限制仓库，生产部署应显式配置。

## Compose

默认服务继续使用 SQLite/inline。先复制两个配置模板，再填写 `backend/.env` 的 API_KEYS 和 `/workspace` 白名单；只打开 profile 不会自动改变 API 的执行模式。

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
# 配好 Key、工作区后启动本地演示：
docker compose up --build
# backend/.env 中 TASK_QUEUE_BACKEND=sqlite，使用独立 Worker：
docker compose --profile worker up --build
```

选择 MySQL + Redis 时，根 `.env` 填写 `DEVPILOT_MYSQL_ROOT_PASSWORD`、`DEVPILOT_MYSQL_PASSWORD`，并在 `backend/.env` 中配置：

```dotenv
DATABASE_BACKEND=mysql
MYSQL_URL=mysql://devpilot:replace-password@mysql:3306/devpilot
TASK_QUEUE_BACKEND=redis
REDIS_URL=redis://redis:6379/0
API_KEYS=replace-with-a-random-secret
ALLOWED_REPO_ROOTS=/workspace
```

连接串密码中的特殊字符需 URL 编码。MySQL/Redis 没有暴露宿主机端口，数据分别存入 named volume；先等待存储健康，再启动应用：

```powershell
docker compose --profile mysql --profile redis up -d --wait mysql redis
docker compose --profile mysql --profile redis --profile worker up --build
```

API 与 Worker 镜像均安装 MySQL/Redis optional dependencies。不要在多主机上用网络文件系统共享 SQLite；MySQL 共享任务、事件、检查点、评测等九张业务表，但工作区仍需另行共享或同步。默认 Compose 的 Docker socket 挂载用于本地演示；生产隔离见下文。

## SQLite 停机迁移到 MySQL

迁移前停止 API、Worker 与评测，备份 SQLite，并确认没有 running/cancelling 任务或 claimed 队列项。源库需已经升级到当前 SQLite 表结构；目标使用专用空 MySQL 数据库。

```powershell
$env:DATABASE_BACKEND = "mysql"
$env:MYSQL_URL = "mysql://devpilot:replace-password@127.0.0.1:3306/devpilot"
.venv/Scripts/python.exe -m backend.scripts.migrate_sqlite_to_mysql --source backend/data/devpilot.db
```

脚本以只读 SQLite 快照分批复制九张表，在同一 MySQL 数据事务内核对行数；中途失败回滚所有插入，目标非空时拒绝覆盖。DDL 建表不在回滚范围内。切换前检查导出的行数和历史任务/事件；该脚本不会迁移 Redis 键或工作区文件。

## 租约、取消与恢复

1. `/plan` 保存 awaiting_approval；批准后 `/execute` 入队或启动 inline 线程。数据库状态迁移防止重复执行。
2. 领取自增 attempts 和 fence_token；心跳、结算检查 owner、token 和未过期租约。失败按最大尝试次数有限重试，次数耗尽进入 dead。
3. API 重启不改变独立 Worker 的运行状态。取消 running 任务只置 cancelling，由 Worker 在安全点触发取消；取消 queued 任务不会删除已经领取的租约。
4. 到期租约隔离为 dead，运行任务置 interrupted，**不自动交接**。SQL 在同一事务中更新队列和任务；Redis 的 Lua 隔离与 SQL 业务状态更新是两个操作，跨存储故障仍需要运维核对。
5. 确认旧 Worker 与工具停止、检查 Diff 后，显式 `/resume`。角色消息恢复后重新进入编排流程，工具可能重放。
6. Worker 接受 SIGINT/SIGTERM；停止领取后继续续租并等待在途任务结束；超过退出期限时仍需检查残留工具/沙箱。

执行服务在事件、检查点、完成与异常收尾前校验租约，存储不可达按失效处理，不缓存授权结果。这是执行层的协作式 fencing 检查；**校验与业务写入之间仍有竞争窗口，不是数据库事务级 fencing**。文件写入/在途工具无法被该校验抢占，不能承诺自动故障切换或副作用恰好一次。同一仓库不同任务没有互斥锁。Redis 队列格式增加全状态索引并修正排序，旧试验队列应停止、排空后升级。

## 独立 Docker Host 与 Kubernetes

远端 daemon 必须看到与 Worker 一致的仓库路径并预装沙箱镜像；本项目不提供自动仓库同步。远程连接示例：

```powershell
$env:DOCKER_HOST = "tcp://docker-host.local:2376"
$env:DOCKER_TLS_VERIFY = "1"
$env:DOCKER_CERT_PATH = "C:/docker-certs"
```

`deploy/kubernetes/` 使用 MySQL + Redis ConfigMap 和 Secret 模板，API 不挂载 Docker socket。部署前准备实际镜像、Secret、workspace PVC、远端 daemon 证书与网络策略。现有清单没有每任务 Job 调度器、Firecracker 执行器，尚未完成集群验收。

## 如何测试与已知限制

```powershell
.venv/Scripts/python.exe -m pytest
# 以下变量只能指向专用测试服务，集成用例会清理测试队列。
$env:TEST_MYSQL_URL = "mysql://root:devpilot@127.0.0.1:33306/devpilot_test"
$env:TEST_REDIS_URL = "redis://127.0.0.1:36379/1"
.venv/Scripts/python.exe -m pytest backend/tests/integration/test_queue_backends.py
Set-Location frontend
npm test
npm run build
```

已覆盖三后端一致性、并发独占领取、优先级、深度拒绝、过期前后持有权、取消竞争、MySQL API→Worker→SSE、迁移回滚和非空保护，以及浏览器认证流程。尚未覆盖真实多主机、断电/网络分区、事务级 fencing、仓库互斥、租户隔离和远端沙箱故障。完整验证与实验证据见 [项目复核](project-review.md)。
