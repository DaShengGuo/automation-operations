"""
core/account_manager.py
账号导入管理 — 统一 AccountProvider 接口

数据源:
  Excel(.xlsx)   账号/密码列(列名: account/账号, password/密码)
  CSV(.csv)      同上
  SQLite(.db)    accounts 表(account, password)
  HTTP(URL)      GET 返回 JSON: [{"account": "...", "password": "..."}]
                 POST /api/accounts 的 body 同格式
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from core.logger import mask_account
from storage.repositories import AccountRepository

logger = logging.getLogger(__name__)


class AccountProvider(ABC):
    """账号数据源统一接口"""

    @abstractmethod
    def fetch_accounts(self) -> list[tuple[str, str]]:
        """返回 [(account, password), ...]"""


class ExcelProvider(AccountProvider):
    ACCOUNT_COLUMNS = ("account", "账号", "username", "user", "手机号", "phone")
    PASSWORD_COLUMNS = ("password", "密码", "passwd", "pwd")

    def __init__(self, path: Path):
        self.path = Path(path)

    def fetch_accounts(self) -> list[tuple[str, str]]:
        import pandas as pd
        df = pd.read_excel(self.path, dtype=str)
        account_col = next((c for c in self.ACCOUNT_COLUMNS if c in df.columns),
                           None)
        if account_col is None:
            raise ValueError(
                f"Excel 缺少账号列，期望列名之一: {self.ACCOUNT_COLUMNS}")
        password_col = next((c for c in self.PASSWORD_COLUMNS
                             if c in df.columns), None)
        items = []
        for _, row in df.iterrows():
            account = str(row[account_col]).strip()
            if not account or account == "nan":
                continue
            password = str(row[password_col]) if password_col and \
                str(row[password_col]) != "nan" else ""
            items.append((account, password))
        return items


class CsvProvider(AccountProvider):
    def __init__(self, path: Path):
        self.path = Path(path)

    def fetch_accounts(self) -> list[tuple[str, str]]:
        import pandas as pd
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                df = pd.read_csv(self.path, dtype=str, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"CSV 编码无法识别: {self.path}")
        account_col = next((c for c in ExcelProvider.ACCOUNT_COLUMNS
                            if c in df.columns), None)
        if account_col is None:
            raise ValueError(
                f"CSV 缺少账号列，期望列名之一: {ExcelProvider.ACCOUNT_COLUMNS}")
        password_col = next((c for c in ExcelProvider.PASSWORD_COLUMNS
                             if c in df.columns), None)
        items = []
        for _, row in df.iterrows():
            account = str(row[account_col]).strip()
            if not account or account == "nan":
                continue
            password = str(row[password_col]) if password_col and \
                str(row[password_col]) != "nan" else ""
            items.append((account, password))
        return items


class SqliteProvider(AccountProvider):
    def __init__(self, path: Path):
        self.path = Path(path)

    def fetch_accounts(self) -> list[tuple[str, str]]:
        import sqlite3
        conn = sqlite3.connect(str(self.path))
        try:
            rows = conn.execute(
                "SELECT account, password FROM accounts").fetchall()
        except sqlite3.OperationalError as e:
            raise ValueError(f"SQLite 读取失败(需要 accounts 表): {e}")
        finally:
            conn.close()
        return [(str(r[0]).strip(), str(r[1]) if r[1] else "")
                for r in rows if str(r[0]).strip()]


class HttpProvider(AccountProvider):
    def __init__(self, url: str, token: str = "", timeout: float = 15):
        self.url = url
        self.token = token
        self.timeout = timeout

    def fetch_accounts(self) -> list[tuple[str, str]]:
        import requests
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = requests.get(self.url, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            data = data.get("accounts", data.get("data", []))
        if not isinstance(data, list):
            raise ValueError("HTTP 账号接口需返回 JSON 数组或 "
                             '{"accounts": [...]}')
        items = []
        for row in data:
            account = str(row.get("account", "")).strip()
            if not account:
                continue
            items.append((account, str(row.get("password", ""))))
        return items


# ── 统一导入入口 ──

def create_provider(source: str) -> AccountProvider:
    """按扩展名/前缀自动选择数据源"""
    s = source.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return HttpProvider(s)
    path = Path(s)
    if not path.exists():
        raise FileNotFoundError(f"账号文件不存在: {source}")
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return ExcelProvider(path)
    if ext == ".csv":
        return CsvProvider(path)
    if ext in (".db", ".sqlite", ".sqlite3"):
        return SqliteProvider(path)
    raise ValueError(f"不支持的文件类型: {ext} (支持 xlsx/csv/db/http)")


def import_accounts(source: str, repo: AccountRepository,
                    max_retry: int = 3,
                    provider: AccountProvider = None) -> dict:
    """导入账号到统一队列。返回统计信息。"""
    provider = provider or create_provider(source)
    items = provider.fetch_accounts()
    if not items:
        return {"source": source, "added": 0, "skipped": 0, "total": 0}
    added, skipped = repo.add_batch(items, max_retry=max_retry)
    logger.info(f"[账号导入] {source}: 新增 {added} 跳过(重复) {skipped}")
    for account, _ in items[:5]:
        logger.debug(f"[账号导入] {mask_account(account)}")
    return {"source": source, "added": added, "skipped": skipped,
            "total": len(items)}
