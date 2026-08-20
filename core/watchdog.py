"""
core/watchdog.py
Watchdog 异常监控与分级恢复

监控项:
  ADB 断连 / 设备离线 / u2 断连 / APP 闪退 / 页面长时间无变化 /
  页面未知 / 网络错误 / 加载超时

恢复等级:
  Level 1  重新识别页面
  Level 2  按返回键
  Level 3  关闭普通弹窗
  Level 4  回到首页
  Level 5  force-stop + 重启 APP
  Level 6  重新初始化 uiautomator2
  Level 7  重新连接 ADB
  Level 8  标记设备异常(DEVICE_ERROR)
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable, Optional

from core.adb_manager import AdbManager
from core.exceptions import AdbError
from models.page_state import PageState

logger = logging.getLogger(__name__)


class AnomalyType(str, Enum):
    NONE = "NONE"
    PAGE_STUCK = "PAGE_STUCK"          # 页面长时间无变化
    APP_CRASHED = "APP_CRASHED"        # APP 闪退/退出
    DEVICE_OFFLINE = "DEVICE_OFFLINE"  # 设备离线
    U2_DISCONNECTED = "U2_DISCONNECTED"  # uiautomator 断连
    ADB_DISCONNECTED = "ADB_DISCONNECTED"  # ADB 断连
    NETWORK_ERROR = "NETWORK_ERROR"    # 游戏网络错误页
    PAGE_UNKNOWN = "PAGE_UNKNOWN"      # 页面长时间未知
    LOAD_TIMEOUT = "LOAD_TIMEOUT"      # 加载超时


# 异常 → 起始恢复等级
ANOMALY_LEVEL = {
    AnomalyType.PAGE_STUCK: 1,
    AnomalyType.PAGE_UNKNOWN: 2,
    AnomalyType.NETWORK_ERROR: 3,
    AnomalyType.LOAD_TIMEOUT: 4,
    AnomalyType.APP_CRASHED: 5,
    AnomalyType.U2_DISCONNECTED: 6,
    AnomalyType.ADB_DISCONNECTED: 7,
    AnomalyType.DEVICE_OFFLINE: 8,
}


class Watchdog:
    """每台设备一个 Watchdog 实例（由 Worker 持有并驱动）"""

    def __init__(self, serial: str, controller, adb: AdbManager,
                 package: str,
                 page_stuck_threshold: int = 5,
                 page_unknown_threshold: int = 5,
                 page_stuck_sec: float = 30.0):
        self.serial = serial
        self.d = controller
        self.adb = adb
        self.package = package
        self.page_stuck_threshold = page_stuck_threshold
        self.page_unknown_threshold = page_unknown_threshold
        # 停滞判定按秒(§八): tick 频率提升后不能按"连续 N 次同屏"判卡死 —
        # 慢加载页面(游戏加载 20-30s 静态画面)绝不能误报 PAGE_STUCK。
        self.page_stuck_sec = page_stuck_sec
        self._last_screen_hash: Optional[str] = None
        self._screen_since: float = 0.0
        self._unknown_page_count = 0
        self.max_level_attempts = 5  # 每个恢复等级最多尝试次数

        # 恢复动作回调（由 Worker 注入）
        self.on_redetect: Callable[[], bool] = lambda: True
        self.on_back: Callable[[], bool] = lambda: True
        self.on_popups: Callable[[], bool] = lambda: True
        self.on_go_home: Callable[[], bool] = lambda: True
        self.on_restart_app: Callable[[], bool] = lambda: False
        self.on_reset_u2: Callable[[], bool] = lambda: False
        self.on_reconnect_adb: Callable[[], bool] = lambda: False

    # ── 监控 ──

    def check(self, current_page: PageState,
              app_should_run: bool = True) -> AnomalyType:
        """检查当前是否存在异常。返回 NONE 表示健康。"""
        # 1. 设备级
        state = self.adb.get_state(self.serial)
        if state == "offline":
            return AnomalyType.DEVICE_OFFLINE
        if state != "device":
            return AnomalyType.ADB_DISCONNECTED

        # 2. u2 会话
        if not self.d.is_healthy():
            return AnomalyType.U2_DISCONNECTED

        # 3. APP 进程
        if app_should_run and self.package:
            if self.adb.pidof(self.serial, self.package) == 0:
                return AnomalyType.APP_CRASHED

        # 4. 页面级
        if current_page == PageState.NETWORK_ERROR:
            return AnomalyType.NETWORK_ERROR
        if current_page == PageState.ERROR:
            return AnomalyType.PAGE_UNKNOWN

        # 5. 页面长时间无变化（截图哈希 + 持续时间, 按秒判定）
        try:
            shot = self.d.screenshot()
            h = str(hash(shot.tobytes()))
        except Exception:
            return AnomalyType.U2_DISCONNECTED
        now = time.time()
        if h == self._last_screen_hash:
            if (now - self._screen_since >= self.page_stuck_sec
                    and current_page not in (PageState.TASK_RUNNING,)):
                self._reset_screen_tracking()
                return AnomalyType.PAGE_STUCK
        else:
            self._last_screen_hash = h
            self._screen_since = now

        # 6. 页面未知计数
        if current_page == PageState.UNKNOWN:
            self._unknown_page_count += 1
            if self._unknown_page_count >= self.page_unknown_threshold:
                self._unknown_page_count = 0
                return AnomalyType.PAGE_UNKNOWN
        else:
            self._unknown_page_count = 0

        return AnomalyType.NONE

    def _reset_screen_tracking(self):
        self._last_screen_hash = None
        self._screen_since = 0.0

    # ── 恢复 ──

    def recover(self, anomaly: AnomalyType,
                max_level: int = 8) -> tuple[bool, int]:
        """按异常对应的等级执行恢复。返回 (是否恢复, 使用的等级)。

        等级内失败 → 逐级升级，直到 max_level 或成功。
        每次尝试之间短等待，禁止无限重试。

        max_level: 页级异常(PAGE_STUCK/PAGE_UNKNOWN/LOAD_TIMEOUT/
        NETWORK_ERROR)由 Worker 传 5 — 最多到重启 APP, 不越级做
        u2/ADB 重连; 设备级异常保持 8(§9 状态感知恢复)。
        """
        level = ANOMALY_LEVEL.get(anomaly, 1)
        while level <= max_level:
            ok = self._execute_level(level)
            if ok:
                logger.info(f"[Watchdog] {self.serial} 恢复成功 "
                            f"(Level {level}, 异常={anomaly.value})")
                self._reset_screen_tracking()
                return True, level
            logger.warning(f"[Watchdog] {self.serial} Level {level} 恢复失败，"
                           f"升级到 Level {level + 1}")
            level += 1
            time.sleep(2)
        logger.error(f"[Watchdog] {self.serial} 恢复失败"
                     f"(Level {max_level})，交由 Worker 处理")
        return False, max_level

    def _execute_level(self, level: int) -> bool:
        try:
            if level == 1:
                return self.on_redetect()
            if level == 2:
                # BACK 动作由 on_back 回调执行 — Worker 注入守卫版
                # (注册页等全屏页面按 BACK=退出游戏, 禁止误退)
                return self.on_back()
            if level == 3:
                return self.on_popups()
            if level == 4:
                return self.on_go_home()
            if level == 5:
                return self.on_restart_app()
            if level == 6:
                return self.on_reset_u2()
            if level == 7:
                return self.on_reconnect_adb()
            if level == 8:
                return False  # 标记设备异常由 Worker 处理
        except AdbError as e:
            logger.warning(f"[Watchdog] Level {level} 执行异常: {e}")
        except Exception as e:
            logger.warning(f"[Watchdog] Level {level} 执行异常: {e}")
        return False
