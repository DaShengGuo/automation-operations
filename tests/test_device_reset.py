"""
tests/test_device_reset.py
设备环境重置(规格 §18) — Worker 停止 / DEVICE_RESET 归还原因 /
Runtime 清理 / 历史数据保留 / 浏览器数据边界 / 失败详情 / 重置日志 /
控制器入口(防重复/防离线)。

说明: 本文件是 Mock 单元测试, 不代表真机测试结果; 真机测试在
交付设备就绪后按 §18 单独执行。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.base_game import TaskOutcome
from core.config import ControlConfig
from core.device_manager import InitReport
from core.device_worker import DeviceWorker
from core.task_scheduler import TaskScheduler
from desktop.checkpoint import CheckpointStore, RuntimeCheckpoint
from desktop.device_registry import DeviceRegistry
from desktop.device_reset import DeviceResetService
from models.account import AccountStatus
from models.device import AndroidDevice
from models.task import TaskResult, TaskRunState
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository
from tests.fakes import FakeDeviceManager, ScriptedAutomation


# ── 重置专用 Fake ──

class ResetAdb:
    """可编程 shell_rc — pm clear 结果 / 浏览器解析结果 / ADB 状态"""

    def __init__(self):
        self.path = "fake-adb"
        self.state = "device"
        self.online_ok = True
        self.game_clear = (0, "Success")        # (rc, out)
        self.browser_resolve = (0, "com.android.chrome/MainActivity")
        self.browser_clear_ok = True
        self.commands: list[str] = []

    def get_state(self, serial):
        return self.state

    def wait_online(self, serial, timeout=30):
        return self.online_ok

    def shell_rc(self, serial, cmd, timeout=15):
        self.commands.append(cmd)
        if cmd.startswith("pm clear "):
            pkg = cmd.split()[-1]
            if pkg == "com.nianticlabs.pokemongo":
                return self.game_clear
            return (0, "Success") if self.browser_clear_ok else (1, "Failed")
        if cmd.startswith("cmd resolve-activity"):
            return self.browser_resolve
        return 0, ""

    def is_app_installed(self, serial, pkg):
        return True


class ResetDeviceManager:
    """重置专用 DM: init_device 记录调用, create_controller 可编程"""

    def __init__(self, adb: ResetAdb, serial: str = "DEV-A"):
        self.adb = adb
        self.device = AndroidDevice(serial=serial, app_installed=True)
        self.init_calls: list[tuple] = []

    def create_controller(self, serial):
        return SimpleNamespace(device=object(), connect=lambda: None)

    def scan(self, fast=False):
        return [self.device]

    def get_device(self, serial):
        return self.device

    def init_device(self, device, target_package=""):
        self.init_calls.append((device.serial, target_package))
        report = InitReport(serial=device.serial)
        report.add("adb_online", "PASS")
        report.passed = True
        return report


class FakeBus:
    """记录 toast 事件(重置失败弹窗用)"""

    def __init__(self):
        self.toasts: list[tuple] = []

        class _Sig:
            def __init__(self, owner):
                self.owner = owner

            def emit(self, *args):
                self.owner.toasts.append(args)

        self.toast = _Sig(self)


class ResetController:
    """duck-typed controller — DeviceResetService 的依赖面"""

    def __init__(self, cfg, accounts, adb: ResetAdb, scheduler=None,
                 bus=None, runtime_dir: Path = None, results=None):
        self.cfg = cfg
        self.registry = DeviceRegistry()
        self.accounts = accounts
        self.results = results
        self.scheduler = scheduler
        self.bus = bus
        self.adb_locator = SimpleNamespace(path="fake-adb")
        self._checkpoints = CheckpointStore(
            runtime_dir or cfg.project_root / "runtime")
        self._adb = adb
        self._dm = None

    def device_manager(self):
        if self._dm is None:
            self._dm = ResetDeviceManager(self._adb)
        return self._dm


# ── fixtures ──

@pytest.fixture
def tmp_cfg(tmp_path):
    """隔离配置 — 项目根指向 tmp_path, 不读真实 config/*.yaml"""
    return ControlConfig(project_root=tmp_path)


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """隔离客户数据目录(不影响真实 %LOCALAPPDATA%)"""
    monkeypatch.setenv("POKEMON_AUTOMATION_DATA_DIR", str(tmp_path / "data"))
    from desktop.app_paths import AppPaths, _paths
    _paths = None  # noqa: F841 — 强制重建(见 fixture teardown)
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def _reset_paths():
    yield
    from desktop import app_paths
    app_paths._paths = None


@pytest.fixture
def repos(tmp_cfg):
    db = Database(tmp_cfg.db_path)
    return (AccountRepository(db, stale_minutes=10),
            TaskResultRepository(db))


def _install_detector(monkeypatch, value="PTC_LOGIN_PAGE"):
    """重置流程第 12 步 detect_state() 的真实页面"""
    state = SimpleNamespace(value=value)
    automation = SimpleNamespace(
        detector=SimpleNamespace(detect=lambda: state))
    monkeypatch.setattr("automation.create_automation",
                        lambda name, controller, cfg: automation)


def _install_device_info(monkeypatch):
    import device_profiles
    monkeypatch.setattr(
        device_profiles.DeviceInfo, "from_adb",
        staticmethod(lambda serial, adb_path: SimpleNamespace(
            serial=serial, manufacturer="realme", brand="realme",
            model="GT Neo2", android_version="13",
            width=1080, height=2400)))


def _read_reset_log(cfg) -> list[str]:
    path = Path(cfg.logs_dir) / "device_reset.log"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


# ── 服务层(无调度器 / 失败路径 / 边界) ──

class TestResetService:
    def test_success_flow_default_no_browser(self, tmp_cfg, repos,
                                             monkeypatch):
        """默认重置: pm clear 游戏, 不碰浏览器, 回到真实页面 READY"""
        accounts, results = repos
        adb = ResetAdb()
        c = ResetController(tmp_cfg, accounts, adb)
        _install_detector(monkeypatch, "PTC_LOGIN_PAGE")
        _install_device_info(monkeypatch)

        outcome = DeviceResetService(c).reset("DEV-A")

        assert outcome.ok is True
        assert outcome.detected_state == "PTC_LOGIN_PAGE"   # 真实检测, 非假设
        rec = c.registry.get("DEV-A")
        assert rec.reset_state == ""
        assert rec.ready is True
        assert "环境重置完成" in rec.ready_detail
        # 游戏数据已清; 浏览器命令从未执行(默认 OFF)
        assert any(cmd == "pm clear com.nianticlabs.pokemongo"
                   for cmd in adb.commands)
        assert not any("resolve-activity" in cmd for cmd in adb.commands)
        # 设备重新初始化(§3 重新初始化自动化环境)
        assert c.device_manager().init_calls == [
            ("DEV-A", "com.nianticlabs.pokemongo")]
        # 重置日志: STARTED + SUCCESS, REASON 恒为 MANUAL(§6 禁止自动触发)
        lines = _read_reset_log(tmp_cfg)
        assert len(lines) == 2
        assert lines[0].startswith("20") and "RESULT=STARTED" in lines[0]
        assert "RESULT=SUCCESS" in lines[1]
        for line in lines:
            assert "REASON=MANUAL" in line
            assert "ACTION=DEVICE_ENVIRONMENT_RESET" in line
            assert "DEVICE=DEV-A" in line
        assert "GAME_DATA=CLEARED" in lines[1]
        assert "BROWSER_DATA=NOT_TOUCHED" in lines[1]
        assert "DETECTED_STATE=PTC_LOGIN_PAGE" in lines[1]

    def test_reset_without_scheduler(self, tmp_cfg, repos, monkeypatch):
        """应用未启动(scheduler=None)时重置仍可用 — 跳过停止 Worker"""
        accounts, results = repos
        adb = ResetAdb()
        c = ResetController(tmp_cfg, accounts, adb)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)

        outcome = DeviceResetService(c).reset("DEV-A")

        assert outcome.ok is True
        assert c.registry.get("DEV-A").ready is True

    def test_pm_clear_failure_sets_reset_failed_with_detail(
            self, tmp_cfg, repos, monkeypatch):
        """§9/§10: pm clear 失败 → RESET_FAILED + 步骤/原因/详细"""
        accounts, results = repos
        adb = ResetAdb()
        adb.game_clear = (1, "Failed")
        bus = FakeBus()
        c = ResetController(tmp_cfg, accounts, adb, bus=bus)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)

        outcome = DeviceResetService(c).reset("DEV-A")

        assert outcome.ok is False
        assert outcome.step == "CLEAR_GAME_DATA"
        assert "pm clear" in outcome.error
        rec = c.registry.get("DEV-A")
        assert rec.reset_state == "RESET_FAILED"
        assert "CLEAR_GAME_DATA" in rec.reset_detail
        assert rec.worker_running is False
        # 失败 toast 必须带 设备/步骤/错误/详细(§10 不给笼统"重置失败")
        assert len(bus.toasts) == 1
        level, msg = bus.toasts[0]
        assert level == "error"
        assert "步骤: CLEAR_GAME_DATA" in msg
        assert "错误:" in msg and "详细:" in msg
        # 日志如实记录失败位置
        lines = _read_reset_log(tmp_cfg)
        assert "RESULT=FAILED" in lines[-1]
        assert "STEP=CLEAR_GAME_DATA" in lines[-1]
        assert "GAME_DATA=FAILED" in lines[-1]

    def test_failure_before_game_data_logs_unknown(self, tmp_cfg, repos,
                                                   monkeypatch):
        """清数据之前意外异常 → GAME_DATA=UNKNOWN(不谎报已清/清失败)"""
        accounts, results = repos
        adb = ResetAdb()
        c = ResetController(tmp_cfg, accounts, adb)

        def _boom(_):
            raise RuntimeError("checkpoint 目录不可写")
        c._checkpoints = SimpleNamespace(load=lambda serial: None,
                                         clear=_boom)

        outcome = DeviceResetService(c).reset("DEV-A")

        assert outcome.ok is False
        assert outcome.step == "UNEXPECTED"
        assert c.registry.get("DEV-A").reset_state == "RESET_FAILED"
        lines = _read_reset_log(tmp_cfg)
        assert "RESULT=FAILED" in lines[-1]
        assert "GAME_DATA=UNKNOWN" in lines[-1]

    def test_browser_cleared_only_when_requested_and_resolvable(
            self, tmp_cfg, repos, monkeypatch):
        """§5: 高级选项 ON 且解析出真实浏览器 → 才执行浏览器清理"""
        accounts, results = repos
        adb = ResetAdb()
        c = ResetController(tmp_cfg, accounts, adb)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)

        outcome = DeviceResetService(c).reset("DEV-A",
                                              include_browser=True)

        assert outcome.ok is True
        assert "pm clear com.android.chrome" in adb.commands
        lines = _read_reset_log(tmp_cfg)
        assert "BROWSER_DATA=CLEARED" in lines[-1]

    def test_browser_skipped_when_unresolvable(self, tmp_cfg, repos,
                                               monkeypatch):
        """§5: 解析不出默认浏览器 → 无法确认影响范围, 不执行"""
        accounts, results = repos
        adb = ResetAdb()
        adb.browser_resolve = (0, "")
        c = ResetController(tmp_cfg, accounts, adb)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)

        outcome = DeviceResetService(c).reset("DEV-A",
                                              include_browser=True)

        assert outcome.ok is True
        assert not any("pm clear com.android.chrome" in cmd
                       for cmd in adb.commands)
        lines = _read_reset_log(tmp_cfg)
        assert "BROWSER_DATA=SKIPPED" in lines[-1]

    def test_browser_skipped_when_resolved_to_non_browser(
            self, tmp_cfg, repos, monkeypatch):
        """§5: 解析结果不是浏览器(系统设置等) → 拒绝清理"""
        accounts, results = repos
        adb = ResetAdb()
        adb.browser_resolve = (0, "com.android.settings/.Settings")
        c = ResetController(tmp_cfg, accounts, adb)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)

        outcome = DeviceResetService(c).reset("DEV-A",
                                              include_browser=True)

        assert outcome.ok is True
        assert not any("pm clear com.android.settings" in cmd
                       for cmd in adb.commands)
        assert "BROWSER_DATA=SKIPPED" in _read_reset_log(tmp_cfg)[-1]

    def test_resume_config_purged_and_checkpoint_cleared(
            self, tmp_cfg, repos, monkeypatch):
        """§3: RuntimeCheckpoint + resume 注入配置随重置清除(仅本设备)"""
        accounts, results = repos
        adb = ResetAdb()
        c = ResetController(tmp_cfg, accounts, adb)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)
        c._checkpoints.save(RuntimeCheckpoint(device_serial="DEV-A",
                                              account_id=1,
                                              masked_account="Rk3***658"))
        c._checkpoints.save(RuntimeCheckpoint(device_serial="DEV-B",
                                              account_id=2))
        tmp_cfg.system["resume"] = {
            "DEV-A": {"account": "Rk3***658", "trust_residual_session": True},
            "DEV-B": {"account": "Dr3***820"},
        }

        outcome = DeviceResetService(c).reset("DEV-A")

        assert outcome.ok is True
        assert c._checkpoints.load("DEV-A") is None
        assert c._checkpoints.load("DEV-B") is not None   # 他机不动
        assert "DEV-A" not in tmp_cfg.system["resume"]
        assert "DEV-B" in tmp_cfg.system["resume"]

    def test_history_results_and_logs_never_touched(
            self, tmp_cfg, repos, monkeypatch):
        """§15: 重置只清手机端环境 — SQLite 历史/运行日志保留"""
        accounts, results = repos
        adb = ResetAdb()
        c = ResetController(tmp_cfg, accounts, adb, results=results)
        _install_detector(monkeypatch)
        _install_device_info(monkeypatch)
        results.save(TaskResult(account_id=1, account="Rk3***658",
                                device_serial="DEV-A",
                                state=TaskRunState.SUCCESS,
                                started_at=1.0, finished_at=2.0))
        log_file = Path(tmp_cfg.logs_dir) / "app.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("历史日志内容", encoding="utf-8")

        DeviceResetService(c).reset("DEV-A")

        assert len(results.list(device_serial="DEV-A")) == 1   # 历史保留
        assert log_file.read_text(encoding="utf-8") == "历史日志内容"


# ── 真实调度器: Worker 运行中重置(§7/§8/§14) ──

class BlockingAutomation(ScriptedAutomation):
    """execute_task 阻塞直到 kill_evt 置位(模拟 pm clear 杀掉游戏进程)"""

    def __init__(self, serial="", kill_evt: threading.Event = None):
        super().__init__(serial)
        self.kill_evt = kill_evt or threading.Event()

    def execute_task(self, account):
        self.calls.append("execute_task")
        deadline = time.time() + 60
        while time.time() < deadline and not self.kill_evt.is_set():
            time.sleep(0.02)
        if self.kill_evt.is_set():
            raise RuntimeError("游戏进程被杀(pm clear)")
        return TaskOutcome(True)


def _wait_for(predicate, timeout=10.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestWorkerResetIntegration:
    def test_reset_stops_worker_returns_account_and_spares_others(
            self, tmp_cfg, repos, monkeypatch):
        """真机语义: Worker 运行中重置 → 停止 + RETRY(DEVICE_RESET),
        Runtime 清理, 历史保留, 重新初始化, 另一台设备不受影响"""
        accounts, results = repos
        tmp_cfg.system["poll_interval"] = 0.02   # 空转轮询加速
        gates = {"DEV-A": threading.Event(), "DEV-B": threading.Event()}
        fake_dm = FakeDeviceManager()
        monkeypatch.setattr("core.task_scheduler.DeviceManager",
                            lambda cfg: fake_dm)
        monkeypatch.setattr("core.task_scheduler.create_automation",
                            lambda name, controller, cfg: BlockingAutomation(
                                controller.serial, gates[controller.serial]))
        s = TaskScheduler(tmp_cfg)
        try:
            fake_dm.rows = [AndroidDevice(serial="DEV-A"),
                            AndroidDevice(serial="DEV-B")]
            acc_a = accounts.add("user001", "p", max_retry=3)
            acc_b = accounts.add("user002", "p", max_retry=3)
            s.start()
            assert _wait_for(lambda: (
                accounts.get(acc_a).status == AccountStatus.RUNNING
                and accounts.get(acc_b).status == AccountStatus.RUNNING))

            # 重置前留一份 checkpoint + 一条历史结果
            adb = ResetAdb()
            c = ResetController(tmp_cfg, accounts, adb, scheduler=s,
                                results=results)
            c._checkpoints.save(RuntimeCheckpoint(
                device_serial="DEV-A", account_id=acc_a,
                masked_account="use***001", current_state="EXECUTE_TASK"))
            results.save(TaskResult(account_id=acc_a, account="use***001",
                                    device_serial="DEV-A",
                                    state=TaskRunState.SUCCESS,
                                    started_at=1.0, finished_at=2.0))
            _install_detector(monkeypatch, "RETURNING_PLAYER")
            _install_device_info(monkeypatch)

            # 后台执行重置; 等 request_stop 落位后模拟游戏进程被杀
            worker_a = s._workers["DEV-A"]
            outcome_holder = {}

            def _run_reset():
                outcome_holder["outcome"] = DeviceResetService(c).reset(
                    "DEV-A")

            t = threading.Thread(target=_run_reset, daemon=True)
            t.start()
            assert _wait_for(lambda: worker_a._local_stop.is_set())
            assert worker_a._stop_reason == "DEVICE_RESET"
            gates["DEV-A"].set()      # 模拟 pm clear 打断执行中的任务
            t.join(timeout=15)
            assert not t.is_alive()

            # Worker 已停止(§7), 在途账号 RETRY + DEVICE_RESET(§14)
            outcome = outcome_holder["outcome"]
            assert outcome.ok is True
            assert not worker_a.is_alive()
            assert "DEV-A" not in s._workers
            acc = accounts.get(acc_a)
            assert acc.status == AccountStatus.RETRY
            assert "DEVICE_RESET" in acc.last_error
            assert "DEV-A" not in s._runtimes            # Runtime 清理
            assert c._checkpoints.load("DEV-A") is None  # Checkpoint 清理

            # 另一台设备继续运行(§8)
            assert "DEV-B" in s._workers
            assert s._workers["DEV-B"].is_alive()
            assert accounts.get(acc_b).status == AccountStatus.RUNNING

            # 历史保留 + 设备重新初始化 + 日志
            assert len(results.list(device_serial="DEV-A")) == 1
            assert c.device_manager().init_calls == [
                ("DEV-A", "com.nianticlabs.pokemongo")]
            lines = _read_reset_log(tmp_cfg)
            assert "RESULT=SUCCESS" in lines[-1]
            assert "PREV_ACCOUNT=use***001" in lines[-1]
            assert "DETECTED_STATE=RETURNING_PLAYER" in lines[-1]
        finally:
            for evt in gates.values():
                evt.set()
            if s.running:
                s.stop()


# ── Worker 归还机制(直接调用 _shutdown) ──

class TestWorkerShutdown:
    def _make_worker(self, tmp_cfg, accounts, results):
        return DeviceWorker(
            "DEV-A", tmp_cfg, FakeDeviceManager(), accounts, results,
            automation_factory=lambda serial: None,
            stop_event=threading.Event(), pause_event=threading.Event())

    def test_shutdown_returns_inflight_account_retry_with_reset_reason(
            self, tmp_cfg, repos):
        accounts, results = repos
        acc_id = accounts.add("user001", "p", max_retry=3)
        accounts.claim_next("DEV-A")
        accounts.mark_running(acc_id, "DEV-A")
        worker = self._make_worker(tmp_cfg, accounts, results)
        worker.account = accounts.get(acc_id)

        worker.request_stop("DEVICE_RESET")
        worker._shutdown()

        acc = accounts.get(acc_id)
        assert acc.status == AccountStatus.RETRY
        assert "DEVICE_RESET" in acc.last_error
        assert acc.status != AccountStatus.SUCCESS   # §14 不得误标成功

    def test_shutdown_releases_prefetched_to_pending_without_burn(
            self, tmp_cfg, repos):
        """预取账号: 停止/重置时回 PENDING, 不烧重试次数"""
        accounts, results = repos
        acc_id = accounts.add("user001", "p", max_retry=3)
        prefetch_id = accounts.add("user002", "p", max_retry=3)
        accounts.claim_next("DEV-A")          # LOCKED
        accounts.claim_next("DEV-A")          # LOCKED(预取槽位)
        accounts.mark_running(acc_id, "DEV-A")
        worker = self._make_worker(tmp_cfg, accounts, results)
        worker.account = accounts.get(acc_id)
        worker._prefetched_account = accounts.get(prefetch_id)

        worker.request_stop("DEVICE_RESET")
        worker._shutdown()

        prefetched = accounts.get(prefetch_id)
        assert prefetched.status == AccountStatus.PENDING
        assert prefetched.retry_count == 0
        assert worker._prefetched_account is None


# ── Checkpoint 单设备清理 ──

class TestCheckpointClear:
    def test_clear_only_targets_own_serial(self, tmp_path):
        store = CheckpointStore(tmp_path)
        store.save(RuntimeCheckpoint(device_serial="DEV-A", account_id=1))
        store.save(RuntimeCheckpoint(device_serial="DEV-B", account_id=2))

        store.clear("DEV-A")

        assert store.load("DEV-A") is None
        assert store.load("DEV-B") is not None


# ── 控制器入口(防重复/防离线/参数透传) ──

class TestControllerResetEndpoint:
    @pytest.fixture
    def controller(self, tmp_data, monkeypatch):
        from desktop.app_paths import AppPaths, _paths
        import desktop.app_paths as ap
        ap._paths = AppPaths(tmp_data)
        ap._paths.ensure_dirs()
        from desktop.controller import DesktopAppController
        c = DesktopAppController(bus=None)
        calls = []

        class FakeService:
            """记录式重置服务 — 不碰真机/真 ADB"""

            def __init__(self, controller):
                pass

            def reset(self, serial, include_browser=False):
                calls.append((serial, include_browser))
                c.registry.mark_resetting(serial, "RESETTING")
                c.registry.mark_resetting(serial, "", "")
                c.registry.mark_ready(serial, True, "环境重置完成")
                from desktop.device_reset import ResetOutcome
                return ResetOutcome(ok=True)

        monkeypatch.setattr("desktop.device_reset.DeviceResetService",
                            FakeService)
        return c, calls

    def test_reset_requires_connected_device(self, controller):
        c, calls = controller
        result = c.reset_device_environment("MISSING-DEV")
        assert result.get("ok") is False
        assert "未连接" in result.get("error", "")
        assert calls == []

    def test_reset_endpoint_spawns_and_completes(self, controller):
        c, calls = controller
        c.registry.refresh_from_adb(
            [{"serial": "DEV-A", "state": "device"}])
        result = c.reset_device_environment("DEV-A")
        assert result.get("ok") is True
        assert _wait_for(lambda: c.registry.get("DEV-A").reset_state == "",
                         timeout=5)
        assert calls == [("DEV-A", False)]          # 默认不含浏览器
        assert c.registry.get("DEV-A").ready is True

    def test_reset_passes_browser_option(self, controller):
        c, calls = controller
        c.registry.refresh_from_adb(
            [{"serial": "DEV-A", "state": "device"}])
        c.reset_device_environment("DEV-A", include_browser=True)
        assert _wait_for(lambda: c.registry.get("DEV-A").reset_state == "",
                         timeout=5)
        assert calls == [("DEV-A", True)]

    def test_duplicate_reset_blocked(self, controller):
        c, calls = controller
        c.registry.refresh_from_adb(
            [{"serial": "DEV-A", "state": "device"}])
        c._resetting.add("DEV-A")                   # 已有重置进行中
        result = c.reset_device_environment("DEV-A")
        assert result.get("ok") is False
        assert "正在重置" in result.get("error", "")
        assert calls == []
