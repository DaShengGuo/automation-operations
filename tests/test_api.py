"""
tests/test_api.py
Web 中控后台冒烟测试 — FastAPI TestClient + Fake 调度器

覆盖: 首页 / 账号导入与查询 / 设备列表 / 任务列表脱敏 /
系统启停链路 / WebSocket 快照推送。

说明: 本文件是 Mock 冒烟测试, 不代表真机测试结果。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.server import app, set_scheduler
from core.config import ControlConfig
from core.task_scheduler import TaskScheduler
from models.device import AndroidDevice
from models.task import TaskResult, TaskRunState
from tests.fakes import FakeDeviceManager, ScriptedAutomation


@pytest.fixture
def tmp_cfg(tmp_path):
    """隔离配置 — 项目根指向 tmp_path, 不读真实 config/*.yaml"""
    return ControlConfig(project_root=tmp_path)


@pytest.fixture
def client(tmp_cfg, monkeypatch):
    """注入 Fake 调度器的 TestClient"""
    fake_dm = FakeDeviceManager()
    monkeypatch.setattr("core.task_scheduler.DeviceManager",
                        lambda cfg: fake_dm)
    monkeypatch.setattr("core.task_scheduler.create_automation",
                        lambda name, controller, cfg:
                        ScriptedAutomation(controller.serial))
    tmp_cfg.system["poll_interval"] = 0.05
    sched = TaskScheduler(tmp_cfg)
    set_scheduler(sched)
    with TestClient(app) as c:   # lifespan 启动 WebSocket 广播循环
        yield c, sched, fake_dm
    if sched.running:
        sched.stop()


def make_device(serial, **kw):
    return AndroidDevice(serial=serial, **kw)


class TestBasic:
    def test_index_page(self, client):
        c, _, _ = client
        r = c.get("/")
        assert r.status_code == 200
        assert "中控" in r.text

    def test_scheduler_missing_returns_503(self, monkeypatch):
        """调度器未注入时查询接口返回 503 而非崩溃"""
        monkeypatch.setitem(__import__("api.server",
                                       fromlist=["_scheduler_holder"])
                            ._scheduler_holder, "scheduler", None)
        with TestClient(app) as c:
            r = c.get("/api/devices")
        assert r.status_code == 503

    def test_accounts_import_and_query(self, client):
        c, sched, _ = client
        r = c.post("/api/accounts", json={
            "accounts": [{"account": "user001", "password": "p1"},
                         {"account": "user002", "password": "p2"}]})
        assert r.status_code == 200
        assert r.json()["added"] == 2
        # 重复导入: 密码一致 → 跳过
        r = c.post("/api/accounts", json={
            "accounts": [{"account": "user001", "password": "p1"}]})
        assert r.json()["skipped"] == 1
        # 查询脱敏
        r = c.get("/api/accounts")
        accounts = r.json()["accounts"]
        assert len(accounts) == 2
        assert all("***" in a["account"] for a in accounts)

    def test_devices_list(self, client):
        c, sched, fake_dm = client
        fake_dm.rows = [make_device("API-DEV-1", model="M1"),
                        make_device("API-DEV-2")]
        r = c.get("/api/devices")
        assert r.status_code == 200
        serials = {d["serial"] for d in r.json()["devices"]}
        assert serials == {"API-DEV-1", "API-DEV-2"}

    def test_tasks_list_masked(self, client):
        c, sched, _ = client
        sched.results.save(TaskResult(
            account_id=1, account="13800138000", device_serial="API-DEV-1",
            state=TaskRunState.SUCCESS,
            started_at=time.time() - 5, finished_at=time.time()))
        r = c.get("/api/tasks")
        assert r.status_code == 200
        tasks = r.json()["tasks"]
        assert len(tasks) == 1
        assert "13800138000" not in str(tasks[0]["account"])
        assert "***" in tasks[0]["account"]


class TestSystemControl:
    def test_start_pause_stop(self, client):
        """REST 走一遍系统启停链路(回归: 修复后 stop 不再因
        未启动线程 join 崩溃)"""
        c, sched, fake_dm = client
        fake_dm.rows = [make_device("API-DEV-1")]
        r = c.post("/api/system/start")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert c.post("/api/system/pause").json()["ok"] is True
        assert c.post("/api/system/stop").json()["ok"] is True
        assert sched.running is False

    def test_device_control_api(self, client):
        c, sched, fake_dm = client
        fake_dm.rows = [make_device("API-DEV-1")]
        assert c.post("/api/devices/API-DEV-1/start").json()["ok"] is True
        assert c.post("/api/devices/API-DEV-1/stop").json()["ok"] is True
        # 不存在的设备
        r = c.post("/api/devices/MISSING/start")
        assert r.json()["ok"] is False


class TestWebSocket:
    def test_ws_status_pushes_snapshot(self, client):
        """连接 /ws/status 立即收到一次快照"""
        c, sched, fake_dm = client
        fake_dm.rows = [make_device("API-DEV-1")]
        sched.start()
        with c.websocket_connect("/ws/status") as ws:
            data = ws.receive_json()
            assert {"system", "devices", "accounts", "throughput"} <= set(data)
            assert data["system"]["running"] is True
