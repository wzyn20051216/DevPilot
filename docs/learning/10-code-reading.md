# 第10章 · 源码导航与分层阅读

[← 第09章](09-run-and-deploy.md) · [课程目录](README.md) · [第11章 →](11-boundaries-and-interview.md)

> 本章目标：从概念读到实现。下列终端命令默认在仓库根目录执行。

## 学习目标

- 按模型、内核、工具、编排、服务和界面阅读。
- 理解生产代码与研究/测试目录的边界。
- 将一次事件定位到后端写入和前端消费位置。

---

## 10.1 目录结构（运行代码与研究代码分离）

```text
DevPilot/
├── backend/
│   ├── src/                    # 生产代码（镜像只打包这里）
│   │   ├── agents/             # Agent 循环与多角色编排
│   │   ├── database/           # SQLite / MySQL 双后端与仓储
│   │   ├── mcp_clients/        # Repository / GitHub MCP 客户端
│   │   ├── mcp_servers/        # Repository MCP Server
│   │   ├── models/             # Task / AgentState / 协议模型
│   │   ├── rag/                # 分块、Embedding、BM25、RRF
│   │   ├── sandbox/            # Docker 隔离执行
│   │   ├── services/           # 执行、队列、发布、上下文
│   │   ├── tools/              # 文件、写入、测试和注册表
│   │   ├── assets/evaluation/  # 最终结果只读快照
│   │   └── main.py             # FastAPI 入口
│   ├── scripts/                # 数据迁移等运行维护
│   └── docker/                 # 沙箱镜像定义
├── frontend/
│   ├── src/                    # 运行页面与客户端
│   └── tests/                  # 前端回归测试
├── tests/                      # 后端测试（与生产代码隔离）
│   ├── backend/                # 单元与集成测试
│   └── support/                # 隔离进程验证工具
├── research/                   # 研究材料（运行应用不导入）
│   ├── evals/                  # 独立评测执行与分析
│   ├── benchmarks/             # 12 个 fixture
│   ├── scripts/                # 参数与工程评估工具
│   └── results/latest/         # **唯一发布结果包**
├── docs/                       # 用户说明书、部署与开发指南
└── deploy/                     # 可选部署模板（K8s 示例）
```

💡 为什么把测试和研究代码挪出 backend/src/？ 生产镜像只打包运行所需代码，不含测试、实验工具和历史报告。这样镜像更小、更安全，也**杜绝了「运行代码偷偷依赖测试代码」**这种腐化。

## 10.2 建议阅读顺序（从模型到底层，再到 UI）

1. backend/src/models/agent_state.py — 先理解 State / Event。

1. backend/src/agents/base_tool_agent.py — 理解 Tool Calling 循环。

1. backend/src/tools/registry.py — 理解工具发现和权限。

1. backend/src/models/agent_protocol.py — 理解严格 JSON 交接协议。

1. backend/src/agents/orchestrator.py — 理解多 Agent 交接和返工。

1. backend/src/services/task_execution_service.py — 理解后台执行与 SSE 解耦 + fencing。

1. backend/src/services/task_queue.py — 理解 SQL / Redis 队列与原子领取。

1. backend/src/database/connection.py — 理解 SQLite / MySQL 方言适配。

1. backend/src/rag/ — 理解混合检索。

1. research/evals/runner.py、real_world.py — 理解独立评测。

1. 最后读前端 Store 和工作台，观察事件如何映射到 UI。

## 源码导航

- [agent_state.py](../../backend/src/models/agent_state.py)
- [trace_service.py](../../backend/src/services/trace_service.py)
- [task.ts](../../frontend/src/stores/task.ts)
- [taskResults.ts](../../frontend/src/utils/taskResults.ts)

## 动手与自检

1. 跟踪tool_result如何同时进入事件和tool_calls记录。
2. 查找前端如何从事件中提取测试和审查结果。
3. 检查backend/src是否导入research或tests。

<details>
<summary>展开参考答案</summary>

运行应用只依赖backend/src与运行维护脚本；研究和测试在独立目录。事件经执行服务持久化，前端Store与extractExecutionResults根据事件恢复展示内容。

</details>

---

[← 第09章](09-run-and-deploy.md) · [返回目录](README.md) · [第11章 →](11-boundaries-and-interview.md)
