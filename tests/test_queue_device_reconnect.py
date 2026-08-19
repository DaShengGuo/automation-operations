"""tests/test_queue_device_reconnect.py
设备断线/重连(规格第 51-55 节): 队列以 Serial 常驻内存,
ADB 断线不清队列, 重连后同一队列继续执行; 重置保留队列;
只有应用关闭才清空。
"""
from __future__ import annotations

from core.account_queues import ManualDeviceQueueManager


class TestQueueSurvivesReconnect:
    def test_reconnect_returns_same_queue_with_tasks(self):
        """第 51/52 节: 断线重连/Worker 重建 → 同一队列实例, 任务不丢。"""
        mgr = ManualDeviceQueueManager()
        q = mgr.queue_for("SER-D1")
        for i in range(3):
            q.add_task(f"u{i}", "pw")
        # 模拟设备断线重连: 调度器重新 queue_for(serial)
        q2 = mgr.queue_for("SER-D1")
        assert q2 is q
        assert q2.pending_total() == 3

    def test_worker_restart_continues_same_queue(self):
        """Worker 线程重建后从同一队列继续 — 无账号重复执行。"""
        mgr = ManualDeviceQueueManager()
        q = mgr.queue_for("SER-D2")
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        a = q.pop_next()                     # 旧 Worker 领取 a
        q.mark_interrupted(a.id, reason="DISCONNECT")
        # 新 Worker: 先恢复 a, 再 b
        resumed = mgr.queue_for("SER-D2").pop_next()
        assert resumed.username == "a"
        mgr.queue_for("SER-D2").mark_success(resumed.id)
        assert mgr.queue_for("SER-D2").pop_next().username == "b"

    def test_offline_device_keeps_queue(self):
        """第 53 节: 设备离线期间队列照常保留, 可继续人工加号。"""
        mgr = ManualDeviceQueueManager()
        q = mgr.queue_for("SER-OFF")
        q.add_task("a", "pa")
        # 离线(没有任何设备注册表交互) — 队列仍在且可编辑
        assert mgr.get("SER-OFF").pending_total() == 1
        mgr.queue_for("SER-OFF").add_task("b", "pb")
        assert mgr.pending_total() == 2

    def test_other_device_activity_does_not_touch_offline_queue(self):
        mgr = ManualDeviceQueueManager()
        offline = mgr.queue_for("SER-OFF")
        offline.add_task("keep", "p")
        # 其他设备连断/清队不影响本设备队列
        mgr.queue_for("SER-ON").add_task("x", "p")
        assert offline.pending_total() == 1


class TestResetPreservesQueue:
    def test_reset_keeps_queue_and_resumes_after(self):
        """第 55/56 节: 重置环境 → 当前账号 INTERRUPTED/RETRY,
        队列保留, 设备启用后自动继续。"""
        mgr = ManualDeviceQueueManager()
        q = mgr.queue_for("SER-R1")
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        a = q.pop_next()
        q.mark_interrupted(a.id, reason="DEVICE_RESET")
        # 重置完成, 设备启用 → 自动继续(队列原样)
        assert mgr.get("SER-R1").pending_total() == 2
        resumed = mgr.queue_for("SER-R1").pop_next()
        assert resumed.username == "a"
        assert resumed.retry_count == 0     # 中断不烧重试


class TestCloseClearsQueue:
    def test_clear_all_only_on_app_close(self):
        """第 28 节: 只有应用关闭才清空; 关闭后新会话是全新队列。"""
        mgr = ManualDeviceQueueManager()
        mgr.queue_for("SER-C1").add_task("a", "pa")
        mgr.clear_all()
        assert mgr.get("SER-C1") is None
        fresh = mgr.queue_for("SER-C1")
        assert fresh.pending_total() == 0

    def test_clear_all_clears_every_device(self):
        mgr = ManualDeviceQueueManager()
        for s in ("SER-1", "SER-2", "SER-3"):
            mgr.queue_for(s).add_task("u", "p")
        mgr.clear_all()
        assert mgr.pending_total() == 0
        assert mgr.iter_queues() == []
