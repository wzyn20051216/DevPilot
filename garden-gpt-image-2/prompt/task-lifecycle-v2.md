Use case: infographic-diagram
Asset type: DevPilot 全生命周期工程流程图，中文技术知识库主图，横版 16:9
Reference images: primarily use the latest attached system-blueprint image for its dominant top workflow, lower persistence / governance zones, bordered stage groups and colored routing. Borrow only the earlier MCP image's compact icon treatment and legend clarity. Do not copy either image's content, watermark, branding, or exact composition.

Primary request: 设计一张出版物级技术流程图，准确呈现 DevPilot 从任务输入、计划审批、Agent 执行、失败返工、独立审查，到人工确认发布 Draft PR 的完整生命周期。不要画成细长的一根流程线；使用四个清晰阶段容器、主流程和两条辅助泳道，让正文宽度下仍然可读。

Header, exact text:
Main title: “DevPilot 任务全生命周期”
Subtitle: “两次人工确认 · 最多两轮返工 · 事件持久化 · 快照一致性门禁”

Main layout: four large numbered stage containers across two rows, connected by solid navy arrows. Operation cards are rounded rectangles; decisions are diamonds; terminal states are compact colored pills. Blue = normal execution, amber = test / decision, teal = review / persistence, green = success, muted red = failure / blocked.

Stage ① exact header: “计划阶段”
Flow:
“输入本地任务 / GitHub Issue” → “Planner 分析仓库” → “生成结构化计划” → diamond “人工审批计划？”
Rejected branch: “拒绝” → red terminal “停止，不修改代码”
Approved branch: “批准” → Stage ②

Stage ② exact header: “受控执行”
Flow:
“后台启动 Agent 任务” → “Coder 读取并修改代码” → “Tester 在 Docker Sandbox 运行测试” → diamond “测试通过？”
Failure loop:
“否” → diamond “返工次数 < 2？”
“是” → “stdout / stderr 反馈给 Coder” → arrow back to Coder
“否” → red terminal “任务失败”
Passing branch: “是” → Stage ③

Stage ③ exact header: “独立审查”
Flow:
“Reviewer 审查 Git Diff” → diamond “approved = true？”
“否” → red terminal “任务失败并返回 issues”
“是” → green terminal “任务完成”
Small note: “Tester 与 Reviewer 不写代码”

Stage ④ exact header: “人工发布”
Flow:
“生成只读 PR Preview” → diamond “人工确认发布？”
“否” → gray terminal “保留本地结果”
“是” → “重新计算 Snapshot Hash” → diamond “代码快照一致？”
“否” → red terminal “阻止发布”
“是” → green terminal “创建 GitHub Draft PR”

Auxiliary swimlane A exact header: “状态与可观测性”
Show a SQLite cylinder receiving arrows from execution, testing and completion. Three stored records: “任务状态” / “Agent Events” / “Tool Calls”. Then arrow to “SSE 按 sequence 断线续传”. Make it visually clear SSE reads persisted events and never executes tasks.

Auxiliary swimlane B exact header: “协作式取消”
Flow: “用户请求取消” → “cancelling” → “模型 / 工具调用边界停止” → “cancelled”. Connect it from “后台启动 Agent 任务”.

Bottom legend, exact:
“实线：主流程” / “回环线：测试失败返工” / “青色线：事件持久化” / “红色：失败或发布阻断”

Text requirements: Simplified Chinese; render every quoted label exactly; preserve Planner, Agent, Coder, Tester, Docker Sandbox, Reviewer, Git Diff, PR Preview, Snapshot Hash, GitHub Draft PR, SQLite, Agent Events, Tool Calls, SSE, sequence, cancelling, cancelled exactly. No pseudo text, no logo, no watermark.
Accuracy constraints: Planner does not modify code; execution starts only after approval; at most 2 repair rounds; Tester and Reviewer do not write code; review happens only after tests pass; publishing happens only after task completion and a second human confirmation; Snapshot Hash mismatch blocks publishing; Draft PR is never automatically merged.
