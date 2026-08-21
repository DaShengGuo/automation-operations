"""
tests/test_desktop.py
桌面层单元测试 — 版本/路径/恢复点/状态注册表/迁移/控制器(不连真机)。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


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


class TestVersion:
    def test_version_source(self):
        from version import APP_NAME, APP_VERSION, APP_VERSION_TAG
        assert APP_NAME == "宝可梦自动化购买脚本"
        assert APP_VERSION_TAG.startswith("v")
        assert APP_VERSION == APP_VERSION_TAG[1:]

    def test_compare_versions(self):
        from desktop.update_service import compare_versions
        assert compare_versions("1.0.1", "1.0.0") > 0
        assert compare_versions("1.0.0", "1.0.1") < 0
        assert compare_versions("1.0.0", "1.0.0") == 0
        assert compare_versions("1.1.0", "1.0.9") > 0
        assert compare_versions("2.0.0", "1.9.9") > 0

    def test_update_service_not_configured(self):
        from desktop.update_service import UpdateService
        info = UpdateService(endpoint="").check()
        assert not info.configured
        assert "未配置" in info.error


class TestAppPaths:
    def test_dirs_and_runtime_clean(self, tmp_data):
        from desktop.app_paths import AppPaths
        paths = AppPaths(tmp_data)
        paths.ensure_dirs()
        assert paths.db_file.parent.exists()
        # runtime 清理不删除永久数据
        marker = paths.logs / "keep.log"
        marker.write_text("x", encoding="utf-8")
        (paths.runtime / "cp.json").write_text("{}", encoding="utf-8")
        paths.clean_runtime()
        assert not (paths.runtime / "cp.json").exists()
        assert marker.exists()

    def test_resource_root_exists(self):
        from desktop.app_paths import resource_root
        root = resource_root()
        assert (root / "config").is_dir()
        assert (root / "templates").is_dir()


class TestCheckpoint:
    def test_save_load_clear(self, tmp_data):
        from desktop.checkpoint import CheckpointStore, RuntimeCheckpoint
        store = CheckpointStore(tmp_data / "runtime")
        cp = RuntimeCheckpoint(device_serial="ABC123", account_id=8,
                               masked_account="Dr3***820",
                               current_state="EXECUTE_TASK")
        store.save(cp)
        loaded = store.load("ABC123")
        assert loaded is not None
        assert loaded.masked_account == "Dr3***820"
        assert loaded.account_id == 8
        store.clear_all()
        assert store.load("ABC123") is None

    def test_load_missing(self, tmp_data):
        from desktop.checkpoint import CheckpointStore
        assert CheckpointStore(tmp_data / "runtime").load("NOPE") is None


class TestStateRegistry:
    def test_ordered_steps(self):
        from desktop.state_registry import PokemonStateRegistry
        steps = PokemonStateRegistry.ordered_steps()
        assert len(steps) >= 10
        assert [s.order for s in steps] == sorted(s.order for s in steps)
        assert PokemonStateRegistry.by_key("AUTO") is None

    def test_suggest_for_page(self):
        from automation.pokemon_go.states import PokemonGoState
        from desktop.state_registry import PokemonStateRegistry
        s = PokemonStateRegistry.suggest_for_page(PokemonGoState.SHOP)
        assert s is not None and s.key == "EXECUTE_TASK"
        s = PokemonStateRegistry.suggest_for_page(PokemonGoState.SETTINGS)
        assert s is not None and s.key == "LOGOUT"

    def test_validate_mismatch(self):
        """人工选择错误状态: 真实页面不匹配 → 拒绝并提示"""
        from automation.pokemon_go.states import PokemonGoState
        from desktop.state_registry import PokemonStateRegistry
        shop_step = PokemonStateRegistry.by_key("EXECUTE_TASK")
        err = PokemonStateRegistry.validate(shop_step,
                                            PokemonGoState.PTC_LOGIN_PAGE)
        assert err is not None
        assert "不匹配" in err and "PTC_LOGIN_PAGE" in err
        ok = PokemonStateRegistry.validate(shop_step, PokemonGoState.SHOP)
        assert ok is None


class TestMigrations:
    def test_fresh_db_gets_version(self, tmp_data):
        from migrations import SCHEMA_VERSION, migrate
        from storage.database import SCHEMA
        db = tmp_data / "db" / "runtime.db"
        result = migrate(db, tmp_data / "backups",
                         lambda c: c.executescript(SCHEMA))
        assert result["ok"]
        assert result["to_version"] == SCHEMA_VERSION
        assert result["backup"] is None  # 新库不备份

    def test_upgrade_backs_up_old_db(self, tmp_data):
        import sqlite3
        from migrations import SCHEMA_VERSION, migrate
        from storage.database import SCHEMA
        db = tmp_data / "db" / "runtime.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO accounts VALUES (1)")
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        conn.close()
        result = migrate(db, tmp_data / "backups",
                         lambda c: c.executescript(SCHEMA))
        assert result["ok"]
        assert result["to_version"] == SCHEMA_VERSION
        assert result["backup"] and Path(result["backup"]).exists()
        # 旧数据保留
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        conn.close()

    def test_failed_migration_keeps_backup(self, tmp_data):
        import sqlite3
        from migrations import migrate
        db = tmp_data / "db" / "runtime.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        def boom(c):
            raise sqlite3.Error("模拟迁移失败")
        result = migrate(db, tmp_data / "backups", boom)
        assert not result["ok"]
        assert result["backup"] and Path(result["backup"]).exists()


class TestController:
    def _make_controller(self, tmp_data):
        from desktop.app_paths import AppPaths, _paths
        import desktop.app_paths as ap
        ap._paths = AppPaths(tmp_data)
        ap._paths.ensure_dirs()
        from desktop.controller import DesktopAppController
        return DesktopAppController(bus=None)

    def test_save_and_read_chat_config(self, tmp_data):
        c = self._make_controller(tmp_data)
        assert c.save_chat_config("qq_ui", "测试群")["ok"]
        source, name = c.chat_config()
        assert (source, name) == ("qq_ui", "测试群")
        # 重启读取(新 controller 实例)
        c2 = self._make_controller(tmp_data)
        assert c2.chat_config() == ("qq_ui", "测试群")
        c.shutdown()
        c2.shutdown()

    def test_save_chat_config_requires_name(self, tmp_data):
        c = self._make_controller(tmp_data)
        result = c.save_chat_config("qq_ui", "  ")
        assert not result["ok"]
        assert "请输入" in result["error"]
        c.shutdown()

    def test_save_chat_config_file_must_exist(self, tmp_data):
        c = self._make_controller(tmp_data)
        result = c.save_chat_config("excel", "N:/不存在/文件.xlsx")
        assert not result["ok"]
        assert "不存在" in result["error"]
        c.shutdown()

    def test_stopped_on_start(self, tmp_data):
        """软件打开时必须是 STOPPED, 禁止自动运行"""
        c = self._make_controller(tmp_data)
        from desktop.runtime_state import ApplicationRunState
        assert c.state == ApplicationRunState.STOPPED
        assert c.scheduler is None
        c.shutdown()

    def test_reidentify_requires_scheduler(self, tmp_data):
        c = self._make_controller(tmp_data)
        result = c.reidentify("ABC")
        assert not result["ok"]
        c.shutdown()

    def test_start_without_accounts_rejected_sync(self, tmp_data):
        """规格 2026-08-21 §一: start() 第一步同步检查账号 —
        无账号立即返回 error(不切 STARTING/不建 Worker)。"""
        c = self._make_controller(tmp_data)
        from desktop.runtime_state import ApplicationRunState
        result = c.start()
        assert not result["ok"], "无账号必须拒绝启动"
        assert "没有可执行账号" in result["error"]
        assert c.state == ApplicationRunState.STOPPED, \
            "拒绝启动时不得切 STARTING"
        assert c.scheduler is None, "不得创建调度器/Worker"
        c.shutdown()

    def test_start_without_accounts_blocked(self, tmp_data):
        """规格 2026-08-21 §七: 队列模式无账号可执行 → 禁止启动 Worker,
        弹窗提示「当前没有可执行账号」, 状态回到 STOPPED。"""
        c = self._make_controller(tmp_data)
        toasts = []

        class _Toast:
            def emit(self, kind, msg):
                toasts.append((kind, msg))

        class _AppState:
            def emit(self, value):
                pass

        class _Bus:
            toast = _Toast()
            app_state = _AppState()

        c.bus = _Bus()
        c.scheduler = object()   # 非 None, 跳过 TaskScheduler 构建
        c._start_scheduler_worker()
        from desktop.runtime_state import ApplicationRunState
        assert c.state == ApplicationRunState.STOPPED, \
            "无账号必须回到 STOPPED(禁止启动)"
        assert toasts and "没有可执行账号" in toasts[0][1], \
            f"必须弹窗提示先添加账号(实际 {toasts})"
        c.shutdown()

    def test_start_with_pending_account_allowed(self, tmp_data):
        """队列有等待账号 → 账号检查放行, 不拦截启动。"""
        c = self._make_controller(tmp_data)
        result = c.add_account("FAKE-DEV", "userA", "p1")
        assert result["ok"]
        c.scheduler = object()   # 非 None
        c._start_scheduler_worker()
        # 未触发无账号拦截: 状态被推进到 RUNNING(或 STARTING 后续流程)
        from desktop.runtime_state import ApplicationRunState
        assert c.state != ApplicationRunState.STOPPED, \
            "有等待账号必须放行启动"
        c.shutdown()


class TestClaimSpecific:
    def test_claim_specific_binds_only_pending(self, tmp_path):
        from storage.database import Database
        from storage.repositories import AccountRepository
        db = Database(tmp_path / "runtime.db")
        repo = AccountRepository(db)
        a1 = repo.add("user001", "p")
        repo.mark_running(a1, "DEV1")          # 已被领
        acc = repo.claim_specific(a1, "DEV2")
        assert acc is None                     # 不抢占 RUNNING
        a2 = repo.add("user002", "p")
        acc = repo.claim_specific(a2, "DEV2")
        assert acc is not None and acc.id == a2
        assert repo.get(a2).status == "LOCKED"
        assert repo.get(a2).device_serial == "DEV2"
        db.close()


class TestResumeInjection:
    def test_residual_session_same_account_skips_logout(self, tmp_path,
                                                         monkeypatch):
        """桌面版停止后继续: 残留会话 == 本账号 → 跳过登出/重登录,
        直接继续任务。账号不匹配 → 照常登出(归属安全)。"""
        from core.config import ControlConfig
        from core.device_worker import DeviceWorker
        from models.account import AccountStatus
        from models.page_state import PageState
        from storage.database import Database
        from storage.repositories import AccountRepository, \
            TaskResultRepository
        from tests.fakes import (FakeDeviceManager, ScriptedAutomation)
        import threading

        tmp_cfg = ControlConfig(project_root=tmp_path)
        tmp_cfg.system["resume"] = {
            "FAKE-R": {"account": "user001", "trust_residual_session": True}}

        class ResidualAutomation(ScriptedAutomation):
            def __init__(self, serial=""):
                super().__init__(serial)
                self.residual = True
                self.login_done = True

            def detect_page(self):
                self.calls.append("detect_page")
                return PageState.HOME  # 始终残留 HOME(已登录)

        db = Database(tmp_path / "runtime.db")
        accounts = AccountRepository(db)
        results = TaskResultRepository(db)
        accounts.add("user001", "p")
        stop, pause = threading.Event(), threading.Event()
        from core.device_worker import WorkerRuntime
        rt = WorkerRuntime(serial="FAKE-R")
        w = DeviceWorker("FAKE-R", tmp_cfg, FakeDeviceManager(),
                         accounts, results,
                         automation_factory=lambda s: ResidualAutomation(s),
                         stop_event=stop, pause_event=pause, runtime=rt,
                         prefetched_account=accounts.claim_next("FAKE-R"))
        # 驱动: 领取同账号 → DETECT_PAGE 看到残留会话 == resume 账号
        # → _login_done=True → WAIT_HOME(不登出)
        for _ in range(30):
            w._tick()
            if "logout" in w.automation.calls or \
                    rt.state in ("WAIT_HOME", "EXECUTE_TASK"):
                break
        assert "logout" not in w.automation.calls  # 跳过登出
        assert w._login_done
        db.close()
