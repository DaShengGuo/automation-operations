"""
storage/database.py
SQLite 连接管理 — WAL 模式 + 多线程安全
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING',
    device_serial TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retry INTEGER NOT NULL DEFAULT 3,
    last_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    account TEXT NOT NULL,
    device_serial TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    duration_sec REAL NOT NULL DEFAULT 0,
    failed_step TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    screenshot TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- 桌面版扩展: 运行批次/账号执行/设备会话/状态事件/错误(IF NOT EXISTS 幂等)
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    chat_source TEXT NOT NULL DEFAULT '',
    device_count INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS account_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    account_id INTEGER,
    masked_account TEXT NOT NULL,
    device_serial TEXT NOT NULL DEFAULT '',
    device_model TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    finished_at REAL,
    duration REAL NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '',
    last_state TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_account_runs_run ON account_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_account_runs_started ON account_runs(started_at);

CREATE TABLE IF NOT EXISTS device_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    device_serial TEXT NOT NULL,
    device_model TEXT NOT NULL DEFAULT '',
    connected_at REAL NOT NULL,
    disconnected_at REAL,
    state TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    device_serial TEXT NOT NULL DEFAULT '',
    masked_account TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_events_run ON state_events(run_id);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    device_serial TEXT NOT NULL DEFAULT '',
    masked_account TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    message_zh TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    traceback TEXT NOT NULL DEFAULT '',
    screenshot TEXT NOT NULL DEFAULT '',
    hierarchy TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    app_version TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id);
"""


class Database:
    """线程安全 SQLite 封装。每线程独立连接，共享同一数据库文件(WAL)。

    backup_dir 提供时执行 schema 迁移(升级前自动备份, 失败不破坏旧库)。
    """

    def __init__(self, db_path: Path | str, backup_dir: Path | str = ""):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()

        if backup_dir:
            # 迁移优先(备份 → 建表 → 版本推进); 失败时保留备份并记录
            import migrations
            mig = migrations.open_with_migration(
                self.path, Path(backup_dir),
                lambda c: c.executescript(SCHEMA))
            if not mig["ok"]:
                import logging
                logging.getLogger(__name__).error(
                    "[数据库] 迁移失败: %s (备份: %s)",
                    mig["error"], mig["backup"])
                # 不破坏旧库: 继续以现有表运行(新表缺失时相关功能降级)
        # 建表(幂等)
        conn = self._connect()
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """当前线程的连接（懒创建）"""
        if not hasattr(self._local, "conn"):
            self._local.conn = self._connect()
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchone()

    def close(self):
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
