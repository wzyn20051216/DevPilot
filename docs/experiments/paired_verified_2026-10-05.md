# Flash 真实仓库配对实验与预算诊断

**主实验已完成 12 次，追加预算诊断已完成 4 次。独立修复率持平，尚无证据证明动态策略带来稳定的整体性能提升。** 全部请求使用 Flash，没有调用 Pro。以下区分候选补丁质量、流程完成与资源消耗，不能仅凭 Token 较少宣布优化成功。

## 主实验结果

两道真实 Issue，每题每组重复三次，共六对、十二次。逐次结果见 [主实验 CSV](paired_verified_2026-10-05.csv)，登记、统计与核验见 [JSON](paired_verified_2026-10-05.json)。

| 指标 | 普通单 Agent `single_no_rag` | 动态工作流 `single_adaptive` |
|---|---:|---:|
| 独立测试通过 | 4/6（66.7%） | 4/6（66.7%） |
| 端到端成功 | 4/6 | 1/6 |
| 平均总 Token | 202,384 | 165,735 |
| 平均 Agent 阶段耗时 | 144.34 秒 | 138.59 秒 |
| 平均单任务墙钟耗时（含工作区准备、裁判） | 177.56 秒 | 171.31 秒 |

按题拆分，两组 Astropy 均为 1/3 独立测试通过，scikit-learn 均为 3/3。三次重复是同一道题的重复运行，不能当作三道独立题。

`tests_passed` 衡量候选补丁通过独立裁判；`success` 还要求执行链没有 error。动态组进一步要求机器工作流契约成立。主实验动态组仅 2/6 流程合规，其中一份候选未通过独立测试；另外三份候选虽通过独立测试，却缺少修改后同一 reproducer 的通过记录，部分还漏掉最后一次修改后的测试或最近测试未通过。因此 1/6 不能直接解释为补丁修复率退化，但它表明本配置下流程完成能力不足。

动态组所有尝试的平均 Token 少 18.1%，Agent 耗时少约 4.0%；完成状态不同，不能将此视为同等成果下的效率收益。两组独立测试均通过的四对中，动态组 Token 少 11.6%，Agent 耗时反而多 11.9%。两组端到端均成功仅一对，动态组 Token 多 46.5%、耗时多 103.2%；样本不足以泛化。Token 包含重复输入及缓存命中，比例也不等于实际账单比例。

Astropy 第三次基线触发 Agent 累计 Token 预算。剔除其**整对**做敏感性核验后，两组独立通过率仍同为 4/5；没有只删除某组失败来改善结果。

## 放宽预算诊断

按用户取消额外限额的要求，整批软预算已撤销；为保持主比较条件一致，十二次主实验仍使用原定 14 轮、250,000 Token 的 Agent 预算。随后另做四次诊断：双方统一 16 轮、取消额外 Agent Token 上限。该诊断使用已见过结果的同两道题、每组每题仅一次，不能合并进主比较，也不能用来估计新题泛化能力。逐次证据见 [诊断 CSV](flash16_diagnostic_2026-10-05.csv)。

| 实例 | 基线独立测试 / 端到端 | 动态独立测试 / 端到端 | 基线 / 动态总 Token |
|---|---|---|---:|
| `astropy__astropy-13977` | 失败 / 失败 | 失败 / 失败 | 250,758 / 217,742 |
| `scikit-learn__scikit-learn-26323` | 通过 / 成功 | 通过 / 成功 | 149,511 / 148,852 |

双方独立测试与端到端成功均为 1/2。Astropy 基线未生成候选补丁，动态组仍缺少通过的修改后复现与测试；scikit-learn 动态组完成了机器验证闭环。仅放宽资源不能解释或解决全部失败；这次诊断仍不支持稳定的修复率提升。唯一共同成功对中动态 Token 少约 0.44%、Agent 耗时少约 11.0%，同样只是单次观察。

本轮十六次新运行合计报告 **2,975,576 Token**。此前一次供应商 402 余额中断的 3,213 Token 单独保留，不计修复率；它不是 Codex 五小时限额。本机没有配置聊天自动唤醒。

## 已冻结的条件与边界

普通单 Agent 与动态工作流共用同一内核、模型接口和工具实现。动态组增加候选比较、修改前后复现与测试门禁，并按任务特征决定结构导航。快照包含接续前保留的工作流 WIP，比较的是策略组合，不是两个已发布版本，也不衡量队列、MySQL、鉴权改动的修复能力收益。

- 请求模型名 `deepseek-v4-flash`，temperature=0.2；reasoning_effort 未显式指定；未设置模型随机种子。官方当前文档说明旧别名路由到 DeepSeek-V4.1-Flash；本轮没有单独归档响应的实际 model 字段，因此只保证请求配置相同，不能声称固定了供应商模型快照。参见 [DeepSeek 模型与价格说明](https://api-docs.deepseek.com/quick_start/pricing/)。
- 主实验工具交互上限双方均为 14 轮，每个 Agent 累计 Token 预算 250,000。预算是累计软阈值，单次模型响应可以超过；收尾按原内核规则执行并计入用量与耗时。
- 每题配对次序跨重复交替 AB/BA，两个题的全局顺序均衡。整批软预算在八条完成后、新配对开始前暂停过一次；按用户要求取消后从原快照继续，没有删改完成记录。
- 模型只接收原始 Issue 和基准代码；官方测试补丁、参考修复仅交给独立裁判。候选不能修改测试或测试配置，独立裁判在干净基准上重新应用候选与官方测试。
- 运行使用冻结代码、固定数据集缓存与镜像 ID、独立数据库；续跑校验指纹，不访问演示服务的业务 SQLite。耗时列是每任务口径，整批启动与暂停间隔不计入。

本机历史报告没有这两道题的完整模型结果，但已有环境校准尝试，不能保证无人看过题或模型预训练未包含题目。使用的是本项目文件级 verifier，**不是 SWE-bench 官方 harness 的 resolved 指标**。两道题的重复运行只能支持小样本观察。

## 环境校准与检查

| 实例 | 原始代码 + 官方测试 | 官方修复 + 官方测试 | 状态 |
|---|---|---|---|
| `astropy__astropy-13977` | 20 failed / 322 passed | 342 passed | 有效；两边另有 4 skipped、1 xfailed |
| `scikit-learn__scikit-learn-26323` | 1 failed / 188 passed | 189 passed | 有效 |
| `psf__requests-2317` | 失败 | 仍有 FAIL_TO_PASS 失败 | 禁网条件下无效，模型运行前排除 |

此前已修复 pytest 颜色码与 scikit-learn 的 `-rN` 关闭短摘要造成的判分缺陷，裁判去除颜色码并传入 `--color=no -r fE`；补齐三个 bare 缓存的空 `refs` 目录以恢复 clone。这些是评测环境修复，不是 Agent 能力提升。

评测准备阶段后端回归为 **189 passed、15 skipped、0 failures、0 errors**；15 项依赖测试 MySQL/Redis，未启动相应服务而跳过。顺序边界与额度续跑的相关八项测试随后全部通过，中断测试不调用模型。本次模型运行后逐项复核十六份候选补丁哈希、失败保留与公共摘要计数；冻结源代码未变，没有排除候选运行的额外失败节点。本次未修改产品代码，未再次重跑后端全套。

## 本机证据与复核

主批次 `paired_verified_20261005_v6`、诊断批次 `flash16_diag_20261005` 均已完成。完整轨迹、补丁、校准与逐文件指纹位于 Git 忽略的本机目录；公开 CSV/JSON 保留指标与补丁哈希，不包含凭据或模型对话。

```text
主实验：E:\desktop\DevPilot\backend\data\paired_real_world\paired_verified_20261005_v6
预算诊断：E:\desktop\DevPilot\backend\data\paired_real_world\flash16_diag_20261005
源码 SHA-256：3a6848d188c35b695d0e641a50547ce2e6b40faa9ae1b04af4a2e6d5d5d969ce
数据 SHA-256：a10f66810a84bdfb40479421cd2992682ad7cd96e69e7acd88322e87e5b8748a
```

以下命令只复核已冻结的主实验配置与校准，不发起模型请求：

```powershell
Set-Location 'E:\desktop\DevPilot\backend\data\paired_real_world\paired_verified_20261005_v6\source'
$env:PYTHONIOENCODING = 'utf-8'
& 'E:\desktop\DevPilot\.venv\Scripts\python.exe' -u -m backend.scripts.paired_real_world `
  --data-root 'E:\desktop\DevPilot\backend\data' `
  --env-file 'E:\desktop\DevPilot\backend\.env' `
  --run-id paired_verified_20261005_v6 `
  --instance astropy__astropy-13977 `
  --instance scikit-learn__scikit-learn-26323 `
  --repeats 3 --iterations 14 --audit-only
```

现有证据优先指向工作流完成与复现记录缺口。后续若修改门禁，应先验证编辑前后同一 reproducer 和最后一次编辑后的测试记录，再冻结实现，在新题上做配对；不要针对这两题反复调参后宣称泛化提升。
