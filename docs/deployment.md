# DevPilot 部署指南

默认部署使用一个 API、一个前端和按需启动的沙箱。API/Worker 分离、MySQL、Redis 是可选配置。各进程应共享模型配置、访问凭据和仓库授权范围。

## 配置文件

根 `.env` 配置 `DEVPILOT_HOST_WORKSPACE_ROOT`；`backend/.env` 配置服务。已有文件时保留凭据，首次部署按模板创建。

| 配置 | 用途 |
|---|---|
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` | 模型服务 |
| `LLM_TIMEOUT_SECONDS`、`LLM_MAX_RETRIES` | 请求超时与有限重试 |
| `API_KEYS` | 应用访问 Key，production 必填 |
| `ALLOWED_REPO_ROOTS` | 允许操作的仓库目录，容器内通常为 `/workspace` |
| `DATABASE_BACKEND` | `sqlite` 或 `mysql` |
| `DATABASE_PATH` | SQLite 文件，容器内为 `/app/backend/data/devpilot.db` |
| `MYSQL_URL` | MySQL 连接串，密码需 URL 编码 |
| `TASK_QUEUE_BACKEND` | `inline`、`sqlite` 或 `redis` |
| `REDIS_URL` | Redis 队列连接 |
| `TASK_INLINE_MAX_RUNNING` | 单 API 进程容量，默认 8 |
| `TASK_WORKER_MAX_CONCURRENCY` | Worker 并发，默认 2 |
| `TASK_QUEUE_MAX_DEPTH` | 新入队容量，默认 100 |
| `TASK_WORKER_LEASE_SECONDS`、`TASK_WORKER_HEARTBEAT_SECONDS` | 租约与续租，默认 300/30 秒 |
| `TASK_MAX_ATTEMPTS` | 普通执行故障最大尝试次数，默认 3 |
| `RAG_MODE` | `manual` 或按特征选择的 `auto` |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub 访问与发布 |

## 运行镜像

| 镜像 | 用途 |
|---|---|
| `devpilot-backend:latest` | API 与 Worker 共用 |
| `devpilot-frontend:latest` | Nginx 与静态前端 |
| `devpilot-sandbox:py312` | Python 命令与验证 |
| `devpilot-sandbox:polyglot` | 可选 TypeScript/Java 命令与验证 |

```powershell
docker compose build sandbox backend frontend
docker compose up -d backend frontend
docker compose ps
```

前端地址 `http://localhost:8080`。容器内仓库路径使用 `/workspace/<repository>`。默认命名卷保存状态数据库；宿主机 Docker socket 用于本机沙箱调度，部署入口应置于可信网络或受保护网关。

## 独立 Worker

在服务配置中设置 `TASK_QUEUE_BACKEND=sqlite`，然后：

```powershell
docker compose --profile worker up -d backend frontend worker
```

SQL 队列可使用 SQLite 或 MySQL。Redis 模式还需配置 `REDIS_URL`。业务 cancelled 由 Worker 按不可重试结果收口为队列 dead，保留取消原因；队列 dead 不是业务失败数量。租约到期任务隔离后，应确认旧执行者停止再恢复。

## MySQL 与 Redis

根 `.env` 设置数据库初始化密码，服务 `.env` 设置：

```dotenv
DATABASE_BACKEND=mysql
MYSQL_URL=mysql://devpilot:填写密码@mysql:3306/devpilot
TASK_QUEUE_BACKEND=redis
REDIS_URL=redis://redis:6379/0
```

```powershell
docker compose --profile mysql --profile redis up -d --wait mysql redis
docker compose --profile mysql --profile redis --profile worker up -d backend frontend worker
```

数据库与 Redis 默认不暴露宿主机端口。SQLite 使用单机数据卷；多个主机共享状态时使用共享数据库及一致的工作区映射。

## 数据迁移与备份

迁移前停止 API/Worker，确认没有在途任务，备份 SQLite。目标 MySQL 使用专用空数据库。

```powershell
$env:DATABASE_BACKEND = 'mysql'
$env:MYSQL_URL = 'mysql://devpilot:填写密码@127.0.0.1:3306/devpilot'
uv run python -m backend.scripts.migrate_sqlite_to_mysql --source backend/data/devpilot.db
```

迁移工具在事务内核对业务表记录；非空目标拒绝覆盖。工作区文件另行同步，切换后检查任务、事件与检查点。

## 服务检查

`/healthz` 检查 API 存活，`/readyz` 检查就绪依赖；健康探针无需 Key。API Key 通过 `Authorization: Bearer ...` 或 `X-API-Key` 传递。

```powershell
docker compose ps
docker compose logs --tail 100 backend
```

前端验证 Key 后使用当前标签页会话保存凭据。执行与发布操作由用户明确确认，不自动重放。停止或重建容器时保留业务卷；删除数据卷会删除状态库，应先备份。
