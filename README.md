# DevPilot

DevPilot 是面向代码仓库的智能研发协作平台，提供需求分析、计划审批、代码修改、隔离验证、差异审查和 GitHub Draft PR 发布。支持单 Agent 与多角色执行，使用持久化任务、SSE 事件和工具轨迹记录整个过程。

项目流程、架构与实验口径见 [技术知识库（飞书同步版，含 10 张配图）](docs/technical-knowledge-base.md)。使用说明见 [用户说明书](docs/user-manual.md)，部署配置见 [部署指南](docs/deployment.md)，开发与验证见 [开发指南](docs/development.md)。

## 功能

- 本地需求与 GitHub Issue 导入，执行前审批计划。
- 代码检索、结构导航、行为复现、源码修改和隔离测试。
- AST/窗口分块、BM25 与向量融合检索，以及动态工具策略。
- 单 Agent 和 Planner/Coder/Tester/Reviewer 多角色执行。
- 任务取消、检查点恢复、SSE 断线续传和持久化事件。
- API Key、仓库目录授权、角色工具权限和 Docker 沙箱资源限制。
- API/Worker 分离，SQLite/MySQL 状态存储和 SQL/Redis 队列。
- Diff、测试与评审结果、发布预览和 Draft PR 确认。
- 最终评测结果包与只读结果 API；当前前端提供工作台和任务详情。

## Docker 启动

安装 Docker Desktop，进入项目根目录。已有配置时保留原文件；首次启动复制模板：

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
```

在根 `.env` 设置 `DEVPILOT_HOST_WORKSPACE_ROOT`；在 `backend/.env` 设置模型、`API_KEYS` 和 `ALLOWED_REPO_ROOTS=/workspace`。

```powershell
docker compose build sandbox backend frontend
docker compose up -d backend frontend
```

访问 `http://localhost:8080`，输入 API Key。仓库填写容器内路径，例如 `/workspace/my-repository`。需要 TypeScript/Java 沙箱时执行 `docker compose build sandbox-polyglot`。

## 本地开发

要求 Python 3.12、uv、Node.js 22、Git；执行隔离工具还需要 Docker。

```powershell
uv sync --frozen
uv run uvicorn backend.src.main:app --reload
```

另开终端启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

## 项目结构

```text
backend/
  src/              API、Agent、工具、RAG、任务与存储
  scripts/          数据迁移等运行维护工具
  docker/           运行沙箱镜像定义
frontend/
  src/              页面、组件、状态与 API 客户端
  tests/            前端回归测试
tests/
  backend/          后端单元与集成测试
  support/          隔离进程验证工具
research/
  evals/            独立评测执行与分析
  benchmarks/       开发验证用例
  scripts/          参数与工程评估工具
  results/latest/   唯一发布结果包
docs/               用户说明书、部署与开发指南
deploy/             可选部署模板
```

运行镜像只打包应用代码和最终结果快照，不包含测试、实验工具、工作区或历史报告。测试与研究工具保留在独立目录，便于维护和复核。

## 最终评测

真实缺陷评测采用 9 道有效 SWE-bench Verified Issue，每题重复 2 次，共 18 次；本项目文件级独立测试通过 **14/18（77.8%）**，流程完整成功 **10/18（55.6%）**。指标定义及逐次数据见 [最终结果包](research/results/latest/README.md)，后端结果 API 读取同一发布批次。

RAG、服务负载及取消验证的最后结果也集中在该目录，不再混用历史批次。Git 中保留代码版本记录，项目目录内仅发布最后结果。
