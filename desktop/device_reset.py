"""
desktop/device_reset.py
DeviceResetService — 单设备「设备环境重置 / 故障恢复」(仅人工触发)。

用途: 测试环境恢复 / 本地状态异常 / 游戏缓存异常 / 登录页面状态混乱 /
Worker/Checkpoint 异常 / 自动化调试。

硬性边界(交付规格):
  - 只允许人工点击触发。禁止按账号数量/风控检测自动清理 — 本功能
    不设计成规避平台账号、设备或风控限制的机制(检测到限制时由既有
    Watchdog/账号状态机截图取证并停止, 与本服务无任何联动);
  - 默认不清理浏览器数据(浏览器无关原则, 保护客户浏览器中的
    Cookie/登录状态/网站数据); 浏览器清理是独立高级选项, 且无法
    安全确认影响范围(解析不出默认浏览器)时绝不执行;
  - 只影响「手机端自动化运行环境 + 当前 Runtime」; 运行日志/SQLite
    历史/错误记录/账号执行历史/设备历史一律不删;
  - 单设备线程内串行执行, 其他设备 Worker 继续运行不受影响。

流程:
  RESET_REQUESTED → 停止 Worker(归还原因 DEVICE_RESET) → 释放账号锁
  → 清理 RuntimeCheckpoint → 清理 Runtime 临时状态 → pm clear 游戏
  (高级选项: 浏览器) → 重检 ADB → 重连 uiautomator2 → 重新获取
  DeviceProfile → 检查 Pokémon GO → detect_state() → READY / RESET_FAILED

重置后不假设任何页面状态: 下一次 Worker 启动由 DETECT_PAGE 按手机
真实页面继续(游戏数据被清后出现的权限页/欢迎页/首次启动流程由既有
PopupHandler/InitialPageHandler 处理)。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from version import APP_VERSION

logger = logging.getLogger(__name__)

# pm clear 输出判定(部分 Android 版本失败时退出码仍为 0, 以文本为准)
_PM_CLEAR_SUCCESS = "Success"

# 默认浏览器解析: 用 https 页面解析 Android 系统默认 VIEW handler
# (PTC 登录跳转的就是这个浏览器)。--brief 输出 "包名/Activity"。
_BROWSER_RESOLVE_CMD = (
    'cmd resolve-activity --brief -a android.intent.action.VIEW '
    '-d "https://www.pokemon.com"')

# 解析结果明显不是浏览器时拒绝清理(无法安全确认影响范围 → 不执行)
_NON_BROWSER_PREFIXES = (
    "com.android.settings", "com.android.vending", "com.google.android.gm",
)


@dataclass
class ResetOutcome:
    """重置结果(GUI 弹窗 / 日志共用)。"""
    ok: bool
    step: str = ""          # 失败步骤, 如 CLEAR_GAME_DATA
    error: str = ""         # 失败原因
    detail: str = ""        # 详细输出
    detected_state: str = ""  # 重置后 detect_state() 真实页面


class DeviceResetError(RuntimeError):
    """重置失败: 携带步骤 + 原因 + 详细输出(规格: 失败必须给具体原因)。"""

    def __init__(self, step: str, message: str, detail: str = ""):
        self.step = step
        self.message = message
        self.detail = detail
        super().__init__(f"[{step}] {message}" + (f" — {detail}" if detail else ""))


class DeviceResetService:
    """设备环境重置执行体。在独立线程中调用 reset()。

    依赖 duck-typed controller(测试可注入 Fake):
      registry / cfg / paths / accounts / _checkpoints / adb_locator /
      device_manager() / scheduler(可 None) / bus(可 None)
    """

    def __init__(self, controller):
        self.controller = controller
        self.log = logging.getLogger(__name__)

    # ── 入口 ──

    def reset(self, serial: str, include_browser: bool = False
              ) -> ResetOutcome:
        c = self.controller
        reg = c.registry
        adb = c.device_manager().adb
        pkg = c.cfg.game_package or "com.nianticlabs.pokemongo"
        prev_account, prev_state, prev_page = self._capture_previous(serial)

        reg.mark_resetting(serial, "RESETTING", "")
        self._write_reset_log(serial, "STARTED", prev_account=prev_account,
                              prev_state=prev_state or prev_page,
                              browser=include_browser)
        try:
            # 1. 停止当前 Worker(在途账号由 Worker finally 归还 RETRY,
            #    原因 DEVICE_RESET)
            self._stop_worker(serial)
            # 2. 释放账号锁: 无 Worker 但账号卡在 LOCKED/RUNNING 时
            #    (程序重启前遗留)同样归还, 绝不误标 SUCCESS
            self._release_orphan_account(serial)
            # 3. 清理 RuntimeCheckpoint(仅本设备)
            c._checkpoints.clear(serial)
            # 4. 清理 resume 注入配置(数据清空后残留会话不再可信)
            resume = c.cfg.system.get("resume")
            if resume and serial in resume:
                resume.pop(serial, None)
                self.log.info("[重置] %s resume 配置已清除", serial)
            # 5. 清理临时自动化状态(registry worker 标记)
            reg.mark_worker(serial, running=False)

            # 6. 应用环境重置: pm clear 游戏数据
            self._clear_game_data(adb, serial, pkg)
            # 7. 浏览器数据(高级选项, 默认关闭)
            browser_result = self._clear_browser(adb, serial) \
                if include_browser else "NOT_TOUCHED"
            # 8. 重检 ADB
            if adb.get_state(serial) != "device":
                if not adb.wait_online(serial, timeout=30):
                    raise DeviceResetError(
                        "ADB_REDETECT", "ADB 状态未恢复 device",
                        f"get-state={adb.get_state(serial)}")
            # 9. 重连 uiautomator2
            self._reconnect_u2(serial)
            # 10. 重新获取 DeviceProfile
            self._refresh_device_profile(serial)
            # 11. 重新初始化自动化环境 + 检查游戏
            self._reinit_device(serial, pkg)
            # 12. detect_state() — 按手机真实页面继续, 不假设任何状态
            detected = self._detect_real_state(serial)

            reg.mark_resetting(serial, "", "")
            reg.mark_ready(serial, True,
                           f"环境重置完成, 真实页面={detected or 'UNKNOWN'}")
            self._write_reset_log(
                serial, "SUCCESS", prev_account=prev_account,
                prev_state=prev_state or prev_page,
                browser=browser_result, detected=detected)
            return ResetOutcome(ok=True, detected_state=detected or "")

        except DeviceResetError as e:
            self._fail(serial, e, prev_account=prev_account,
                       prev_state=prev_state or prev_page,
                       browser=("REQUESTED" if include_browser
                                else "NOT_TOUCHED"))
            return ResetOutcome(ok=False, step=e.step, error=e.message,
                                detail=e.detail)
        except Exception as e:  # 意外异常也要落 RESET_FAILED + 日志
            err = DeviceResetError("UNEXPECTED", repr(e))
            self._fail(serial, err, prev_account=prev_account,
                       prev_state=prev_state or prev_page,
                       browser=("REQUESTED" if include_browser
                                else "NOT_TOUCHED"))
            return ResetOutcome(ok=False, step=err.step, error=err.message,
                                detail=err.detail)

    # ── 各步骤 ──

    def _capture_previous(self, serial: str) -> tuple[str, str, str]:
        """重置前状态(日志用): (账号, worker_state, 检测页)。"""
        sched = self.controller.scheduler
        account = state = page = ""
        if sched is not None:
            try:
                snap = sched.snapshot()
                dev = next((d for d in snap["devices"]
                            if d["serial"] == serial), None)
                if dev:
                    account = dev.get("account", "")
                    state = dev.get("worker_state", "") or ""
                    page = dev.get("page", "") or ""
            except Exception:
                pass
        if not account:
            cp = self.controller._checkpoints.load(serial)
            if cp:
                account = cp.masked_account
                state = cp.current_state
                page = cp.detected_page
        return account, state, page

    def _stop_worker(self, serial: str):
        sched = self.controller.scheduler
        if sched is None:
            return
        try:
            result = sched.stop_device(serial, reason="DEVICE_RESET")
            if result.get("ok"):
                self.log.info("[重置] %s Worker 已停止(DEVICE_RESET)", serial)
        except Exception as e:
            self.log.warning("[重置] %s Worker 停止异常(继续重置): %s",
                             serial, e)

    def _release_orphan_account(self, serial: str):
        """Worker 不存在但账号仍卡 LOCKED/RUNNING → RETRY(原因 DEVICE_RESET)。"""
        cp = self.controller._checkpoints.load(serial)
        account_id = cp.account_id if cp else None
        if account_id is None:
            return
        acc = self.controller.accounts.get(account_id)
        if acc is not None and acc.status.value in ("LOCKED", "RUNNING"):
            self.controller.accounts.mark_retry(
                account_id, serial, "DEVICE_RESET")
            self.log.info("[重置] %s 孤儿账号 %s → RETRY(DEVICE_RESET)",
                          serial, acc.masked())

    def _clear_game_data(self, adb, serial: str, pkg: str):
        self.log.info("[重置] %s pm clear 游戏数据: %s", serial, pkg)
        try:
            rc, out = adb.shell_rc(serial, f"pm clear {pkg}", timeout=60)
        except Exception as e:
            raise DeviceResetError("CLEAR_GAME_DATA", "ADB shell 命令失败",
                                   repr(e))
        if rc == 0 and _PM_CLEAR_SUCCESS in out:
            self.log.info("[重置] %s 游戏数据已清除", serial)
            return
        # rc!=0 或输出 "Failed"(部分机型 rc=0 但输出 Failed)
        raise DeviceResetError(
            "CLEAR_GAME_DATA",
            f"pm clear 返回失败(rc={rc}, 输出未含 Success)",
            out[:300] or "(无输出)")

    def _clear_browser(self, adb, serial: str) -> str:
        """高级选项: 解析系统默认浏览器并清理。无法确认影响范围时不执行。

        返回 NOT_TOUCHED / SKIPPED(无法确认) / CLEARED。
        """
        try:
            rc, out = adb.shell_rc(serial, _BROWSER_RESOLVE_CMD, timeout=20)
        except Exception as e:
            self.log.warning("[重置] %s 浏览器解析失败(跳过清理): %s",
                             serial, e)
            return "SKIPPED"
        pkg = (out or "").split("/", 1)[0].strip()
        if (not pkg or rc != 0 or pkg == "android"
                or pkg.startswith(_NON_BROWSER_PREFIXES)):
            # 解析不出/结果不像浏览器 → 无法安全确认影响范围, 不执行
            self.log.warning("[重置] %s 无法确认默认浏览器(解析=%r), "
                             "按规格跳过浏览器清理", serial, out[:120])
            return "SKIPPED"
        try:
            rc2, out2 = adb.shell_rc(serial, f"pm clear {pkg}", timeout=60)
        except Exception as e:
            raise DeviceResetError("BROWSER_DATA", "ADB shell 命令失败",
                                   repr(e))
        if rc2 == 0 and _PM_CLEAR_SUCCESS in out2:
            self.log.info("[重置] %s 浏览器 %s 数据已清除", serial, pkg)
            return "CLEARED"
        raise DeviceResetError(
            "BROWSER_DATA", f"pm clear {pkg} 返回失败",
            out2[:300] or "(无输出)")

    def _reconnect_u2(self, serial: str):
        try:
            controller = self.controller.device_manager().create_controller(
                serial)
            controller.connect()
        except Exception as e:
            raise DeviceResetError("U2_RECONNECT", "uiautomator2 重连失败",
                                   str(e))

    def _refresh_device_profile(self, serial: str):
        """重新获取 DeviceProfile(DeviceInfo.from_adb)并回填注册表。"""
        try:
            from device_profiles import DeviceInfo
            adb_path = self.controller.adb_locator.path
            info = DeviceInfo.from_adb(serial, adb_path)
            self.controller.registry.update_hardware_info(
                serial, model=info.model, brand=info.brand,
                manufacturer=info.manufacturer,
                android_version=info.android_version,
                resolution=f"{info.width}x{info.height}")
        except Exception as e:
            raise DeviceResetError("DEVICE_PROFILE", "设备信息读取失败",
                                   str(e))

    def _reinit_device(self, serial: str, pkg: str):
        dm = self.controller.device_manager()
        device = dm.get_device(serial)
        if device is None:
            try:
                dm.scan()
                device = dm.get_device(serial)
            except Exception as e:
                raise DeviceResetError("REINIT", "设备扫描失败", str(e))
        if device is None:
            raise DeviceResetError("REINIT", "设备不在 ADB 列表中", "")
        if not adb_is_healthy(device):
            raise DeviceResetError("REINIT",
                                   f"ADB 状态异常: {device.adb_state}", "")
        if not getattr(device, "app_installed", True) and not adb_app_installed(
                dm, serial, pkg):
            raise DeviceResetError("CHECK_GAME", "Pokémon GO 未安装",
                                   f"package={pkg}")
        try:
            report = dm.init_device(device, target_package=pkg)
        except Exception as e:
            raise DeviceResetError("REINIT", "设备初始化异常", str(e))
        if not report.passed:
            raise DeviceResetError("REINIT", "重新初始化未通过",
                                   device.init_error or report.format())

    def _detect_real_state(self, serial: str) -> str:
        """detect_state() — 按手机真实页面继续, 绝不假设任何状态。"""
        try:
            from automation import create_automation
            dm = self.controller.device_manager()
            controller = dm.create_controller(serial)
            if controller.device is None:
                controller.connect()
            automation = create_automation(
                self.controller.cfg.game_adapter, controller,
                self.controller.cfg)
            if automation is None or not hasattr(automation, "detector"):
                raise DeviceResetError("DETECT_STATE", "adapter 无页面检测器",
                                       "")
            state = automation.detector.detect()
            name = getattr(state, "value", str(state))
            self.log.info("[重置] %s 真实页面检测: %s", serial, name)
            return name
        except DeviceResetError:
            raise
        except Exception as e:
            raise DeviceResetError("DETECT_STATE", "真实页面检测失败", str(e))

    # ── 失败收尾 / 日志 ──

    def _fail(self, serial: str, e: DeviceResetError, prev_account: str,
              prev_state: str, browser):
        reg = self.controller.registry
        detail = f"{e.step} — {e.message}" + (f": {e.detail}" if e.detail else "")
        reg.mark_resetting(serial, "RESET_FAILED", detail)
        reg.mark_worker(serial, running=False)
        self._write_reset_log(serial, "FAILED", prev_account=prev_account,
                              prev_state=prev_state, browser=browser,
                              step=e.step, error=e.message, detail=e.detail)
        if self.controller.bus is not None:
            model = (reg.get(serial).model if reg.get(serial) else "") or "-"
            self.controller.bus.toast.emit(
                "error",
                f"设备环境重置失败\n\n设备: {model}\n"
                f"步骤: {e.step}\n错误: {e.message}\n详细: {e.detail or '—'}")
        logger.error("[重置] %s 失败: %s", serial, detail)

    def _write_reset_log(self, serial: str, result: str, prev_account: str = "",
                         prev_state: str = "", browser="", step: str = "",
                         error: str = "", detail: str = "",
                         detected: str = ""):
        """持久化重置日志(logs/device_reset.log, 追加, 永不清理)。"""
        try:
            reg = self.controller.registry
            model = (reg.get(serial).model if reg.get(serial) else "") or "-"
            # GAME_DATA 如实反映: 成功=已清; 失败在清数据步=清失败;
            # 失败在后续步=已清; 未走到清数据步(意外异常)=未知
            if result == "SUCCESS":
                game_data = "CLEARED"
            elif step == "CLEAR_GAME_DATA":
                game_data = "FAILED"
            elif step in ("", "UNEXPECTED"):
                game_data = "UNKNOWN"
            else:
                game_data = "CLEARED"
            parts = [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                f"DEVICE={serial}",
                f"MODEL={model}",
                "ACTION=DEVICE_ENVIRONMENT_RESET",
                "REASON=MANUAL",
                f"PREV_ACCOUNT={prev_account or '-'}",
                f"PREV_STATE={prev_state or '-'}",
                f"GAME_DATA={game_data}",
                f"BROWSER_DATA={browser or 'NOT_TOUCHED'}",
                f"DETECTED_STATE={detected or '-'}",
                f"RESULT={result}",
                f"STEP={step or '-'}",
                f"ERROR={error or '-'}",
                f"DETAIL={(detail or '-')[:200]}",
                f"VERSION={APP_VERSION}",
            ]
            line = " | ".join(parts) + "\n"
            path = Path(self.controller.cfg.logs_dir) / "device_reset.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning("[重置] 重置日志写入失败(不中断重置): %s", e)


def adb_is_healthy(device) -> bool:
    return getattr(device, "is_adb_healthy", False)


def adb_app_installed(dm, serial: str, pkg: str) -> bool:
    try:
        return bool(dm.adb.is_app_installed(serial, pkg))
    except Exception:
        return False
