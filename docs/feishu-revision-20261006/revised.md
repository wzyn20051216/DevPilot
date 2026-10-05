# DevPilot 技术知识库  

面向 AI 应用／Agent 开发学习、项目答辩与面试复盘。本文于 2026-10-06 对照当前工作区源码核对；实验数据仍引用 2026-10-05 的冻结发布结果，不把后续协议修改当作新的评测成绩。

阅读建议

- 第一次读：按「一 → 四 → 五 → 七」建立全局认识。

- 准备面试：重点读「一、六、七、十一」。

- 想跑起来：直接读「八」。

- 想看懂代码：按「九」的学习路径逐个文件读。

一句话总结：DevPilot 不是「让大模型聊天」，而是把不稳定的模型输出约束在一条可观察、可验证、可中断的工程流程里。

---

## 一、🎾 项目简介与简历写法

### 1.1 项目是什么

DevPilot 是一个面向真实代码仓库的 AI 软件工程平台。用户可以输入本地开发任务，也可以导入 GitHub Issue；系统先分析仓库并生成计划，等待人工审批后，再由 Agent 调用代码检索、文件修改、测试执行和 Git Diff 等工具完成任务。

它解决的核心问题不是「让大模型聊天」，而是把不稳定的模型输出约束在一条可观察、可验证、可中断的工程流程中。具体体现为六条分工：

1. LLM 只负责推理和选择工具，不直接接触宿主机 Shell。

1. 工具层负责执行动作，并在执行时实施路径、权限和命令边界。

1. 开发流程中的真实测试与 Reviewer 审查共同决定多角色流程是否完成；研究评测在流程结束后另用独立 verifier 裁决补丁正确性。模型的文字声明不能替代机器测试。

1. 持久化 Trace 负责保存过程（任务、事件、工具调用），浏览器断线不会终止后台任务。

1. 人工审批负责高风险边界，代码执行与 GitHub 发布分别确认。

1. 评测系统负责验证价值，同时保留成功案例与失败案例。

💡 为什么这样设计？ 大模型输出是概率性的：同样输入可能给出不同答案，甚至「自信地做错」。工程化的关键，是在模型外围加一圈确定性的脚手架——固定的工具、固定的状态机、固定的验收标准。模型可以变，脚手架不变。

### 1.2 技术栈

- 后端：Python 3.12、FastAPI、Pydantic v2、Uvicorn

- Agent Runtime：自研 Tool Calling 循环（BaseToolAgent），OpenAI 兼容 API

- 检索：Hybrid Code RAG：AST/滑窗分块 + BM25 + 向量检索 + RRF 融合

- 多 Agent：Planner / Coder / Tester / Reviewer + 编排器（Orchestrator）

- 协议：MCP（Repository / GitHub）、严格 JSON 交接协议

- 隔离执行：Docker Sandbox（禁网、只读挂载、CPU/内存/进程限制）

- 存储：SQLite（默认）／ MySQL 8.0+ 双后端

- 任务队列：SQL 队列（task_queue 表）／ Redis 队列（Lua 原子脚本）

- 前端：Vue 3、TypeScript、Pinia、Vite

- 可观测：SSE 事件流、持久化事件与工具调用记录

- 鉴权：API Key（Authorization: Bearer / X-API-Key）+ 仓库目录白名单

### 1.3 可直接用于简历的项目描述

DevPilot 多智能体软件工程平台｜AI 应用／Agent 开发

项目简介：面向真实代码仓库设计并实现可执行、可观测、可评测的 Agent 平台，支持本地任务与 GitHub Issue 导入，通过 Planner → Coder → Tester → Reviewer 多角色流程完成代码修改，并以 Docker 独立验证、人工审批和持久化 Trace 约束执行风险。

负责内容：

1. Agent Runtime：抽象 BaseToolAgent 统一工具循环，集中处理 Tool Calling、事件流、Token/耗时统计、错误观测回填、最大迭代与协作式取消。

1. 多 Agent 编排与协议：实现 Planner → Coder → Tester → Reviewer 串行流程，四个角色均采用严格 JSON 输出与交接信封；测试失败、审查拒绝共享默认 2 轮返工预算，每轮修改后重新测试和审查。

1. 权限与沙箱：按角色暴露工具白名单，并在执行层二次校验；测试命令在禁网、只读挂载、512 MB 内存、1 CPU、128 进程上限的 Docker Sandbox 中执行。

1. Hybrid Code RAG：实现代码结构化分块、384 维向量检索、BM25 与 RRF 融合；在 720 组参数网格上以开发集选参、验证集报告，16 条验证查询 Recall@5 达 84.38%、MRR 0.6615，返回上下文字符数较默认减少 47.65%。

1. 可靠任务执行：将 Agent 执行从 SSE 连接中解耦，事件写入状态库；支持断线续传、取消、进程重启后的中断识别与显式恢复。

1. 分布式任务队列：API/Worker 分离，队列泛化为 SQL 与 Redis 双实现，Redis 侧全部操作以 Lua 脚本原子化消除「先读后写」竞态；每次领取自增 fencing 令牌，失租执行者被拒绝写入；入队深度、Worker 并发、本机 inline 三处背压。

1. 状态存储与访问控制：实现 SQLite／MySQL 8.0+ 双后端及显式迁移工具；默认仍为 SQLite，不会因启动 MySQL 自动迁移。接入 API Key 与仓库根目录白名单，规范化路径后检查目录边界与符号链接。

1. 评测闭环：构建 12 个合成 fixture 及独立 verifier，并引入 SWE-bench Verified 真实缺陷、错误基线／金补丁双校准、测试文件保护与证据持久化。在 9 道有效 Issue、每题 2 次的 18 次评测中，文件级独立测试通过 14/18（77.8%），流程完整成功 10/18（55.6%）；不等同于官方榜单 resolved。

### 1.4 面试时如何用一分钟介绍

DevPilot 是我做的代码 Agent 工程化项目。它不是简单调用一次大模型，而是让模型通过受控工具读取和修改真实仓库。任务先生成计划并由用户审批，执行进入后台线程，所有事件和工具调用写入状态库，因此前端断线后可以继续接收。

测试在只读、禁网、限资源的 Docker 容器中运行，多角色流程必须同时具备机器测试通过和 Reviewer 批准；研究评测还会独立复验候选补丁。在 9 道真实 Issue 的 18 次重复中，文件级独立测试通过 14/18，流程完整成功 10/18。代码保留四种 Agent／RAG 变体，但当前冻结批次不能证明多 Agent 或 RAG 相对单 Agent 的优势。

工程侧重点是任务可靠性：支持进程内执行或 API／Worker 分离，SQL 与 Redis 队列都有租约、心跳和领取令牌，并在入队、Worker 并发和 inline 并发处限制容量。已验证的跨进程行为与真实多主机故障演练需要区分，不能将同机测试描述成多机生产验收。

### 1.5 项目能证明什么

这个项目可以证明以下能力：

- 能把 LLM 变成具有工具、状态和反馈回路的 Agent，而不只是调用 SDK。

- 理解模型能力与确定性软件系统之间的边界，知道哪些事必须交给代码而不是 Prompt。

- 能实现 RAG、MCP、沙箱、SSE、Tracing、任务队列与评测系统。

- 能建立同题配对、失败诊断与证据核对的方法；当前默认模式是产品选择，其收益仍需冻结对照实验验证。

- 能区分 Demo、实验结论、生产系统三者的差别。

---

## 二、🥝 前置知识

这一章是为了让没接触过 Agent 开发的人也能读懂后面。如果你已经熟悉，可以跳到第三章。

### 2.1 LLM、Tool Calling 与 Agent

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

### 2.2 State 与 Event 的区别

这是初学者最容易混淆的一对概念：

- State（状态）：某一时刻的完整快照。例如：当前轮次、已修改文件列表、测试报告、累计 Token。

- Event（事件）：刚刚发生的一个动作。例如：开始、工具调用、角色交接、测试结果、错误。

打个比方：State 是「此刻房间的照片」，Event 是「监控录像的一帧」。

为什么要有这个区分？

- 传 State 给前端，每帧都要传全量，浪费带宽，也无法表达「变化过程」。

- 传 Event 更适合 SSE，也更容易持久化和回放——把事件按顺序存下来，就能重放整个执行过程。

DevPilot 在 Agent 内部用 AgentState 聚合状态，对外用 AgentEvent 传输增量事件。

### 2.3 SSE 流式会话

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

### 2.4 多 Agent 与工作流

多 Agent 并不天然优于单 Agent。 很多人以为「角色越多越智能」，其实角色拆分带来的价值是隔离职责和权限：

- Tester 不能写文件；可读取 Diff／源码、搜索代码、调用 run_test 和受限 run_command。最终通过与否必须来自最后一次真实 run_test，不能仅凭静态分析。

- Reviewer 独立阅读 Diff，不参与写代码，因此不会为自己的代码辩护。

- Planner 只做计划，不碰代码。

代价则是：结构化输出失败点变多、上下文重复、延迟增加。

后端与研究工具保留单 Agent、多 Agent 两类模式；当前工作台仅提供 multi_rag 与 multi_no_rag，默认 multi_rag。角色隔离是设计动机，尚不能据此断言多角色修复率更高。

### 2.5 Human-in-the-loop（人在回路）

高风险动作不能只靠 Prompt 约束——因为 Prompt 可能被绕过，也可能被模型「善意地误解」。DevPilot 设置两个明确的审批点：

1. 执行审批：Planner 只生成计划，用户批准后 Coder 才能修改文件。

1. 发布审批：系统先生成 PR Preview 和代码快照，用户确认后才创建分支、提交并发布 Draft PR。

第二个审批点还会重新计算 snapshot_hash。如果审批后文件发生变化，发布会被阻止——避免「用户批准的内容」与「实际推送的内容」不一致。

💡 这个机制对应安全领域的 TOCTOU 防护（Time-of-Check to Time-of-Use）：检查时的内容，必须与使用时的内容一致。

### 2.6 MCP（Model Context Protocol）

MCP 把「模型可调用的工具」抽象为可发现的协议服务。简单说：它是一套标准，让模型能动态「发现」有哪些工具可用，而不用把工具硬编码进提示词。

DevPilot 把仓库只读能力放在 Repository MCP Server 中：

- list_files、read_file、search_code、retrieve_code、git_diff

客户端在一次 Agent 运行开始时做 tool discovery，再按角色白名单过滤。

这是当前项目的实现取舍：只读仓库工具经 Repository MCP 动态发现，写入和沙箱执行保留为本地受控工具。MCP 协议本身也能承载写操作；GitHub 发布就使用单独的可写 MCP 会话，安全性依赖权限与执行实现。

### 2.7 Hybrid Code RAG（混合代码检索）

图 7｜Hybrid Code RAG：源码索引、两路召回、加权 RRF 与文件去重

![07-hybrid-rag.png](images/07-hybrid-rag.png)

普通 RAG 面向自然语言文档，Code RAG 还要保留文件、符号和行号信息。DevPilot 的索引流程是：

```text
源码文件
  → 按 AST 或滑动窗口分块
  → 生成带文件名和符号名的 embedding_text
  → all-MiniLM-L6-v2 生成 384 维归一化向量
  → 同时建立 BM25 语料
```

产品默认查询分别取向量 Top 20 与 BM25 Top 20，再按 chunk 融合。参数为 candidate_k=20、rrf_k=60、vector_weight=0.5；排名从 1 开始。MCP retrieve_code 默认最终 top_k=8，研究中的 Recall@5 使用 top_k=5，两者不能混同。

```text
score(d) = 2w / (k + rank_vector(d))
         + 2(1-w) / (k + rank_bm25(d))

某一路未召回该 chunk 时，该路贡献为 0。
默认 w=0.5、k=60，等价于两路各加 1/(60+rank)。
```

按融合分数降序后，每个文件只保留最高分 chunk，再截取最终 top_k。返回文件路径、符号、起止行号、分数和源码片段，模型可继续 read_file 阅读原文。索引保存在目标仓库的 .devpilot/（chunks.json、vectors.npy、manifest.json）；加载时校验源码指纹，源码变化后重建，不是向量数据库服务。

为什么要混合检索？

- 向量检索：语义相近但词面不同的查询（如「处理超时」→「timeout handling」）

- BM25：函数名、配置项、异常名等精确词匹配

- RRF 融合：只依赖排名而非原始分数，避免两套分数尺度不一致

### 2.8 为什么检索指标高，修复率仍可能下降

这是本项目最反直觉、也最值得讲的发现：

检索系统回答的是「相关文件能否被找到」； Agent 任务回答的是「模型能否基于证据完成正确修改」。 两者之间还隔着理解、工具选择、运行时验证和兼容性推断。

检索指标与修复指标不能互相替代。当前发布包报告了本项目查询集的检索表现，以及一个真实缺陷批次；没有同题、同预算的 RAG 开／关冻结对照，因此不能从这批数据判断 RAG 是否提高或降低修复率。以下是需要用实验验证的可能原因：

- 额外上下文会形成错误锚点，把模型引到不相关的方向。

- 检索结果可能相关，却不是决定行为的运行时证据。

- 小仓库本来就能通过 list_files + search_code + read_file 快速定位。

- RAG 增加索引和推理延迟。

结论：RAG 应当是可评测、可关闭、可按仓库规模启用的能力——而不是「有就一定开」。

### 2.9 上下文压缩

图 8｜上下文压缩：程序摘要、精确观测去重与工具回合配对

![08-context-compression.png](images/08-context-compression.png)

Tool Calling 长任务会重复携带历史消息，输入 Token 往往呈近似二次增长——对话越长，每一轮都在重发全部历史。DevPilot 的确定性压缩策略是：

- 永久保留 system message 和用户原始 Issue。

- 默认保留最近 12 条消息；若边界落在 tool result 中，向前回退到对应 assistant tool_calls，因此实际保留条数可能超过 12。

- 用程序抽取更早回合中的工具名、参数片段和输出片段，形成最多 6,000 字符的压缩记录；不是另调 LLM 生成摘要。

- 保留已修改文件和最近测试结论。

- 裁剪点回退到 assistant tool call 边界，避免出现「孤立的 tool result」（有结果没调用，模型会困惑）。

- 单次工具 observation 最多进入上下文 10,000 字符。

压缩仅改变下一轮发给模型的上下文；执行轨迹仍保留事件与工具结果预览。重复观测按工具名、规范化参数及内容去重，read_file 额外比较内容 SHA-256。单条 observation 超限时保留首尾；这些字符上限不是 Token 上限，也不能直接换算费用。

### 2.10 独立 Verifier（独立裁决器）

Agent 说「测试通过」不等于代码正确。 DevPilot 把验证分成两层：

- 运行流程中的 Tester：为 Agent 提供反馈，帮助它在任务过程中自我修正。

- 评测器（独立 Verifier）：在 Agent 结束后，从受保护的验证目标重新运行测试——它不看 Agent 说了什么，只看真实结果。

真实缺陷评测还会排除候选补丁对测试文件和测试配置的修改，防止 Agent 通过删除断言来「修复」任务。

测试是验收标准。若候选补丁能同时修改标准，测试通过就可能失去意义，因此研究评测保护测试文件及测试配置，并在 Agent 结束后独立复验；这是一项风险控制，不代表所有 Agent 必然篡改测试。

---

## 三、⚾️ 业务背景

### 3.1 使用场景

开发者面对一个不熟悉的仓库和 Issue 时，需要完成定位、修改、测试、审查和发布。普通聊天模型只能给建议；全自动 Agent 又可能误改文件、执行危险命令或错误宣称成功。

DevPilot 的目标用户是希望观察和控制执行过程的开发者。典型任务包括：

- 修复明确的后端 Bug。

- 修改配置传播逻辑。

- 实现一个范围受控的小功能。

- 从 GitHub Issue 生成计划并形成 Draft PR。

- 对比单 Agent、多 Agent、RAG 和无 RAG 的效果。

### 3.2 核心需求

把「模型随机性」与「工程确定性」隔离开，需要满足四条：

1. 可控：高风险动作（改文件、发布）必须人工审批。

1. 可观察：记录角色进度、工具调用、参数、结果预览、错误和用量；thinking 是进度事件名，不代表系统完整保存或展示模型内部推理。

1. 可验证：成功与否由独立测试裁决，而非模型自述。

1. 可恢复：断线、重启、取消都不能让任务「半死不活」。

### 3.3 设计原则

- 证据约束：产品默认 multi_rag；是否优于其他模式，需要同题、同预算、冻结版本的对照评测。

- 纵深防御：权限、路径、命令、沙箱多层校验，任何一层都不单独承担安全

- 确定性优先：能用代码保证的，不交给 Prompt

- 证据优先：结论必须附带代码、命令、原始报告和失败案例

- 诚实边界：明确写出「未做」「未验证」的部分，不夸大

---

## 四、🐧 项目流程

### 4.1 本地任务主流程（一张图看懂）

图 3｜默认 inline 任务时序：计划、审批、后台执行与持久化 SSE

![03-local-sequence.png](images/03-local-sequence.png)

```mermaid
sequenceDiagram
    participant U as 用户
    participant UI as Vue 工作台
    participant API as FastAPI
    participant DB as 状态库
    participant A as 后台执行服务与 Agent
    participant S as Docker Sandbox
    U->>UI: 输入仓库与需求
    UI->>API: POST /api/tasks/plan
    API->>A: Planner 只读分析
    A-->>API: 严格 JSON 计划
    API->>DB: 保存 awaiting_approval
    API-->>UI: 返回计划与 task_id
    U->>UI: 批准计划
    UI->>API: POST /api/tasks/{id}/execute
    API->>DB: inline 容量检查与 CAS running
    API->>A: 启动独立后台线程
    A->>DB: 持久化事件和工具记录
    A->>S: 测试与受限命令
    S-->>A: 机器结果
    API->>DB: 查询 sequence 后的新事件
    API-->>UI: SSE 增量事件
    A->>DB: completed / failed / cancelled
    Note over API,A: 图示为默认 inline；队列模式由 Worker 领取后执行
```

关键点逐条解释：

1. Plan 与 Execute 分离：生成计划不修改任何代码，用户先看清要做什么，再决定是否执行。

1. inline 的 /execute 在本进程锁内检查容量，再用带旧状态条件的 SQL UPDATE 领取；并发审批仅一个成功。队列模式先幂等入队，Worker 再 CAS 为 running，不能把入队等同于已经运行。

1. 后台线程 + 事件先落库：前端只是「事件的消费者」，不是「任务的持有者」。

1. 完成后不自动发布：发布需要第二个审批点。

### 4.2 多 Agent 流程

```text
Planner 输出计划
  ↓
Coder 修改代码并查看 Git Diff
  ↓
Tester 运行真实测试
  ├─ 失败：把 stdout/stderr 交还 Coder，最多返工 2 轮
  └─ 通过：进入 Reviewer
  ↓
Reviewer 独立检查需求、边界和非必要修改
  ├─ approved = true：完成
  └─ approved = false：交还 Coder 返工（与测试失败共享同一返工预算）
```

返工规则（max_repair_rounds 默认 2）：

- 测试失败与审查拒绝共享同一返工预算，因此不会「测试返工 2 次 + 审查返工 2 次」无限循环。

- 返工交接携带原始需求、已批准计划及本轮失败报告，回到 Coder，再执行 Tester 和 Reviewer；不会重新调用 Planner，也不会重新生成审批计划。

- 每次 Coder 执行后重新验证，不复用旧的测试通过或审查批准。

Planner、Coder、Tester、Reviewer 均使用严格协议模型，只接受一个 JSON 对象，拒绝围栏、前后解释、未知／缺失字段和类型转换。失败后最多进行一次禁止工具的格式纠正；仍不合法则 protocol_error，停止交接。测试工具、收集或超时错误属于 test_execution_error，停止验证；普通断言失败才进入有界返工。

### 4.3 GitHub Issue 流程

1. 用户填写 owner、repo、Issue 编号和本地仓库路径。

1. GitHub MCP 读取 Issue 标题、正文和元数据。

1. 系统把 Issue 转换为完整开发请求。

1. Planner 生成计划并保存任务来源。

1. 后续执行流程与本地任务一致。

1. 完成后生成只读 PR Preview。

1. 用户二次审批后创建分支、提交文件并发布 Draft PR。

### 4.4 取消、断线与恢复

- 取消：状态先变 cancelling，后台设置 threading.Event；Agent 在每轮模型调用和工具调用边界检查，随后写入 cancelled

- 断线续传：前端记录最后事件序号，重连 /events?after_sequence=N

- 进程重启：仅 inline 模式启动时把遗留 running/cancelling 标记为 interrupted；队列模式 API 重启不影响独立 Worker 的任务

- SQL／Redis 队列的过期租约都隔离为 dead，业务任务标记 interrupted；不直接自动接管仍可能执行副作用的旧任务。确认旧 Worker／工具停止后，用户显式 /resume。

- 恢复：用户显式调用 /resume，系统从已批准的计划重新执行

⚠️ 诚实的边界：execute_stream 仍从编排入口运行，阶段、返工计数与工具副作用没有事务恢复。检查点会校验调用配对、保留系统和原始任务，但不能描述为「无损续跑」——已经发生的文件写入无法回滚。

---

## 五、🏗️ 系统架构

### 5.1 总体架构

图 1｜总体架构：四角色串行、默认 inline 与可选 Worker 路径

![01-architecture.png](images/01-architecture.png)

```mermaid
flowchart LR
    U[开发者] --> Web[Vue 3 工作台]
    Web -->|HTTP 请求| API[FastAPI + API Key + 目录授权]
    API -->|SSE 事件| Web
    API -->|inline 默认| Exec[TaskExecutionService]
    API -.->|可选：仅入队| Q[(SQL 或 Redis 队列)]
    Q -.->|Worker 领取与续租| W[独立 Worker]
    W -.-> Exec
    Exec --> Runtime[四角色串行编排与 Tool Calling]
    Runtime --> LLM[OpenAI 兼容模型 API]
    Runtime --> Registry[工具发现与权限校验]
    Registry --> Repo[Repository MCP：只读与 Hybrid RAG]
    Registry --> Write[本地文件写入工具]
    Registry --> Sandbox[Docker Sandbox：测试与命令]
    API --> DB[(状态库：SQLite 默认／MySQL 可选)]
    Exec -->|事件、工具记录、检查点| DB
    API --> GH[GitHub MCP：只读导入／审批后写入发布]
    Repo --> FS[目标仓库 + .devpilot 本地索引]
    Write --> FS
    FS -->|只读挂载| Sandbox
```

与初版的区别（这几块是新增的）：

- API Key 鉴权层：所有业务请求先过 require_api_key。

- 任务队列：inline 时是进程内线程，sqlite/redis 时是持久化队列 + 独立 Worker。

- fencing 校验：执行服务写事件/状态前校验「本执行者是否仍是任务的合法持有者」。

- 双后端状态库：SQLite 或 MySQL，由统一方言适配层屏蔽差异。

### 5.2 分层职责

- API 层（main.py、schemas.py）：鉴权、参数校验、路由与 SSE 转发。当前部分路由直接调用 git_diff、仓库分析和发布收集工具；这是现有实现，不应描述为已完全隔离所有仓库操作。

- 服务层（services/）：任务执行、队列、发布、上下文（不做什么：不包含模型提示词逻辑）

- Agent 层（agents/）：Tool Calling 循环、角色、编排（不做什么：不绕过工具直接动文件）

- 工具层（tools/、mcp_*）：实际执行动作，实施边界（不做什么：不做业务决策）

- 数据层（database/）：状态库读写、方言适配（不做什么：不包含业务规则）

分层帮助集中管理职责和替换实现，例如 Agent 不直接处理 SQLite／MySQL 方言。但实际依赖并非严格只向下一层：工具调用 MCP 客户端，服务调用 Agent，API 也有直接工具调用；描述现有边界比宣称理想架构更准确。

### 5.3 任务状态机

图 2｜任务状态机：七种状态与逐项迁移条件

![02-task-states.png](images/02-task-states.png)

```mermaid
stateDiagram-v2
    [*] --> awaiting_approval: 计划已生成
    awaiting_approval --> running: inline 批准／Worker 领取
    awaiting_approval --> cancelled: 队列模式取消未领取任务
    running --> completed: 测试通过且审查批准
    running --> failed: 执行／协议／验证失败
    running --> cancelling: 请求取消
    cancelling --> cancelled: 到达协作式取消点
    running --> interrupted: inline 重启／队列失租
    cancelling --> interrupted: inline 重启／队列失租
    interrupted --> running: 用户恢复后重新领取
    failed --> running: 仅队列模式剩余尝试的重试
    Note right of awaiting_approval: queued 是队列状态，不是业务 TaskStatus
    Note right of running: approved 是预留值，当前主流程不使用
```

关键状态迁移使用 compare-and-set（带旧状态条件的 SQL Update）。这样并发请求中只有一个能成功迁移状态，天然防重。

### 5.4 数据模型

状态库共 9 张表，职责如下：

- tasks：任务主体：仓库路径、需求、状态、执行模式

- plan_steps：Planner 生成并等待审批的计划；批准后使用同一份计划执行，不是只保存审批通过的步骤。

- agent_events：全量事件流（含递增 sequence），SSE 的数据源

- tool_calls：工具调用的参数、结果预览、耗时、成败

- task_sources：任务来源（本地 / GitHub Issue）

- publish_previews：PR 预览、快照 Hash、发布状态

- evaluation_results：历史评测记录表。当前结果 API 读取 backend/src/assets/evaluation/latest.json 发布快照，不把业务库中的旧记录自动合并为新实验。

- task_queue：队列任务（含 fence_token、租约、尝试次数）

- agent_contexts：上下文检查点，用于中断恢复

SQLite 连接开启外键和 WAL，以改善单机读写并发；这些 PRAGMA 不适用于 MySQL。SQL 队列领取时，SQLite 用 BEGIN IMMEDIATE 写事务，MySQL 用 FOR UPDATE SKIP LOCKED；WAL 本身不能代替领取、租约与重试机制。

---

## 六、🧩 核心实现拆解

### 6.1 BaseToolAgent：统一的工具循环

backend/src/agents/base_tool_agent.py 处理所有角色共有的机制：

1. 初始化 State 并发送 start。

1. 发现 MCP 工具并按白名单过滤。

1. 压缩旧上下文（见 2.9）。

1. 调用 OpenAI 兼容的 Chat Completions。

1. 累加 Token 和 LLM 耗时。

1. 解析 Tool Call 参数并发送 tool_call。

1. 在注册表执行工具，把异常也转换为 observation（而不是直接崩溃）。

1. 截断过长的工具结果并发送 tool_result。

1. 无工具调用时校验角色输出与机器证据后输出角色 final；单个角色 final 不是任务 completed，多角色 completed 由编排器在 Tester 通过与 Reviewer 批准后产生。

1. 最大轮数后进行一次禁止工具的强制收尾；仍不合法则失败。

💡 为什么把循环集中在基类？ 让角色只定义 Prompt、工具权限和迭代上限，循环逻辑只有一份。改一次，四个角色都受益——这就是「消灭重复」。

### 6.2 两层工具权限（纵深防御）

图 4｜纵深防御：角色权限、路径校验与 Docker 执行边界

![04-security.png](images/04-security.png)

- 第一层在 get_tool_definitions()：模型只能看见当前角色允许的工具。

- 第二层在 execute_tool()：即使手工构造了未授权的 Tool Call，执行时仍会抛出 PermissionError。

工具权限按各角色构造函数固定：Planner 使用 list_files、read_file、search_code；Coder 另外可 write_file、replace_in_file、git_diff、protocol_probe；Tester 使用 git_diff、read_file、search_code、run_test、run_command；Reviewer 使用 read_file、search_code、git_diff。启用 RAG 时仅 Planner／Coder／Reviewer 增加 retrieve_code，Tester 不增加。Coder 没有 run_test／run_command，Tester／Reviewer 不能写入。模型看见的工具和执行时再次校验使用同一白名单。

### 6.3 文件安全

所有文件路径都遵循三步校验：

```text
1. 与仓库根目录拼接
2. 执行 resolve()（解析符号链接）
3. 通过 relative_to(base_path) 确认目标仍在仓库内
```

因此 ../../secret 和指向仓库外的符号链接都会被拒绝。

其他限制：

- read_file 默认拒绝读取超过 1 MiB 的磁盘文件；这是读取工具的限制，不是所有工具或仓库文件的统一大小上限。

- 进入上下文的内容按字符数截断。

- write_file／replace_in_file 只能修改已有文本文件，不能创建新文件；拒绝 .env、.env.local、.env.production 以及 .git／.devpilot 路径，采用临时文件 + os.replace 原子替换。大文件优先使用从原文读取的唯一锚点；这种原子替换不等于多任务对同一仓库的冲突隔离。

### 6.4 Docker Sandbox（隔离执行）

默认白名单为 python、pytest、ruff、mypy、node、npx、javac、java。普通命令使用 argv 数组与 shell=False；研究评测可通过可信 SandboxProfile 包装固定启动前缀，并对候选 argv 做 shlex.join。白名单限制入口，不限制 Python／Node 解释器内任意代码，真正的副作用边界还依赖容器隔离。

容器限制：

```text
--network none                                   # 完全禁网
--memory 512m                                    # 内存上限
--cpus 1.0                                        # CPU 上限
--pids-limit 128                                  # 进程数上限
--read-only                                       # 根文件系统只读
--tmpfs /tmp:rw,noexec,nosuid,size=64m            # 临时目录不可执行
--mount source=<repo>,target=/workspace,readonly  # 仓库只读挂载
```

这意味着：测试命令可以读取候选代码，但不能借测试过程修改宿主机仓库，也不能访问外网。

### 6.5 结构化输出（让模型「说人话」变成「说机器话」）

四个角色的必填输出：Planner={summary, steps[{id,title,description}]}；Coder={status,summary,modified_files,changes,risks}；Tester={passed,summary,stdout,stderr}；Reviewer={approved,summary,issues}。分别由四个 ProtocolOutput 模型严格校验，不能互相代替。AgentHandoff 携带 protocol_version、source、target、phase、task、plan、coder_report、test_report、review_report、repair_round，由编排器生成并验证交接顺序。以下是当前角色协议的处理：

- parse_protocol_output 直接严格解析唯一 JSON 对象，不从 Markdown 围栏或混合文本提取；宽容 parse_structured_output 仍在兼容路径使用，不是新角色协议的放行规则。

- 字段或格式无效时，最多发起一次 tool_choice=none 的纠正请求；禁止调用新工具、修改代码，禁止把 blocked／拒绝改成 implemented／批准，也不能删除原审查问题。

- 最终用 Pydantic 严格校验字段和类型。

- 拒绝未知字段和隐式类型转换（如字符串 "true" 冒充布尔 true）。

协议还内建了语义一致性校验（这是最巧妙的部分）：

- Coder：status = blocked 时必须在 risks 中说明阻塞原因，不能空报告跳过

- Reviewer：approved = true 时 issues 必须为空；拒绝时必须列出具体问题

- Planner：计划步骤 id 必须唯一且为正整数

Tester 结论以最后一次真实 run_test 为准：模型自报 passed=true 不能覆盖机器失败；后一次测试超时或异常会使早先通过证据失效。Coder 的 modified_files 必须匹配本轮真实写入记录。协议严格合法只证明能交接，不证明业务修复正确。

### 6.6 后台执行与 Trace

图 9｜背压与 fencing：容量、租约校验、失租隔离及副作用边界

![09-backpressure-fencing.png](images/09-backpressure-fencing.png)

TaskExecutionService 维护当前进程内的后台线程与取消事件；inline 运行在 API 进程，可选队列模式运行在 Worker 进程。事件先持久化，SSE 再查询。tool_result 同时写入 tool_calls 的参数、结果预览、耗时和成败，可计算以下指标；Trace 不是所有 stdout／文件内容的无限量完整存档。

- 总事件数和 Tool Call 数。

- 失败工具数。

- 总 Token 与估算成本。

- LLM 耗时和工具耗时。

- 每个 Agent 的调用分布。

- RAG 调用次数和检索耗时。

队列模式的 fencing 校验检查 worker_id、fence_token、claimed 状态和未过期租约；在事件／检查点／终态写入前检查，失租后停止后续写入并协作式取消。过期任务先隔离，不在仍有副作用的情况下直接交给新 Worker。inline 没有队列租约。

fencing 是写入前的应用层检查，检查与业务写入并非跨 SQL／Redis 的同一事务；仍存在检查后失租的竞态窗口。它也不能抢占在途模型／工具调用或回滚文件写入，因此不能承诺副作用恰好一次。不同任务同时操作同一仓库仍需独立工作区或额外串行化。

### 6.7 发布一致性（TOCTOU 防护）

Preview 收集相对 HEAD 的新增／修改及未跟踪文件，过滤敏感路径，保存文件内容快照、Hash、分支与 PR 文案。确认发布时复核仓库、Git 基线、工作区及快照，变化时要求重新预览。GitHub MCP 依次创建远端分支、提交快照文件、创建 Draft PR；不在用户本地仓库自动提交。删除文件当前被拦截，多步远端发布也不是一个原子事务。

💡 这对应安全系统中的 TOCTOU 防护：检查时的内容必须与使用时的内容一致。用户批准的是「这一份代码」，不是「这个分支名」。

---

## 七、🧪 评测设计与真实结果

这一章回答一个问题：你怎么知道这东西真的有用？ 答案是：不靠感觉，靠可复现的分层评测。

### 7.1 为什么需要三层评测

图 5｜三层评测：合成基线、组件检索与真实缺陷分别验收

![05-evaluation-layers.png](images/05-evaluation-layers.png)

只做组件评测，会把「找得到文件」误当成「修得好代码」；只展示 Demo，又无法知道失败率。因此 DevPilot 用三层：

- 合成回归：我的改动有没有破坏已知能力？（手段：12 个自建 fixture + 独立 verifier）

- 组件评测：RAG 检索准不准？（手段：标注查询 + Recall@K / MRR）

- 真实缺陷：能不能修真实仓库的 Bug？（手段：SWE-bench Verified + 双校准门禁）

### 7.2 四种架构变量（对照实验的基石）

- single_no_rag：单 Agent，不用检索

- single_rag：单 Agent，用混合检索

- multi_no_rag：多 Agent，不用检索

- multi_rag：多 Agent，用混合检索（当前默认）

每次运行都会记录：成功与否、测试结果、工具调用、迭代次数、返工次数、耗时、Token、LLM/工具耗时、工作区和错误信息——这样才能做同题配对对比，而不是「跑一次感觉不错」。

💡 注意：前端只暴露 multi_rag 与 multi_no_rag 两个选项（默认 multi_rag）。single_* 变体是研究用的实验开关，不面向最终用户。

### 7.3 合成回归（测「有没有退化」）

数据集共 12 个 fixture：9 个基础 Python 用例（easy／medium／hard 各 3 个），另有 TypeScript、Java、Python 跨模块各 1 个；全部难度分布为 easy 3、medium 5、hard 4。audit_report.json 记录 12/12 未修复基线失败，只证明 verifier 能识别初始缺陷，不代表最新 Agent 已完成 12/12 修复。

💡 为什么初始必须失败？ 如果一个任务在没修的时候测试就通过，那它根本证明不了 Anything。这叫「校准门禁」，是评测的第一道防线。

合成回归用于检测已知能力是否退化；需要真实运行候选 Agent 并检查独立 verifier 后才能声明修复通过。当前保留的审计是初始基线结果，不能替代候选运行，也不能代表真实仓库总体性能。

### 7.4 RAG 检索评测

参数网格：720 组配置、32 条人工查询；开发集选参、验证集报告（避免在验证集上过拟合）。

16 条验证查询的选定配置：Recall@5=84.38%、MRR@5=0.6615、平均返回 11,646.75 字符；同批默认为 68.75%、0.4604、22,247.4375 字符，字符数减少 47.65%。选定参数为 window80_no_overlap、top_k=5、candidate_k=20、rrf_k=10、vector_weight=0.5；产品源码默认仍为 AST／窗口兜底、80/15、rrf_k=60，没有自动改成选定配置。开发与验证的目标文件有重合，指标只描述本项目标注查询，字符减少不是生产费用节省。

Embedding 首次缺缓存时会下载模型，已缓存时优先 local_files_only 加载；backend／worker 共享独立模型缓存卷。发布包的网格计算缓存查询向量和每路候选，独立查询耗时包含编码与 BM25 构建、不含索引构建；不能把网格耗时当成线上逐次查询延迟。

### 7.5 SWE-bench 真实缺陷评测

图 6｜冻结真实缺陷结果：14/18 独立测试通过，10/18 流程完整成功

![06-real-issue-results.png](images/06-real-issue-results.png)

每题固定官方 base commit 与镜像，先验证错误基线失败、金补丁通过，再运行 Agent；裁判使用本项目映射的文件级测试，候选测试／测试配置改动被保护和排除。最初 10 个实例中 matplotlib__matplotlib-20676 未通过环境校准、没有模型运行，最终统计 9 个有效实例 × 2 次；无效实例保留在报告中。

最终冻结批次由 adaptive_multi_20261005 及其缓存修复补跑组成，使用 deepseek-v4-flash、temperature=0.2。以下 18 次统计全部保留，不是只挑通过结果；当前严格角色协议修改后的 smoke 不能冒充这一批次的成绩。

- 有效 Issue 数量：9 道

- 每题重复次数：2 次

- 完整模型运行：18 次

- 文件级独立测试通过：14/18（77.8%）

- 工作流合规：11/18（61.1%）

- 流程完整成功：10/18（55.6%）

- 两次都修对的题：6/9

- 至少一次修对的题：8/9

- 累计模型 Token：3,236,730

- 平均 Agent 耗时：211.8 秒

这是本项目独立文件级裁判口径，不等同于官方 SWE-bench resolved。正确修复=独立测试通过；工作流合规=满足本项目的必需工具门禁且没有流程错误；流程完整成功=正确修复且工作流合规。因此是 14 次修对、11 次合规、两者交集 10 次，不能把三项比例相加或互换。

### 7.6 失败样本为什么有价值

失败比成功更能说明问题。历史诊断中发现：

- 模型会用不同作用域的对象替代 Issue 原始复现（改了「看起来像」的东西）。

- 模型会误判生成器异常的边界（把环境错误当成代码错误）。

这类失败直接指向「运行时探针」和「协议兼容检查」的能力缺口——比一个漂亮的成功案例更有信息量。

💡 有的失败还暴露了评测环境本身的问题（工作区不完整造成假阴性）。修好环境后，同一个候选补丁能通过——所以评测基础设施本身也要被验证。

### 7.7 如何正确表达实验结论

可以说：

- 「合成审计确认 12/12 未修复基线失败；候选修复成绩需另附实际运行报告。」

- 「文件级 RAG 在 16 条验证查询上 Recall@5 为 84.38%。」

- 「9 道真实 Issue 各重复 2 次，文件级独立测试通过 14/18。」

不应说：

- ❌「DevPilot 修复真实 Bug 的成功率是 78%。」（样本太少，且是自研口径）

- ❌「RAG 已经显著提升代码修复效果。」（实验没有支持这个结论）

- ❌「多 Agent 一定比单 Agent 可靠。」（不同任务类型结论不同）

💡 为什么这么严格？ 因为样本量、重复次数和仓库类型都不足以支持泛化。一个诚实的 60% 比一个注水的 90% 更有价值——后者在面试中被追问一句「样本多大」就崩了。

---

## 八、🚀 本地运行与部署

### 8.1 环境要求

- Python 3.12、uv、Node.js 22、Git

- Docker Desktop（运行 Sandbox、Compose 和真实评测时需要）

- 一个 OpenAI 兼容 API Key（例如 DeepSeek）

### 8.2 后端启动

```powershell
# 已有 backend/.env 时保留原文件，首次才复制模板
Copy-Item backend\.env.example backend\.env
# 配置模型三项；本地若启用 API_KEYS，浏览器需输入对应应用 Key
uv sync --frozen
uv run uvicorn backend.src.main:app --reload
```

- API 文档：http://127.0.0.1:8000/docs

- 健康探针：http://127.0.0.1:8000/healthz、/readyz（无需 API Key）

### 8.3 前端启动

```powershell
Set-Location frontend
npm ci
npm run dev
```

工作台：http://127.0.0.1:5173

### 8.4 测试

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

### 8.5 Docker Compose

```powershell
Copy-Item .env.example .env
Copy-Item backend\.env.example backend\.env
docker compose build sandbox backend frontend
docker compose up -d backend frontend
docker compose ps
```

访问 http://localhost:8080 并输入 API Key。Compose 设置 APP_ENV=production，API_KEYS 为空会拒绝启动；根 .env 的 DEVPILOT_HOST_WORKSPACE_ROOT 映射为 /workspace，工作台填写容器路径。SQLite 位于 /app/backend/data/devpilot.db 的命名卷 devpilot_data，与宿主机 backend/data/devpilot.db 独立。需要 Worker、MySQL、Redis 时显式设置后端配置并启用相应 profile，已有历史不会自动迁移。

⚠️ Compose 为了在本地演示中启动 Sandbox，会把 Docker Socket 挂载到 Backend。这个权限接近宿主机管理员能力，不能直接照搬到公网生产环境。

### 8.6 核心配置

图 10｜业务存储 × 执行队列：六种组合与实际写入位置

![10-storage-queue-matrix.png](images/10-storage-queue-matrix.png)

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

### 8.7 核心 API

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

### 8.8 常见问题

Q：Docker Sandbox 报路径不存在？ Compose 中应使用容器路径 /workspace/<project>，不能把 Windows 的 E:\... 直接传给 Backend。

Q：模型可以聊天但任务执行失败？ 检查 LLM_MODEL 是否支持 Tool Calling，并确认兼容端点会返回 tool_calls 和 usage。

Q：SSE 断开后页面没有后续事件？ 读取任务详情确认后台状态，再用最后收到的 sequence 请求 /events?after_sequence=N。

Q：测试无法启动？ 先运行 docker info，再确认 devpilot-sandbox:py312 镜像存在，以及工作区路径可被 Docker daemon 访问。

Q：返回 401 / 422 / 429？ 401 → 检查 API Key；422 → 检查仓库路径或请求参数；429 → 队列或并发已满，等待 Retry-After 头提示的时间后重试。

---

## 九、📁 目录结构与学习路径

### 9.1 目录结构（运行代码与研究代码分离）

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

### 9.2 建议阅读顺序（从模型到底层，再到 UI）

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

---

## 十、🛡️ 当前边界与下一步

### 10.1 当前边界（诚实清单）

评测边界

- 真实评测为 9 道有效 Issue、每题 2 次（18 次），是自研文件级口径，非官方榜单成绩；样本不足以泛化。

- 当前真实集主要是 Python，跨语言能力仅由合成 benchmark 部分覆盖。

- 失败样本诊断显示，模型在「运行时行为推断」和「协议兼容检查」上仍有缺口。

成本证据

- 上下文压缩、观测去重是已实现的控制机制；当前发布包未提供冻结、足够重复的成本配对实验，不能承诺某个 Token 节省比例。cache 命中统计不是实测货币节省，费用估算还取决于显式配置的单价。

任务执行边界

- 队列已支持 SQL（SQLite / MySQL）+ Redis（Lua 原子） 双后端，含租约、心跳、fencing 令牌、有限重试与三处背压。

- 未实现 Redis Cluster／自动故障切换；当前通过 REDIS_URL 使用普通单端点连接，没有 Sentinel 发现与切换实现。MySQL 队列深度是软上限，并发入队可略超阈值；这不是精确配额。

- fencing 不能抢占在途工具调用：已发出的文件写入无法回滚。

恢复边界

- 状态库保存角色消息检查点，校验系统/原始请求保留、工具回合配对。但编排阶段和工具副作用未事务化，多角色流程仍从入口运行，不能承诺无损续跑或恰好一次执行。

部署边界

- API 已有 API Key 鉴权与仓库目录白名单；但未做 JWT / 多租户；共享 API Key 是应用级凭据，组织级用户身份管理未实现。

- K8s 文件是示例清单；每任务 Job 和 Firecracker 执行器尚未实现。

- 多机场景仅用「多进程同机」模拟，没有真实多主机 / 网络分区演练。

### 10.2 优先级最高的改进

1. 扩样验收：逐项排查校准未过的实例；解决个别官方镜像启动错误；先用未调参任务做同题配对评测，再谈泛化。

1. 探针配对实验：运行时协议探针已实现，下一步用有/无探针配对验证收益——采纳工具本身不等于正确修复。

1. 动态 RAG 阈值：启发式决策已实现，但阈值尚未通过大仓库真实对照验收，不能声称自适应优于固定开关。

1. 故障演练：补真实多主机下的租约、fencing、分区演练；Redis 尚非已验收的生产后端。

1. 隔离部署：实现每任务 Job 与 microVM 执行器；仅改 DOCKER_HOST 不能接入任意 microVM 服务。

---

## 十一、💬 高频面试问题

### 11.1 为什么默认使用多 Agent + RAG？

multi_rag 是当前 API 和工作台默认值。多角色提供职责／工具权限隔离，RAG 提供可选择的定位证据，这是产品设计动机。当前真实缺陷发布包没有同题冻结对照，不能称它证明默认模式优于 single_* 或 multi_no_rag，也不能称大型仓库收益已完成验收。

当前工作台仅提供 multi_rag／multi_no_rag；single_* 仍可通过 API 或研究工具调用。RAG_MODE=auto 的启发式仅应用于 single_*，multi_* 的检索开关跟随 execution_mode；模型仍可选择不用已暴露的检索工具。

### 11.2 多 Agent 的价值是什么？

主要价值是职责、权限和验证视角的隔离：

- Tester 无写入权限，可读取 Diff／源码并执行 run_test／run_command；机器测试结果约束最终报告，静态检查不能替代实际测试。

- Reviewer 独立读取 Diff，不为自己的代码辩护。

代价是更多模型调用、更多结构化输出边界和编排失败点。所以它需要和单 Agent 做消融实验，而不是默认假设更优。

### 11.3 为什么不用 LangGraph / 现成框架？

当前流程对事件结构、持久化、取消点、Token 统计和错误语义有明确要求。自研小型 Runtime 更容易展示底层机制并精确控制行为——尤其是 fencing、背压、租约这些框架不一定暴露的细节。

但：生产项目是否选框架，应依据团队维护成本、生态和工作流复杂度，而不是为了「自研」本身。面试时这样回答，既展示深度，又不显得偏执。

### 11.4 如何防止 Agent 执行危险命令？

Prompt 只是第一层。真正的边界由以下共同实施：

1. 角色工具白名单（模型看不见不该用的工具）。

1. 执行层二次权限检查（手工构造的调用也会被拒）。

1. 路径解析（resolve() + relative_to()）。

1. 命令入口白名单 + 普通路径 argv／shell=False，避免把候选参数直接交宿主 Shell 解释；解释器仍能执行容器内任意代码，需配合禁网、只读与限资源隔离。

1. Docker Sandbox（禁网、只读、限资源）。

### 11.5 如何证明任务真的成功？

先区分两个层面：产品多角色 completed 要求有效角色报告、最后一次机器测试通过及 Reviewer 批准；研究的端到端成功还要求独立 verifier 通过并满足工作流合规。真实缺陷评测先双校准并保护测试，不依赖模型自述。

把候选补丁与验收标准分离，在 Agent 结束后独立复验，才能减少通过修改测试而获得假通过的风险；普通开发任务与研究评测的测试保护规则也要区分。

### 11.6 SSE 断线为什么不影响任务？

inline 的 Agent 在 API 后台线程执行，队列模式由独立 Worker 执行，事件都写业务状态库。SSE 从库中按 sequence 查询新事件，重连只是继续观察；服务重启／恢复与浏览器重连是不同操作。

### 11.7 RAG 为什么使用 RRF？

向量相似度和 BM25 分数不在同一尺度，直接加权需要额外归一化和调参。RRF 只依赖排名，简单稳定，并能奖励同时被两路检索排在前面的结果。

### 11.8 如果做成生产系统，最先改什么？

先修可靠性基础设施，再优化模型策略：

1. 真实多主机下的租约与 fencing 演练。

1. 隔离 Docker daemon，实现每任务 microVM。

1. 补齐配额与多租户权限。

1. 按真实业务量设计 Redis 高可用：Sentinel／Cluster 需要各自客户端、拓扑与故障演练，当前没有这些实现，不能仅替换连接串就视为已接入。

💡 为什么这个顺序？ 因为模型策略再优化，如果任务会丢、状态会错乱，一切白搭。基础设施的可靠性是 1，模型效果是后面的 0。

---

## 十二、📌 证据与复现入口

- 最终结果包（唯一发布口径）：research/results/latest/

- 真实缺陷汇总：research/results/latest/real_issues.json

- RAG 参数网格：research/results/latest/rag_parameters.csv

- RAG 逐查询结果：research/results/latest/rag_queries.csv

- 服务负载：research/results/latest/service_performance.csv

- 工程统计：research/results/latest/engineering.json

- 合成数据审计：research/benchmarks/audit_report.json

- 后端测试：tests/backend/

- 前端测试：frontend/tests/

复现命令：

```powershell
# 无付费模型的合成基线审计（写到独立输出，不覆盖发布包）
.venv\Scripts\python.exe -m research.evals.audit --output research/artifacts/audit-local.json

# 文件级 RAG 评测：需要模型缓存；缺缓存时首次下载
.venv\Scripts\python.exe -m research.evals.retrieval --top-k 5

# 以下会真实调用配置的模型；这是新实验，不是直接复现冻结 18 次成绩
.venv\Scripts\python.exe -m research.evals.runner --full --repeats 1 `
  --variant multi_no_rag --variant multi_rag

# 真实缺陷：还需指定／准备实例镜像、数据、版本与参数
.venv\Scripts\python.exe -m research.evals.real_world --help
```

评测执行在 research/evals/，参数与工程评估在 research/scripts/；运行应用不导入 research。冻结结果应连同 manifest、源码／数据指纹、补丁和轨迹核验，不能用当前源码新跑的数据覆盖原批次。服务负载的 3,312 次请求来自本机可控 HTTP 负载，不是 LLM 修复吞吐或生产 SLA。

最可靠的项目介绍方式，是同时给出代码、复现命令、原始报告和失败案例。DevPilot 的价值不在于声称 Agent 已经无所不能，而在于建立了一套可以持续验证和改进 Agent 的工程闭环。

---

本次正文修订：2026-10-06，对照当前工作区源码；实验口径：2026-10-05 冻结发布包。原文备份、修改清单、配图提示词与验证记录保存于 docs/feishu-revision-20261006/。
