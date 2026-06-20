"""
douyin_core/adb_controller.py
抖音自动化框架 — ADB/uiautomator2 + Airtest图像识别 混合操作层

三级定位策略（按优先级）：
  1. 图像模板匹配（Airtest）— 不受UI文本变化影响，抖音更新也能识别
  2. UI层级文本/描述匹配（uiautomator2）
  3. 屏幕坐标兜底

坐标来源：用户提供截图 + 智谱视觉分析
  底部 5 个 Tab（从左到右）：首页(0.10) 朋友(0.30) +(0.50) 消息(0.70) 我(0.90)
  评论区按钮：右侧 (0.93, 0.71)
  互动消息入口：消息页中上部 (0.20, 0.28)
"""
from __future__ import annotations

import logging
import time
import random
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import uiautomator2 as u2

from douyin_core import config as cfg

logger = logging.getLogger(__name__)

# ── 底部导航 Tab 横坐标 ──
TAB_HOME = 0.10       # 首页
TAB_FRIENDS = 0.30    # 朋友
TAB_CREATE = 0.50     # + 号
TAB_MESSAGES = 0.70   # 消息
TAB_ME = 0.90         # 我
TAB_Y = 0.96          # 底部 Tab 纵坐标

# ── 模板图片目录 ──
TEMPLATES_DIR = cfg.PROJECT_ROOT / "materials" / "templates"

# 模板匹配阈值（0-1，越高越严格）
TEMPLATE_THRESHOLD = 0.7


class TemplateMatcher:
    """
    图像模板匹配器。
    基于 OpenCV 模板匹配（Airtest 核心算法），不依赖 Airtest 框架以减少耦合。
    支持屏幕缩放适配。
    """

    def __init__(self, screen_w: int, screen_h: int):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._cache: dict[str, np.ndarray] = {}

    def _load_template(self, name: str) -> Optional[np.ndarray]:
        """加载模板图片（带缓存）"""
        if name in self._cache:
            return self._cache[name]
        path = TEMPLATES_DIR / f"{name}.png"
        if not path.exists():
            logger.debug(f"[TemplateMatcher] 模板不存在: {path}")
            return None
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is not None:
            self._cache[name] = img
        return img

    def find(self, template_name: str, screenshot: np.ndarray = None,
             threshold: float = None) -> Optional[tuple[int, int]]:
        """
        在屏幕截图中查找模板，返回中心坐标 (x, y)，未找到返回 None。
        支持多尺度匹配以适应不同屏幕分辨率。
        """
        if threshold is None:
            threshold = TEMPLATE_THRESHOLD

        tpl = self._load_template(template_name)
        if tpl is None:
            return None

        if screenshot is None:
            return None

        th, tw = tpl.shape[:2]
        sh, sw = screenshot.shape[:2]

        if tw > sw or th > sh:
            return None

        # 多尺度匹配：原始 + 0.9x + 1.1x 缩放
        scales = [1.0, 0.9, 1.1]
        best_val = 0
        best_pos = None

        for scale in scales:
            nw, nh = int(tw * scale), int(th * scale)
            if nw > sw or nh > sh or nw < 10 or nh < 10:
                continue
            resized = cv2.resize(tpl, (nw, nh))
            result = cv2.matchTemplate(screenshot, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_pos = (max_loc[0] + nw // 2, max_loc[1] + nh // 2)

        if best_val >= threshold and best_pos is not None:
            logger.debug(
                f"[TemplateMatcher] 找到 '{template_name}' "
                f"置信度={best_val:.2f} 位置={best_pos}"
            )
            return best_pos

        return None

    def exists(self, template_name: str, screenshot: np.ndarray = None,
               threshold: float = None) -> bool:
        return self.find(template_name, screenshot, threshold) is not None


class BaseActions:
    """基础设备操作（点击/滑动/截图/按键/智能定位）"""

    def __init__(self, device: u2.Device):
        self.d = device
        ws = self.d.window_size()
        self.screen_w = ws[0]
        self.screen_h = ws[1]
        self.tm = TemplateMatcher(self.screen_w, self.screen_h)

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

    def _capture_screen(self) -> np.ndarray:
        """截图并转为 numpy 数组（用于模板匹配）"""
        pil_img = self.d.screenshot()
        return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    def _smart_find_and_tap(self,
                            template: str = None,
                            text: str = None,
                            desc: str = None,
                            coord: tuple[float, float] = None,
                            timeout: float = 3.0) -> bool:
        """
        三级混合定位点击（图像 > UI层级 > 坐标）：
        1. template: 模板文件名（不含.png），存在则优先图像匹配
        2. text/desc: UI 层级文本匹配（uiautomator2）
        3. coord: 坐标兜底 (rx, ry)
        """
        # ── 第一级：图像模板匹配 ──
        if template:
            try:
                screen = self._capture_screen()
                pos = self.tm.find(template, screen)
                if pos is not None:
                    self._tap(pos[0], pos[1])
                    return True
            except Exception as e:
                logger.debug(f"[SmartTap] 模板匹配异常: {e}")

        # ── 第二级：UI层级文本匹配 ──
        if text or desc:
            try:
                if text:
                    el = self.d(text=text)
                else:
                    el = self.d(description=desc)
                if el.wait(timeout=timeout):
                    el.click()
                    self._rand_delay()
                    return True
            except Exception:
                pass

        # ── 第三级：坐标兜底 ──
        if coord:
            self._tap_ratio(coord[0], coord[1])
            return True

        return False

    def _find_and_tap(self, text: str = None, desc: str = None,
                      timeout: float = 5.0) -> bool:
        """兼容旧接口：纯UI层级匹配"""
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
        """打开当前视频评论区 — 图像匹配优先，不受UI文字变化影响"""
        self.b._smart_find_and_tap(
            template="comment_btn",
            desc="评论",
            coord=(0.93, 0.71),
            timeout=3.0
        )
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
        """点击底部「消息」Tab — 图像模板优先，然后坐标"""
        self.b._smart_find_and_tap(
            template="msg_tab",
            desc="消息",
            coord=(TAB_MESSAGES, TAB_Y),
            timeout=2.0
        )
        time.sleep(2.0)
        return True

    def open_interaction_messages(self) -> bool:
        """在消息页面中点击「互动消息」入口 — 图像模板优先"""
        found = self.b._smart_find_and_tap(
            template="interaction_msg",
            text="互动消息",
            coord=(0.20, 0.28),
            timeout=3.0
        )
        if not found:
            self.b._find_and_tap(text="互动", timeout=2.0)
        time.sleep(1.5)
        return True

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

    # ── 评论区底部输入栏坐标 ──
    COMMENT_INPUT_Y = 0.965   # 输入栏纵坐标（紧贴屏幕底部）
    COMMENT_INPUT_X = 0.35    # 输入框横坐标（输入栏左侧文字区域）

    def __init__(self, base: BaseActions):
        self.b = base

    def input_comment_text(self, text: str):
        """
        点击评论区底部输入框并输入文字。
        注意：输入框在屏幕最底部(y≈0.965)，左边是文字输入区(x≈0.35)。
        不能点太高或太靠右，否则会点到用户头像/评论内容。
        """
        # 用智能定位：图像模板兜底 + 坐标
        self.b._smart_find_and_tap(
            template=None,  # 评论区输入框变化大，暂不用模板
            text=None,
            desc=None,
            coord=(self.COMMENT_INPUT_X, self.COMMENT_INPUT_Y),
        )
        time.sleep(1.0)
        self.b.d.send_keys(text)
        self.b._rand_delay(0.5, 1.0)

    def add_comment_images(self, image_paths: list[str]) -> bool:
        """
        点击评论区图片按钮添加图片。
        图片按钮在输入栏右侧第一个图标(x≈0.78, y≈0.965)。
        """
        if not image_paths:
            return True
        # 点击图片按钮（输入栏右侧第一个图标）
        self.b._smart_find_and_tap(
            template=None,
            desc="图片",
            coord=(0.78, self.COMMENT_INPUT_Y),
            timeout=2.0
        )
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
        """点击发布/发送按钮"""
        return self.b._find_and_tap(text="发布", timeout=3.0) or \
               self.b._find_and_tap(text="发送", timeout=3.0) or \
               self.b._find_and_tap(desc="发布", timeout=3.0) or \
               self.b._tap_ratio(0.88, self.COMMENT_INPUT_Y)

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
