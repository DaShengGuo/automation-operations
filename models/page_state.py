"""
models/page_state.py
页面状态枚举 — 游戏页面识别结果
"""
from __future__ import annotations

from enum import Enum


class PageState(str, Enum):
    """当前设备屏幕所处的页面状态"""
    UNKNOWN = "UNKNOWN"                # 无法识别
    SPLASH = "SPLASH"                  # 启动闪屏
    LOGIN = "LOGIN"                    # 登录页
    LOGIN_LOADING = "LOGIN_LOADING"    # 登录中
    HOME = "HOME"                      # 首页
    POPUP = "POPUP"                    # 普通弹窗
    TASK_PAGE = "TASK_PAGE"            # 任务入口页
    TASK_RUNNING = "TASK_RUNNING"      # 任务执行中
    TASK_SUCCESS = "TASK_SUCCESS"      # 任务成功页
    LOGOUT = "LOGOUT"                  # 退出登录确认页
    NETWORK_ERROR = "NETWORK_ERROR"    # 网络错误
    UPDATE_DIALOG = "UPDATE_DIALOG"    # 更新弹窗
    ERROR = "ERROR"                    # 异常页

    @property
    def is_error(self) -> bool:
        return self in (PageState.NETWORK_ERROR, PageState.ERROR)

    @property
    def is_popup(self) -> bool:
        return self in (PageState.POPUP, PageState.UPDATE_DIALOG)
