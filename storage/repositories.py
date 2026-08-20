"""
storage/repositories.py
账号仓库 + 任务结果仓库

账号领取采用 SQLite 事务原子锁定，禁止两台设备执行同一账号。
"""
from __future__ import annotations

import time
from typing import Optional

from models.account import Account, AccountStatus
from models.task import TaskResult, TaskRunState
from storage.database import Database


class AccountRepository:
    """账号队列（SQLite 持久化）"""

    def __init__(self, db: Database, stale_minutes: float = 10.0):
        self.db = db
        self.stale_minutes = stale_minutes

    # ── 导入 ──

    def add(self, account: str, password: str = "",
            max_retry: int = 3) -> int:
        """新增账号（重复账号跳过），返回 id（已存在返回其 id）"""
        now = time.time()
        row = self.db.query_one(
            "SELECT id FROM accounts WHERE account = ?", (account,))
        if row:
            return row["id"]
        cur = self.db.execute(
            "INSERT INTO accounts (account, password, status, max_retry, "
            "created_at, updated_at) VALUES (?, ?, 'PENDING', ?, ?, ?)",
            (account, password, max_retry, now, now))
        return cur.lastrowid

    def add_batch(self, items: list[tuple[str, str]], max_retry: int = 3
                  ) -> tuple[int, int]:
        """批量导入 [(account, password), ...] → (新增数, 跳过数)

        QQ 群是凭据源: 已入库账号若密码与群内不一致 → 更新密码,
        并把 FAILED/RETRY 复活为 PENDING(运营在群里发新密码后
        自动生效)。密码一致 → 跳过。RUNNING/LOCKED 不动(执行中)。
        """
        added = skipped = 0
        for account, password in items:
            account = (account or "").strip()
            if not account:
                continue
            row = self.db.query_one(
                "SELECT id, password, status FROM accounts "
                "WHERE account = ?", (account,))
            if row:
                if row["password"] == password:
                    skipped += 1
                    continue
                now = time.time()
                status = row["status"]
                if status in ("FAILED", "RETRY"):
                    self.db.execute(
                        "UPDATE accounts SET password = ?, status = "
                        "'PENDING', retry_count = 0, last_error = '', "
                        "updated_at = ? WHERE id = ?",
                        (password, now, row["id"]))
                else:
                    self.db.execute(
                        "UPDATE accounts SET password = ?, updated_at = ? "
                        "WHERE id = ?", (password, now, row["id"]))
                continue
            self.add(account, password, max_retry)
            added += 1
        return added, skipped

    # ── 原子领取 ──

    def claim_next(self, device_serial: str,
                   retry_cooldown: float = 120.0) -> Optional[Account]:
        """原子领取一个可执行账号(PENDING 或冷却后的 RETRY)，绑定设备。

        使用 BEGIN IMMEDIATE 写事务保证并发安全。
        RETRY 冷却: 坏账号不立即重新占用设备 — 先处理正常账号。
        """
        conn = self.db.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            # <=: Windows 下 time.time() 分辨率约 15.6ms, 连续两次调用可能
            # 返回相同值; 冷却刚好到期的 RETRY 必须可领取(否则 cooldown=0
            # 时刚失败过的账号永远领不到)
            row = conn.execute(
                "SELECT * FROM accounts WHERE status='PENDING' "
                "OR (status='RETRY' AND updated_at <= ?) "
                "ORDER BY (status='RETRY'), id LIMIT 1",
                (now - retry_cooldown,)).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE accounts SET status='LOCKED', device_serial=?, "
                "updated_at=? WHERE id=?",
                (device_serial, now, row["id"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return self._row_to_account(row)

    # ── 状态流转 ──

    def claim_specific(self, account_id: int, device_serial: str
                       ) -> Optional[Account]:
        """领取指定账号(PENDING 时绑定设备) — 桌面版「停止后继续」用,
        保证恢复的账号归属确定(不依赖 id 顺序)。
        账号非 PENDING(已被领/已完成)返回 None, 不抢占。
        """
        conn = self.db.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM accounts WHERE id=? AND status='PENDING'",
                (account_id,)).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE accounts SET status='LOCKED', device_serial=?, "
                "updated_at=? WHERE id=?",
                (device_serial, time.time(), account_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return self._row_to_account(row)

    def mark_running(self, account_id: int, device_serial: str):
        self._update(account_id, status="RUNNING", device_serial=device_serial,
                     started_at=time.time())

    def mark_success(self, account_id: int, device_serial: str):
        self._update(account_id, status="SUCCESS", device_serial=device_serial,
                     finished_at=time.time())

    def mark_retry(self, account_id: int, device_serial: str, error: str
                   ) -> AccountStatus:
        """失败→重试。retry_count 超过账号 max_retry → FAILED。返回最终状态。

        语义: max_retry=3 表示最多重试 3 次(共 4 次尝试机会)。
        """
        acc = self.get(account_id)
        retry = (acc.retry_count if acc else 0) + 1
        if acc and retry > acc.max_retry:
            self._update(account_id, status="FAILED",
                         device_serial=device_serial, last_error=error,
                         retry_count=retry, finished_at=time.time())
            return AccountStatus.FAILED
        self._update(account_id, status="RETRY", device_serial="",
                     last_error=error, retry_count=retry)
        return AccountStatus.RETRY

    def mark_failed(self, account_id: int, device_serial: str, error: str):
        acc = self.get(account_id)
        retry = (acc.retry_count if acc else 0) + 1
        self._update(account_id, status="FAILED",
                     device_serial=device_serial, last_error=error,
                     retry_count=retry, finished_at=time.time())

    def mark_disabled(self, account_id: int):
        self._update(account_id, status="DISABLED")

    def release(self, account_id: int, reason: str = ""):
        """未开始执行就释放回 PENDING（设备异常等场景）"""
        self._update(account_id, status="PENDING", device_serial="",
                     last_error=reason)

    # ── 恢复 ──

    def recover_stale(self) -> int:
        """程序意外退出后，把卡在 LOCKED/RUNNING 的账号恢复为可执行。

        超过最大重试次数 → FAILED；否则 → RETRY（可再次领取）。
        返回恢复数量。
        """
        now = time.time()
        stale_before = now - self.stale_minutes * 60
        rows = self.db.query(
            "SELECT * FROM accounts WHERE status IN ('LOCKED','RUNNING') "
            "AND updated_at < ?", (stale_before,))
        recovered = 0
        for row in rows:
            acc = self._row_to_account(row)
            if acc.retry_count >= acc.max_retry:
                self._update(acc.id, status="FAILED",
                             last_error="stale recovery: max retry exceeded",
                             finished_at=now)
            else:
                self._update(acc.id, status="RETRY", device_serial="",
                             retry_count=acc.retry_count + 1,
                             last_error="stale recovery: device disconnected",
                             updated_at=now)
            recovered += 1
        return recovered

    # ── 查询 ──

    def get(self, account_id: int) -> Optional[Account]:
        row = self.db.query_one(
            "SELECT * FROM accounts WHERE id = ?", (account_id,))
        return self._row_to_account(row) if row else None

    def get_by_account(self, account: str) -> Optional[Account]:
        row = self.db.query_one(
            "SELECT * FROM accounts WHERE account = ?", (account,))
        return self._row_to_account(row) if row else None

    def list_all(self, status: Optional[str] = None) -> list[Account]:
        if status:
            rows = self.db.query(
                "SELECT * FROM accounts WHERE status = ? ORDER BY id",
                (status,))
        else:
            rows = self.db.query("SELECT * FROM accounts ORDER BY id")
        return [self._row_to_account(r) for r in rows]

    def stats(self) -> dict:
        rows = self.db.query(
            "SELECT status, COUNT(*) AS n FROM accounts GROUP BY status")
        stats = {s.value: 0 for s in AccountStatus}
        for r in rows:
            stats[r["status"]] = r["n"]
        stats["total"] = sum(stats.values())
        return stats

    def _update(self, account_id: int, **fields) -> int:
        """动态更新字段（强制限制白名单字段）"""
        allowed = {"status", "device_serial", "retry_count", "last_error",
                   "started_at", "finished_at", "updated_at"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        sets["updated_at"] = time.time()
        sql = "UPDATE accounts SET " + ", ".join(f"{k}=?" for k in sets) \
              + " WHERE id=?"
        self.db.execute(sql, tuple(sets.values()) + (account_id,))
        return account_id

    def _row_to_account(self, row) -> Account:
        return Account(
            id=row["id"], account=row["account"], password=row["password"],
            status=AccountStatus(row["status"]),
            device_serial=row["device_serial"],
            retry_count=row["retry_count"], max_retry=row["max_retry"],
            last_error=row["last_error"], created_at=row["created_at"],
            updated_at=row["updated_at"], started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


class TaskResultRepository:
    """任务结果记录 + 导出"""

    def __init__(self, db: Database):
        self.db = db

    def count_success(self, account_id: int, since: float = None) -> int:
        """某账号的成功执行次数(test_cycles 循环测试用)。

        since 给定时只统计该时间之后的记录, 避免历史成功记录
        污染本轮循环计数。
        """
        if since:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM task_results "
                "WHERE account_id = ? AND state = 'SUCCESS' "
                "AND started_at >= ?", (account_id, since))
        else:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM task_results "
                "WHERE account_id = ? AND state = 'SUCCESS'", (account_id,))
        return int(row["n"]) if row else 0

    def save(self, result: TaskResult) -> int:
        cur = self.db.execute(
            "INSERT INTO task_results (account_id, account, device_serial, "
            "state, started_at, finished_at, duration_sec, failed_step, "
            "error, retry_count, screenshot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result.account_id, result.account, result.device_serial,
             result.state.value, result.started_at, result.finished_at,
             result.duration_sec, result.failed_step, result.error,
             result.retry_count, result.screenshot))
        return cur.lastrowid

    def list(self, limit: int = 500, device_serial: str = "",
             account_id: int = 0) -> list[dict]:
        if device_serial:
            rows = self.db.query(
                "SELECT * FROM task_results WHERE device_serial = ? "
                "ORDER BY id DESC LIMIT ?", (device_serial, limit))
        elif account_id:
            rows = self.db.query(
                "SELECT * FROM task_results WHERE account_id = ? "
                "ORDER BY id DESC LIMIT ?", (account_id, limit))
        else:
            rows = self.db.query(
                "SELECT * FROM task_results ORDER BY id DESC LIMIT ?",
                (limit,))
        return [dict(r) for r in rows]

    def export_xlsx(self, dest) -> str:
        """导出结果到 Excel，返回文件路径"""
        import pandas as pd  # 延迟导入，减小启动开销
        rows = self.db.query("SELECT * FROM task_results ORDER BY id")
        df = pd.DataFrame([dict(r) for r in rows])
        if df.empty:
            df = pd.DataFrame(columns=[
                "id", "account_id", "account", "device_serial", "state",
                "started_at", "finished_at", "duration_sec", "failed_step",
                "error", "retry_count", "screenshot"])
        else:
            df["account"] = df["account"].apply(
                lambda a: (a[:3] + "***" + a[-3:]) if len(a) > 6 else a[:2] + "***")
            df["started_at"] = pd.to_datetime(df["started_at"], unit="s")
            df["finished_at"] = pd.to_datetime(df["finished_at"], unit="s")
        df.to_excel(dest, index=False)
        return str(dest)
