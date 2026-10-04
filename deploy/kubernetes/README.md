# Kubernetes 部署示例说明

本目录是 API/Worker 分离的结构示例，尚未验收真实集群。启动、迁移及边界见 [部署文档](../../docs/deployment.md)。

- ConfigMap 默认 MySQL 共享业务状态、Redis Lua 队列，并设置仓库白名单及容量限制。
- API 和 Worker 引用同一 devpilot-secrets；用 secret.example.yaml 创建自己的配置，真实凭据不得提交。
- 准备 namespace、实际应用镜像、MySQL/Redis 服务、workspace PVC、TLS 证书和网络策略后再部署。
- MySQL 与 Redis 共享状态不会同步仓库文件；远端 Docker daemon 必须访问一致的挂载路径。
- Redis 不支持 Cluster；租约过期隔离后显式恢复，不承诺自动切换或恰好一次副作用。
- 执行层租约校验存在校验到写入的竞争窗口；没有仓库互斥或多租户隔离。
- 当前为常驻 Worker，没有每任务 Job 调度器或 Firecracker 适配器。不能把任意 microVM 地址直接当 DOCKER_HOST。

建议先在专用测试环境验证执行、取消、SSE 重连、API 重启、租约隔离与迁移，再进行真实多主机故障演练。
