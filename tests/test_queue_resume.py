"""tests/test_queue_resume.py
停止/恢复(规格第 26/27/47/54-56 节): 停止保留等待队列,
在途账号 INTERRUPTED 插回队首并优先恢复, 插到队首不打断当前账号,
release 不烧重试。
"""
from __future__ import annotations

from core.account_queues import DeviceAccountQueue, QueueAccountStatus


def make_queue(serial: str = "SER-1") -> DeviceAccountQueue:
    return DeviceAccountQueue(serial)


class TestStopKeepsQueue:
    def test_stop_keeps_waiting_and_resumes_current_first(self):
        """第 26/27 节: 停止保留 WAITING 队列, 重启先恢复被打断账号。"""
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        q.add_task("c", "pc")
        a = q.pop_next()                     # a 执行中
        # 停止 → a INTERRUPTED 插回队首
        q.mark_interrupted(a.id, reason="STOP")
        assert a.status == QueueAccountStatus.INTERRUPTED
        assert q.pending_total() == 3        # 队列一个不少
        # Worker 重启: 优先恢复 a, 再按 FIFO b, c
        for expected in ("a", "b", "c"):
            t = q.pop_next()             # 生产流: 领取 → 执行 → 归还
            assert t.username == expected
            q.mark_success(t.id)

    def test_front_interrupted_only_reports_real_interrupt(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        assert q.front_interrupted() is None
        a = q.pop_next()
        q.mark_interrupted(a.id, reason="DEVICE_RESET")
        assert q.front_interrupted() is a


class TestInsertToFrontNoInterrupt:
    def test_to_front_does_not_disturb_running(self):
        """第 47 节: 插到队首只影响后续顺序, 不打断 RUNNING 账号。"""
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("c", "pc")
        a = q.pop_next()                     # a RUNNING
        q.add_task("b", "pb", to_front=True) # 运行中加号+插队
        assert q.current is a                # 当前账号不受影响
        assert q.current.status == QueueAccountStatus.RUNNING
        q.mark_success(a.id)
        b = q.pop_next()
        assert b.username == "b"          # 插队生效
        q.mark_success(b.id)
        assert q.pop_next().username == "c"

    def test_move_to_front_waits_until_current_done(self):
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        q.add_task("c", "pc")
        a = q.pop_next()
        q.move_to_front(next(
            t.id for t in q._deque if t.username == "c"))
        q.mark_success(a.id)
        got = []
        for _ in range(2):
            t = q.pop_next()             # 生产流: 领取 → 执行 → 归还
            got.append(t.username)
            q.mark_success(t.id)
        assert got == ["c", "b"]


class TestReleaseWithoutRetryBurn:
    def test_release_back_to_waiting(self):
        """未真正执行就归还的账号 → WAITING(队首), 不烧重试次数。"""
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        a = q.pop_next()
        q.release(a.id, reason="设备不可用")
        assert a.status == QueueAccountStatus.WAITING
        assert a.retry_count == 0
        assert a.last_error == "设备不可用"
        assert q.pop_next().username == "a"

    def test_defer_task_moves_to_tail(self):
        """执行锁冲突 defer: 队尾重排, 不阻塞后续账号。"""
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        a = q.pop_next()
        q.defer_task(a.id)                   # 冲突 → a 回 WAITING 队尾
        assert a.status == QueueAccountStatus.WAITING
        assert q.current is None
        b = q.pop_next()
        assert b.username == "b"
        q.mark_success(b.id)
        assert q.pop_next().username == "a"

    def test_defer_deque_task_rotates(self):
        q = make_queue()
        ids = {}
        for u in ("a", "b", "c"):
            t, _ = q.add_task(u, f"p{u}")
            ids[u] = t.id
        assert q.defer_task(ids["a"])
        assert q.pop_next().username == "b"  # a 被挪到队尾


class TestResetPreservesQueue:
    def test_reset_interrupts_current_only(self):
        """第 56 节: 重置打断当前账号 → INTERRUPTED, 队列其余保留。"""
        q = make_queue()
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        q.add_task("c", "pc")
        a = q.pop_next()
        q.mark_interrupted(a.id, reason="DEVICE_RESET")
        assert a.status == QueueAccountStatus.INTERRUPTED
        assert a.last_error == "DEVICE_RESET"
        # 其余账号原样保留(FIFO 顺序)
        assert [t.username for t in q._deque] == ["a", "b", "c"]
