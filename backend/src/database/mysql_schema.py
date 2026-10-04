"""! @brief MySQL 8.0+ 建表语句。

与 ``connection.init_database`` 里的 SQLite 表结构一一对应，列名与语义保持一致，
仓储层 SQL 因而无需分叉。MySQL 与 SQLite 的差异在这里集中处理：

- 主键、唯一键、外键列不能用无长度的 TEXT，统一用 ``VARCHAR``；
- 大文本（事件 JSON、上下文检查点、PR 正文）用 ``MEDIUMTEXT/LONGTEXT``，
  避免 64KB 的 ``TEXT`` 上限截断；
- 不支持 ``CREATE INDEX IF NOT EXISTS``，索引内联在 ``CREATE TABLE`` 里；
- 排序规则用 ``utf8mb4_bin``，让 ID、状态等比较与 SQLite 一样区分大小写。

这是全新库的 DDL，已包含所有历史迁移列，不提供增量迁移。
"""

_TABLE_OPTIONS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"

MYSQL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS tasks (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        repo_path TEXT NOT NULL,
        question MEDIUMTEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        execution_mode VARCHAR(32) NOT NULL DEFAULT 'single_no_rag',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS plan_steps (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        task_id VARCHAR(64) NOT NULL,
        step_index INT NOT NULL,
        title TEXT NOT NULL,
        description MEDIUMTEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS agent_events (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        task_id VARCHAR(64) NOT NULL,
        sequence BIGINT NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        agent VARCHAR(64) NOT NULL,
        iteration INT NOT NULL,
        message MEDIUMTEXT NOT NULL,
        data_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY idx_agent_events_task_sequence (task_id, sequence),
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS tool_calls (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        task_id VARCHAR(64) NOT NULL,
        agent VARCHAR(64) NOT NULL,
        iteration INT NOT NULL,
        tool VARCHAR(128) NOT NULL,
        arguments_json LONGTEXT NOT NULL,
        result_preview MEDIUMTEXT NOT NULL,
        duration_seconds DOUBLE NOT NULL DEFAULT 0,
        succeeded TINYINT NOT NULL DEFAULT 1,
        created_at VARCHAR(40) NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS task_sources (
        task_id VARCHAR(64) NOT NULL PRIMARY KEY,
        source_type VARCHAR(64) NOT NULL,
        source_json MEDIUMTEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS publish_previews (
        task_id VARCHAR(64) NOT NULL PRIMARY KEY,
        owner VARCHAR(255) NOT NULL,
        repo VARCHAR(255) NOT NULL,
        base_branch VARCHAR(255) NOT NULL,
        head_branch VARCHAR(255) NOT NULL,
        commit_message TEXT NOT NULL,
        pr_title TEXT NOT NULL,
        pr_body MEDIUMTEXT NOT NULL,
        files_json MEDIUMTEXT NOT NULL,
        snapshot_hash VARCHAR(128) NOT NULL,
        status VARCHAR(32) NOT NULL,
        pr_url VARCHAR(2048) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS evaluation_results (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        run_id VARCHAR(64) NOT NULL,
        case_id VARCHAR(128) NOT NULL,
        variant VARCHAR(64) NOT NULL,
        repeat_index INT NOT NULL DEFAULT 1,
        success TINYINT NOT NULL,
        tests_passed TINYINT NOT NULL,
        tool_calls INT NOT NULL,
        iterations INT NOT NULL,
        repair_rounds INT NOT NULL,
        elapsed_seconds DOUBLE NOT NULL,
        prompt_tokens BIGINT NOT NULL,
        completion_tokens BIGINT NOT NULL,
        total_tokens BIGINT NOT NULL,
        llm_seconds DOUBLE NOT NULL DEFAULT 0,
        tool_seconds DOUBLE NOT NULL DEFAULT 0,
        estimated_cost DOUBLE NOT NULL DEFAULT 0,
        workspace_path TEXT NOT NULL,
        error TEXT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_evaluation_results_run_id (run_id)
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS task_queue (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        task_id VARCHAR(64) NOT NULL UNIQUE,
        idempotency_key VARCHAR(255) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'queued',
        priority INT NOT NULL DEFAULT 0,
        attempts INT NOT NULL DEFAULT 0,
        max_attempts INT NOT NULL DEFAULT 3,
        claimed_by VARCHAR(255) NULL,
        lease_expires_at VARCHAR(40) NULL,
        heartbeat_at VARCHAR(40) NULL,
        last_error TEXT NULL,
        fence_token BIGINT NOT NULL DEFAULT 0,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_task_queue_status (status, priority, id),
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS agent_contexts (
        task_id VARCHAR(64) NOT NULL,
        agent_name VARCHAR(64) NOT NULL,
        messages_json LONGTEXT NOT NULL,
        message_count INT NOT NULL DEFAULT 0,
        total_tokens INT NOT NULL DEFAULT 0,
        truncated TINYINT NOT NULL DEFAULT 0,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (task_id, agent_name),
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    ) {_TABLE_OPTIONS}
    """,
)
