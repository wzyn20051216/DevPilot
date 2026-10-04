# Kubernetes 部署示例说明

本目录是 API/Worker 分离的**结构示例**，不是已验收的生产部署包。请先阅读 [部署文档](../../docs/deployment.md)。

- 当前默认执行方式为 inline；示例队列配置需检查与实际环境一致。
- 即使选择 Redis，任务、事件、检查点仍写 SQLite，不能假定多个 Pod 自动共享状态。
- Redis 后端没有原子领取和故障恢复保证；SQLite 租约到期采用隔离后显式恢复，不是自动重投。
- 需要实际准备 namespace、应用镜像、Secret、PVC、证书和网络策略，再替换 YAML 中的占位值。
- 远端 Docker daemon 要能访问与 Worker 一致的仓库路径，并具备所需沙箱镜像。
- API 未实现鉴权；多副本、故障恢复、容量和远端沙箱路径尚未完成部署验收。
- 当前为常驻 Worker 轮询，没有每任务 Job 调度器，也没有 Firecracker 适配器。不能把 Fly Machines 或 AWS Lambda 地址直接当成兼容 Docker API 的 DOCKER_HOST。

建议先用单机 SQLite + 一个 API + 一个 Worker 完成执行、取消、断线重连和租约隔离验收，再设计统一数据库、fencing 与多副本部署。
