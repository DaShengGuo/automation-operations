"""tests/test_manual_device_queue.py
队列核心(规格第 16-35/61 节):
  FIFO / 插到队首 / 状态流转 / RETRY 延后 / INTERRUPTED 优先
  / 人工编辑守卫 / 密码安全 / 毫秒级读取 / 即时唤醒。
"""
from __future__ import annotations

import threading
import time

from core.account_queues import (AccountTask, DeviceAccountQueue,
                                 QueueAccountStatus)


def make_queue(serial: str = "SER-001") -> DeviceAccountQueue:
    return DeviceAccountQueue(serial)


class TestFifoAndFront:
    def test_fifo_order(self):
        q = make_queue()
        for u in ("a", "b", "c"):
            q.add_task(u, f"p-{u}")
        got = []
        for _ in range(3):
            t = q.pop_next()             # 生产流: 领取 → 执行 → 归还
            got.append(t.username)
            q.mark_success(t.id)
        assert got == ["a", "b", "c"]
        assert q.pop_next() is None

    def test_to_front_add(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb", to_front=True)
        t = q.pop_next()
        assert t.username == "b"
        q.mark_success(t.id)
        assert q.pop_next().username == "a"

    def test_duplicate_add_rejected(self):
        q = make_queue()
        t1, ok1 = q.add_task("dup", "p1")
        dup, ok2 = q.add_task("dup", "p2")
        assert ok1 and not ok2
        assert dup is t1                  # 返回已存在的任务
        assert dup.password == "p1"       # 保留原密码


class TestStatusFlow:
    def test_success(self):
        q = make_queue()
        q.add_task("a", "pa")
        t = q.pop_next()
        assert t.status == QueueAccountStatus.RUNNING
        assert q.current is t
        assert t.started_at is not None
        q.mark_success(t.id)
        assert t.status == QueueAccountStatus.SUCCESS
        assert t.finished_at is not None
        assert q.current is None
        assert q.completed_count == 1
        c = q.counts()
        assert c["completed"] == 1
        assert c["SUCCESS"] == 0          # 终态任务不留在活跃计数

    def test_retry_deferred_after_normal(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        t = q.pop_next()                  # a
        final = q.mark_retry(t.id, error="登录失败")
        assert final == QueueAccountStatus.RETRY
        assert t.retry_count == 1
        assert t.last_error == "登录失败"
        # RETRY 排到队尾: 正常账号 b 先执行(坏账号不饿死正常账号)
        b = q.pop_next()
        assert b.username == "b"
        q.mark_success(b.id)
        assert q.pop_next().username == "a"

    def test_retry_exhausted_fails(self):
        q = make_queue()
        q.add_task("a", "pa")
        t = q.pop_next()
        for i in range(t.max_retry):
            final = q.mark_retry(t.id, error=f"e{i}")
            assert final == QueueAccountStatus.RETRY
            t = q.pop_next()
        final = q.mark_retry(t.id, error="exhausted")
        assert final == QueueAccountStatus.FAILED
        assert q.failed_count == 1
        assert t.last_error == "exhausted"

    def test_mark_failed_direct(self):
        q = make_queue()
        q.add_task("a", "pa")
        t = q.pop_next()
        q.mark_failed(t.id, error="致命错误")
        assert t.status == QueueAccountStatus.FAILED
        assert q.failed_count == 1

    def test_interrupted_front_priority(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        t = q.pop_next()                  # a RUNNING
        q.mark_interrupted(t.id, reason="STOP")
        assert t.status == QueueAccountStatus.INTERRUPTED
        assert q.front_interrupted() is t
        # 恢复时先完成被打断的 a, 再按 FIFO 出 b
        resumed = q.pop_next()
        assert resumed.username == "a"
        q.mark_success(resumed.id)
        assert q.pop_next().username == "b"


class TestManualEditingGuards:
    def test_remove_only_removable(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        a = q.pop_next()                  # a RUNNING(不可删)
        assert not q.remove_task(a.id)
        q.mark_interrupted(a.id)          # INTERRUPTED(不可删)
        assert not q.remove_task(a.id)
        assert q.remove_task(q.get_task(next(
            t.id for t in q._deque if t.username == "b")).id)
        assert q.pop_next().username == "a"

    def test_edit_only_waiting(self):
        q = make_queue()
        q.add_task("a", "pa")
        t = q.pop_next()                  # RUNNING(不在 deque 中, 不可编辑)
        ok, err = q.update_task(t.id, "a2", "p2")
        assert not ok
        q.mark_interrupted(t.id)          # INTERRUPTED 插回 deque
        ok, err = q.update_task(t.id, "a2", "p2")
        assert not ok and "等待" in err   # 仅 WAITING 可编辑
        q.add_task("w", "pw")             # WAITING 可编辑
        w = q.get_task(next(
            t.id for t in q._deque if t.username == "w"))
        ok, err = q.update_task(w.id, "w2", "pw2")
        assert ok, err
        assert q.get_task(w.id).username == "w2"
        assert q.get_task(w.id).password == "pw2"

    def test_edit_dup_rejected(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        b = q.get_task(next(
            t.id for t in q._deque if t.username == "b"))
        ok, err = q.update_task(b.id, "a", "px")
        assert not ok
        assert "已在此设备队列中" in err

    def test_clear_waiting_keeps_current_and_interrupted(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        q.add_task("c", "pc")
        a = q.pop_next()                  # a 先失败一次 → RETRY 排到队尾
        q.mark_retry(a.id, error="x")
        b = q.pop_next()                  # b RUNNING(current)
        q.mark_interrupted(b.id)          # 停止 → b INTERRUPTED(保留)
        cleared = q.clear_waiting()
        assert cleared == 2               # 清掉 WAITING 的 c + RETRY 的 a
        left = [t.username for t in q._deque]
        assert left == ["b"]              # 仅保留 INTERRUPTED 的 b

    def test_move_up_down(self):
        q = make_queue()
        ids = {}
        for u in ("a", "b", "c"):
            t, _ = q.add_task(u, f"p{u}")
            ids[u] = t.id
        assert q.move_task(ids["c"], "up")
        order = []
        for _ in range(2):
            t = q.pop_next()
            order.append(t.username)
            q.mark_success(t.id)
        assert order == ["a", "c"]        # b 被 c 顶到后面


class TestSafety:
    def test_snapshot_never_contains_password(self):
        q = make_queue()
        q.add_task("alpha", "SECRET-PW-1")
        q.add_task("beta", "SECRET-PW-2")
        q.pop_next()                      # alpha → current(RUNNING)
        snap = q.snapshot()
        for row in snap["tasks"]:
            assert "password" not in row
            assert "SECRET" not in str(row)
        assert snap["current"] is not None
        assert "password" not in snap["current"]

    def test_pending_pairs(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        assert q.pending_pairs() == [("a", "pa"), ("b", "pb")]

    def test_task_ids_unique_on_fast_adds(self):
        q = make_queue()
        tasks = [q.add_task(f"u{i}", "p")[0] for i in range(50)]
        assert len({t.id for t in tasks}) == 50

    def test_masked_never_leaks_password(self):
        t = AccountTask(id=1, username="Rk3-658", password="SUPER-SECRET")
        assert t.password not in t.masked()
        assert t.account == "Rk3-658"

    def test_ms_level_reads(self):
        """第 61 节: 队列读取毫秒级 — 1 万次计数应远快于 1 秒。"""
        q = make_queue()
        for i in range(20):
            q.add_task(f"u{i}", "p")
        start = time.perf_counter()
        for _ in range(10000):
            q.counts()
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"1万次读取耗时 {elapsed:.3f}s"

    def test_wait_for_task_instant_wake(self):
        """第 23/61 节: 新账号加入立即唤醒等待中的 Worker(毫秒级)。"""
        q = make_queue()
        result = {}

        def waiter():
            start = time.perf_counter()
            ok = q.wait_for_task(5.0)
            result["ok"] = ok
            result["elapsed"] = time.perf_counter() - start

        th = threading.Thread(target=waiter)
        th.start()
        time.sleep(0.1)
        q.add_task("wake", "pw")
        th.join(timeout=2)
        assert result.get("ok") is True
        assert result["elapsed"] < 1.0, f"唤醒耗时 {result['elapsed']:.3f}s"

    def test_on_change_callback(self):
        fired = []
        q = DeviceAccountQueue("SER-1", on_change=lambda: fired.append(1))
        q.add_task("a", "pa")
        assert fired, "队列变化应触发回调(第 58 节事件驱动)"

    def test_clear_all_resets(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.pop_next()
        q.mark_success(q.current.id) if q.current else None
        q.clear_all()
        assert q.pending_total() == 0
        assert q.current is None
        assert q.completed_count == 0
        assert q.failed_count == 0
