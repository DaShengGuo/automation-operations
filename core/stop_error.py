"""
core/stop_error.py
协作式停止信号 — GUI「全部停止」→ 1 秒内中断 Worker 长循环。

设计(2026-08-21 停止按钮失效修复):
  旧实现只在 DeviceWorker.run() 循环头检查 stop_event, 但进入
  execute_task/login/wait_home 后是单个长调用, 期间绝不检查 — GUI
  点停止后后台仍继续控制手机(客户实测: 弹"已停止"但手机仍在点击滑动)。

  修复: 自动化层所有长循环每轮已调 tick_heartbeat() 刷新心跳 — 复用
  该注入点检查 stop_event, 置位时抛 WorkerStopRequested。
  WorkerStopRequested 继承 BaseException(非 Exception), 穿透
  _tick 内的 `except Exception` 恢复逻辑, 直达 run() 顶层安静退出。
  长循环每轮间隔 ≤2s(滑动循环 0.4s, 登录轮询 0.5-2s), 保证 1 秒内中断。
"""
from __future__ import annotations


class WorkerStopRequested(BaseException):
    """停止指令生效 — 协作式中断当前 Worker 长循环。

    由 adapter.tick_heartbeat() 在 stop_event 置位时抛出。
    继承 BaseException 而非 Exception: 避免被 worker/automation 各层
    `except Exception` 吞掉转入错误恢复(那会掩盖停止意图)。
    """

    def __init__(self, msg: str = "收到停止指令"):
        super().__init__(msg)
