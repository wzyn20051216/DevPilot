# 真实仓库配对实验：等待模型额度

**尚未取得完整 A/B 结果，不能判断修复率、Token 或延迟是否提升。**

本轮实际调用了 `deepseek-v4-flash`。首项 Astropy 动态组任务在第 2 轮收到供应商 `402 Insufficient Balance`，中断前累计 3,213 Token、Agent 阶段 26.85 秒，没有完成候选补丁。此记录是额度中断，不能算作算法修复失败，更不能与旧结果计算性能提升。这不是 Codex 的五小时用量窗口；本次没有配置自动恢复聊天。

## 已冻结的比较条件

普通单 Agent（`single_no_rag`）与动态工作流（`single_adaptive`）共用同一内核、模型接口和工具实现。动态组增加候选比较、修改前后复现与测试门禁，并按任务特征决定结构导航。快照包含接续前保留的工作流 WIP，因此本实验比较的是策略组合，不是两个已发布版本，也不衡量队列/MySQL/鉴权改动的修复能力收益。

- temperature=0.2；工具交互上限两组均为 14 轮；每个 Agent 累计 Token 预算两组均为 250,000。收尾请求按内核原有规则执行，计入 Token/耗时；未设置模型随机种子。
- 每题两组各重复 3 次，按配对任务交替 AB/BA 次序。最终有效计划是 2 题 × 2 组 × 3 次，共 12 次。
- 模型只接收原始 Issue 和基准代码；官方测试补丁、参考修复仅交给独立裁判。候选不能修改测试或测试配置。
- 记录修复 success、独立 tests_passed、Token、工具次数、Agent 耗时，以及包含工作区准备和裁判的墙钟耗时。额度/限流中断另存、保留原轨迹、恢复后重跑原任务。
- 运行使用冻结源代码、固定数据集缓存和镜像 ID、独立数据库。续跑会拒绝代码、数据或配置指纹变化；不访问演示服务的业务 SQLite。

本机历史 `report.json` 没有下面两道题的完整模型结果，但已有环境校准尝试；因此应称为本机尚未完成模型评测的题，不能保证完全未参与历史人工查看或模型预训练。

## 环境校准

| 实例 | 原始代码 + 官方测试 | 官方修复 + 官方测试 | 状态 |
|---|---|---|---|
| `astropy__astropy-13977` | 20 failed / 322 passed | 342 passed | 有效；两边均另有 4 skipped、1 xfailed |
| `scikit-learn__scikit-learn-26323` | 1 failed / 188 passed | 189 passed | 有效 |
| `psf__requests-2317` | 失败 | 仍有 FAIL_TO_PASS 失败 | 禁网条件下无效，模型执行前排除 |

本轮修复了两处评测缺陷：pytest 颜色控制码导致失败行未被识别；scikit-learn 的 `-rN` 配置关闭短摘要，导致目标失败无法匹配。裁判现在去除颜色码，并明确传入 `--color=no -r fE`。还补齐了三个 bare 缓存缺失的空 `refs` 目录，解决本地 clone 失败。这些是环境/判分修复，不能作为 Agent 能力提升证据。

后端全套回归为 **189 passed、15 skipped、0 failures、0 errors**；15 项依赖测试用 MySQL/Redis 服务，本轮未启动这些服务，按既有条件跳过。随后补充了偶数题数下的 AB/BA 顺序边界测试，相关 8 项全部通过。402 续跑测试模拟供应商中断后恢复，确认原任务重试、已完成结果保留、中断轨迹不会被覆盖；此测试不调用模型，不能作为修复能力证据。JUnit 报告保存在待运行目录。

两个有效实例的官方修复均退出 0，没有排除额外失败节点。但使用的是本项目文件级 verifier，仍不是 SWE-bench 官方 harness 的 resolved 指标。即使 12 次完成，只有 2 道题，结果也只能支持小样本观察，不能宣称稳定的泛化收益。

## 证据与续跑

摘要与登记条件见 [JSON](paired_verified_2026-10-05.json)。模型中断原始记录在本机 `backend/data/paired_real_world/paired_verified_20261005_v3/`；有效校准在 `paired_verified_20261005_v4/`。待运行批次 `paired_verified_20261005_v6` 已验证能读取登记内容，校准来源与其 `backend/src` 字节完全一致，模型结果列表为空。

冻结代码 SHA-256：`3a6848d188c35b695d0e641a50547ce2e6b40faa9ae1b04af4a2e6d5d5d969ce`。完整快照、逐文件指纹和报告保存在被 Git 忽略的本机运行目录。应从该快照续跑，避免混入随后发生的产品代码改动。

额度恢复后，在 PowerShell 执行以下命令。它调用模型并继续已登记的 12 次任务；增加 `--audit-only` 只检查配置与校准，不调用模型。

```powershell
Set-Location 'E:\desktop\DevPilot\backend\data\paired_real_world\paired_verified_20261005_v6\source'
$env:PYTHONIOENCODING = 'utf-8'
& 'E:\desktop\DevPilot\.venv\Scripts\python.exe' -u -m backend.scripts.paired_real_world `
  --data-root 'E:\desktop\DevPilot\backend\data' `
  --env-file 'E:\desktop\DevPilot\backend\.env' `
  --run-id paired_verified_20261005_v6 `
  --instance astropy__astropy-13977 `
  --instance scikit-learn__scikit-learn-26323 `
  --repeats 3 --iterations 14
```

最终分析应比较完整配对结果，并单独报告“两组均成功”的任务成本；不能因为某组更早失败、花费更少，就宣称它更高效。本轮没有修改简历中的性能百分比。
