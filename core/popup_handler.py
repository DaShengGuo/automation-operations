"""
core/popup_handler.py
全局弹窗处理 — 游戏公告/签到/活动/确认/网络重试/更新/权限/系统弹窗

配置(config/game.yaml popups 段):
  popups:
    announcement:                    # 弹窗名（任意）
      match: {text: "我知道了"}       # 判定条件: text/desc/resource_id/template 任一
      close:
        - {action: click_text, text: "我知道了"}
        - {action: press_key, key: back}   # 兜底
"""
from __future__ import annotations

import logging
import time

from core.actions import ActionExecutor

logger = logging.getLogger(__name__)


class PopupHandler:
    """弹窗识别 + 关闭动作执行"""

    def __init__(self, detector, actions: ActionExecutor, popups_config: dict):
        self.detector = detector
        self.actions = actions
        self.popups = self._parse(popups_config)

    @staticmethod
    def _parse(popups_config: dict) -> list[dict]:
        out = []
        for name, cfg in (popups_config or {}).items():
            if not isinstance(cfg, dict):
                continue
            out.append({
                "name": name,
                "match": cfg.get("match") or {},
                "close": cfg.get("close") or [],
            })
        return out

    def _matches(self, popup: dict) -> bool:
        m = popup["match"]
        if not m:
            return False
        # 非阻塞快速判断(2026-08-21 速度优化): 旧实现 find_element(timeout=1.5)
        # 用 u2 el.wait() 阻塞轮询, 无弹窗时每个 popup 睡满 1.5s — N 个 popup
        # 配置 ×1.5s 累积(handle_popups 每次调用 4.5s+, 主页→点球路径被多次
        # 调用, 是「检测主页成功后等几十秒」的根因之一)。改用 d.exists 瞬时
        # 判断(一次 dump_hierarchy + 内存匹配, 不阻塞)。
        d = self.detector.d
        try:
            if "text" in m and m["text"] and d.exists(text=m["text"], timeout=0):
                return True
            if "desc" in m and m["desc"] and d.exists(description=m["desc"], timeout=0):
                return True
            if "resource_id" in m and m["resource_id"]:
                if d.exists(resourceId=m["resource_id"], timeout=0):
                    return True
        except Exception as e:
            logger.debug(f"[弹窗] exists 判断异常: {e}")
        if "template" in m and m["template"] and self.actions.matcher:
            try:
                shot = self.detector.d.screenshot()
            except Exception:
                return False
            if self.actions.matcher.exists(m["template"], shot):
                return True
        return False

    def handle(self, max_rounds: int = 3) -> int:
        """循环处理弹窗直到没有匹配或超过轮数。返回处理掉的弹窗数量。"""
        handled = 0
        for _ in range(max_rounds):
            found = None
            for popup in self.popups:
                if self._matches(popup):
                    found = popup
                    break
            if found is None:
                break
            self._close(found)
            handled += 1
            time.sleep(0.8)
        if handled:
            logger.info(f"[弹窗] 本轮处理了 {handled} 个弹窗")
        return handled

    def _close(self, popup: dict):
        """依次执行关闭动作，直到有一个成功"""
        logger.info(f"[弹窗] 处理: {popup['name']}")
        for action in popup["close"]:
            result = self.actions.execute(action)
            if result.ok:
                logger.debug(f"[弹窗] {popup['name']} 已关闭: {result.detail}")
                return
        logger.warning(f"[弹窗] {popup['name']} 关闭动作全部失败")
