"""
desktop/controller.py
DesktopAppController — GUI 与自动化系统之间的唯一控制层。

MainWindow 只调用本类, 不直接操作 DeviceManager/TaskScheduler。
自动化继续由 DeviceWorker 线程执行, 本类不阻塞 GUI。

职责:
  start()/stop_all()/stop_device()/start_device()
  reidentify()/resume_from_step()
  confirm_and_run() — SAVE + START
  save_chat_config()/check_environment()
  get_history()/snapshot()/shutdown()
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

from core.config import ControlConfig
from core.logger import setup_logging
from desktop.app_paths import AppPaths, resource_root
from desktop.adb_locator import AdbLocator
from desktop.checkpoint import CheckpointStore, RuntimeCheckpoint
from desktop.device_monitor import DeviceMonitor
from desktop.device_registry import DeviceRegistry
from desktop.runtime_state import ApplicationRunState
from desktop.state_registry import PokemonStateRegistry
from desktop.update_service import UpdateService
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository
from version import APP_NAME, APP_VERSION

logger = logging.getLogger(__name__)

# 账号来源选项(GUI 下拉框动态读取)
CHAT_SOURCES = [
    ("qq_ui", "QQ群聊"),
    ("excel", "Excel 文件"),
    ("csv", "CSV 文件"),
]


class DesktopAppController:
    """桌面应用控制层(线程安全)。bus 为 None 时不发事件(测试用)。"""

    def __init__(self, bus=None):
        self.bus = bus
        self.paths: AppPaths = AppPaths()
        self.paths.ensure_dirs()

        # 程序资源(只读) + 客户数据目录(可写)分离
        self.cfg: ControlConfig = ControlConfig.load_with_data_dirs(
            project_root=resource_root(),
            data_dirs={
                "data_dir": self.paths.database,
                "screenshots_dir": self.paths.screenshots,
                "logs_dir": self.paths.logs,
                "user_config": self.paths.user_config_file,
            },
            game_name="pokemon_go")

        # 文件日志(append, 不覆盖) — 完整日志永久保留
        setup_logging(self.cfg.logs_dir, self.cfg.log_level)

        # SQLite(迁移 + 备份)
        self.db = Database(self.cfg.db_path,
                           backup_dir=self.paths.backups)
        self.accounts = AccountRepository(
            self.db,
            stale_minutes=float(self.cfg.get("stale_recover_minutes", 10)))
        self.results = TaskResultRepository(self.db)

        self.scheduler = None          # 懒创建(确认并运行/开始运行时)
        self._state = ApplicationRunState.STOPPED
        self._lock = threading.RLock()
        self.run_id: Optional[int] = None
        self._checkpoints = CheckpointStore(self.paths.runtime)
        self.update_service = UpdateService()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        # 轮询持久化: 上次看到的设备账号(检测账号完成/状态变化)
        self._last_device_account: dict[str, str] = {}
        # 设备环境重置(人工触发): 进行中的 serial 集合, 防重复触发
        self._resetting: set[str] = set()

        # ── 设备单一状态源 + 热插拔监控(0 设备 BUG 整改) ──
        # ADB 定位统一入口: 捆绑 platform-tools 优先, 注入
        # ADBUTILS_ADB_PATH 让 u2/adbutils 内部共用同一份 adb,
        # 杜绝多版本 adb 客户端互相杀 server 导致的设备列表瞬时空窗。
        self.adb_locator = AdbLocator()
        self.registry = DeviceRegistry()
        self._dm = None                # DeviceManager 懒建(共享单例)
        self._monitor: Optional[DeviceMonitor] = None
        if bus is not None:
            # GUI 模式: 启动热插拔监控(测试/无 GUI 不启动, 避免噪音)
            self.start_monitor()

    # ── 应用运行状态 ──

    @property
    def state(self) -> ApplicationRunState:
        return self._state

    def _set_state(self, state: ApplicationRunState):
        with self._lock:
            self._state = state
        logger.info(f"[桌面] 应用状态: {state.display}")
        if self.bus is not None:
            self.bus.app_state.emit(state.value)

    # ── 设备监控(单一状态源) ──

    def device_manager(self):
        """共享 DeviceManager 单例(懒建) — 禁止每次快照新建。"""
        if self._dm is None:
            from core.device_manager import DeviceManager
            self._dm = DeviceManager(self.cfg)
        return self._dm

    def start_monitor(self) -> None:
        """启动设备热插拔监控线程(幂等)。"""
        if self._monitor is None:
            self._monitor = DeviceMonitor(
                self.registry, bus=self.bus,
                device_manager=self.device_manager(),
                adb_locator=self.adb_locator)
            self._monitor.is_running = \
                lambda: self._state == ApplicationRunState.RUNNING
            self._monitor.start()

    def stop_monitor(self) -> None:
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None

    def rescan_now(self) -> dict:
        """立即重新检测设备(GUI「重新检测设备」按钮)。"""
        self.start_monitor()
        if self._monitor is not None:
            self._monitor.refresh_now()
        counts = self.registry.counts()
        return {"ok": True, "counts": counts}

    def diagnostics(self) -> str:
        """诊断信息(「复制诊断信息」按钮): ADB 来源/版本 + 设备注册表 +
        环境检查 + 最近设备发现日志线索。"""
        lines = [
            f"程序版本: {APP_VERSION} ({APP_NAME})",
            f"ADB 路径: {self.adb_locator.path}",
            f"ADB 版本: {self.adb_locator.version()}",
            f"环境变量 ADB_PATH: "
            f"{__import__('os').environ.get('ADB_PATH', '(未设置)')}",
            f"环境变量 ADBUTILS_ADB_PATH: "
            f"{__import__('os').environ.get('ADBUTILS_ADB_PATH', '(未设置)')}",
            "",
            "设备注册表(DeviceRegistry):",
        ]
        counts = self.registry.counts()
        lines.append(
            f"  检测到 {counts['detected']} / READY {counts['ready']} / "
            f"运行中Worker {counts['running']}")
        for r in self.registry.records():
            lines.append(
                f"  - {r.serial}: adb={r.adb_state} ready={r.ready} "
                f"worker={r.worker_running}({r.worker_state}) "
                f"{r.brand} {r.model} {r.resolution}"
                + (f" [拒绝原因] {r.reject_reason}" if r.reject_reason else ""))
        lines.append("")
        lines.append("环境检查:")
        env = self.check_environment()
        for name, ok, detail in env["items"]:
            lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        return "\n".join(lines)

    # ── 配置 ──

    def save_chat_config(self, source: str, chat_name: str) -> dict:
        """保存账号来源配置(内存立即生效 + 持久化到客户数据目录)。"""
        source = source or "qq_ui"
        chat_name = (chat_name or "").strip()
        if source == "qq_ui" and not chat_name:
            return {"ok": False, "error": "请输入接收账号的QQ群聊或聊天框名称"}
        if source in ("excel", "csv") and not chat_name:
            return {"ok": False, "error": "请选择账号文件"}
        if source in ("excel", "csv") and not Path(chat_name).exists():
            return {"ok": False, "error": f"文件不存在: {chat_name}"}

        user_cfg: dict = {"account_provider": source}
        if source == "qq_ui":
            user_cfg["account_provider_qq_group"] = chat_name
        else:
            user_cfg["account_provider_file"] = chat_name
        try:
            self.paths.user_config_file.write_text(
                yaml.safe_dump(user_cfg, allow_unicode=True),
                encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": f"配置保存失败: {e}"}
        self.cfg.system.update(user_cfg)
        return {"ok": True}

    def chat_config(self) -> tuple[str, str]:
        """返回 (source, 名称/路径) — GUI 启动时回填。"""
        source = self.cfg.account_provider
        if source == "qq_ui":
            return source, self.cfg.qq_group_name
        return source, str(self.cfg.get("account_provider_file", ""))

    def _import_file_accounts(self) -> tuple[int, str]:
        """Excel/CSV 来源: 启动前导入账号文件。返回 (新增数, 错误)。"""
        path = Path(self.cfg.get("account_provider_file", ""))
        if not path.exists():
            return 0, f"账号文件不存在: {path}"
        from core.account_manager import CsvProvider, ExcelProvider
        provider = (ExcelProvider(path) if path.suffix.lower() in
                    (".xlsx", ".xls") else CsvProvider(path))
        try:
            items = provider.fetch_accounts()
        except Exception as e:
            return 0, f"账号文件读取失败: {e}"
        added = self.accounts.add_batch(items)
        return added, ""

    # ── 环境检查 ──

    def check_environment(self) -> dict:
        """首次启动/确认运行前的环境自检(不初始化设备)。"""
        items: list[tuple[str, bool, str]] = []
        adb = Path(self.adb_locator.path)
        if not adb.exists() and self.adb_locator.path != "adb":
            items.append(("ADB", False,
                          f"adb 不存在: {self.adb_locator.path}"))
        else:
            items.append(("ADB", True,
                          f"{self.adb_locator.path} "
                          f"({self.adb_locator.version()})"))

        try:
            probe = self.paths.base / "runtime" / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            items.append(("数据目录", True, str(self.paths.base)))
        except OSError as e:
            items.append(("数据目录", False, f"不可写: {e}"))

        items.append(("游戏配置", bool(self.cfg.game),
                      self.cfg.game_name))

        # u2 资源(u2.jar/apk) — 缺失时设备初始化必失败(0 worker 根因之一)
        from desktop.prerequisites import u2_assets_check
        items.append(u2_assets_check())

        # VC++ 运行库 — 客户机无开发环境时的常见崩溃源
        from desktop.prerequisites import vc_runtime_check
        items.append(vc_runtime_check())

        # 设备(来自 DeviceRegistry 单一状态源, 非本次临时扫描)
        counts = self.registry.counts()
        device_count = counts["detected"]
        records = self.registry.records()
        if device_count == 0:
            # 注册表为空时做一次即时探测(首启/监控未跑完)
            if self._monitor is not None:
                self._monitor.refresh_now()
                counts = self.registry.counts()
                device_count = counts["detected"]
                records = self.registry.records()
        if device_count > 0:
            detail_parts = []
            for r in records:
                if r.is_connected:
                    detail_parts.append(
                        f"{r.serial[:12]} {r.brand} {r.model}")
                else:
                    detail_parts.append(
                        f"{r.serial[:12]} [{r.adb_state}] "
                        f"{r.reject_reason}")
            items.append(("手机扫描", True,
                          f"检测到 {device_count} 台: "
                          + "; ".join(detail_parts)))
        else:
            items.append(("手机扫描", False,
                          "未检测到设备 — 请检查 USB 连接/USB 调试/"
                          "授权弹窗"))
        ok = all(it[1] for it in items)
        return {"ok": ok, "items": items,
                "device_count": device_count, "counts": counts}

    # ── 确认并运行 / 开始运行 ──

    def preflight_vpn(self) -> dict:
        """运行前 VPN 预检(GUI 线程同步调用, READY 设备逐台检查)。

        返回 {ok, detail, serials} — ok=False 时 GUI 弹窗确认。
        PTC 登录需要科学上网: 手机无 VPN 时登录页能加载但提交后
        跳转超时, 自动化会卡在登录界面反复重试。
        """
        from desktop.vpn_check import check_vpn
        serials = [r.serial for r in self.registry.records()
                   if r.ready and r.is_connected]
        if not serials:
            return {"ok": True, "detail": "无 READY 设备", "serials": []}
        for serial in serials:
            ok, detail = check_vpn(self.adb_locator.path, serial)
            logger.info("[VPN] 运行前预检 %s: ok=%s (%s)", serial, ok, detail)
            if not ok:
                return {"ok": False, "detail": detail,
                        "serials": [serial]}
        return {"ok": True, "detail": "", "serials": serials}

    def confirm_and_run(self, source: str = "", chat_name: str = "") -> dict:
        """确认并运行 = 验证输入 + 保存配置 + 环境检测 + 启动生产。"""
        if self._state not in (ApplicationRunState.STOPPED,
                               ApplicationRunState.ERROR):
            return {"ok": False, "error": "当前正在运行, 请先停止"}
        src = source or self.cfg.account_provider
        name = chat_name
        if src == "qq_ui" and not name:
            name = self.cfg.qq_group_name
        saved = self.save_chat_config(src, name)
        if not saved["ok"]:
            return saved
        return self._start_scheduler()

    def start(self) -> dict:
        """开始运行(STOPPED → RUNNING)。不重新保存配置。"""
        if self._state == ApplicationRunState.RUNNING:
            return {"ok": True, "detail": "already running"}
        if self._state in (ApplicationRunState.STARTING,
                           ApplicationRunState.STOPPING):
            return {"ok": False, "error": "正在切换状态, 请稍候"}
        return self._start_scheduler()

    def _start_scheduler(self) -> dict:
        """后台线程执行调度器启动(设备初始化可能耗时, 不阻塞 GUI)。"""
        self._set_state(ApplicationRunState.STARTING)
        threading.Thread(target=self._start_scheduler_worker, daemon=True,
                         name="desktop-start").start()
        return {"ok": True}

    def _start_scheduler_worker(self):
        try:
            if self.scheduler is None:
                from core.task_scheduler import TaskScheduler
                self.scheduler = TaskScheduler(self.cfg)
            # 文件来源先导入账号
            if self.cfg.account_provider in ("excel", "csv"):
                added, err = self._import_file_accounts()
                if err:
                    self._set_state(ApplicationRunState.ERROR)
                    if self.bus is not None:
                        self.bus.toast.emit("error", err)
                    return
                logger.info(f"[账号] 文件导入完成, 新增 {added} 个")
            result = self.scheduler.start()
            if not result.get("ok"):
                raise RuntimeError(str(result))
            self._record_run_start()
            self._start_polling()
            self._set_state(ApplicationRunState.RUNNING)
            if self.bus is not None:
                self.bus.toast.emit(
                    "info", f"运行已启动: {result.get('workers', 0)} 台设备")
        except Exception as e:
            logger.error(f"[桌面] 启动失败: {e}")
            self._set_state(ApplicationRunState.ERROR)
            if self.bus is not None:
                self.bus.toast.emit("error", f"启动失败: {e}")

    def _record_run_start(self):
        try:
            cur = self.db.execute(
                "INSERT INTO runs (started_at, chat_source, version) "
                "VALUES (?, ?, ?)",
                (time.time(), self.cfg.account_provider, APP_VERSION))
            self.run_id = cur.lastrowid
        except Exception as e:
            logger.warning(f"[桌面] run 记录失败: {e}")

    # ── 停止 ──

    def stop_all(self) -> dict:
        """停止全部: STOPPING → 所有 Worker 安全检查点退出 → STOPPED。"""
        if self._state not in (ApplicationRunState.RUNNING,
                               ApplicationRunState.ERROR):
            return {"ok": True, "detail": "not running"}
        self._set_state(ApplicationRunState.STOPPING)
        threading.Thread(target=self._stop_all_worker, daemon=True,
                         name="desktop-stop").start()
        return {"ok": True}

    def _stop_all_worker(self):
        self._save_checkpoints()
        try:
            if self.scheduler is not None:
                self.scheduler.stop()   # 优雅停止(join 30s/worker)
        except Exception as e:
            logger.error(f"[桌面] 停止异常: {e}")
        self._stop_polling()
        self._finish_run("user_stop")
        self._set_state(ApplicationRunState.STOPPED)
        if self.bus is not None:
            self.bus.toast.emit("info", "已停止全部设备")

    def stop_device(self, serial: str) -> dict:
        """停止单台设备(其他设备继续运行)。"""
        if self.scheduler is None:
            return {"ok": False, "error": "调度器未启动"}
        self._save_checkpoint_for(serial)
        result = self.scheduler.stop_device(serial)
        if result.get("ok"):
            logger.info(f"[桌面] 设备 {serial} 已停止")
        return result

    # ── 恢复/继续 ──

    def start_device(self, serial: str) -> dict:
        """启动/恢复单台设备。有 Checkpoint 时注入「停止后继续」配置:
        真实页面检测优先于 Checkpoint(worker DETECT_PAGE 会先识别页面)。"""
        if self.scheduler is None:
            return {"ok": False, "error": "调度器未启动"}
        cp = self._checkpoints.load(serial)
        account_id = cp.account_id if cp else None
        if account_id is not None:
            acc = self.accounts.get(account_id)
            if acc is not None:
                resume = dict(self.cfg.get("resume") or {})
                resume[serial] = {"account": acc.account,
                                  "trust_residual_session": True}
                self.cfg.system["resume"] = resume
                logger.info(f"[桌面] 注入恢复配置: {serial} → "
                            f"账号 {acc.masked()}")
        return self.scheduler.start_device(serial, account_id=account_id)

    def reidentify(self, serial: str) -> dict:
        """重新识别当前步骤: 检测手机真实页面并建议继续步骤。"""
        if self.scheduler is None:
            return {"ok": False, "error": "调度器未启动"}
        page, err = self.scheduler.detect_device_page(serial)
        if err:
            return {"ok": False, "error": f"页面识别失败: {err}"}
        if page is None:
            return {"ok": False, "error": "页面识别返回空"}
        suggested = PokemonStateRegistry.suggest_for_page(page)
        return {
            "ok": True,
            "page": page.value,
            "suggested": suggested.display_name if suggested else "—",
        }

    def resume_from_step(self, serial: str, step_key: str) -> dict:
        """从指定步骤重新开始: 校验真实页面 → 匹配才继续, 否则拒绝。"""
        if step_key == PokemonStateRegistry.AUTO:
            return {"ok": False,
                    "error": "「自动识别当前步骤」不支持手动恢复"}
        step = PokemonStateRegistry.by_key(step_key)
        if step is None:
            return {"ok": False, "error": f"未知步骤: {step_key}"}
        if self.scheduler is None:
            return {"ok": False, "error": "调度器未启动"}
        # 前置校验: 手机真实页面必须匹配步骤要求
        page, err = self.scheduler.detect_device_page(serial)
        if err:
            return {"ok": False, "error": f"页面识别失败: {err}"}
        mismatch = PokemonStateRegistry.validate(step, page)
        if mismatch:
            return {"ok": False, "error": mismatch}
        # 通过校验 → 从该状态继续
        return self.scheduler.resume_from_state(serial, step.worker_state)

    # ── 设备环境重置(人工触发) ──

    def reset_device_environment(self, serial: str,
                                 include_browser: bool = False) -> dict:
        """重置单台设备的自动化运行环境(后台线程, 不阻塞 GUI/他机)。

        仅人工触发(设备卡片按钮)。默认只清游戏数据 + Runtime 状态,
        不清理浏览器数据。只影响本设备: 其他设备 Worker 继续运行。
        """
        with self._lock:
            if serial in self._resetting:
                return {"ok": False, "error": "该设备正在重置中, 请稍候"}
            rec = self.registry.get(serial)
            if rec is None or rec.adb_state == "missing":
                return {"ok": False, "error": "设备未连接, 无法重置"}
            self._resetting.add(serial)
        threading.Thread(target=self._reset_device_worker,
                         args=(serial, include_browser), daemon=True,
                         name=f"device-reset-{serial}").start()
        return {"ok": True}

    def _reset_device_worker(self, serial: str, include_browser: bool):
        try:
            from desktop.device_reset import DeviceResetService
            DeviceResetService(self).reset(serial, include_browser)
        finally:
            with self._lock:
                self._resetting.discard(serial)
            if self.bus is not None:
                rec = self.registry.get(serial)
                ok = (rec is not None and rec.reset_state
                      not in ("RESETTING", "RESET_FAILED"))
                if ok and rec is not None:
                    self.bus.toast.emit(
                        "info", f"设备 {rec.model or serial[:12]} 环境重置完成")
                    # 刷新设备列表(重置后重新初始化, 状态变化立即可见)
                    self.rescan_now()

    # ── Checkpoint ──

    def _save_checkpoints(self):
        if self.scheduler is None:
            return
        for serial in self.scheduler.snapshot()["devices"]:
            self._save_checkpoint_for(serial["serial"])

    def _save_checkpoint_for(self, serial: str):
        """停止时保存该设备运行恢复点(临时; 关闭程序时清理)。"""
        try:
            snap = self.scheduler.snapshot()
            dev = next((d for d in snap["devices"]
                        if d["serial"] == serial), None)
            if dev is None:
                return
            page = ""
            try:
                p, _ = self.scheduler.detect_device_page(serial)
                if p is not None:
                    page = p.value
            except Exception:
                pass
            acc_id = None
            if self.scheduler._runtimes.get(serial):
                acc_id = self.scheduler._runtimes[serial].account_id
            cp = RuntimeCheckpoint(
                device_serial=serial,
                account_id=acc_id,
                masked_account=dev.get("account", ""),
                current_state=dev.get("worker_state", ""),
                detected_page=page,
                last_action="user_stop",
                app_version=APP_VERSION,
            )
            self._checkpoints.save(cp)
        except Exception as e:
            logger.debug(f"[桌面] checkpoint 保存失败 {serial}: {e}")

    # ── 轮询持久化(state_events/account_runs) ──

    def _start_polling(self):
        self._poll_stop.clear()
        self._last_device_account = {}
        self._poll_thread = threading.Thread(target=self._poll_loop,
                                             daemon=True,
                                             name="desktop-persist")
        self._poll_thread.start()

    def _stop_polling(self):
        self._poll_stop.set()

    def _poll_loop(self):
        """低频持久化: 状态变化事件 + 账号完成记录。不阻塞 Worker。"""
        last_state: dict[str, str] = {}
        while not self._poll_stop.is_set():
            time.sleep(2)
            try:
                if self.scheduler is None:
                    continue
                snap = self.scheduler.snapshot()
                for dev in snap["devices"]:
                    serial = dev["serial"]
                    state = dev["worker_state"]
                    if last_state.get(serial) != state:
                        last_state[serial] = state
                        self.db.execute(
                            "INSERT INTO state_events "
                            "(run_id, device_serial, masked_account, state, "
                            "created_at) VALUES (?,?,?,?,?)",
                            (self.run_id, serial, dev.get("account", ""),
                             state, time.time()))
                    # 账号变化 → 记录上一个账号完成
                    prev = self._last_device_account.get(serial)
                    cur = dev.get("account", "")
                    if prev and prev != cur:
                        self._record_account_done(serial, prev)
                    self._last_device_account[serial] = cur
            except Exception as e:
                logger.debug(f"[桌面] 轮询持久化异常: {e}")

    def _record_account_done(self, serial: str, masked: str):
        try:
            self.db.execute(
                "INSERT INTO account_runs "
                "(run_id, masked_account, device_serial, started_at, "
                "finished_at, result, app_version) VALUES (?,?,?,?,?,?,?)",
                (self.run_id, masked, serial, time.time(), time.time(),
                 "DONE", APP_VERSION))
        except Exception:
            pass

    def _finish_run(self, stop_reason: str):
        if self.run_id is None:
            return
        stats = self.accounts.stats()
        try:
            self.db.execute(
                "UPDATE runs SET ended_at=?, stop_reason=?, completed=?, "
                "failed=? WHERE id=?",
                (time.time(), stop_reason,
                 stats.get("SUCCESS", 0), stats.get("FAILED", 0),
                 self.run_id))
        except Exception as e:
            logger.warning(f"[桌面] run 结束记录失败: {e}")
        self.run_id = None

    # ── 查询 ──

    def snapshot(self) -> dict:
        """状态快照。设备数据来自 DeviceRegistry(单一状态源)。

        调度器未启动: registry 记录(热插拔监控实时更新);
        调度器运行中: 调度器快照 + registry 覆盖 worker/adb 状态。
        GUI 每秒轮询本方法 — 纯内存读取, 不再触发任何 subprocess。
        """
        if self.scheduler is None:
            devices = self._registry_devices()
            counts = self.registry.counts()
            return {"system": {"running": False, "paused": False,
                               "started_at": 0, "max_workers": 0,
                               "workers": 0},
                    "devices": devices, "counts": counts,
                    "accounts": self.accounts.stats(),
                    "throughput": {}}
        snap = self.scheduler.snapshot()
        # worker 状态同步回注册表(单一状态源)
        for dev in snap["devices"]:
            state = dev.get("worker_state", "-")
            self.registry.mark_worker(
                dev["serial"], running=state not in ("-", "STOPPED"),
                state=state)
        # 注册表补充 adb 连接状态(热插拔检测, 调度器快照不含)
        registry_map = {r.serial: r for r in self.registry.records()}
        for dev in snap["devices"]:
            rec = registry_map.get(dev["serial"])
            if rec is not None:
                dev["adb_state"] = rec.adb_state
                dev["ready"] = rec.ready
                dev["reject_reason"] = rec.reject_reason
                dev["brand"] = rec.brand or dev.get("brand", "-")
                dev["model"] = rec.model or dev.get("model", "-")
                dev["reset_state"] = rec.reset_state
                dev["reset_detail"] = rec.reset_detail
        snap["counts"] = self.registry.counts()
        return snap

    def _registry_devices(self) -> list[dict]:
        """registry → GUI 设备卡片数据结构(不触发扫描)。"""
        devices = []
        for r in self.registry.records():
            devices.append({
                "serial": r.serial,
                "model": r.model or "-",
                "brand": r.brand or "-",
                "resolution": r.resolution or "-",
                "adb_state": r.adb_state,
                "status": ("ONLINE" if r.is_connected
                           else ("OFFLINE" if r.adb_state == "offline"
                                 else "DEVICE_ERROR")),
                "ready": r.ready,
                "ready_detail": r.ready_detail,
                "reject_reason": r.reject_reason,
                "worker_state": r.worker_state,
                "page": "-",
                "account": "",
                "error": r.ready_detail if not r.ready else "",
                "reset_state": r.reset_state,
                "reset_detail": r.reset_detail,
                "success_count": 0,
                "fail_count": 0,
                "last_duration": 0,
            })
        return devices

    def get_history(self, scope: str = "today", limit: int = 500) -> list:
        """历史记录(账号执行结果)。scope: today / all。"""
        since = ""
        if scope == "today":
            lt = time.localtime()
            day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                     0, 0, 0, 0, 0, -1))
            since = f"AND started_at >= {day_start}"
        rows = self.db.query(
            f"SELECT * FROM task_results WHERE 1=1 {since} "
            f"ORDER BY started_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def export_history(self, dest: str) -> str:
        return self.results.export_xlsx(dest)

    # ── 关闭 ──

    def shutdown(self):
        """应用关闭: 安全停止 → flush → 清理 runtime(保留永久数据)。"""
        logger.info("[桌面] 正在关闭...")
        if self._state in (ApplicationRunState.RUNNING,
                           ApplicationRunState.STOPPING):
            try:
                self.scheduler.stop()
            except Exception:
                pass
        self._stop_polling()
        self.stop_monitor()
        self._finish_run("app_closed")
        try:
            self.db.close()
        except Exception:
            pass
        self.paths.clean_runtime()  # 仅清理临时 checkpoint/session
        logger.info(f"[桌面] 已关闭({APP_NAME} {APP_VERSION})")
