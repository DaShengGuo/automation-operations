"""core/device_worker.py 回归测试"""
import inspect

from core.device_worker import DeviceWorker


class TestDeviceWorker:
    def test_prefetch_slot_does_not_shadow_next_account_method(self):
        """回归: __init__ 的预取槽位曾叫 _next_account, 遮蔽同名方法,
        导致超时/失败路径 TypeError: 'NoneType' object is not callable,
        真机表现为 TIME_BUDGET_EXCEEDED 死循环烧光重试次数。"""
        src = inspect.getsource(DeviceWorker.__init__)
        # 槽位必须叫 _prefetched_account(桌面版支持 prefetched_account 注入)
        assert "_prefetched_account" in src
        w = DeviceWorker.__new__(DeviceWorker)
        w._prefetched_account = object()  # 槽位被占时方法仍可调用
        assert callable(w._next_account)
