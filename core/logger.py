"""
core/logger.py
统一日志系统 — 主日志 + 每设备独立日志 + 结构化 KEY=VALUE 字段

格式示例:
  2026-08-12 14:20:01 [INFO] control.device: DEVICE=ABC123 ACCOUNT=user001 STATE=LOGIN ACTION=CLICK_LOGIN RESULT=SUCCESS TIME=1.32s 消息内容
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional


class StructuredFormatter(logging.Formatter):
    """把 extra 中的字段渲染为 DEVICE=xxx ACCOUNT=xxx ... 形式"""

    FIELDS = ("device", "account", "state", "action", "result", "time")

    def format(self, record: logging.LogRecord) -> str:
        asctime = self.formatTime(record, self.datefmt)
        tokens = " ".join(
            f"{f.upper()}={getattr(record, f)}"
            for f in self.FIELDS
            if getattr(record, f, "")
        )
        line = f"{asctime} [{record.levelname}] {record.name}:"
        if tokens:
            line += f" {tokens}"
        line += f" {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def mask_account(account: str) -> str:
    """账号脱敏: abc***123（日志与导出统一使用）"""
    if not account:
        return ""
    if len(account) <= 6:
        return account[:2] + "***"
    return f"{account[:3]}***{account[-3:]}"


def mask_password(password: str) -> str:
    """密码永不落日志"""
    return "******" if password else ""


class ContextLogger(logging.LoggerAdapter):
    """带设备/账号上下文的日志器 — 自动附带结构化字段"""

    def process(self, msg, kwargs):
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs

    def action(self, action: str, message: str, result: str = "",
               elapsed: Optional[float] = None, level: str = "info",
               **fields):
        """记录一次动作：ACTION=xxx RESULT=xxx TIME=xx.xxs"""
        extra = dict(fields)
        extra["action"] = action
        if result:
            extra["result"] = result
        if elapsed is not None:
            extra["time"] = f"{elapsed:.2f}s"
        getattr(self, level)(message, extra=extra)


# ── 全局初始化 ──

_initialized = False
_init_lock = threading.Lock()
_main_logger: Optional[logging.Logger] = None
_device_handlers: dict[str, logging.FileHandler] = {}
_device_handlers_lock = threading.Lock()


def _make_formatter() -> StructuredFormatter:
    return StructuredFormatter(
        "%(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """初始化根日志：控制台 + 主日志文件。幂等。"""
    global _initialized, _main_logger
    with _init_lock:
        if _initialized:
            return _main_logger

        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        console = logging.StreamHandler()
        console.setFormatter(_make_formatter())
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

        from datetime import datetime
        main_file = logging.FileHandler(
            log_dir / f"control_{datetime.now():%Y-%m-%d}.log",
            encoding="utf-8",
        )
        main_file.setFormatter(_make_formatter())

        root = logging.getLogger()
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.handlers.clear()
        root.addHandler(console)
        root.addHandler(main_file)

        # uiautomator2 / adbutils 内部日志降噪
        for noisy in ("uiautomator2", "adbutils", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        _main_logger = logging.getLogger("control")
        _initialized = True
        return _main_logger


def get_logger(name: str, device_serial: Optional[str] = None,
               account: Optional[str] = None) -> ContextLogger:
    """获取带上下文的日志器。

    - 提供 device_serial 时自动挂接 logs/device_<SERIAL>.log
      （每个设备独立文件，只挂一次；记录同时进入主日志）
    - 提供 account 时账号自动脱敏
    """
    logger = logging.getLogger(name)
    extra = {}
    if device_serial:
        extra["device"] = device_serial
        _attach_device_handler(logger, device_serial)
    if account:
        extra["account"] = mask_account(account)
    return ContextLogger(logger, extra)


def _attach_device_handler(logger: logging.Logger, serial: str):
    """为设备创建独立日志文件（线程安全，幂等）。

    日志文件绝不写入安装目录 — 桌面版用已注册的 ControlConfig 单例
    (load_with_data_dirs 注入的客户数据目录)。文件创建失败只告警
    不中断: 自动化不得因日志不可写而启动失败(实测安装目录只读时
    Worker 启动被 [Errno 13] Permission denied 打死)。
    """
    global _device_handlers
    with _device_handlers_lock:
        if serial in _device_handlers:
            return
        from core.config import ControlConfig
        try:
            log_dir = ControlConfig.load().logs_dir
            handler = logging.FileHandler(
                log_dir / f"device_{serial}.log", encoding="utf-8")
        except Exception as e:
            logging.getLogger("core.logger").warning(
                f"[日志] 设备日志文件创建失败 {serial}: {e} — 仅主日志记录")
            return
        handler.setFormatter(_make_formatter())
        logger.addHandler(handler)  # propagate 保持 True → 同时进主日志
        _device_handlers[serial] = handler
