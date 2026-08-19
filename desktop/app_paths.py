"""
desktop/app_paths.py
AppPaths — 客户数据目录统一管理。

程序文件(只读, 随安装包分发):
  - 开发: 仓库根(config/ templates/ adb/ ...)
  - Release: PyInstaller 解包目录(sys._MEIPASS, onedir 时为 exe 旁目录)

客户数据(可写, 升级/卸载不删除):
  %LOCALAPPDATA%\\PokemonAutomation\\
  ├─ config\\      客户配置(账号来源等; 旧 QQ 群字段静默兼容)
  ├─ database\\    SQLite(runtime.db + 历史)
  ├─ logs\\        运行日志(append, 不覆盖)
  ├─ screenshots\\ 错误截图/关键帧
  ├─ error_reports\\ BUG 现场快照
  ├─ exports\\     Excel 导出
  ├─ backups\\     数据库迁移前备份
  └─ runtime\\     临时 Checkpoint/Session(应用关闭可清理)

禁止把任何运行数据写入 sys._MEIPASS(只读) 或 Program Files。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from version import APP_NAME_EN


def resource_root() -> Path:
    """程序资源根(只读): PyInstaller 解包目录 > exe 旁目录 > 源码仓库根"""
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: 资源在 exe 旁的 _internal/; onefile: _MEIPASS
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent / "_internal"
    return Path(__file__).resolve().parent.parent


class AppPaths:
    """单例路径管理器。默认单用户 → %LOCALAPPDATA%。"""

    def __init__(self, base_dir: Path | str | None = None):
        if base_dir is None:
            override = os.environ.get("POKEMON_AUTOMATION_DATA_DIR", "")
            if override:
                base_dir = Path(override)
            else:
                local_appdata = os.environ.get("LOCALAPPDATA", "")
                if local_appdata:
                    base_dir = Path(local_appdata) / APP_NAME_EN
                else:  # 极端环境兜底: 用户目录
                    base_dir = Path.home() / f".{APP_NAME_EN}"
        self.base = Path(base_dir)

        self.config = self.base / "config"
        self.database = self.base / "database"
        self.logs = self.base / "logs"
        self.screenshots = self.base / "screenshots"
        self.error_reports = self.base / "error_reports"
        self.exports = self.base / "exports"
        self.backups = self.base / "backups"
        self.runtime = self.base / "runtime"

        self.user_config_file = self.config / "user_config.yaml"
        self.db_file = self.database / "runtime.db"

    def ensure_dirs(self) -> None:
        for d in (self.config, self.database, self.logs, self.screenshots,
                  self.error_reports, self.exports, self.backups,
                  self.runtime):
            d.mkdir(parents=True, exist_ok=True)

    def clean_runtime(self) -> None:
        """清理临时 Runtime 数据(Checkpoint/Session/锁)。

        仅在应用正常退出时调用 — 数据库/日志/截图等永久数据绝不清理。
        """
        try:
            for p in self.runtime.iterdir():
                if p.is_file():
                    p.unlink()
        except OSError:
            pass


_paths: AppPaths | None = None


def get_paths() -> AppPaths:
    global _paths
    if _paths is None:
        _paths = AppPaths()
        _paths.ensure_dirs()
    return _paths
