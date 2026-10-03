Use case: infographic-diagram
Asset type: DevPilot 中文技术知识库的开篇总览图，横版 16:9，读者为 AI 应用 / Agent 开发工程师
Reference images: primarily use the latest attached system-blueprint image as the benchmark for a top main chain plus lower supporting domains, bordered subsystem groups, routing clarity and engineering density. Use the earlier MCP image only for typography and icon polish. Do not copy either image's content, watermark, branding, or exact arrangement.

Primary request: 设计一张出版物级、课程讲义级的高密度技术信息图，一页准确讲清 DevPilot 是什么。整体像资深信息设计师为工程团队制作的架构教学页：白色背景、海军蓝主标题、钴蓝模块标题、青绿色成功链路、琥珀色验证节点；细描边圆角卡片；统一线性 / 轻拟物图标；清晰编号；高信息密度但不拥挤。禁止 3D 科幻场景、机器人吉祥物和装饰性代码雨。

Header text, render verbatim:
Main title: “DevPilot 可执行 AI 软件工程 Agent”
Subtitle: “把模型推理约束为可审批、可执行、可验证、可追踪的软件工程流程”

Composition:
- Use a rigorous five-column architecture across the center, with a single left-to-right numbered flow ①→②→③→④→⑤.
- Each column is a large outlined container with one clear header, 2–4 nested cards, one meaningful icon per card, and short supporting copy.
- Main request / execution arrows are solid blue; verification/result arrows are solid green; tool calls are dashed violet. Put a compact legend at bottom right.

Exact module content:
① “任务入口”
  cards: “本地开发任务” / “GitHub Issue”
  note: “真实代码仓库 + 明确需求”
② “计划与人工审批”
  cards: “Planner 分析仓库” / “生成结构化计划” / “用户批准后才执行”
  safety badge: “未批准，不修改代码”
③ “Agent 执行流水线”
  three vertically stacked role cards with arrows:
  “Coder｜读取并修改代码”
  “Tester｜Docker Sandbox 隔离测试”
  “Reviewer｜独立审查 Git Diff”
  loop label between Tester and Coder: “失败反馈，最多返工 2 次”
④ “受控工具与上下文”
  cards: “Repository MCP” / “安全写文件工具” / “Hybrid Code RAG” / “Docker Sandbox”
  note: “模型不能直接访问宿主机 Shell”
⑤ “证据闭环与发布”
  cards: “SQLite Agent Events” / “Tool Calls” / “独立 Verifier” / “PR Preview → Draft PR”
  release badge: “二次确认 + Snapshot Hash”

Footer: three equal principle cards with icons:
“权限边界｜路径、角色、命令三层约束”
“结果可证｜真实测试裁决，不采信模型自述”
“过程可追｜事件持久化，SSE 支持断线续传”

Text requirements: Simplified Chinese. Render every quoted string exactly; preserve English terms exactly. Use a clean Chinese sans-serif font. Keep body copy at readable size. No pseudo text, no extra modules, no autonomous deployment, no automatic merge.
Visual requirements: premium Chinese technology-education infographic, clean grid, consistent 8 px rhythm, generous outer margins, subtle pale-blue background bands, iconography similar in density and clarity to the reference. No logo, no watermark, no signature.
