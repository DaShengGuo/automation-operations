"""tests/test_device_queue_concurrency.py
队列并发(规格第 14/23/61 节): 线程安全、无丢失、即时唤醒、
生产者-消费者压力、执行锁并发。
"""
from __future__ import annotations

import threading
import time

from core.account_queues import (DeviceAccountQueue,
                                 GlobalAccountExecutionRegistry,
                                 ManualDeviceQueueManager)


class TestConcurrentAdds:
    def test_parallel_adds_no_loss(self):
        q = DeviceAccountQueue("SER-1")
        n_threads, per_thread = 8, 25
        errors = []

        def add_batch(prefix):
            try:
                for i in range(per_thread):
                    q.add_task(f"{prefix}-{i}", "pw")
            except Exception as e:          # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=add_batch, args=(f"t{i}",))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert q.pending_total() == n_threads * per_thread

    def test_producer_consumer_all_completed_once(self):
        """100 个账号并发加入, 消费线程逐个完成 — 不丢不重。"""
        q = DeviceAccountQueue("SER-1")
        total = 100
        done = threading.Event()

        def producer():
            for i in range(total):
                q.add_task(f"u{i}", "pw")

        def consumer():
            while q.completed_count < total:
                t = q.pop_next()
                if t is None:
                    if not q.wait_for_task(1.0):
                        continue
                    continue
                q.mark_success(t.id)
            done.set()

        ct = threading.Thread(target=consumer)
        ct.start()
        producer()
        assert done.wait(timeout=10), "消费未在时限内完成"
        ct.join(timeout=2)
        assert q.completed_count == total
        assert q.pending_total() == 0

    def test_many_waiters_wake_on_single_add(self):
        q = DeviceAccountQueue("SER-1")
        results = []
        lock = threading.Lock()

        def waiter(i):
            ok = q.wait_for_task(5.0)
            with lock:
                results.append((i, ok))

        threads = [threading.Thread(target=waiter, args=(i,))
                   for i in range(5)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        q.add_task("one", "pw")
        for t in threads:
            t.join(timeout=2)
        assert len(results) == 5
        assert all(ok for _, ok in results), results

    def test_stress_add_pop_retry_invariants(self):
        """混合操作压力: 最终 完成+失败+在队+当前 == 总添加数。"""
        q = DeviceAccountQueue("SER-1")
        n_threads, per_thread = 6, 40
        stop = threading.Event()

        def worker():
            while not stop.is_set():
                t = q.pop_next()
                if t is None:
                    q.wait_for_task(0.01)
                    continue
                q.mark_retry(t.id, error="stress")
                t = q.pop_next()
                if t is not None and t.retry_count > 2:
                    q.mark_failed(t.id, error="stress-final")
                elif t is not None:
                    q.mark_success(t.id)

        workers = [threading.Thread(target=worker) for _ in range(4)]
        for w in workers:
            w.start()

        added = 0
        for i in range(n_threads):
            for j in range(per_thread):
                q.add_task(f"w{i}-{j}", "pw")
                added += 1
        # 等全部沉淀(完成/失败/在队)
        deadline = time.time() + 8
        while time.time() < deadline:
            with q._lock:
                active = q.pending_total() + (1 if q.current else 0)
            if q.completed_count + q.failed_count + active == added:
                break
            time.sleep(0.05)
        stop.set()
        for w in workers:
            w.join(timeout=2)
        with q._lock:
            active = q.pending_total() + (1 if q.current else 0)
        assert q.completed_count + q.failed_count + active == added


class TestConcurrentConsumerProtection:
    def test_stale_current_reclaimed_not_lost(self):
        """并发消费保护(心跳重建时新旧 Worker 短暂共存):
        current 被占用时新 pop 绝不静默丢号 — 原任务按 INTERRUPTED
        收回, 其余任务留在队列。"""
        q = DeviceAccountQueue("SER-1")
        q.add_task("a", "pa")
        q.add_task("b", "pb")
        a = q.pop_next()                     # 旧 Worker 领取 a
        assert q.current is a
        t = q.pop_next()                     # 新 Worker 又来领取
        # a 被立即收回(INTERRUPTED 优先), 绝不丢失
        assert t is a
        assert q.current is a
        assert a.retry_count == 0            # 收回不烧重试
        q.mark_success(a.id)
        assert q.pop_next().username == "b"  # b 仍在队列


class TestExecutionRegistryConcurrency:
    def test_same_username_never_two_devices(self):
        """第 39 节: 同 username 并发抢锁 — 跨设备只有一个赢家。"""
        reg = GlobalAccountExecutionRegistry()
        results = []
        lock = threading.Lock()

        def racer(serial):
            ok = reg.try_acquire("shared-user", serial, task_id=1)
            with lock:
                results.append((serial, ok))

        threads = [threading.Thread(target=racer, args=(f"SER-{i}",))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [s for s, ok in results if ok]
        assert len(winners) == 1, f"跨设备赢家应唯一: {winners}"
        assert reg.owner_of("shared-user") == winners[0]

    def test_same_device_idempotent(self):
        reg = GlobalAccountExecutionRegistry()
        assert reg.try_acquire("u", "SER-1", 1)
        assert reg.try_acquire("u", "SER-1", 2)   # 同设备幂等
        assert reg.owner_of("u") == "SER-1"


class TestManagerConcurrency:
    def test_totals_during_adds(self):
        mgr = ManualDeviceQueueManager()
        stop = threading.Event()
        errors = []

        def adder():
            try:
                for i in range(100):
                    mgr.queue_for("SER-1").add_task(f"u{i}", "pw")
            except Exception as e:          # pragma: no cover
                errors.append(e)

        def reader():
            while not stop.is_set():
                mgr.totals()

        t1 = threading.Thread(target=adder)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        stop.set()
        t2.join()
        assert not errors
        assert mgr.totals()["waiting"] == 100
