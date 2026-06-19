"""
douyin_core/adb_controller.py
抖音自动化框架 — ADB/uiautomator2 统一操作层
"""
from __future__ import annotations

import time
import random
from pathlib import Path
from typing import Optional

import uiautomator2 as u2

from douyin_core import config as cfg


class BaseActions:
    """基础设备操作（点击/滑动/截图/按键）"""

    def __init__(self, device: u2.Device):
        self.d = device
        ws = self.d.window_size()
        self.screen_w = ws[0]
        self.screen_h = ws[1]

    def _rand_delay(self, lo: float = None, hi: float = None):
        time.sleep(random.uniform(
            lo or cfg.CLICK_DELAY_MIN, hi or cfg.CLICK_DELAY_MAX))

    def _tap(self, x: int, y: int):
        self.d.click(x, y)
        self._rand_delay()

    def _tap_ratio(self, rx: float, ry: float):
        x = int(self.screen_w * rx)
        y = int(self.screen_h * ry)
        self._tap(x, y)

    def _swipe_up(self, distance_ratio: float = 0.5):
        sx = int(self.screen_w * 0.5)
        sy = int(self.screen_h * 0.8)
        ey = int(self.screen_h * (0.8 - distance_ratio))
        self.d.swipe(sx, sy, sx, ey,
                     duration=random.uniform(cfg.SWIPE_DURATION_MIN,
                                             cfg.SWIPE_DURATION_MAX))
        self._rand_delay()

    def _press_back(self):
        self.d.press("back")
        self._rand_delay(0.8, 1.5)

    def _find_and_tap(self, text: str = None, desc: str = None,
                      timeout: float = 5.0) -> bool:
        try:
            if text:
                el = self.d(text=text)
            elif desc:
                el = self.d(description=desc)
            else:
                return False
            if el.wait(timeout=timeout):
                el.click()
                self._rand_delay()
                return True
        except Exception:
            pass
        return False

    def screenshot(self, name: str = None) -> str:
        if name is None:
            name = f"screenshot_{int(time.time())}"
        path = str(cfg.SCREENSHOT_DIR / f"{name}.png")
        img = self.d.screenshot()
        img.save(path)
        return path

    def check_captcha(self) -> bool:
        xml = self.d.dump_hierarchy()
        for kw in cfg.MANUAL_INTERVENTION_KEYWORDS:
            if kw in xml:
                return True
        return False

    def dump_hierarchy(self) -> str:
        return self.d.dump_hierarchy()


class NavigateActions:
    """导航操作（刷推荐Tab、搜索、进视频）"""

    def __init__(self, base: BaseActions):
        self.b = base

    def open_recommend_tab(self):
        self.b._tap_ratio(0.13, 0.95)
        time.sleep(1.5)

    def swipe_next_video(self):
        self.b._swipe_up(0.65)

    def open_comments(self) -> bool:
        found = self.b._find_and_tap(desc="评论", timeout=3.0)
        if not found:
            self.b._tap_ratio(0.85, 0.75)
        time.sleep(1.5)
        return True

    def close_comments(self):
        self.b._press_back()
        time.sleep(0.8)

    def search_keyword(self, keyword: str) -> bool:
        self.b._tap_ratio(0.92, 0.06)
        time.sleep(1.5)
        self.b.d.send_keys(keyword)
        self.b._rand_delay(1.0, 2.0)
        self.b.d.press("enter")
        time.sleep(2)
        return self.b._find_and_tap(text="视频", timeout=3.0) or \
               self.b._find_and_tap(desc="视频", timeout=2.0)

    def enter_video_by_index(self, index: int) -> bool:
        col = index % 2
        row = index // 2
        card_h = 0.28
        start_y = 0.15
        cx = 0.26 if col == 0 else 0.74
        cy = start_y + row * card_h
        if cy > 0.85:
            return False
        self.b._tap_ratio(cx, cy)
        time.sleep(random.uniform(1.5, 3.0))
        return True


class CommentActions:
    """评论操作（发布评论、上传图片、删除评论）"""

    def __init__(self, base: BaseActions):
        self.b = base

    def input_comment_text(self, text: str):
        self.b._tap_ratio(0.5, 0.92)
        time.sleep(1.0)
        self.b.d.send_keys(text)
        self.b._rand_delay(0.5, 1.0)

    def add_comment_images(self, image_paths: list[str]) -> bool:
        if not image_paths:
            return True
        self.b._tap_ratio(0.12, 0.92)
        time.sleep(2.0)
        for _ in image_paths[:2]:
            self.b._find_and_tap(text="最近", timeout=2.0) or \
            self.b._find_and_tap(text="图片", timeout=2.0) or \
            self.b._find_and_tap(text="相册", timeout=2.0) or \
            self.b._tap_ratio(0.25, 0.35)
            time.sleep(1.0)
        return self.b._find_and_tap(text="完成", timeout=2.0) or \
               self.b._find_and_tap(text="确定", timeout=2.0) or \
               self.b._find_and_tap(desc="完成", timeout=2.0)

    def submit_comment(self) -> bool:
        return self.b._find_and_tap(text="发布", timeout=3.0) or \
               self.b._find_and_tap(text="发送", timeout=3.0) or \
               self.b._find_and_tap(desc="发布", timeout=3.0) or \
               self.b._tap_ratio(0.88, 0.92)

    def verify_comment_published(self) -> bool:
        time.sleep(cfg.POST_VERIFY_WAIT)
        xml = self.b.dump_hierarchy()
        return "删除" in xml or "刚刚" in xml

    def delete_my_comment(self) -> bool:
        found = self.b._find_and_tap(text="删除", timeout=2.0)
        if not found:
            self.b.d.long_click(
                int(self.b.screen_w * 0.5),
                int(self.b.screen_h * 0.45),
                duration=1.0
            )
            time.sleep(1.0)
            found = self.b._find_and_tap(text="删除", timeout=2.0)
        if found:
            time.sleep(0.5)
            self.b._find_and_tap(text="确认", timeout=1.5) or \
            self.b._find_and_tap(text="确定", timeout=1.5)
        return found

    def reply_to_comment(self, reply_text: str) -> bool:
        found = self.b._find_and_tap(text="回复", timeout=2.0)
        if not found:
            return False
        time.sleep(0.5)
        self.b.d.send_keys(reply_text)
        self.b._rand_delay(0.3, 0.8)
        return self.submit_comment()


class UserActions:
    """用户操作（关注、私信）"""

    def __init__(self, base: BaseActions):
        self.b = base

    def click_user_avatar(self) -> bool:
        return self.b._find_and_tap(desc="头像", timeout=2.0) or \
               self.b._tap_ratio(0.08, 0.38)

    def follow_user(self) -> bool:
        return self.b._find_and_tap(text="关注", timeout=3.0) or \
               self.b._find_and_tap(desc="关注", timeout=3.0)

    def send_dm(self, message: str) -> bool:
        found = self.b._find_and_tap(text="私信", timeout=3.0) or \
                self.b._find_and_tap(desc="私信", timeout=3.0)
        if not found:
            found = self.b._find_and_tap(text="聊天", timeout=2.0)
        if not found:
            return False
        time.sleep(1.5)
        self.b.d.send_keys(message)
        time.sleep(0.5)
        return self.b._find_and_tap(text="发送", timeout=2.0) or \
               self.b._find_and_tap(desc="发送", timeout=2.0) or \
               self.b.d.press("enter")


class DouyinController:
    """统一控制器：组合所有 Action 类"""

    def __init__(self, device_addr: str = None):
        addr = device_addr or cfg.MUMU_ADB_ADDR
        self.d = u2.connect(addr) if addr else u2.connect()
        info = self.d.info
        ws = self.d.window_size()
        print(f"[设备] 已连接: {info.get('productName', 'unknown')} "
              f"{ws[0]}×{ws[1]}")
        self.base = BaseActions(self.d)
        self.nav = NavigateActions(self.base)
        self.comment = CommentActions(self.base)
        self.user = UserActions(self.base)

    def open_douyin(self):
        self.d.app_start(cfg.DOUYIN_PACKAGE)
        time.sleep(3)
        print("[App] 抖音已启动")

    def close_douyin(self):
        self.d.app_stop(cfg.DOUYIN_PACKAGE)

    def restart_douyin(self):
        self.close_douyin()
        time.sleep(2)
        self.open_douyin()

    def check_captcha(self) -> bool:
        return self.base.check_captcha()
