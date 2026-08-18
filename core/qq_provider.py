"""
core/qq_provider.py
QQ 群账号来源 — 群聊「上一条消息=账号, 下一条=密码」

只有 config.yaml 配置 account_provider: qq_ui 时才启用(规格第 47 节)。
每台设备操作自己手机上已登录的 QQ(互不干扰), 与业务解耦:
Pokémon GO 适配器只接收 account.username / account.password。

消息格式(真机录屏验证):
    czt24720      ← 账号(字母开头的字母数字串)
    Aa12345.      ← 密码(紧随其后的下一条消息)
已导入账号通过 accounts 表唯一约束自动去重, 不会重复执行。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from core.logger import mask_account

logger = logging.getLogger(__name__)

QQ_PACKAGE = "com.tencent.mobileqq"
# 真机实测: 搜索输入框是自定义 TextView, 非 EditText, 其 text 恒为
# 占位符「搜索指定内容」(输入内容不进 hierarchy)。
# 注意 QQ 的 resource-id 会混淆(消息列表搜索入口本次为 id/z7v, 之前是
# id/kbs), 入口一律用 desc「搜索」+ clickable 定位, 不依赖 id。
QQ_SEND_BTN_ID = "com.tencent.mobileqq:id/send_btn"
QQ_SEARCH_INPUT_TEXT = "搜索指定内容"
# 兜底: 搜索页输入框两代 dump 的 resource-id 都是 kbs(入口节点才会混淆)
QQ_SEARCH_INPUT_ID = "com.tencent.mobileqq:id/kbs"
# 清输入框残留: MOVE_END(123) + 连删(67), 单条 shell 命令。
# 搜索框是自定义 View, UiObject.clear_text 无效; 而 desc「删除」节点是
# 「清空最近搜索记录」按钮(真机实拍), 点击会弹确认框 — 不能用。
# 真机已验证: 30 次 DEL 足以清空上次残留的群名。
QQ_CLEAR_KEYEVENTS = ["123"] + ["67"] * 30

ACCOUNT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{5,15}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_!@#$%^&*.,?~]{4,20}$")
# 过滤非消息文本(昵称/时间戳/系统提示)
NOISE_RE = re.compile(r"管理员|LV|Lv|星期|今天|昨天|撤回|图片|视频|语音|文件|来单")


def is_account_text(text: str) -> bool:
    """判定账号消息: 字母开头的纯字母数字串(如 czt24720)"""
    t = text.strip()
    return bool(ACCOUNT_RE.match(t)) and not NOISE_RE.search(t)


def is_password_text(text: str) -> bool:
    """判定密码消息: 字母数字+特殊字符, 且(含特殊字符或大小写混合)"""
    t = text.strip()
    if not PASSWORD_RE.match(t):
        return False
    if NOISE_RE.search(t):
        return False
    has_special = any(c in "!@#$%^&*.,?~" for c in t)
    has_mixed_case = any(c.islower() for c in t) and \
        any(c.isupper() for c in t)
    return has_special or has_mixed_case


def pair_messages(texts: list[str]) -> list[tuple[str, str]]:
    """从消息序列(旧→新)提取 (账号, 密码) 对。

    规则: 密码消息紧邻其上一条账号消息。
    """
    pairs = []
    i = 0
    while i < len(texts):
        t = texts[i].strip()
        if is_account_text(t) and i + 1 < len(texts) and \
                is_password_text(texts[i + 1].strip()):
            pairs.append((t, texts[i + 1].strip()))
            i += 2
        else:
            i += 1
    return pairs


class QQAccountProvider:
    """从本机 QQ 群读取最新账号(账号消息 + 下一条密码消息)"""

    def __init__(self, controller, group_name: str = "游戏自动化购买",
                 log=None):
        self.d = controller            # DeviceController(本机 u2 会话)
        self.group_name = group_name
        self.log = log or logging.getLogger(__name__)

    # ── 导航 ──

    def open_qq(self, timeout: float = 30) -> bool:
        """打开 QQ(已登录状态, 不处理 QQ 自身登录)"""
        try:
            cur = self.d.current_package()
            if cur == QQ_PACKAGE:
                return True
            self.d.device.app_start(QQ_PACKAGE, wait=False)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self.d.current_package() == QQ_PACKAGE:
                    return True
                time.sleep(1.5)
        except Exception as e:
            self.log.warning(f"[QQ] 打开失败: {e}")
        return False

    # 聊天页特征(真机 dump 实拍, 按属性值精确匹配):
    # 注意「表情」在搜索页也有(表情 tab), 不能作为特征;
    # 「聊天设置」是右上角三条杠按钮的 desc, 不用它判断(避免把
    # 三条杠菜单页误判成聊天页)。
    _CHAT_FEATURE_RE = re.compile(
        r'(?:content-desc|text)="(发送|听筒模式|语音|相册|拍照|泡泡|'
        r'更多功能|返回消息未读0|我的资料卡)"')

    def enter_group(self, timeout: float = 60) -> bool:
        """进入目标群聊 — 严格流程:

        搜索群名 → 点搜索结果 → 验证聊天页。
        QQ 冷启动会恢复上次窗口(可能就在本群聊天页) —
        已在聊天页时立即成功, 不做任何点击(避免误触右上角三条杠)。
        进入聊天页 → 取号 → 打开游戏之间不做任何其他操作。
        """
        # 0. 已在聊天页(冷启动恢复窗口) → 零操作直接成功
        if self._is_in_chat():
            self.log.info(f"[QQ] 已在群聊: {self.group_name}")
            return True
        return self._search_group(timeout)

    def _search_group(self, timeout: float) -> bool:
        """搜索群名并进入聊天页。找不到搜索框就放弃, 不乱点。

        真机实测(Redmi + QQ): 消息列表搜索入口是 desc「搜索」的可点击
        节点(id 会混淆: 本次 id/z7v, 旧 dump 是 id/kbs); 搜索页输入框是
        自定义 TextView(text 恒为占位符「搜索指定内容」, 非 EditText)。
        两页 hierarchy 无可靠判别特征(「取消」是绘制按钮不进 hierarchy;
        消息列表还会残留隐藏的搜索 fragment 节点) — 所以不区分页面:
        有入口就点入口(消息列表→进搜索页) → 点输入框聚焦 → keyevent
        清残留 → 输入群名 → 点含群名的结果进入聊天页。
        """
        # 0. 等 QQ 界面渲染完成(冷启动 splash 阶段 hierarchy 为空,
        #    此时按返回会直接退出 QQ — 真机卡顿根因之一)
        ready_deadline = time.time() + 20
        while time.time() < ready_deadline:
            if self._is_in_chat():
                self.log.info(f"[QQ] 已在群聊: {self.group_name}")
                return True
            try:
                if len(self.d.dump_hierarchy()) > 100:
                    break  # 界面已渲染(真实 QQ 页面 dump 远超此值)
            except Exception:
                pass
            time.sleep(1)
        # 1. 确保在消息列表(搜索入口)或搜索页(输入框), 否则按返回
        for _ in range(2):
            if self._find_el(description="搜索", clickable=True,
                             timeout=1.5) is not None:
                break
            if self._find_search_input(timeout=1.5) is not None:
                break
            self.d.press("back")
            time.sleep(1.5)
        # 2. 消息列表 → 点搜索入口进搜索页(已在搜索页则跳过)
        entry = self._find_el(description="搜索", clickable=True,
                              timeout=2)
        if entry is not None:
            try:
                entry.click()
            except Exception:
                pass
            time.sleep(1.5)
        # 3. 点输入框聚焦(输入内容不进 hierarchy, 只能按占位符定位)
        inp = self._find_search_input(timeout=3)
        if inp is None:
            self.log.warning("[QQ] 找不到搜索输入框, 放弃搜索")
            return False
        try:
            inp.click()
        except Exception:
            pass
        time.sleep(1)
        # 4. 清残留(输入框是自定义 View, 系统无 clear 节点; 只发键事件,
        #    不点「删除」— 那是清搜索历史按钮, 会弹确认框)
        try:
            self.d.device.shell(
                ["input", "keyevent"] + QQ_CLEAR_KEYEVENTS)
        except Exception:
            pass
        time.sleep(1)
        # 5. 输入群名
        try:
            self.d.device.send_keys(self.group_name)
        except Exception as e:
            self.log.warning(f"[QQ] 输入群名失败: {e}")
        time.sleep(2)
        # 6. 逐个点含群名的结果, 进入聊天页即成功
        deadline = time.time() + timeout
        tried = 0
        while time.time() < deadline:
            try:
                els = self.d.device.xpath(
                    f'//*[contains(@text, "{self.group_name}")]').all()
            except Exception:
                break
            if tried >= len(els):
                break
            try:
                els[tried].click()
            except Exception:
                tried += 1
                continue
            time.sleep(2)
            if self._is_in_chat():
                self.log.info(f"[QQ] 已进入群聊: {self.group_name}")
                return True
            # 点错(群资料卡等) → 返回换下一个结果
            self.d.press("back")
            time.sleep(1.5)
            tried += 1
        ok = self._is_in_chat()
        if not ok:
            self.log.warning(f"[QQ] 未能进入群: {self.group_name}")
        return ok

    def _find_el(self, timeout: float = 3, **selector):
        """按 u2 selector 查元素, 找不到返回 None(不抛异常)"""
        try:
            el = self.d.device(**selector)
            if el.wait(timeout=timeout):
                return el
        except Exception as e:
            self.log.debug(f"[QQ] 查元素失败: {e}")
        return None

    def _find_search_input(self, timeout: float = 3):
        """搜索页输入框: 按占位符「搜索指定内容」或 kbs 宽节点定位
        (id 混淆两代都兼容)。找不到返回 None(不抛异常)。"""
        deadline = time.time() + timeout
        while True:
            try:
                # 优先占位符文本(输入框 id 会混淆, 文本不会)
                for el in self.d.device.xpath(
                        f'//*[@text="{QQ_SEARCH_INPUT_TEXT}"]').all():
                    return el
                # 兜底: kbs 宽节点(避开「最近搜索」标题等同 id 小节点)
                best, best_w = None, 0
                for el in self.d.device.xpath(
                        f'//*[@resource-id="{QQ_SEARCH_INPUT_ID}"]').all():
                    # u2 XMLElement.bounds: (left, top, right, bottom)
                    l, _t, r, _b = el.bounds
                    w = r - l
                    if w > best_w:
                        best, best_w = el, w
                if best is not None and best_w > 500:
                    return best
            except Exception as e:
                self.log.debug(f"[QQ] 查搜索输入框失败: {e}")
            if time.time() >= deadline:
                return None
            time.sleep(0.5)

    def _is_in_chat(self) -> bool:
        """是否在群聊天页(区别于群资料/设置/搜索/消息列表页)。

        真机实测: 聊天页输入框是自定义 View(非 EditText), 以发送按钮
        resource-id + 聊天页独有 desc 为准; 三条杠 desc「聊天设置」
        聊天页本身有, 但不作为判定词(避免把三条杠菜单页误判成聊天页)。
        """
        try:
            xml = self.d.dump_hierarchy()
            # 群设置页独有特征(真机 dump 实拍, 聊天页不会出现) → 不是聊天页
            for kw in ("群号", "加群设置", "群机器人", "精华消息",
                       "查找聊天记录", "群快捷栏", "发言管理"):
                if kw in xml:
                    return False
            if QQ_SEND_BTN_ID in xml:
                return True
            return bool(self._CHAT_FEATURE_RE.search(xml))
        except Exception:
            return False

    # ── 读取消息 ──

    def read_messages(self, max_msgs: int = 20) -> list[str]:
        """读当前群聊可见消息(旧→新顺序)。

        QQ 聊天消息在 hierarchy 的 text 属性中; 最新消息在屏幕底部。
        过滤时间戳/昵称/系统提示。
        """
        try:
            xml = self.d.dump_hierarchy()
        except Exception as e:
            self.log.warning(f"[QQ] 层级读取失败: {e}")
            return []
        texts = re.findall(r'text="([^"]+)"', xml)
        out = []
        for t in texts:
            t = t.strip()
            if not t or len(t) < 4:
                continue
            if NOISE_RE.search(t):
                continue
            if ":" in t and t.replace(":", "").isdigit():
                continue  # 时间戳
            out.append(t)
        return out[-max_msgs:]

    def fetch_latest(self, max_pairs: int = 5) -> list[tuple[str, str]]:
        """打开 QQ → 进群 → 读取最新 (账号, 密码) 对"""
        if not self.open_qq():
            return []
        if not self.enter_group():
            return []
        messages = self.read_messages()
        pairs = pair_messages(messages)
        self.log.info(f"[QQ] 读到 {len(messages)} 条消息, "
                      f"配对 {len(pairs)} 组账号")
        for acc, _ in pairs:
            # 账号同样脱敏(规格: 日志不出现完整账号名; 实测曾泄漏 Rk3***658)
            self.log.info(f"[QQ] 账号 {mask_account(acc)} (密码已脱敏)")
        return pairs[-max_pairs:]

    def back_to_game(self, game_package: str, timeout: float = 30) -> bool:
        """取号完成回到游戏前台(游戏进程未退出, am start 恢复)"""
        try:
            self.d.device.app_start(game_package, wait=False)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self.d.current_package() == game_package:
                    return True
                time.sleep(1.5)
        except Exception as e:
            self.log.warning(f"[QQ] 返回游戏失败: {e}")
        return False
