# 取消任务队列收尾修复（2026-10-05）

取消后的任务原本正确进入业务 `cancelled`，但 Worker 把所有非 `completed` 结果当作可重试失败，重新放入 `queued`；后续领取只会因业务状态被拒绝，却仍消耗 attempts。现已为队列结算增加默认开启的 `retryable` 参数：取消结果明确传 false，立即进入队列 `dead`，记录“任务已取消”，清空领取者、租约与心跳，业务仍为 `cancelled`。取消不会伪报成功，普通故障继续按原上限重试。

修复覆盖单次 `run_worker_once`、持续 Worker 的 `_settle_running`，以及领取后发现业务已经取消的竞争路径。SQL/Redis 收口都继续先校验 owner、fence_token 与未过期租约；不可重试标记不授予旧 Worker 越权结算能力。没有增加新队列状态或迁移数据库。

## 验证

- SQLite、真实 MySQL、真实 Redis 的相同用例验证三种 Worker 场景：执行中取消、持续 Worker 收尾、领取与取消竞争。新取消任务尝试次数为 1，额外领取为零；取消竞争不会构造运行器。
- 三后端分别验证错误 owner、错误 token、过期租约不能执行不可重试收尾；既有普通失败有限重试、背压与优先级用例继续通过。
- 针对性队列/执行测试 **62 passed、0 skipped**。
- 后端全套回归 **234 passed、0 skipped、0 failures、0 errors**，其中新增三后端共 18 项取消及 fencing 场景。
- 本机真实 API + 独立 Worker 进程，单次与持续模式各一次：约 0.89/0.95 秒进入 `cancelled/dead`；均为一次 start、一次 cancelled，attempts=1，后续正常任务仍为 `completed/done`。可控运行器不调用模型。

全套回归统计与原始复测摘要见 [JSON](cancellation_fix_2026-10-05.json)。旧[四项工程报告](engineering_evaluation_2026-10-05.md)及 JSON 继续保留修复前的观测，不把旧 queued 记录改写成通过。

## 复现

运行目录使用专用数据库，不能指向演示或生产存储。输出要求新目录：

```powershell
Set-Location 'E:\desktop\DevPilot'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -m backend.scripts.verify_worker_cancellation `
  --output 'E:\desktop\DevPilot\backend\data\cancel_fix\rerun' --port 18019
```

本轮原始日志、JUnit 与隔离 SQLite 保存在：

```text
E:\desktop\DevPilot\backend\data\cancel_fix\20261005
```

没有操作既有演示数据库，也没有消耗付费模型额度。任务副作用无法抢占、租约校验到业务写入仍有竞争窗口等既有边界未被这项修复改变；取消后的队列 dead 表示消费终止，不能据此统计业务失败。
