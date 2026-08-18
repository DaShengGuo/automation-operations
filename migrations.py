"""
migrations.py
DatabaseMigrationManager — SQLite schema 版本化迁移。

规则:
  - schema_version 存在 PRAGMA user_version
  - 每次升级 schema: MIGRATIONS 追加一条 (version, sql)
  - 升级前自动备份: backups/runtime_v{N}_before_upgrade.db
  - 迁移失败: 停止继续, 记录错误, 保留备份, 绝不删除数据库重建
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 旧库列补齐表: 表名 → {列名: "ALTER TABLE ADD COLUMN 定义"}
_LEGACY_COLUMNS = {
    "accounts": {
        "password": "password TEXT NOT NULL DEFAULT ''",
        "status": "status TEXT NOT NULL DEFAULT 'PENDING'",
        "device_serial": "device_serial TEXT NOT NULL DEFAULT ''",
        "retry_count": "retry_count INTEGER NOT NULL DEFAULT 0",
        "max_retry": "max_retry INTEGER NOT NULL DEFAULT 3",
        "last_error": "last_error TEXT NOT NULL DEFAULT ''",
        "created_at": "created_at REAL NOT NULL DEFAULT 0",
        "updated_at": "updated_at REAL NOT NULL DEFAULT 0",
        "started_at": "started_at REAL",
        "finished_at": "finished_at REAL",
    },
    "task_results": {
        "duration_sec": "duration_sec REAL NOT NULL DEFAULT 0",
        "failed_step": "failed_step TEXT NOT NULL DEFAULT ''",
        "error": "error TEXT NOT NULL DEFAULT ''",
        "retry_count": "retry_count INTEGER NOT NULL DEFAULT 0",
        "screenshot": "screenshot TEXT NOT NULL DEFAULT ''",
    },
}

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status)",
    "CREATE INDEX IF NOT EXISTS idx_accounts_device ON accounts(device_serial)",
    "CREATE INDEX IF NOT EXISTS idx_results_device ON task_results(device_serial)",
    "CREATE INDEX IF NOT EXISTS idx_results_started ON task_results(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_account_runs_run ON account_runs(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_account_runs_started ON account_runs(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_state_events_run ON state_events(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id)",
)


def _migrate_v1(conn: sqlite3.Connection):
    """v1: 旧库列补齐(ALTER ADD, 不丢数据) + 索引(幂等)。"""
    for table, columns in _LEGACY_COLUMNS.items():
        try:
            existing = {r[1] for r in
                        conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue  # 表不存在 → 由 SCHEMA 建
        for col, ddl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    for sql in _INDEXES:
        conn.execute(sql)


# 迁移列表(只追加, 不修改已发布条目)
MIGRATIONS: list[tuple[int, str, Optional[Callable[[sqlite3.Connection], None]]]] = [
    # (目标版本, 说明, 迁移函数)
    (1, "初始 schema + 旧库列补齐 + 索引", _migrate_v1),
]

SCHEMA_VERSION = MIGRATIONS[-1][0] if MIGRATIONS else 1


def _backup_db(db_path: Path, backup_dir: Path) -> Optional[Path]:
    """迁移前备份数据库(仅对非空旧库)。"""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{db_path.stem}_v{_read_version(db_path)}_before_upgrade.db"
    try:
        shutil.copy2(db_path, backup)
        return backup
    except OSError as e:
        logger.error(f"[迁移] 数据库备份失败: {e}")
        return None


def _read_version(db_path: Path) -> int:
    try:
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def migrate(db_path: Path, backup_dir: Path,
            create_schema: Callable[[sqlite3.Connection], None]) -> dict:
    """执行待应用的迁移。返回 {ok, from_version, to_version, backup, error}。"""
    result = {"ok": True, "from_version": 0, "to_version": 0,
              "backup": None, "error": ""}

    from_version = _read_version(db_path)
    if from_version >= SCHEMA_VERSION:
        result["from_version"] = from_version
        result["to_version"] = from_version
        return result

    backup = _backup_db(db_path, backup_dir)
    result["backup"] = str(backup) if backup else None
    result["from_version"] = from_version

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # 基线: 由 Database 的 SCHEMA 负责建表(幂等 IF NOT EXISTS)
        create_schema(conn)
        for version, _desc, fn in MIGRATIONS:
            if version <= from_version:
                continue
            try:
                if fn is not None:
                    fn(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
            except sqlite3.Error as e:
                result["ok"] = False
                result["error"] = f"迁移到 v{version} 失败: {e}"
                logger.error("[迁移] %s (备份保留: %s)",
                             result["error"], result["backup"])
                return result
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        result["to_version"] = SCHEMA_VERSION
        logger.info("[迁移] schema v%s → v%s (备份: %s)",
                    from_version, SCHEMA_VERSION, result["backup"] or "无(新库)")
    except sqlite3.Error as e:
        result["ok"] = False
        result["error"] = f"迁移异常: {e}"
    finally:
        conn.close()
    return result


def open_with_migration(db_path: Path, backup_dir: Path,
                        create_schema: Callable[[sqlite3.Connection], None]
                        ) -> dict:
    """Database 初始化时调用: 迁移后返回连接配置信息。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return migrate(db_path, backup_dir, create_schema)
