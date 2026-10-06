# 第09章 · 运行、配置与部署

[← 第08章](08-evaluation.md) · [课程目录](README.md) · [第10章 →](10-code-reading.md)

> 本章目标：从概念读到实现。下列终端命令默认在仓库根目录执行。

## 学习目标

- 正确配置模型Key与应用访问Key。
- 完成本地/Docker启动，理解仓库路径映射。
- 理解可选Worker、数据库和队列的显式切换。

---

## 9.1 环境要求

- Python 3.12、uv、Node.js 22、Git

- Docker Desktop（运行 Sandbox、Compose 和真实评测时需要）

- 一个 OpenAI 兼容 API Key（例如 DeepSeek）

## 9.2 后端启动

```powershell
# 已有 backend/.env 时保留原文件，首次才复制模板
Copy-Item backend\.env.example backend\.env
# 配置模型三项；本地若启用 API_KEYS，浏览器需输入对应应用 Key
uv sync --frozen
uv run uvicorn backend.src.main:app --reload
```

- API 文档：http://127.0.0.1:8000/docs

- 健康探针：http://127.0.0.1:8000/healthz、/readyz（无需 API Key）

## 9.3 前端启动

```powershell
Set-Location frontend
npm ci
npm run dev
```

工作台：http://127.0.0.1:5173

## 9.4 测试

```powershell
# 后端（默认 SQLite，跳过需要外部服务的用例）
uv run python -m pytest

# 需要 MySQL / Redis 时，先配置环境变量再跑
$env:TEST_MYSQL_URL = 'mysql://root:pass@127.0.0.1:3306/devpilot_test'
$env:TEST_REDIS_URL = 'redis://127.0.0.1:6379/1'
uv run python -m pytest

# 前端
Set-Location frontend
npm test
npm run build
```

本次 2026-10-06 复核：后端 242 passed、27 skipped（46.23 秒），未配置专用 MySQL／Redis 测试服务，跳过不算本次外部服务验收。前端 8 项测试及生产构建通过。2026-10-05 validation.json 另记录当时含真实 MySQL／Redis 的 242 passed、0 skipped，是不同运行口径。

💡 测试必须自洽：tests/conftest.py 在会话开始时关闭鉴权与仓库白名单，避免开发者本机 backend/.env 里的 API_KEYS 干扰测试结果。本地能过、CI 也能过才算数。

## 9.5 Docker Compose

```powershell
Copy-Item .env.example .env
Copy-Item backend\.env.example backend\.env
docker compose build sandbox backend frontend
docker compose up -d backend frontend
docker compose ps
```

访问 http://localhost:8080 并输入 API Key。Compose 设置 APP_ENV=production，API_KEYS 为空会拒绝启动；根 .env 的 DEVPILOT_HOST_WORKSPACE_ROOT 映射为 /workspace，工作台填写容器路径。SQLite 位于 /app/backend/data/devpilot.db 的命名卷 devpilot_data，与宿主机 backend/data/devpilot.db 独立。需要 Worker、MySQL、Redis 时显式设置后端配置并启用相应 profile，已有历史不会自动迁移。

⚠️ Compose 为了在本地演示中启动 Sandbox，会把 Docker Socket 挂载到 Backend。这个权限接近宿主机管理员能力，不能直接照搬到公网生产环境。

## 9.6 核心配置

图 10｜业务存储 × 执行队列：六种组合与实际写入位置

![图 10：存储 × 队列 6 种组合矩阵](../assets/learning/10-storage-queue-matrix.png)

- LLM_API_KEY、LLM_BASE_URL、LLM_MODEL：模型服务（默认：—）

- API_KEYS：应用访问 Key（逗号分隔多值），production 必填（默认：空（不鉴权））

- ALLOWED_REPO_ROOTS：允许操作的仓库目录，容器内通常为 /workspace（默认：空（不限制））

- DATABASE_BACKEND：sqlite 或 mysql（默认：sqlite）

- MYSQL_URL：MySQL 连接串（密码需 URL 编码）（默认：—）

- TASK_QUEUE_BACKEND：inline、sqlite 或 redis（默认：inline）

- REDIS_URL：Redis 队列连接（默认：—）

- TASK_QUEUE_MAX_DEPTH：入队深度上限（背压）（默认：100）

- TASK_WORKER_MAX_CONCURRENCY：Worker 并发上限（默认：2）

- TASK_INLINE_MAX_RUNNING：单 API 进程容量（默认：8）

- TASK_WORKER_LEASE_SECONDS、TASK_WORKER_HEARTBEAT_SECONDS：租约与续租（默认：300 / 30 秒）

- TASK_MAX_ATTEMPTS：普通执行故障最大尝试次数（默认：3）

- RAG_MODE：默认 manual；auto 的仓库规模／定位信号启发式只用于 single_* 执行策略。当前工作台 multi_* 由执行模式显式选择是否暴露检索工具；暴露 retrieve_code 不代表模型一定调用过。

- GITHUB_PERSONAL_ACCESS_TOKEN：GitHub 访问与发布（默认：—）

LLM_API_KEY 供后端调用模型，API_KEYS 供用户访问平台，二者不同。业务库由 DATABASE_BACKEND 选择，队列由 TASK_QUEUE_BACKEND 独立选择；sqlite 队列名实际代表 SQL 实现，底层跟随 SQLite／MySQL。Redis 只存队列、领取、租约与重试，任务／事件／检查点仍写业务库。修改 .env 后重建 API／Worker 容器读取新配置，仅 restart 不更新注入环境。

## 9.7 核心 API

- GET /healthz / /readyz — 存活 / 就绪探针（免鉴权）

- GET /api/auth/check — 校验 API Key

- POST /api/tasks/plan — 生成待审批计划

- POST /api/tasks/{id}/execute — 批准并执行

- GET /api/tasks/{id}/events?after_sequence=N — SSE 事件流（断线续传）

- POST /api/tasks/{id}/cancel — 请求取消

- POST /api/tasks/{id}/resume — 从中断状态恢复

- GET /api/tasks/{id}/diff — 查看未提交改动

- POST /api/tasks/{id}/publish-preview — 生成发布预览

- POST /api/tasks/{id}/publish — 确认发布 Draft PR

业务接口使用 Authorization: Bearer <key> 或 X-API-Key: <key>；健康探针免鉴权。另有 /api/agent/stream、/api/multi-agent/stream 开发用直接执行接口，它们不走工作台的持久化计划审批流程，不能宣称所有执行入口均强制人工审批。当前前端只有工作台和任务详情两条路由；后端 /api/evals/* 发布快照接口仍保留，前端评测页已移除。

## 9.8 常见问题

Q：Docker Sandbox 报路径不存在？ Compose 中应使用容器路径 /workspace/<project>，不能把 Windows 的 E:\... 直接传给 Backend。

Q：模型可以聊天但任务执行失败？ 检查 LLM_MODEL 是否支持 Tool Calling，并确认兼容端点会返回 tool_calls 和 usage。

Q：SSE 断开后页面没有后续事件？ 读取任务详情确认后台状态，再用最后收到的 sequence 请求 /events?after_sequence=N。

Q：测试无法启动？ 先运行 docker info，再确认 devpilot-sandbox:py312 镜像存在，以及工作区路径可被 Docker daemon 访问。

Q：返回 401 / 422 / 429？ 401 → 检查 API Key；422 → 检查仓库路径或请求参数；429 → 队列或并发已满，等待 Retry-After 头提示的时间后重试。

## 源码导航

- [config.py](../../backend/src/config.py)
- [docker-compose.yml](../../docker-compose.yml)
- [deployment.md](../../docs/deployment.md)

## 动手与自检

1. 解释容器内/workspace路径与宿主路径的对应关系。
2. 回答仅启动Redis容器是否会自动切换队列。
3. 对照部署指南准备自己的.env；已有配置文件保持，确认配置后再启动。

<details>
<summary>展开参考答案</summary>

模型Key与平台Key用途不同。DATABASE_BACKEND和TASK_QUEUE_BACKEND独立选项；启动服务不改变配置，修改.env后需重建容器，历史数据也不会自动迁移。

</details>

---

[← 第08章](08-evaluation.md) · [返回目录](README.md) · [第10章 →](10-code-reading.md)
