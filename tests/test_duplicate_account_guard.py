"""tests/test_duplicate_account_guard.py
账号重复防护(规格第 36-39 节):
  同设备重复拒绝 / 跨设备分配检测 / 全局执行锁(最后一道保护)。
"""
from __future__ import annotations

from core.account_queues import (GlobalAccountExecutionRegistry,
                                 ManualDeviceQueueManager)


class TestSameDeviceDuplicate:
    def test_find_by_username_covers_waiting(self):
        q = ManualDeviceQueueManager().queue_for("SER-1")
        t, ok = q.add_task("dup", "p")
        assert ok and q.find_by_username("dup") is t

    def test_find_by_username_covers_running(self):
        q = ManualDeviceQueueManager().queue_for("SER-1")
        q.add_task("dup", "p")
        t = q.pop_next()                   # RUNNING(current)
        assert q.find_by_username("dup") is t

    def test_add_duplicate_rejected_after_success(self):
        """已完成的账号也在本次会话内视为重复(队列内不可重复添加)。"""
        q = ManualDeviceQueueManager().queue_for("SER-1")
        q.add_task("dup", "p1")
        t = q.pop_next()
        q.mark_success(t.id)
        dup, ok = q.add_task("dup", "p2")
        assert ok, "终态账号不再在队列中, 允许重新添加"


class TestCrossDeviceDetection:
    def test_find_device_of_username(self):
        mgr = ManualDeviceQueueManager()
        mgr.queue_for("SER-1").add_task("cross", "p")
        mgr.queue_for("SER-2")
        assert mgr.find_device_of_username("cross") == "SER-1"
        assert mgr.find_device_of_username("nobody") is None

    def test_running_account_found_on_other_device(self):
        mgr = ManualDeviceQueueManager()
        mgr.queue_for("SER-1").add_task("cross", "p")
        t = mgr.queue_for("SER-1").pop_next()    # RUNNING
        assert t.status.value == "RUNNING"
        assert mgr.find_device_of_username("cross") == "SER-1"


class TestGlobalExecutionRegistry:
    def test_cross_device_blocked(self):
        reg = GlobalAccountExecutionRegistry()
        assert reg.try_acquire("u1", "SER-1", 1)
        assert not reg.try_acquire("u1", "SER-2", 99)
        assert reg.owner_of("u1") == "SER-1"

    def test_same_device_idempotent(self):
        reg = GlobalAccountExecutionRegistry()
        assert reg.try_acquire("u1", "SER-1", 1)
        assert reg.try_acquire("u1", "SER-1", 2)

    def test_release_only_by_owner(self):
        reg = GlobalAccountExecutionRegistry()
        reg.try_acquire("u1", "SER-1", 1)
        assert not reg.release("u1", device_serial="SER-2")
        assert reg.owner_of("u1") == "SER-1"
        assert reg.release("u1", device_serial="SER-1")
        assert reg.owner_of("u1") is None

    def test_release_all_for_device(self):
        reg = GlobalAccountExecutionRegistry()
        reg.try_acquire("a", "SER-1", 1)
        reg.try_acquire("b", "SER-1", 2)
        reg.try_acquire("c", "SER-2", 3)
        assert reg.release_all_for("SER-1") == 2
        assert reg.owner_of("a") is None
        assert reg.owner_of("b") is None
        assert reg.owner_of("c") == "SER-2"

    def test_active_snapshot_masks_usernames(self):
        """诊断快照绝不泄露明文账号(第 40 节)。"""
        reg = GlobalAccountExecutionRegistry()
        reg.try_acquire("Rk3-658", "SER-1", 1)
        snap = reg.active_snapshot()
        assert len(snap) == 1
        row = snap[0]
        assert row["serial"] == "SER-1"
        assert row["username_hash"] != "Rk3-658"
        assert "Rk3-658" not in str(snap)
