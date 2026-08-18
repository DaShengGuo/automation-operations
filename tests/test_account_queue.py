"""
tests/test_account_queue.py
账号队列单元测试 — 原子领取 / 状态流转 / 卡死恢复 / 导入
"""
from __future__ import annotations

import threading
import time

import pytest

from core.account_manager import CsvProvider, import_accounts
from models.account import AccountStatus
from storage.database import Database
from storage.repositories import AccountRepository


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "test.db")
    r = AccountRepository(db, stale_minutes=1)
    yield r
    db.close()


class TestAccountQueue:

    def test_add_and_dedup(self, repo):
        id1 = repo.add("user001", "pass1")
        id2 = repo.add("user001", "pass1")  # 重复 → 返回已存在 id
        assert id1 == id2
        assert repo.stats()["PENDING"] == 1

    def test_claim_atomic_no_double_claim(self, repo):
        """两个设备不能领到同一账号"""
        repo.add("user001", "p1")
        repo.add("user002", "p2")
        a1 = repo.claim_next("DEV_A")
        a2 = repo.claim_next("DEV_B")
        assert a1 is not None and a2 is not None
        assert a1.id != a2.id
        assert repo.claim_next("DEV_C") is None  # 无剩余

    def test_claim_sets_locked_and_device(self, repo):
        repo.add("user001", "p1")
        a = repo.claim_next("DEV_A")
        assert a is not None
        loaded = repo.get(a.id)
        assert loaded.status == AccountStatus.LOCKED
        assert loaded.device_serial == "DEV_A"

    def test_claim_concurrent_threads(self, repo):
        """多线程并发领取不重复(模拟多设备)"""
        for i in range(20):
            repo.add(f"user{i:03d}", "p")
        claimed = []
        lock = threading.Lock()

        def worker(name):
            while True:
                a = repo.claim_next(name)
                if a is None:
                    return
                with lock:
                    claimed.append(a.id)

        threads = [threading.Thread(target=worker, args=(f"DEV{i}",))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(claimed) == 20
        assert len(set(claimed)) == 20  # 无重复领取

    def test_full_lifecycle(self, repo):
        repo.add("user001", "p1")
        a = repo.claim_next("DEV_A")
        repo.mark_running(a.id, "DEV_A")
        assert repo.get(a.id).status == AccountStatus.RUNNING
        repo.mark_success(a.id, "DEV_A")
        acc = repo.get(a.id)
        assert acc.status == AccountStatus.SUCCESS
        assert acc.finished_at is not None

    def test_retry_flow_under_limit(self, repo):
        repo.add("user001", "p1", max_retry=3)
        a = repo.claim_next("DEV_A")
        repo.mark_running(a.id, "DEV_A")
        final = repo.mark_retry(a.id, "DEV_A", "任务失败")
        assert final == AccountStatus.RETRY
        acc = repo.get(a.id)
        assert acc.retry_count == 1
        # RETRY 可被再次领取
        again = repo.claim_next("DEV_B", retry_cooldown=0)
        assert again is not None and again.id == a.id

    def test_retry_exceeds_limit_becomes_failed(self, repo):
        """max_retry=1: 第 2 次失败(retry_count=2)超过上限 → FAILED"""
        repo.add("user001", "p1", max_retry=1)
        a = repo.claim_next("DEV_A")
        repo.mark_running(a.id, "DEV_A")
        assert repo.mark_retry(a.id, "DEV_A", "失败1") == AccountStatus.RETRY
        a2 = repo.claim_next("DEV_A", retry_cooldown=0)
        repo.mark_running(a2.id, "DEV_A")
        final = repo.mark_retry(a2.id, "DEV_A", "失败2")
        assert final == AccountStatus.FAILED
        assert repo.get(a.id).status == AccountStatus.FAILED

    def test_mark_failed_direct(self, repo):
        repo.add("user001", "p1")
        a = repo.claim_next("DEV_A")
        repo.mark_failed(a.id, "DEV_A", "密码错误")
        assert repo.get(a.id).status == AccountStatus.FAILED

    def test_release_back_to_pending(self, repo):
        repo.add("user001", "p1")
        a = repo.claim_next("DEV_A")
        repo.release(a.id, "设备异常")
        acc = repo.get(a.id)
        assert acc.status == AccountStatus.PENDING
        assert acc.device_serial == ""

    def test_recover_stale_locked(self, repo):
        """程序崩溃后卡在 LOCKED/RUNNING 的账号恢复"""
        repo.add("user001", "p1", max_retry=3)
        a = repo.claim_next("DEV_A")
        # 伪造卡死: updated_at 回拨 2 分钟
        old = time.time() - 120
        repo.db.execute(
            "UPDATE accounts SET updated_at=? WHERE id=?", (old, a.id))
        recovered = repo.recover_stale()
        assert recovered == 1
        acc = repo.get(a.id)
        assert acc.status == AccountStatus.RETRY
        assert acc.retry_count == 1
        assert "stale" in acc.last_error

    def test_recover_stale_over_max_becomes_failed(self, repo):
        """重试次数已用尽(max_retry=1 且 retry_count=1)的卡死账号 → FAILED"""
        repo.add("user001", "p1", max_retry=1)
        a = repo.claim_next("DEV_A")
        repo.mark_running(a.id, "DEV_A")
        repo.db.execute(
            "UPDATE accounts SET retry_count=1 WHERE id=?", (a.id,))
        old = time.time() - 120
        repo.db.execute(
            "UPDATE accounts SET updated_at=? WHERE id=?", (old, a.id))
        recovered = repo.recover_stale()
        assert recovered == 1
        assert repo.get(a.id).status == AccountStatus.FAILED

    def test_recover_stale_ignores_fresh(self, repo):
        repo.add("user001", "p1")
        a = repo.claim_next("DEV_A")
        assert repo.recover_stale() == 0  # 刚锁定不恢复
        assert repo.get(a.id).status == AccountStatus.LOCKED

    def test_add_batch_syncs_new_password_from_group(self, repo):
        """回归(2026-08-16): QQ 群是凭据源 — 运营在群里发新密码后,
        已入库账号必须更新密码(旧实现只跳过, 新密码永不生效)"""
        repo.add("user001", "oldpass")
        added, skipped = repo.add_batch([("user001", "newpass")])
        assert (added, skipped) == (0, 0)
        acc = repo.get_by_account("user001")
        assert acc is not None and acc.password == "newpass"
        assert acc.status == AccountStatus.PENDING  # PENDING 不变

    def test_add_batch_revives_failed_with_new_password(self, repo):
        """FAILED 账号收到新密码 → 复活为 PENDING(重试计数清零)"""
        repo.add("user001", "oldpass")
        a = repo.claim_next("DEV_A")
        repo.mark_failed(a.id, "DEV_A", "密码错误")
        assert repo.get(a.id).status == AccountStatus.FAILED
        repo.add_batch([("user001", "newpass")])
        acc = repo.get(a.id)
        assert acc.status == AccountStatus.PENDING
        assert acc.retry_count == 0
        assert acc.password == "newpass"

    def test_add_batch_skips_identical_password(self, repo):
        repo.add("user001", "samepass")
        added, skipped = repo.add_batch([("user001", "samepass")])
        assert (added, skipped) == (0, 1)

    def test_add_batch_does_not_touch_running(self, repo):
        """执行中的账号只更新密码, 不动状态"""
        repo.add("user001", "oldpass")
        a = repo.claim_next("DEV_A")
        repo.mark_running(a.id, "DEV_A")
        repo.add_batch([("user001", "newpass")])
        acc = repo.get(a.id)
        assert acc.status == AccountStatus.RUNNING
        assert acc.password == "newpass"

    def test_csv_provider(self, tmp_path, repo):
        """CSV 重复导入同账号不同密码 → 更新密码(凭据源语义,
        2026-08-16 起与 QQ 群取号一致: 新密码必须能传播入库)"""
        csv = tmp_path / "accounts.csv"
        csv.write_text("account,password\nuser001,pass1\nuser002,\n"
                       "user001,dup\n", encoding="utf-8")
        result = import_accounts(str(csv), repo)
        assert result["added"] == 2
        assert result["skipped"] == 0  # user001 密码更新, 不算跳过
        assert repo.get_by_account("user001").password == "dup"

    def test_csv_utf8_bom_and_gbk(self, tmp_path, repo):
        csv = tmp_path / "a_gbk.csv"
        csv.write_bytes("账号,密码\n手机号001,abc\n".encode("gbk"))
        provider = CsvProvider(csv)
        items = provider.fetch_accounts()
        assert len(items) == 1 and items[0][0] == "手机号001"

    def test_masked_account(self, repo):
        repo.add("13800138000", "p")
        a = repo.claim_next("DEV_A")
        assert a.masked() == "138***000"
        assert "***" in a.masked()
        assert "13800138000" not in a.masked()

    def test_stats(self, repo):
        repo.add("a", "1")
        repo.add("b", "2")
        repo.add("c", "3")
        s = repo.stats()
        assert s["PENDING"] == 3 and s["total"] == 3
        repo.claim_next("DEV_A")
        s = repo.stats()
        assert s["LOCKED"] == 1 and s["PENDING"] == 2


class TestRetryCooldown:
    """生产语义: 失败账号冷却期内不被领取(先处理正常账号)"""

    def test_retry_in_cooldown_not_claimable(self, tmp_path):
        from storage.database import Database
        from storage.repositories import AccountRepository
        db = Database(tmp_path / "t.db")
        repo = AccountRepository(db)
        repo.add("bad_user", "p", max_retry=3)
        repo.add("good_user", "p", max_retry=3)
        a = repo.claim_next("DEV_A")
        assert a.account == "bad_user"
        repo.mark_running(a.id, "DEV_A")
        repo.mark_retry(a.id, "DEV_A", "fail")
        # 冷却期内: 领到的是 good_user
        nxt = repo.claim_next("DEV_A", retry_cooldown=120)
        assert nxt is not None and nxt.account == "good_user"
        # 冷却期过后: bad_user 可再领
        nxt2 = repo.claim_next("DEV_A", retry_cooldown=-1)
        assert nxt2 is not None and nxt2.account == "bad_user"
        db.close()
