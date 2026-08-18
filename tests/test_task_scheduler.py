"""
tests/test_task_scheduler.py
任务调度器单元测试 — Mock 设备/自动化, 不依赖真机

覆盖: 启动时设备筛选(禁用/离线/初始化失败跳过)、卡死账号启动恢复、
Worker 心跳重建、暂停恢复、状态看板、设备级 API、优雅停止。

说明: 本文件是 Mock 单元测试, 不代表真机测试结果。
"""
from __future__ import annotations

import time

import pytest

from core.config import ControlConfig
from core.task_scheduler import TaskScheduler
from models.account import AccountStatus
from models.device import AndroidDevice, DeviceStatus
from tests.fakes import FakeDeviceManager, ScriptedAutomation


@pytest.fixture
def tmp_cfg(tmp_path):
    """隔离配置 — 项目根指向 tmp_path, 不读真实 config/*.yaml"""
    return ControlConfig(project_root=tmp_path)


@pytest.fixture
def scheduler(tmp_cfg, monkeypatch):
    """构造调度器: 设备管理换 Fake, 自动化工厂换脚本化实现"""
    fake_dm = FakeDeviceManager()
    monkeypatch.setattr("core.task_scheduler.DeviceManager",
                        lambda cfg: fake_dm)
    monkeypatch.setattr("core.task_scheduler.create_automation",
                        lambda name, controller, cfg:
                        ScriptedAutomation(controller.serial))
    tmp_cfg.system["poll_interval"] = 0.05   # 空转轮询加速
    s = TaskScheduler(tmp_cfg)
    yield s, fake_dm
    if s.running:
        s.stop()


def make_device(serial, **kw):
    return AndroidDevice(serial=serial, **kw)


class TestSchedulerStart:
    def test_start_spawns_workers_and_skips_bad_devices(self, scheduler):
        """禁用/离线/初始化失败的设备跳过, 健康设备各起一个 Worker"""
        s, dm = scheduler
        dm.rows = [
            make_device("GOOD-A"),
            make_device("DISABLED-B", status=DeviceStatus.DISABLED),
            make_device("OFFLINE-C", adb_state="offline"),
            make_device("INITFAIL-D"),
        ]
        dm.init_fail_serials = {"INITFAIL-D"}

        summary = s.start()
        assert summary["ok"] is True
        assert summary["workers"] == 1
        assert "GOOD-A" in s._workers
        snap = s.snapshot()
        assert snap["system"]["running"] is True
        assert snap["system"]["workers"] == 1
        serials = {d["serial"] for d in snap["devices"]}
        assert serials == {"GOOD-A", "DISABLED-B", "OFFLINE-C", "INITFAIL-D"}
        by_serial = {d["serial"]: d for d in snap["devices"]}
        assert by_serial["GOOD-A"]["worker_state"] != "-"   # Worker 已启动
        assert by_serial["DISABLED-B"]["worker_state"] == "-"

    def test_start_is_idempotent(self, scheduler):
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        s.start()
        again = s.start()
        assert again["workers"] == 1          # 不重复启动
        assert len(s._workers) == 1

    def test_max_workers_cap(self, scheduler):
        """并发上限: 设备数超过 max_workers 时其余跳过并如实记录
        (回归: 旧实现用线程池提交无限循环任务, 超限设备静默饿死)"""
        s, dm = scheduler
        s.cfg.system["workers"] = {"max": 2}
        dm.rows = [make_device(f"GOOD-{i}") for i in range(5)]
        summary = s.start()
        assert summary["workers"] == 2
        assert len(s._workers) == 2

    def test_start_recovers_stale_accounts(self, scheduler):
        """程序意外退出后卡在 LOCKED 的账号, 启动时自动恢复"""
        s, dm = scheduler
        dm.rows = []                          # 无设备, 只看恢复逻辑
        acc_id = s.accounts.add("user001", "p", max_retry=3)
        s.accounts.claim_next("DEAD-DEVICE")  # 卡在 LOCKED
        old = time.time() - 3600
        s.adb_db.execute(
            "UPDATE accounts SET updated_at=? WHERE id=?", (old, acc_id))
        s.start()
        acc = s.accounts.get(acc_id)
        assert acc.status == AccountStatus.RETRY
        assert "stale" in acc.last_error


class TestSchedulerLifecycle:
    def test_pause_resume(self, scheduler):
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        s.start()
        assert s.snapshot()["system"]["paused"] is False
        s.pause()
        assert s.snapshot()["system"]["paused"] is True
        s.resume()
        assert s.snapshot()["system"]["paused"] is False

    def test_stop_joins_workers_and_clears(self, scheduler):
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A"), make_device("GOOD-B")]
        s.start()
        assert len(s._workers) == 2
        s.stop()
        assert s.running is False
        assert len(s._workers) == 0

    def test_stop_clears_snapshot_worker_state(self, scheduler):
        """回归: 停止后快照不得残留运行中状态(GUI「运行中Worker」计数归零)。"""
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        s.start()
        s._runtimes["GOOD-A"].state = "EXECUTE_TASK"  # 模拟停止前残留
        s.stop()
        dev = [d for d in s.snapshot()["devices"]
               if d["serial"] == "GOOD-A"][0]
        assert dev["worker_state"] == "-"

    def test_stop_device_clears_snapshot_worker_state(self, scheduler):
        """回归: 单台停止后快照同样报 '-'。"""
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        s.start_device("GOOD-A")
        s._runtimes["GOOD-A"].state = "EXECUTE_TASK"
        s.stop_device("GOOD-A")
        dev = [d for d in s.snapshot()["devices"]
               if d["serial"] == "GOOD-A"][0]
        assert dev["worker_state"] == "-"

    def test_heartbeat_stall_rebuilds_worker(self, scheduler):
        """Worker 心跳停止 → 标记并重建(新线程接管)"""
        s, dm = scheduler
        s.cfg.system["performance"] = {"worker_heartbeat_timeout": 1.0}
        dm.rows = [make_device("GOOD-A")]
        s.start()
        old_worker = s._workers["GOOD-A"]
        old_worker.last_action_ts = time.time() - 999  # 伪造心跳停止
        s._check_worker_heartbeats()
        new_worker = s._workers["GOOD-A"]
        assert new_worker is not old_worker        # 已重建
        assert s.running is True                   # 调度器本体不受影响


class TestDeviceControl:
    def test_start_device_not_found(self, scheduler):
        s, dm = scheduler
        result = s.start_device("MISSING")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_start_device_offline(self, scheduler):
        s, dm = scheduler
        dm.rows = [make_device("OFFLINE-X", adb_state="offline")]
        result = s.start_device("OFFLINE-X")
        assert result["ok"] is False
        assert "adb state" in result["error"]

    def test_start_and_stop_device(self, scheduler):
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        assert s.start_device("GOOD-A")["ok"] is True
        assert "GOOD-A" in s._workers
        assert s.stop_device("GOOD-A")["ok"] is True
        # 已停止的 Worker 再次停止 → 报错而非崩溃
        assert s.stop_device("GOOD-A")["ok"] is False

    def test_restart_device(self, scheduler):
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        s.start_device("GOOD-A")
        old = s._workers["GOOD-A"]
        assert s.restart_device("GOOD-A")["ok"] is True
        assert s._workers["GOOD-A"] is not old


class TestSnapshot:
    def test_snapshot_structure(self, scheduler):
        """看板快照: system/devices/accounts/throughput 四段齐全"""
        s, dm = scheduler
        dm.rows = [make_device("GOOD-A")]
        s.start()
        snap = s.snapshot()
        assert {"system", "devices", "accounts", "throughput"} <= set(snap)
        assert snap["accounts"]["total"] >= 0
        dev = snap["devices"][0]
        for field in ("serial", "model", "resolution", "status",
                      "worker_state", "page", "account", "error",
                      "success_count", "fail_count"):
            assert field in dev
