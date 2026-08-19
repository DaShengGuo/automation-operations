"""
core/config.py
中控系统全局配置 — 加载 config/*.yaml + .env

优先级: .env 环境变量 > config.yaml > 代码默认值
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv 未安装时降级为纯 os.environ
    def load_dotenv(*_args, **_kwargs):
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
LOGS_DIR = PROJECT_ROOT / "logs"
TEMPLATES_DIR = PROJECT_ROOT / "templates" / "game"

# ── 默认超时（秒）── 键与 WorkerState 小写名对应
DEFAULT_TIMEOUTS = {
    "check_device": 60,
    "launch": 60,
    "start_game": 60,
    "detect_page": 60,
    "login": 90,
    "home": 120,
    "wait_home": 120,
    "popup": 15,
    "handle_popups": 15,
    "task": 180,
    "execute_task": 180,
    "verify": 60,
    "verify_task": 60,
    "logout": 30,
    "cleanup": 15,
    "recovery": 60,
    "next_account": 15,
}

DEFAULT_RETRIES = {
    "login": 3,
    "task": 2,
    "restart": 3,
    "watchdog": 5,       # 每次 RECOVERY 最多尝试的恢复等级次数
    "account_max": 3,    # 账号最大失败重试次数
}


class ControlConfig:
    """中控配置 — 单例访问 ControlConfig.load()

    data_dirs: 桌面版数据目录覆盖(程序文件与客户数据分离)。
      {"data_dir": ..., "screenshots_dir": ..., "logs_dir": ...,
       "user_config": ...}  # user_config 为客户可写配置 yaml
    """

    def __init__(self, project_root: Path = PROJECT_ROOT,
                 game_name: str = "game",
                 data_dirs: Optional[dict] = None):
        self.project_root = Path(project_root)
        load_dotenv(self.project_root / ".env")

        self.config_dir = self.project_root / "config"
        self.data_dir = self.project_root / "data"
        self.screenshots_dir = self.project_root / "screenshots"
        self.logs_dir = self.project_root / "logs"
        self.templates_dir = self.project_root / "templates" / "game"

        # 桌面版: 数据目录从 AppPaths 注入(绝不写 Program Files/_MEIPASS)
        if data_dirs:
            for key, attr in (("data_dir", "data_dir"),
                              ("screenshots_dir", "screenshots_dir"),
                              ("logs_dir", "logs_dir")):
                if data_dirs.get(key):
                    setattr(self, attr, Path(data_dirs[key]))

        self.system: dict = {}
        self.devices: dict = {}
        self.game: dict = {}
        self.game_name = game_name

        self._load_yaml("config.yaml", "system")
        self._load_yaml("devices.yaml", "devices")
        # game_name 决定游戏配置文件: "game" → game.yaml,
        # "pokemon_go" → game_pokemon_go.yaml
        game_file = ("game.yaml" if game_name == "game"
                     else f"game_{game_name}.yaml")
        self._load_yaml(game_file, "game")

        # 客户可写配置(群聊名称等)合并进 system — 用户设置优先于默认
        if data_dirs and data_dirs.get("user_config"):
            user_cfg = Path(data_dirs["user_config"])
            if user_cfg.exists():
                try:
                    user_data = yaml.safe_load(
                        user_cfg.read_text(encoding="utf-8")) or {}
                    if isinstance(user_data, dict):
                        self.system.update(user_data)
                except yaml.YAMLError as e:
                    import logging
                    logging.getLogger(__name__).error(
                        f"[配置] 用户配置解析失败 {user_cfg}: {e} — 使用默认值")

        # 建立运行时目录
        for _d in (self.data_dir, self.screenshots_dir, self.logs_dir,
                   self.templates_dir):
            _d.mkdir(parents=True, exist_ok=True)

    # ── YAML 加载 ──

    def _load_yaml(self, filename: str, target: str):
        path = self.config_dir / filename
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                setattr(self, target, data if isinstance(data, dict) else {})
                return
            except yaml.YAMLError as e:
                import logging
                logging.getLogger(__name__).error(
                    f"[配置] 解析失败 {path}: {e} — 该配置段将为空, "
                    f"请检查 YAML 语法(引号/特殊字符)")
        setattr(self, target, {})

    # ── 路径 ──

    @property
    def adb_path(self) -> str:
        """ADB 可执行文件：.env ADB_PATH > 项目捆绑 > 系统 PATH"""
        candidates = [
            os.environ.get("ADB_PATH", ""),
            str(self.project_root / "adb" / "platform-tools" / "adb.exe"),
            str(self.project_root / "adb" / "platform-tools" / "adb"),
            str(Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe"),
            "adb",
        ]
        for p in candidates:
            if not p:
                continue
            if p == "adb" or Path(p).exists():
                return p
        return "adb"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "runtime.db"

    @property
    def log_level(self) -> str:
        return os.environ.get(
            "LOG_LEVEL", str(self.system.get("logging", {}).get("level", "INFO"))
        ).upper()

    @property
    def max_workers(self) -> int:
        return int(self.system.get("workers", {}).get("max", 4))

    # ── 超时 / 重试 ──（config.yaml 与 game.yaml 中的 timeouts/retries 均生效）

    def state_timeout(self, state_name: str) -> float:
        t = dict(DEFAULT_TIMEOUTS)
        t.update(self.system.get("timeouts", {}))
        t.update(self.game.get("timeouts", {}))
        return float(t.get(state_name, 60))

    def retry_for(self, key: str) -> int:
        r = dict(DEFAULT_RETRIES)
        r.update(self.system.get("retries", {}))
        r.update(self.game.get("retries", {}))
        return int(r.get(key, 3))

    # ── 支付安全 ──

    @property
    def payment_dry_run(self) -> bool:
        """默认 True：禁止自动执行真实支付最终确认"""
        return bool(self.system.get("payment", {}).get("dry_run", True))

    @property
    def payment_allowed(self) -> bool:
        """双重开关：配置 dry_run=false 且 .env 明确授权才允许"""
        return (not self.payment_dry_run
                and os.environ.get("CONTROL_CENTER_ALLOW_PAYMENT", "0") == "1")

    # ── 设备配置 ──

    def device_override(self, serial: str) -> dict:
        overrides = self.devices.get("overrides") or {}
        return overrides.get(serial, {}) if isinstance(overrides, dict) else {}

    def is_device_disabled(self, serial: str) -> bool:
        return bool(self.device_override(serial).get("disabled", False))

    # ── 账号来源 ──

    @property
    def account_provider(self) -> str:
        """账号来源: 'manual_queue'=人工按设备队列(v1.2.0 默认);
        空=手动导入(Excel/CSV/HTTP)。旧值 'qq_ui' 不再有生产入口。"""
        return str(self.system.get("account_provider", ""))

    @property
    def qq_group_name(self) -> str:
        """旧 QQ 群取号配置(v1.2.0 起仅为遗留字段, 无生产用途)。"""
        return str(self.system.get("account_provider_qq_group", ""))

    # ── 游戏配置 ──

    @property
    def game_package(self) -> str:
        return str(self.game.get("package", ""))

    @property
    def game_activity(self) -> str:
        return str(self.game.get("activity", ""))

    @property
    def game_adapter(self) -> str:
        return str(self.game.get("adapter", "target_game"))

    @property
    def game_pages(self) -> dict:
        return self.game.get("pages", {})

    @property
    def game_popups(self) -> dict:
        return self.game.get("popups", {})

    @property
    def game_steps(self) -> list:
        return self.game.get("steps", [])

    @property
    def game_login(self) -> dict:
        return self.game.get("login", {})

    @property
    def game_logout(self) -> dict:
        return self.game.get("logout", {})

    @property
    def game_template_threshold(self) -> float:
        return float(self.game.get("image", {}).get("threshold", 0.8))

    def get(self, key: str, default: Any = None) -> Any:
        """通用读取 system 配置"""
        return self.system.get(key, default)

    # ── 单例(按 game_name 区分) ──

    _instance: Optional["ControlConfig"] = None

    @classmethod
    def load(cls, game_name: str = None) -> "ControlConfig":
        # 桌面版已注册数据目录单例时, 模块内默认 load() 必须返回它 —
        # 否则 game_name 默认 'game' 与桌面 'pokemon_go' 不匹配会另建
        # 实例, 路径退回 project_root → 安装目录(Program Files 只读)
        # 写入 Permission denied(实测: _internal\logs\device_*.log)。
        if getattr(cls, "_registered_with_data_dirs", False) and game_name is None:
            return cls._instance
        name = game_name or "game"
        if cls._instance is None or cls._instance.game_name != name:
            cls._instance = cls(game_name=name)
        return cls._instance

    @classmethod
    def load_with_data_dirs(cls, project_root: Path, data_dirs: dict,
                            game_name: str = "pokemon_go") -> "ControlConfig":
        """桌面版入口: 程序资源目录 + 客户数据目录分离, 并注册为单例
        (core.logger 等模块内 ControlConfig.load() 也指向本实例)。"""
        cls._instance = cls(project_root=project_root, game_name=game_name,
                            data_dirs=data_dirs)
        cls._registered_with_data_dirs = True
        return cls._instance

    @classmethod
    def reset(cls):
        """测试用：清除单例缓存"""
        cls._instance = None
        cls._registered_with_data_dirs = False
