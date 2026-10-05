# 动态策略端到端链路修复与单题实验

本次目标是减少「代码已修对但验证/收尾未完成」的执行失败。90% 以上是后续多题评测目标，本次只做一道已见过的真实题，不能据此估计总体成功率。

## 实现与验收口径

- 内核保存修改前已失败的 reproducer 参数，每批编辑完成后自动原样重跑；复用既有工具权限、沙箱、事件、Token/耗时统计和工具观测通道，不增加模型交互轮次。
- 同一批多个编辑只复验一次；以后再次编辑仍会使旧复验与测试证据失效。自动复验失败时仍阻止完成，允许模型继续修复代码。
- 全量测试通过后，仅在工作流契约也满足时关闭工具调用，消除「缺少复验却无法再调用工具」的死锁。
- 普通代码缺陷也获得与门禁一致的 reproducer 提示；问题同时包含文档和行为关键词时，行为验证优先。
- 每轮提示剩余验证项；复验已通过但尚未测试时，最后三轮优先开放 `run_test`。已有测试失败时仍开放定位与修复工具。
- Windows 独立工作区在首次 checkout/reset 前配置 `core.longpaths=true`，修复深层评测目录导致的裁判启动失败；没有修改全局 Git 配置。

`strategy=None` 的历史提示词和工具集快照保持一致。新增回归覆盖原样自动复验、同批多次写入、失败复验不可被通过的测试掩盖、完整工具消息配对、全量测试后补齐门禁、分类优先级及长路径设置顺序。

## 真实单题实验

题目：SWE-bench Verified 的 `scikit-learn__scikit-learn-26323`。该题已经用于之前的配对实验，本次属于链路回归，不是未见题泛化验证。

模型请求配置为 `deepseek-v4-flash`，temperature=0.2，16 轮，无额外 Agent Token 上限，不启用 RAG。源码冻结并保存逐文件 SHA-256；使用独立数据库。Agent 只收到原始 Issue 和基准仓库，官方测试及参考修复只用于环境校准和独立裁判。

首次运行 `f9487a0db9d0464fa1aeace18b43ad97`：

- 环境校准：基准失败、参考修复通过。
- Agent 修改一个生产文件，新增 3 行；失败探针签名与编辑后自动通过探针一致。
- Agent 目标测试 188 项通过，工作流合规并在第 11 轮正常收尾；103,407 Token，Agent 阶段 84.04 秒。
- 独立裁判在创建更长名称的工作区时触发 Windows `Filename too long`，原始报告保留为 `success=false / tests_passed=false`。
- 修复裁判工作区的长路径配置后，原补丁独立重放为 **189 passed**，未排除测试，新增模型调用为 0。补丁 SHA-256：`dbf960f79f1a1f1885e8a9eec97da4ddd2dfe055c04f7f140e815969380ed301`。

随后进行一次从头执行的确认运行 `4ed42e2982b646bfbe0015bbe93cfd8f`：原始代码失败、参考修复通过；动态 Agent 在第 8 轮正常收尾，同一 reproducer 自动通过、目标测试 **188 passed**；独立裁判 **189 passed**，无测试排除，`success=true / tests_passed=true / compliant=true`。Agent 阶段为 63.09 秒、87,529 Token、12 次工具调用。该次与首次生成的生产补丁哈希一致。

两次真实模型运行共 190,936 Token。首次失败未被覆盖；原始两次报告的 success 为 0/1 与 1/1，原补丁独立重放另行记录，不能把重放算作第三次模型成功，也不能把同题重复当作两道独立题。公共指标及证据校验见 [JSON](adaptive_e2e_2026-10-05.json)。

## 验证与已知限制

修改及长路径修复后的后端回归：**201 passed、15 skipped、0 failures**。15 项为需要专用 MySQL/Redis 测试配置的集成测试；未为本次链路修改启用这些配置。针对性回归为 55 passed。

这里使用文件级独立 verifier，不是 SWE-bench 官方 harness 的 resolved 指标。单题确认只能证明这条链路能够完成，不能证明动态策略已有 90% 以上总体成功率，也不能证明跨题修复能力提升。本次没有运行前端 UI、队列或发布 PR 全流程实验。

## 本机证据与复现

```text
首次完整轨迹、补丁、环境校准及原始失败报告：
E:\desktop\DevPilot\backend\data\e2e_improvement\smoke_20261005
原补丁独立重放结果：
E:\desktop\DevPilot\backend\data\e2e_improvement\smoke_20261005\verifier_replay.json
环境修复后的从头确认：
E:\desktop\DevPilot\backend\data\e2e_improvement\confirm_20261005
修改前源码备份：
E:\desktop\DevPilot\backend\data\e2e_improvement\before
```

以下命令仅运行离线回归，不调用模型：

```powershell
Set-Location 'E:\desktop\DevPilot'
& '.\.venv\Scripts\python.exe' -m pytest
```

使用现有生产入口再次做单题实验会调用已配置的模型及 Docker，并生成新工作区和报告：

```powershell
Set-Location 'E:\desktop\DevPilot'
& '.\.venv\Scripts\python.exe' -m backend.src.evals.real_world `
  --dataset verified --instance scikit-learn__scikit-learn-26323 `
  --variant single_adaptive --repeats 1
```

本次实际使用的隔离配置启动脚本及冻结源码保存在上述本机证据目录，避免向业务数据库写入实验数据。
