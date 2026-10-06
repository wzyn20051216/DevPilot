# 多 Agent 通信与输出协议

本项目的新任务默认采用 `multi_rag`：Planner → Coder → Tester → Reviewer。Planner、Coder、Reviewer 可使用 Hybrid RAG，Tester 依据修改和实际测试验证。单 Agent 模式保留为实验选项。

## 交接格式

编排器构造并校验 `AgentHandoff`，用一个 JSON 对象传递数据；不再靠拼接自然语言转交不同角色。原始任务的空白、换行及内容保留，执行计划的编号必须唯一且为正整数。

```json
{
  "protocol_version": "1.0",
  "source": "tester",
  "target": "coder",
  "phase": "repair",
  "task": "原始用户需求",
  "plan": [{"id": 1, "title": "实现", "description": "公开契约及验证方式", "status": "pending"}],
  "coder_report": {
    "status": "implemented",
    "summary": "实现摘要",
    "modified_files": ["module.py"],
    "changes": ["修改公开行为"],
    "risks": []
  },
  "test_report": {"passed": false, "summary": "目标测试失败", "stdout": "失败证据", "stderr": ""},
  "review_report": null,
  "repair_round": 1
}
```

阶段与来源/目标固定：用户到 Planner 为 planning；Planner/用户到 Coder 为 implementation；Coder 到 Tester 为 testing；Tester 到 Reviewer 为 review；Tester/Reviewer 到 Coder 为 repair。验证/审查必须有 Coder 报告，审查必须有通过的机器测试；返工必须有相应未通过的测试或审查反馈。

## 每个角色的输出

所有字段必填。新角色协议只接受一个 JSON 对象，拒绝 Markdown 围栏、前后解释、缺字段、未知字段以及字符串布尔值等隐式类型转换。每个角色的实际 JSON Schema 自动加入 system prompt，模型和程序校验使用同一份定义。

| 角色 | 必填字段 | 职责与约束 |
|---|---|---|
| Planner | summary、steps | summary/标题/说明非空；steps 至少一个，id 唯一、为正整数；不声明实现或测试已经完成 |
| Coder | status、summary、modified_files、changes、risks | status 为 implemented 或 blocked；文件列表必须匹配本轮真实写入记录；blocked 必须有原因；不得添加 passed/approved |
| Tester | passed、summary、stdout、stderr | 最后一次真实 run_test 为权威来源；机器结果覆盖模型自报；摘要说明目标测试范围或全套范围 |
| Reviewer | approved、summary、issues | approved=true 时 issues 为空；拒绝时必须给出具体问题；issues 必须为字符串数组 |

通用提示词说明输入来源、原始需求及计划不可丢失、工具权限、公开行为契约、失败分类及 JSON 转义；各角色追加定位/验收/审查的具体职责。任务文字中的内容不能修改角色权限和协议。

## 输出纠正与事实来源

- 普通工具交互仍使用 Tool Calling；收尾/格式纠正请求使用 JSON 对象模式及本地严格 Schema 校验。
- 最多一次禁止工具的格式纠正，禁止在此阶段改仓库或执行新测试。仍不合法时发出 protocol_error，禁止向下游交接。
- 格式纠正不能把审查拒绝改成批准、删除原审查问题，或把 Coder 的 blocked 改成 implemented。
- 兼容服务明确不支持 response_format 时，可回退到提示词约束，并在事件中记录兼容回退；本地 Schema 校验始终执行，其他 API 错误不做这种回退。
- Tester 有真实机器记录时，即使解释 JSON 纠正后仍坏，也可由程序生成严格报告；没有 run_test 记录时不能宣称验证通过。
- 后一次 run_test 超时/异常会使早先的通过记录失效。不能把传输错误或未完成测试当成绿色结果。
- 新协议角色恢复旧检查点时更新为当前角色 system prompt 与 Schema，避免旧自由文本约定继续影响交接；单 Agent 的历史上下文逻辑保留。

SSE 的既有 plan、test_report、review_report、repair_rounds 字段保持兼容；增加 handoff、coder_report、structured_output、output_validation 和明确的 failure_kind，便于观察协议检查与纠正情况。

## 返工和终态

Coder/任一角色出现 error、cancelled 或未产出有效报告时，停止下游执行。Coder blocked 明确报告任务阻塞。

测试失败交回 Coder；Reviewer 拒绝也交回 Coder，再运行 Tester、Reviewer。两种返工共享原有 max_repair_rounds（默认 2），不会无限循环；每次返工携带完整原始需求、计划及上游失败报告，每次 Coder 执行后重新验证，不沿用旧测试或审查批准。

明确的测试工具、收集或超时错误终止验证并报告 test_execution_error，不让 Coder 据此盲目修改实现。无法确认原因的正常测试失败仍不能被忽略。只有机器测试通过且 Reviewer 批准，编排器才产出最终 completed 事件。

## 验证与已知限制

本次离线后端回归：242 passed、27 skipped（未配置专用外部测试服务）；前端 8 项通过并构建成功。新增覆盖严格字段/类型、文件记录真实性、一次纠正、拒绝不可改成批准、测试事实优先、旧通过证据失效、交接顺序、测试/审查返工、默认 multi_rag、历史重置白名单与模型缓存。

真实 Flash 模型已执行一次自建加法缺陷：基准测试失败；四个角色首次输出都通过严格 Schema；候选修改后沙箱 3 项测试通过，编排器正常收尾。该次虽然启用了 RAG 工具，模型选择了局部读取而未调用 retrieve_code；检索工具另行验证，不把工具可见误称为检索已经执行。

单独通过真实 Repository MCP 调用 retrieve_code，在宿主机与 Docker 容器中都返回 calculator.py、test_calculator.py 两个相关片段。首次检索曾因冷启动超过 30 秒；现在模型优先读取本地缓存，缺缓存时允许首次下载，backend/worker 使用独立的 Hugging Face 模型缓存卷。容器缓存已准备并实际检索成功。缓存规则依据模型可用性，不涉及仓库名或题目 ID。

这验证通信与执行链，不是新的真实缺陷正确率或 90% 泛化证明。JSON 合法、流程完成仍不等同于业务修复正确，需要相关测试、独立审查及后续冻结评测。历史评测结果没有改写。

```powershell
Set-Location 'E:\desktop\DevPilot'
$env:API_KEYS = ''
$env:ALLOWED_REPO_ROOTS = ''
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -m pytest
```

上述环境设置只用于隔离测试进程，不修改部署凭据。早期 smoke 的轨迹与临时脚本已移出项目做本机归档；当前协议验收使用 `tests/backend/unit/test_multi_agent_protocol.py` 等回归，业务历史与冻结评测保持独立。

## 历史清空

`backend.scripts.reset_history` 默认只检查，传入 --apply 后先备份，再事务清空当前 SQLite 的 DevPilot 白名单表：任务、计划、事件、工具调用、来源、发布预览、队列、上下文和历史评测记录。不删除配置、源码、其他表或研究报告；无法确认库结构时拒绝删除。

本次本机与 Docker 版均配置 SQLite，分别清空 94 条旧任务及关联历史。未发现本项目 MySQL/Redis 配置或存储卷。Docker 清理时暂停本项目后端写入，重启后验证历史为空。之后新创建的任务保留，已发布的研究评测快照也保留。
