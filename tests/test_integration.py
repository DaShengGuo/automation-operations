"""
tests/test_integration.py
中控系统集成测试（不连设备）

覆盖端到端链路: 账号入库 → 原子领取 → DeviceWorker 状态机流水线
→ 结果落库/导出, 以及生产安全护栏(支付 dry_run / 双重授权开关 /
配置缺失快速失败 / 时间预算释放设备 / 多 Worker 并发不重复执行)。

说明: 本文件全部使用 Fake 设备与脚本化自动化, 不代表真机测试结果。
真机验证以 `python main.py doctor` / compat report 为准。
"""
from __future__ import annotations

import threading
import time

import pytest

from automation.base_game import BaseGameAutomation, LoginResult, TaskOutcome
from core.actions import ActionExecutor
from core.config import ControlConfig
from core.device_worker import DeviceWorker
from core.exceptions import PaymentBlockedError
from core.state_machine import WorkerState
from models.account import AccountStatus
from models.page_state import PageState
from models.task import TaskResult, TaskRunState
from storage.database import Database
from storage.repositories import AccountRepository, TaskResultRepository
from tests.fakes import (BrokenAutomation, FakeAdb, FakeDeviceManager,
                         ScriptedAutomation)


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_cfg(tmp_path):
    """隔离配置 — 项目根指向 tmp_path, 不读真实 config/*.yaml"""
    return ControlConfig(project_root=tmp_path)


@pytest.fixture
def repos(tmp_path):
    db = Database(tmp_path / "runtime.db")
    yield AccountRepository(db), TaskResultRepository(db)
    db.close()


def make_worker(serial, cfg, accounts, results,
                automation_cls=None, device_manager=None):
    """构造一个不自动启动线程的 DeviceWorker(测试手动驱动 _tick)"""
    dm = device_manager or FakeDeviceManager()
    cls = automation_cls or ScriptedAutomation
    stop, pause = threading.Event(), threading.Event()
    w = DeviceWorker(
        serial=serial, cfg=cfg, device_manager=dm,
        account_repo=accounts, result_repo=results,
        automation_factory=lambda s: cls(s),
        stop_event=stop, pause_event=pause)
    return w, stop


# ── 测试 ──────────────────────────────────────────────────────────

class TestAllImports:
    def test_all_imports(self):
        """新中控系统所有模块可导入(旧 comment_bot/douyin_core 已迁出)"""
        from core import (account_manager, actions, adb_manager, config,
                          coordinate, device_manager, device_worker,
                          exceptions, image_matcher, logger, ocr, perf,
                          popup_handler, qq_provider, state_machine,
                          task_scheduler, ui_detector, watchdog)
        from models import account, device, page_state, task
        from storage import database, repositories
        from automation import base_game, target_game
        from automation.pokemon_go import (adapter, detector, logout,
                                           recovery, selectors, shop,
                                           states, web_context)
        from api import server, websocket
        import main  # CLI 入口可导入(不执行)

        # 关键默认语义: 支付默认禁止(真实 config.yaml 即 dry_run=true)
        assert ControlConfig.load().payment_dry_run


class TestWorkerPipeline:
    def test_worker_full_lifecycle(self, tmp_cfg, repos):
        """单账号完整流水线: 领取 → 状态机全链路 → SUCCESS → 结果落库"""
        accounts, results = repos
        accounts.add("user001", "pass1")
        w, _ = make_worker("FAKE-INT-1", tmp_cfg, accounts, results)

        for _ in range(30):
            w._tick()
            if w.runtime.success_count >= 1:
                break

        acc = accounts.get_by_account("user001")
        assert acc.status == AccountStatus.SUCCESS
        assert w.runtime.success_count == 1
        rows = results.list()
        assert len(rows) == 1
        assert rows[0]["state"] == TaskRunState.SUCCESS.value
        assert rows[0]["device_serial"] == "FAKE-INT-1"
        assert rows[0]["duration_sec"] >= 0
        # 状态机按预期顺序走过关键状态
        sources = {s.value for s, _ in w.fsm.history}
        for state in ("START_GAME", "LOGIN", "EXECUTE_TASK",
                      "LOGOUT", "CLEANUP"):
            assert state in sources
        # 下一账号预取: 队列空时 account 释放
        assert w.account is None

    def test_multi_worker_concurrency_no_duplicate(self, tmp_cfg, repos):
        """3 Worker 并发领取 6 账号 — 每账号恰好执行一次(多设备压力测试
        的单元侧; 真机 3 设备并发见 docs/REMEDIATION 未测项)"""
        accounts, results = repos
        for i in range(6):
            accounts.add(f"user{i:03d}", "p")
        tmp_cfg.system["poll_interval"] = 0.05  # 空转轮询加速
        workers = [make_worker(f"FAKE-C{i}", tmp_cfg, accounts, results)[0]
                   for i in range(3)]

        deadline = time.time() + 15

        def drive(w):
            while time.time() < deadline:
                w._tick()

        threads = [threading.Thread(target=drive, args=(w,), daemon=True)
                   for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert accounts.stats()["SUCCESS"] == 6
        rows = results.list()
        assert len(rows) == 6
        # 每 (账号, 设备) 对唯一 — 并发下不可能两台设备执行同一账号
        executed = {(r["account_id"], r["device_serial"]) for r in rows}
        assert len(executed) == 6
        # 结果记录中的设备与账号最终绑定设备一致
        for r in rows:
            acc = accounts.get(r["account_id"])
            assert acc.device_serial == r["device_serial"]

    def test_cold_start_does_not_false_alarm_app_crash(self, tmp_cfg, repos):
        """冷启动回归: 进入 START_GAME 时游戏进程尚不存在属正常, watchdog
        巡检不得在 launch 执行前误报 APP_CRASHED 进 RECOVERY
        (真机首跑 7s 即误入, 每个账号白耗一次 Level 5 重启)"""
        accounts, results = repos
        accounts.add("user001", "p")
        tmp_cfg.game["package"] = "fake.game.pkg"  # 空包会跳过 pidof 巡检
        adb = FakeAdb()
        running = {"v": False}
        adb.pidof = lambda serial, package: 12345 if running["v"] else 0

        class ColdStartAutomation(ScriptedAutomation):
            def launch(self):
                running["v"] = True  # 模拟 launch 后进程出现
                return super().launch()

        w, _ = make_worker("FAKE-COLD", tmp_cfg, accounts, results,
                           automation_cls=ColdStartAutomation,
                           device_manager=FakeDeviceManager(adb=adb))
        for _ in range(5):
            w._tick()

        states = [s for s, _ in w.fsm.history]
        assert WorkerState.RECOVERY not in states  # 全程未误入恢复
        # launch 已执行且流水线正常推进(登录甚至已完成)
        assert "launch" in w.automation.calls
        assert w.fsm.state in (WorkerState.DETECT_PAGE, WorkerState.LOGIN,
                               WorkerState.WAIT_HOME, WorkerState.HANDLE_POPUPS)

    def test_task_failure_releases_account_and_moves_on(self, tmp_cfg, repos):
        """回归: 任务失败标记 RETRY/FAILED 后必须释放账号并领取下一个
        (真机曾同一账号无限重跑任务, retry_count 烧到 9 才被硬预算救出)"""
        accounts, results = repos
        accounts.add("user001", "p")
        accounts.add("user002", "p")

        class FailingTaskAutomation(ScriptedAutomation):
            def execute_task(self, account):
                return TaskOutcome(False, "OPEN_MAIN_MENU", "无法打开主菜单")

        w, _ = make_worker("FAKE-FAIL", tmp_cfg, accounts, results,
                           automation_cls=FailingTaskAutomation)
        for _ in range(60):
            w._tick()
            if accounts.get_by_account("user002").status == AccountStatus.RETRY:
                break

        a1 = accounts.get_by_account("user001")
        a2 = accounts.get_by_account("user002")
        assert a1.status == AccountStatus.RETRY
        assert a1.retry_count == 1          # 每个账号只烧一次重试
        assert a2.status == AccountStatus.RETRY  # 第二个账号正常被领取并失败
        assert a2.retry_count == 1
        assert len(results.list()) == 2     # 每账号一条结果, 不重复落库

    def test_residual_session_logs_out_before_login(self, tmp_cfg, repos):
        """回归: 异常中断后游戏残留上一账号的 HOME 会话 — 新账号周期必须
        先登出再走 LOGIN, 绝不能跳过登录直接执行任务(真机曾 Rk*** 周期
        无 LOGIN 状态, 任务归属错乱)"""
        accounts, results = repos
        accounts.add("user001", "p")

        class ResidualSessionAutomation(ScriptedAutomation):
            def __init__(self, serial=""):
                super().__init__(serial)
                self.residual = True

            def detect_page(self):
                self.calls.append("detect_page")
                if self.residual:
                    return PageState.HOME  # 残留会话: 未登录就已在 HOME
                return PageState.HOME if self.login_done else PageState.LOGIN

            def logout(self):
                self.calls.append("logout")
                self.residual = False       # 登出后残留会话消失
                return True

        w, _ = make_worker("FAKE-RESID", tmp_cfg, accounts, results,
                           automation_cls=ResidualSessionAutomation)
        for _ in range(30):
            w._tick()
            if accounts.get_by_account("user001").status == AccountStatus.SUCCESS:
                break

        assert accounts.get_by_account("user001").status == AccountStatus.SUCCESS
        calls = w.automation.calls
        login_i = next(i for i, c in enumerate(calls)
                       if c.startswith("login:"))
        assert "logout" in calls[:login_i]   # 先登出残留会话
        assert "execute_task" in calls       # 之后才执行任务
        assert calls.index("execute_task") > login_i
        assert calls.count("execute_task") == 1

    def test_residual_session_reset_bounded(self, tmp_cfg, repos):
        """残留会话登出失败有上限: 2 次后释放账号, 不死循环"""
        accounts, results = repos
        accounts.add("user001", "p")

        class StickySessionAutomation(ScriptedAutomation):
            def detect_page(self):
                self.calls.append("detect_page")
                return PageState.HOME  # 登出无效, 始终残留 HOME

        w, _ = make_worker("FAKE-STICKY", tmp_cfg, accounts, results,
                           automation_cls=StickySessionAutomation)
        for _ in range(20):
            w._tick()
            if accounts.get_by_account("user001").status == AccountStatus.RETRY:
                break

        acc = accounts.get_by_account("user001")
        assert acc.status == AccountStatus.RETRY
        assert "SESSION_RESET_FAILED" in acc.last_error
        assert acc.retry_count == 1
        assert w.automation.calls.count("logout") == 2  # 恰好 2 次, 有界
        assert w.account is None

    def test_login_timeout_retry_skips_game_restart(self, tmp_cfg, repos):
        """回归: 登录 TIMEOUT 重试不得冷重启游戏 — 只暖拉回游戏(launch)再
        重走 DETECT_PAGE→LOGIN。真机曾 TIMEOUT 后冷重启 + 看门狗误杀 +
        冷却 6 分钟卡在选号页; 也实测过只重检测时外部浏览器挡屏导致
        DETECT_PAGE 死循环"""
        accounts, results = repos
        accounts.add("user001", "p")

        class FlakyLoginAutomation(ScriptedAutomation):
            def __init__(self, serial=""):
                super().__init__(serial)
                self.login_attempts = 0
                self.restarts = 0

            def login(self, account):
                self.calls.append(f"login:{account.masked()}")
                self.login_attempts += 1
                if self.login_attempts < 3:
                    return LoginResult.TIMEOUT
                self.login_done = True
                return LoginResult.SUCCESS

            def restart(self):
                self.restarts += 1
                self.calls.append("restart")
                return True

        w, _ = make_worker("FAKE-FLAKY", tmp_cfg, accounts, results,
                           automation_cls=FlakyLoginAutomation)
        for _ in range(40):
            w._tick()
            if accounts.get_by_account("user001").status == AccountStatus.SUCCESS:
                break

        assert accounts.get_by_account("user001").status == AccountStatus.SUCCESS
        assert w.automation.login_attempts == 3     # 重试直到成功
        assert w.automation.restarts == 0           # TIMEOUT 重试不冷重启
        assert w.automation.calls.count("launch") >= 3   # 每次 TIMEOUT 重试暖拉回游戏
        assert w.automation.calls.count("execute_task") == 1

    def test_worker_wires_heartbeat_into_automation(self, tmp_cfg, repos):
        """回归: Worker 构建自动化后必须注入心跳回调(长等待循环刷新用),
        否则登录阻塞 >90s 被调度器误判 WORKER_STALLED 重建"""
        accounts, results = repos
        accounts.add("user001", "p")

        class HeartbeatTarget:
            heartbeat_cb = None

        class WiredAutomation(ScriptedAutomation):
            def __init__(self, serial=""):
                super().__init__(serial)
                self.web = HeartbeatTarget()
                self.detector = HeartbeatTarget()

        w, _ = make_worker("FAKE-WIRE", tmp_cfg, accounts, results,
                           automation_cls=WiredAutomation)
        for _ in range(10):
            w._tick()
            if w.automation is not None:
                break

        assert w.automation is not None
        assert callable(w.automation.heartbeat_cb)
        assert callable(w.automation.web.heartbeat_cb)
        assert callable(w.automation.detector.heartbeat_cb)
        before = w.last_action_ts
        time.sleep(0.02)
        w.automation.web.heartbeat_cb()          # 回调应刷新心跳时间戳
        assert w.last_action_ts >= before

    def test_run_defaults_to_pokemon_go(self, tmp_cfg, repos):
        """回归: 裸 `python main.py run` 必须加载 pokemon_go 而不是旧
        douyin 模板(game.yaml)。真机曾漏 --game 把抖音当目标游戏跑"""
        import main
        from core.config import ControlConfig
        args = main.build_parser().parse_args(["run"])
        assert args.game == "pokemon_go"
        ControlConfig.reset()
        try:
            cfg = ControlConfig.load(game_name=args.game or None)
            assert cfg.game_name == "pokemon_go"
            assert cfg.game.get("package") == "com.nianticlabs.pokemongo"
        finally:
            ControlConfig.reset()  # 单例复位, 不污染其他测试

    def test_config_error_fails_account_without_retry(self, tmp_cfg, repos):
        """选择器未标定(配置缺失) → 立即 FAILED, 不重试、不烧时间"""
        accounts, results = repos
        accounts.add("user001", "p")
        w, stop = make_worker("FAKE-INT-2", tmp_cfg, accounts, results,
                              automation_cls=BrokenAutomation)
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            acc = accounts.get_by_account("user001")
            if acc.status == AccountStatus.FAILED:
                break
            time.sleep(0.05)
        stop.set()
        t.join(timeout=10)

        acc = accounts.get_by_account("user001")
        assert acc.status == AccountStatus.FAILED
        assert "选择器未配置" in acc.last_error
        rows = results.list()
        assert len(rows) == 1
        assert rows[0]["state"] == TaskRunState.FAILED.value
        assert rows[0]["failed_step"] == "CONFIG"

    def test_time_budget_releases_device(self, tmp_cfg, repos):
        """坏账号超过硬预算 → RETRY 并释放设备, 不拖死吞吐"""
        accounts, results = repos
        accounts.add("user001", "p")
        tmp_cfg.system["performance"] = {
            "soft_account_timeout": 0.05, "hard_account_timeout": 0.1}
        w, _ = make_worker("FAKE-INT-3", tmp_cfg, accounts, results)
        w._claim_next()
        time.sleep(0.3)
        w._tick()  # 预算检查 → 超时 → RETRY + 释放

        acc = accounts.get_by_account("user001")
        assert acc.status == AccountStatus.RETRY
        assert "TIME_BUDGET_EXCEEDED" in acc.last_error
        assert w.account is None
        rows = results.list()
        assert len(rows) == 1 and rows[0]["state"] == TaskRunState.FAILED.value


class TestPaymentSafety:
    def test_payment_dry_run_blocks_click_payment(self):
        """默认 dry_run: 真实支付动作被拦截, 返回失败而非执行"""
        executor = ActionExecutor(None, None)
        result = executor.execute({"action": "click_payment",
                                   "text": "确认支付"})
        assert not result.ok
        assert "dry_run 拦截" in result.detail
        assert "确认支付" in result.detail

    def test_payment_allowed_passes_guard(self):
        """明确授权后护栏放行(此时才真正尝试点击支付按钮)"""
        executor = ActionExecutor(None, None)
        executor.payment_allowed = True
        result = executor.execute({"action": "click_payment"})
        assert not result.ok  # 无 matcher/控件, 但失败原因不再是护栏
        assert "dry_run 拦截" not in result.detail

    def test_payment_double_switch_requires_env(self, tmp_cfg, monkeypatch):
        """双重开关: dry_run=false 且 .env 明确授权才放行, 缺一不可"""
        assert tmp_cfg.payment_dry_run          # 默认禁止
        assert not tmp_cfg.payment_allowed
        tmp_cfg.system["payment"] = {"dry_run": False}
        monkeypatch.delenv("CONTROL_CENTER_ALLOW_PAYMENT", raising=False)
        assert not tmp_cfg.payment_allowed      # 配置开但环境变量缺
        monkeypatch.setenv("CONTROL_CENTER_ALLOW_PAYMENT", "1")
        assert tmp_cfg.payment_allowed

    def test_confirm_payment_raises_without_authorization(self, tmp_cfg):
        """BaseGameAutomation.confirm_payment 未授权时直接抛异常"""
        auto = BaseGameAutomation.__new__(BaseGameAutomation)
        auto.cfg = tmp_cfg
        with pytest.raises(PaymentBlockedError):
            auto.confirm_payment(product="礼包", amount="30")


class TestResultExport:
    def test_result_export_roundtrip(self, repos, tmp_path):
        """结果导出 Excel — 账号脱敏、状态正确"""
        accounts, results = repos
        acc_id = accounts.add("13800138000", "p")
        r = TaskResult(account_id=acc_id, account="13800138000",
                       device_serial="DEV1", state=TaskRunState.SUCCESS,
                       started_at=time.time() - 5, finished_at=time.time())
        results.save(r)
        out = results.export_xlsx(tmp_path / "results.xlsx")

        import pandas as pd
        df = pd.read_excel(out)
        assert len(df) == 1
        assert "13800138000" not in str(df["account"].iloc[0])
        assert "***" in str(df["account"].iloc[0])
        assert df["state"].iloc[0] == "SUCCESS"
