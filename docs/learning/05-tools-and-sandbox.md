# 第05章 · Agent内核、工具权限与沙箱

[← 第04章](04-architecture.md) · [课程目录](README.md) · [第06章 →](06-protocol-and-reliability.md)

> 本章目标：从概念读到实现。下列终端命令默认在仓库根目录执行。

## 学习目标

- 理解共享Tool Calling内核与角色白名单。
- 掌握路径、写入和命令的执行边界。
- 识别容器隔离和宿主Docker socket的不同风险。

---

## 5.1 BaseToolAgent：统一的工具循环

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

## 5.2 两层工具权限（纵深防御）

图 4｜纵深防御：角色权限、路径校验与 Docker 执行边界

![图 4：纵深防御分层图](../assets/learning/04-security.png)

- 第一层在 get_tool_definitions()：模型只能看见当前角色允许的工具。

- 第二层在 execute_tool()：即使手工构造了未授权的 Tool Call，执行时仍会抛出 PermissionError。

工具权限按各角色构造函数固定：Planner 使用 list_files、read_file、search_code；Coder 另外可 write_file、replace_in_file、git_diff、protocol_probe；Tester 使用 git_diff、read_file、search_code、run_test、run_command；Reviewer 使用 read_file、search_code、git_diff。启用 RAG 时仅 Planner／Coder／Reviewer 增加 retrieve_code，Tester 不增加。Coder 没有 run_test／run_command，Tester／Reviewer 不能写入。模型看见的工具和执行时再次校验使用同一白名单。

## 5.3 文件安全

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

## 5.4 Docker Sandbox（隔离执行）

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

## 源码导航

- [registry.py](../../backend/src/tools/registry.py)
- [file_tool.py](../../backend/src/tools/file_tool.py)
- [write_tool.py](../../backend/src/tools/write_tool.py)
- [docker_runner.py](../../backend/src/sandbox/docker_runner.py)

## 动手与自检

1. 列出Coder、Tester、Reviewer的不同权限。
2. 说明解释器白名单为何不能替代容器隔离。
3. 运行uv run python -m pytest tests/backend/unit/test_file_tool.py tests/backend/unit/test_enhanced_toolkit.py。

<details>
<summary>展开参考答案</summary>

Coder可写已有文件但没有run_test/run_command；Tester可测试与受限命令但不能写文件；Reviewer只读审查。Python/Node能执行容器内代码，禁网、只读和资源限制共同约束副作用。

</details>

---

[← 第04章](04-architecture.md) · [返回目录](README.md) · [第06章 →](06-protocol-and-reliability.md)
