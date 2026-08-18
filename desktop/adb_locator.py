"""
desktop/adb_locator.py
AdbLocator — 全应用唯一的 ADB 可执行文件定位器。

背景(0 设备 BUG 根因之一): 项目捆绑 platform-tools(37.0.0) 与
adbutils 包内置 adb(36.0.0) 并存时, 客户端互相杀掉对方启动的
server, `adb devices -l` 间歇性返回空表 → GUI 显示 0 台设备。

规则:
  1. 捆绑 platform-tools 优先(_MEIPASS/adb/platform-tools/adb.exe
     或仓库 adb/platform-tools/adb.exe), 其次是环境变量 ADB_PATH,
     最后才是系统 PATH 的 adb。
  2. resolve() 一次性把结果写入 ADBUTILS_ADB_PATH + ADB_PATH,
     让 adbutils/uiautomator2 内部 adb_path() 也指向同一份二进制,
     杜绝多版本 adb 打架。
  3. 禁止任何模块裸写 subprocess.run(["adb", ...]) 依赖 PATH。

AdbLocator 不依赖 Qt — 可被 core/ 与 desktop/ 共同引用。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from desktop.process_runner import run_hidden_process

logger = logging.getLogger(__name__)


class AdbLocator:
    """ADB 定位 + server 管理 + 设备枚举(全部隐藏执行)。"""

    _cached_path: str | None = None

    def __init__(self):
        self.path: str = self.resolve()

    # ── 定位 ──

    @classmethod
    def candidates(cls) -> list[str]:
        """候选顺序: 捆绑 > ADB_PATH 环境变量 > 系统 PATH。"""
        cands: list[str] = []

        # 1. 项目捆绑 platform-tools(Release: _MEIPASS/_internal;
        #    开发: 仓库 adb/platform-tools)
        try:
            from desktop.app_paths import resource_root
            bundled = resource_root() / "adb" / "platform-tools" / "adb.exe"
            if bundled.exists():
                cands.append(str(bundled))
        except Exception:
            pass
        if not cands:
            repo = Path(__file__).resolve().parent.parent \
                / "adb" / "platform-tools" / "adb.exe"
            if repo.exists():
                cands.append(str(repo))

        # 2. 环境变量 ADB_PATH
        env_path = os.environ.get("ADB_PATH", "")
        if env_path and Path(env_path).exists():
            cands.append(env_path)

        # 3. 系统 PATH 的 adb
        found = shutil.which("adb")
        if found:
            cands.append(found)

        return cands

    @classmethod
    def resolve(cls) -> str:
        """解析 ADB 路径(带缓存), 并注入环境变量统一全进程 adb 来源。"""
        if cls._cached_path:
            return cls._cached_path
        for p in cls.candidates():
            cls._cached_path = p
            break
        if not cls._cached_path:
            cls._cached_path = "adb"  # 最后兜底(允许报清晰错误)
            logger.error("[ADB] 未找到任何 adb 可执行文件, 兜底 PATH 查找")
        # 关键: 让 adbutils/uiautomator2 内部的 adb_path() 也走同一份
        os.environ.setdefault("ADBUTILS_ADB_PATH", cls._cached_path)
        os.environ.setdefault("ADB_PATH", cls._cached_path)
        logger.info("[ADB] 定位 adb: %s", cls._cached_path)
        return cls._cached_path

    @classmethod
    def reset_cache(cls) -> None:
        cls._cached_path = None

    # ── 命令(全部隐藏执行) ──

    def run(self, args: list[str], timeout: float = 15,
            check: bool = False) -> subprocess.CompletedProcess:
        if not args or args[0] != self.path:
            args = [self.path] + list(args)
        return run_hidden_process(args, timeout=timeout, check=check,
                                  encoding="utf-8", errors="replace")

    def version(self) -> str:
        try:
            r = self.run(["version"], timeout=10)
            return r.stdout.strip().splitlines()[0] if r.stdout.strip() \
                else "unknown"
        except Exception as e:  # 版本探测失败不致命
            return f"unknown({e.__class__.__name__})"

    def start_server(self, timeout: float = 20) -> bool:
        """显式启动 adb server(在 devices -l 之前调用, 避免首查落空)。"""
        try:
            r = self.run(["start-server"], timeout=timeout)
            if r.returncode != 0:
                logger.warning("[ADB] start-server 返回 %s: %s",
                               r.returncode, r.stderr.strip()[:200])
                return False
            return True
        except Exception as e:
            logger.warning("[ADB] start-server 失败: %s", e)
            return False

    def devices(self, retries: int = 3, delay: float = 1.5) -> list[dict]:
        """`adb devices -l` + 发现重试。

        重试用于吸收 server 重启/设备枚举的瞬时空窗。
        """
        last: list[dict] = []
        for attempt in range(1, retries + 1):
            try:
                r = self.run(["devices", "-l"], timeout=10)
                parsed = parse_devices_output(r.stdout)
                if parsed:
                    return parsed
                last = parsed
                if attempt < retries:
                    self.start_server()
                    import time
                    time.sleep(delay)
            except Exception as e:
                logger.warning("[ADB] devices 枚举第 %s 次失败: %s",
                               attempt, e)
                last = []
                if attempt < retries:
                    import time
                    time.sleep(delay)
        return last


def parse_devices_output(stdout: str) -> list[dict]:
    """解析 `adb devices -l` 输出 → [{serial, state, ...}](纯函数, 可测)。

    处理 Windows CRLF / 制表符 / 空格 / 型号带空格 / GBK 乱码
    (调用方已按 utf-8+replace 解码)。
    """
    devices: list[dict] = []
    for raw in stdout.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("*") \
                or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        # 只认合法状态; 空表只包含表头时自然得到 []
        if state not in ("device", "offline", "unauthorized", "no",
                         "unknown", "recovery", "sideload", "bootloader"):
            continue
        info: dict = {"serial": serial, "state": state}
        for kv in parts[2:]:
            if ":" in kv:
                k, v = kv.split(":", 1)
                info[k] = v
        devices.append(info)
    return devices
