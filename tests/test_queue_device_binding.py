"""tests/test_queue_device_binding.py
按设备绑定(规格第 3/4/19 节): 队列 Key 必须是 ADB Serial,
同型号多台手机队列完全隔离, 禁止跨设备偷号。
"""
from __future__ import annotations

from core.account_queues import (ManualDeviceQueueManager,
                                 ManualDeviceQueueProvider)


class TestKeyBySerialNeverModel:
    def test_queue_for_returns_same_instance(self):
        mgr = ManualDeviceQueueManager()
        q1 = mgr.queue_for("SER-AAA")
        q2 = mgr.queue_for("SER-AAA")
        assert q1 is q2

    def test_same_model_different_serials_isolated(self):
        """同型号两台手机(serial 不同) → 队列完全独立(第 3/4 节)。"""
        mgr = ManualDeviceQueueManager()
        q1 = mgr.queue_for("SER-111")
        q2 = mgr.queue_for("SER-222")
        assert q1 is not q2
        q1.add_task("only-on-device-1", "p")
        assert q1.pending_total() == 1
        assert q2.pending_total() == 0

    def test_tasks_stamped_with_device_serial(self):
        mgr = ManualDeviceQueueManager()
        t, _ = mgr.queue_for("SER-777").add_task("u", "p")
        assert t.device_serial == "SER-777"


class TestStrictDeviceQueue:
    def test_pop_never_crosses_devices(self):
        """第 19 节: 从设备 A 领号绝不影响设备 B(无全局共享队列)。"""
        mgr = ManualDeviceQueueManager()
        qa = mgr.queue_for("SER-A")
        qb = mgr.queue_for("SER-B")
        for i in range(3):
            qa.add_task(f"a{i}", "p")
            qb.add_task(f"b{i}", "p")
        got_a = []
        for _ in range(2):
            t = qa.pop_next()
            got_a.append(t.username)
            qa.mark_success(t.id)
        assert got_a == ["a0", "a1"]
        assert qb.pending_total() == 3          # B 一个不少
        assert qb.pop_next().username == "b0"

    def test_get_does_not_create(self):
        mgr = ManualDeviceQueueManager()
        assert mgr.get("NOPE") is None
        assert mgr.iter_queues() == []

    def test_totals_aggregate_all_devices(self):
        mgr = ManualDeviceQueueManager()
        mgr.queue_for("SER-1").add_task("a", "p")
        mgr.queue_for("SER-1").add_task("b", "p")
        mgr.queue_for("SER-2").add_task("c", "p")
        t = mgr.queue_for("SER-2").pop_next()
        mgr.queue_for("SER-2").mark_success(t.id)
        totals = mgr.totals()
        assert totals["waiting"] == 2
        assert totals["success"] == 1
        assert totals["devices_with_queue"] == 2
        assert mgr.pending_total() == 2

    def test_provider_reads_all_pending(self):
        mgr = ManualDeviceQueueManager()
        mgr.queue_for("SER-1").add_task("a", "pa")
        mgr.queue_for("SER-2").add_task("b", "pb")
        provider = ManualDeviceQueueProvider(mgr)
        pairs = provider.fetch_accounts()
        assert sorted(pairs) == [("a", "pa"), ("b", "pb")]
