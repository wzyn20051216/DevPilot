# 第02章 · LLM、Tool Calling 与工程状态

[← 第01章](01-overview.md) · [课程目录](README.md) · [第03章 →](03-task-workflow.md)

> 本章目标：从概念读到实现。下列终端命令默认在仓库根目录执行。

## 学习目标

- 画出模型请求工具、程序执行、观测回填的循环。
- 区分State快照、Event增量和SSE连接。
- 理解MCP发现工具与实际执行权限的关系。

---

这一章是为了让没接触过 Agent 开发的人也能读懂后面。如果你已经熟悉，可以跳到第三章。

## 2.1 LLM、Tool Calling 与 Agent

LLM 本身只生成文本或结构化参数。它不能读文件、不能跑测试、不能改代码。所谓 Agent，就是在模型外部增加一个循环：

```text
用户任务
  → [ 组装上下文 ] → 调用 LLM
  → LLM 返回：要么给最终答案，要么请求调用工具
  → 如果需要工具：执行工具，把结果塞回上下文，回到第一步
  → 循环直到给出最终答案 / 达到上限
```

这个循环里，模型是「大脑」，工具是「手脚」，循环控制逻辑是「脊髓反射」——它决定了什么时候停、什么时候重试、出错怎么办。

💡 关键洞察：Agent 的可靠性，更多取决于循环控制逻辑，而不是模型本身。同一个模型，配上不同的工具边界、重试策略和验收逻辑，效果可能天差地别。

DevPilot 的 BaseToolAgent.run_stream() 就是这个循环。每轮都会记录 thinking、tool_call 和 tool_result 事件；达到最大轮数、超过 Token 预算或发生异常时，必须输出明确的 error，不能假装完成。

## 2.2 State 与 Event 的区别

这是初学者最容易混淆的一对概念：

- State（状态）：某一时刻的完整快照。例如：当前轮次、已修改文件列表、测试报告、累计 Token。

- Event（事件）：刚刚发生的一个动作。例如：开始、工具调用、角色交接、测试结果、错误。

打个比方：State 是「此刻房间的照片」，Event 是「监控录像的一帧」。

为什么要有这个区分？

- 传 State 给前端，每帧都要传全量，浪费带宽，也无法表达「变化过程」。

- 传 Event 更适合 SSE，也更容易持久化和回放——把事件按顺序存下来，就能重放整个执行过程。

DevPilot 在 Agent 内部用 AgentState 聚合状态，对外用 AgentEvent 传输增量事件。

## 2.3 SSE 流式会话

SSE（Server-Sent Events）是基于 HTTP 的服务端单向事件流：浏览器用 text/event-stream 持续接收消息。它比 WebSocket 更适合「客户端提交任务，服务端持续推送进度」的场景——因为不需要双向通信，实现更简单。

早期最常见的错误，是把 Agent 生成器直接绑定在 SSE 请求上：

```text
❌ 错误做法：HTTP 请求 → 运行 Agent → 边跑边推送
问题：浏览器一断线，生成器被销毁，任务也停了。
```

DevPilot 的做法是把连接的生命周期和任务的生命周期解耦：

1. inline 模式中 /execute 在容量检查后原子地将 awaiting_approval 改成 running；持久化队列模式只入队，业务状态仍为 awaiting_approval，Worker 领取后才原子迁移为 running。

1. inline 模式由 API 进程的后台线程执行；SQL／Redis 模式由独立 Worker 调用执行服务启动任务。

1. 每条事件先写入状态库，并分配递增 sequence。

1. SSE 接口查询状态库中的新事件并向浏览器发送；数据库不直接连接浏览器。前端使用 fetch 读取流，能够携带 API Key。

1. 客户端用 after_sequence 从断点继续。

因此，连接断了，任务照跑。

## 2.4 多 Agent 与工作流

多 Agent 并不天然优于单 Agent。 很多人以为「角色越多越智能」，其实角色拆分带来的价值是隔离职责和权限：

- Tester 不能写文件；可读取 Diff／源码、搜索代码、调用 run_test 和受限 run_command。最终通过与否必须来自最后一次真实 run_test，不能仅凭静态分析。

- Reviewer 独立阅读 Diff，不参与写代码，因此不会为自己的代码辩护。

- Planner 只做计划，不碰代码。

代价则是：结构化输出失败点变多、上下文重复、延迟增加。

后端与研究工具保留单 Agent、多 Agent 两类模式；当前工作台仅提供 multi_rag 与 multi_no_rag，默认 multi_rag。角色隔离是设计动机，尚不能据此断言多角色修复率更高。

## 2.5 Human-in-the-loop（人在回路）

高风险动作不能只靠 Prompt 约束——因为 Prompt 可能被绕过，也可能被模型「善意地误解」。DevPilot 设置两个明确的审批点：

1. 执行审批：Planner 只生成计划，用户批准后 Coder 才能修改文件。

1. 发布审批：系统先生成 PR Preview 和代码快照，用户确认后才创建分支、提交并发布 Draft PR。

第二个审批点还会重新计算 snapshot_hash。如果审批后文件发生变化，发布会被阻止——避免「用户批准的内容」与「实际推送的内容」不一致。

💡 这个机制对应安全领域的 TOCTOU 防护（Time-of-Check to Time-of-Use）：检查时的内容，必须与使用时的内容一致。

## 2.6 MCP（Model Context Protocol）

MCP 把「模型可调用的工具」抽象为可发现的协议服务。简单说：它是一套标准，让模型能动态「发现」有哪些工具可用，而不用把工具硬编码进提示词。

DevPilot 把仓库只读能力放在 Repository MCP Server 中：

- list_files、read_file、search_code、retrieve_code、git_diff

客户端在一次 Agent 运行开始时做 tool discovery，再按角色白名单过滤。

这是当前项目的实现取舍：只读仓库工具经 Repository MCP 动态发现，写入和沙箱执行保留为本地受控工具。MCP 协议本身也能承载写操作；GitHub 发布就使用单独的可写 MCP 会话，安全性依赖权限与执行实现。

## 源码导航

- [agent_state.py](../../backend/src/models/agent_state.py)
- [base_tool_agent.py](../../backend/src/agents/base_tool_agent.py)
- [repository_client.py](../../backend/src/mcp_clients/repository_client.py)

## 动手与自检

1. 解释tool_call_id为什么要与tool result配对。
2. 说明MCP协议是否天生只读，并举出项目中的读写分工。
3. 运行uv run python -m pytest tests/backend/unit/test_agent_kernel.py。

<details>
<summary>展开参考答案</summary>

模型生成工具参数，由程序执行并回填同一调用ID的结果。MCP可承载读写，项目将仓库只读能力与高风险写入隔开；GitHub发布另用写会话。

</details>

---

[← 第01章](01-overview.md) · [返回目录](README.md) · [第03章 →](03-task-workflow.md)
