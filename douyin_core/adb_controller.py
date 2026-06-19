"""
douyin_core/adb_controller.py
抖音自动化框架 — ADB/uiautomator2 统一操作层

坐标来源：用户提供截图 + 智谱视觉分析
  底部 5 个 Tab（从左到右）：首页(0.10) 朋友(0.30) +(0.50) 消息(0.70) 我(0.90)
  评论区按钮：右侧 (0.93, 0.71)
  互动消息入口：消息页中上部 (0.20, 0.28)
"""
from __future__ import annotations

import time
import random
from pathlib import Path
from typing import Optional

import uiautomator2 as u2

from douyin_core import config as cfg

# ── 底部导航 Tab 横坐标（5等分，基于1080px宽度实测） ──
TAB_HOME = 0.10       # 首页
TAB_FRIENDS = 0.30    # 朋友
TAB_CREATE = 0.50     # + 号
TAB_MESSAGES = 0.70   # 消息
TAB_ME = 0.90         # 我
TAB_Y = 0.96          # 底部 Tab 纵坐标


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
    """导航操作（刷推荐Tab、搜索、进视频、消息通知）"""

    def __init__(self, base: BaseActions):
        self.b = base

    def open_recommend_tab(self):
        """点击底部「首页」回到推荐Tab"""
        self.b._tap_ratio(TAB_HOME, TAB_Y)
        time.sleep(1.5)

    def swipe_next_video(self):
        """手动下滑刷下一个视频"""
        self.b._swipe_up(0.65)

    def open_comments(self) -> bool:
        """打开当前视频评论区（右侧评论按钮）"""
        found = self.b._find_and_tap(desc="评论", timeout=3.0)
        if not found:
            self.b._tap_ratio(0.93, 0.71)  # 右侧评论区按钮（截图实测）
        time.sleep(1.5)
        return True

    def close_comments(self):
        """关闭评论区"""
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

    # ── 消息通知 + 互动消息导航 ──

    def check_message_badge(self) -> bool:
        """
        检查底部「消息」Tab是否有红色未读标记。
        通过截图+OCR或UI层级检测。
        """
        xml = self.b.dump_hierarchy()
        # 抖音消息Tab的红点通常有 content-desc 包含数字或"未读"
        import re
        # 查找包含未读数字的节点
        if re.search(r'未读|消息.*\d+', xml):
            return True
        # 备用：检查 content-desc 中的数字红点
        if re.search(r'content-desc="\d+"', xml):
            return True
        return False

    def open_messages_tab(self) -> bool:
        """点击底部「消息」Tab（第4个Tab，实测 0.70）"""
        self.b._tap_ratio(TAB_MESSAGES, TAB_Y)
        time.sleep(2.0)
        return True

    def open_interaction_messages(self) -> bool:
        """在消息页面中点击「互动消息」入口"""
        found = self.b._find_and_tap(text="互动消息", timeout=3.0) or \
                self.b._find_and_tap(desc="互动消息", timeout=3.0)
        if not found:
            found = self.b._find_and_tap(text="互动", timeout=2.0)
        if not found:
            # 坐标兜底：消息页中上部，互动消息入口（截图实测≈0.20, 0.28）
            self.b._tap_ratio(0.20, 0.28)
            found = True
        time.sleep(1.5)
        return found

    def open_first_reply_detail(self) -> bool:
        """
        在互动消息列表中，点击第一条回复进入详情页。
        每条显示「XXX回复了你的评论」，第一条在列表最上方。
        """
        # 互动消息列表第一项（实测约屏幕上部 1/3 偏下）
        self.b._tap_ratio(0.50, 0.35)
        time.sleep(2.0)
        return True

    def reply_in_message_detail(self, reply_text: str) -> bool:
        """
        在评论详情页（从互动消息进入）直接回复。
        详情页底部有回复输入框。
        """
        # 点击底部回复输入框
        self.b._tap_ratio(0.5, 0.92)
        time.sleep(1.0)
        self.b.d.send_keys(reply_text)
        self.b._rand_delay(0.3, 0.8)
        # 点击发送
        return self.b._find_and_tap(text="发送", timeout=2.0) or \
               self.b._find_and_tap(desc="发送", timeout=2.0) or \
               self.b.d.press("enter")

    def close_message_detail(self):
        """从评论详情页返回互动消息列表"""
        self.b._press_back()
        time.sleep(0.8)

    def close_messages_to_home(self):
        """从消息页返回首页推荐Tab"""
        self.open_recommend_tab()


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
