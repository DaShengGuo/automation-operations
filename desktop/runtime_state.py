"""
desktop/runtime_state.py
ApplicationRunState — 应用级运行状态统一枚举。

按钮 enable/disable 全部由此驱动, 避免连续点击生成重复 Worker。
"""
from enum import Enum


class ApplicationRunState(str, Enum):
    STOPPED = "STOPPED"      # 未运行(软件打开时的初始状态, 禁止自动运行)
    STARTING = "STARTING"    # 确认并运行 / 开始运行 的初始化过程中
    RUNNING = "RUNNING"      # 生产运行中
    STOPPING = "STOPPING"    # 收到停止请求, Worker 在安全检查点退出
    ERROR = "ERROR"          # 初始化失败(环境/ADB 错误)

    @property
    def display(self) -> str:
        return {
            ApplicationRunState.STOPPED: "已停止",
            ApplicationRunState.STARTING: "启动中",
            ApplicationRunState.RUNNING: "运行中",
            ApplicationRunState.STOPPING: "正在停止",
            ApplicationRunState.ERROR: "错误",
        }[self]
