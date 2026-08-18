"""
core/actions.py
统一动作执行器 — 游戏步骤与弹窗关闭共用同一套动作解释器

动作类型 (config yaml 中定义):
  wait            等待 N 秒                    {seconds: 2}
  click_text      点击文本控件                 {text: "开始"}
  click_desc      点击 content-desc 控件       {desc: "搜索"}
  click_resource_id  点击 resource-id 控件     {resource_id: "com.x:id/btn"}
  click_xpath     点击 XPath 控件              {xpath: "//*[@text='开始']"}
  click_image     模板匹配点击                 {image: "task_button", threshold: 0.85}
  click_coord     比例坐标点击                 {ratio: [0.5, 0.8]} 或 {base: [540, 2000]}
  input_text      向输入框输入文本             {selector: {...}, text: "..."}
  press_key       系统按键                    {key: "back"|"home"|"enter"}
  swipe           滑动                        {direction: "up"|"down"|"left"|"right", distance: 0.5}
  wait_page       等待页面状态                 {page: "HOME", timeout: 30}
  verify_text     验证文本存在                 {text: "任务完成"}
  verify_image    验证模板存在                 {image: "success", timeout: 10}
  restart_app     重启目标应用                 {}
  read_product    只读商品信息(dry-run 安全)   {verify_text: ...}
  click_payment   点击真实支付确认（默认被 dry_run 拦截）
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from core.exceptions import PaymentBlockedError

logger = logging.getLogger(__name__)

PAYMENT_ACTIONS = {"click_payment", "purchase_gift", "recharge"}


class ActionResult:
    """动作执行结果"""
    def __init__(self, ok: bool, detail: str = ""):
        self.ok = ok
        self.detail = detail

    def __repr__(self):
        return f"ActionResult(ok={self.ok}, detail={self.detail!r})"


class ActionExecutor:
    """执行单个动作 dict。依赖 DeviceController + UiDetector + PopupHandler。"""

    def __init__(self, controller, detector, matcher=None, popup_handler=None):
        self.d = controller          # DeviceController
        self.detector = detector     # UiDetector
        self.matcher = matcher or getattr(controller, "matcher", None)
        self.popups = popup_handler  # 步骤前后可自动处理弹窗
        self.payment_allowed = False  # 由 BaseGameAutomation 注入

    def execute(self, action: dict, context: dict = None) -> ActionResult:
        """执行一个动作，返回 ActionResult。未知动作类型返回失败(不抛异常)。"""
        action_type = str(action.get("action", ""))
        ctx = context or {}
        handler = getattr(self, f"_do_{action_type}", None)
        if handler is None:
            return ActionResult(False, f"未知动作类型: {action_type}")
        try:
            if action_type in PAYMENT_ACTIONS:
                self._guard_payment(action)
            return handler(action, ctx)
        except PaymentBlockedError as e:
            return ActionResult(False, str(e))
        except Exception as e:
            logger.debug(f"动作 {action_type} 异常: {e}")
            return ActionResult(False, f"{action_type} 失败: {e}")

    # ── 支付护栏 ──

    def _guard_payment(self, action: dict):
        """默认 dry_run：禁止自动点击真实支付确认"""
        if not self.payment_allowed:
            label = action.get("label", action.get("text",
                                 action.get("desc", "支付确认")))
            raise PaymentBlockedError(
                f"真实支付操作已被 dry_run 拦截: {label}。"
                f"需人工确认或配置明确授权后才会执行。")

    # ── 动作实现 ──

    def _do_wait(self, a, ctx):
        seconds = float(a.get("seconds", 1))
        time.sleep(seconds)
        return ActionResult(True, f"等待 {seconds}s")

    def _do_click_text(self, a, ctx):
        return self._click_element(a, timeout=a.get("timeout", 5))

    def _do_click_desc(self, a, ctx):
        return self._click_element(a, timeout=a.get("timeout", 5))

    def _do_click_resource_id(self, a, ctx):
        return self._click_element(a, timeout=a.get("timeout", 5))

    def _do_click_xpath(self, a, ctx):
        ok, el = self.detector.find_xpath(a.get("xpath", ""),
                                          timeout=a.get("timeout", 5))
        if ok and el:
            try:
                el.click()
                return ActionResult(True, f"点击 xpath: {a['xpath']}")
            except Exception as e:
                return ActionResult(False, f"xpath 点击失败: {e}")
        return ActionResult(False, f"xpath 未找到: {a.get('xpath')}")

    def _click_element(self, a, timeout):
        sel = {k: v for k, v in a.items()
               if k in ("text", "desc", "resource_id") and v}
        if not sel:
            return ActionResult(False, "动作缺少 text/desc/resource_id")
        ok, el = self.detector.find_element(sel, timeout=timeout)
        if not ok:
            return ActionResult(False, f"控件未找到: {sel}")
        try:
            el.click()
            return ActionResult(True, f"已点击: {sel}")
        except Exception as e:
            return ActionResult(False, f"控件点击失败: {e}")

    def _do_click_image(self, a, ctx):
        if self.matcher is None:
            return ActionResult(False, "ImageMatcher 未初始化")
        name = a.get("image", "")
        roi = a.get("roi")
        ok = self.matcher.click(
            name,
            click_fn=self.d.click,
            screenshot_fn=self.d.screenshot,
            threshold=a.get("threshold"),
            timeout=a.get("timeout", 10),
            roi=roi,
        )
        if ok:
            return ActionResult(True, f"已点击模板: {name}")
        return ActionResult(False, f"模板未找到: {name}")

    def _do_click_coord(self, a, ctx):
        if "ratio" in a:
            x, y = self.d.click_ratio(*a["ratio"])
        elif "base" in a:
            x, y = self.d.click_base(*a["base"])
        else:
            return ActionResult(False, "click_coord 需要 ratio 或 base")
        return ActionResult(True, f"点击坐标: ({x},{y})")

    def _do_input_text(self, a, ctx):
        text = str(a.get("text", ""))
        if not text:
            return ActionResult(False, "input_text 缺少 text")
        sel = a.get("selector")
        if sel:
            ok, el = self.detector.find_element(sel,
                                                timeout=a.get("timeout", 5))
            if not ok:
                return ActionResult(False, f"输入框未找到: {sel}")
            try:
                el.click()
                time.sleep(0.5)
                el.set_text(text)
                return ActionResult(True, f"已输入: {text[:20]}")
            except Exception as e:
                return ActionResult(False, f"输入失败: {e}")
        # 无 selector：输入到当前焦点
        try:
            self.d.send_text(text)
            return ActionResult(True, f"已发送文本: {text[:20]}")
        except Exception as e:
            return ActionResult(False, f"文本发送失败: {e}")

    def _do_press_key(self, a, ctx):
        key = a.get("key", "back")
        self.d.press(key)
        return ActionResult(True, f"按键: {key}")

    def _do_swipe(self, a, ctx):
        direction = a.get("direction", "up")
        distance = float(a.get("distance", 0.5))
        self.d.swipe_direction(direction, distance)
        return ActionResult(True, f"滑动: {direction}")

    def _do_wait_page(self, a, ctx):
        page = a.get("page", "HOME")
        timeout = float(a.get("timeout", 30))
        ok = self.detector.wait_page(page, timeout=timeout)
        if ok:
            return ActionResult(True, f"页面就绪: {page}")
        return ActionResult(False, f"等待页面超时: {page}")

    def _do_verify_text(self, a, ctx):
        ok, _ = self.detector.find_element(
            {"text": a.get("text", "")}, timeout=a.get("timeout", 5))
        if ok:
            return ActionResult(True, f"验证文本存在: {a.get('text')}")
        return ActionResult(False, f"验证文本不存在: {a.get('text')}")

    def _do_verify_image(self, a, ctx):
        if self.matcher is None:
            return ActionResult(False, "ImageMatcher 未初始化")
        pos = self.matcher.wait_for(
            a.get("image", ""),
            screenshot_fn=self.d.screenshot,
            timeout=a.get("timeout", 10),
            threshold=a.get("threshold"),
            roi=a.get("roi"),
        )
        if pos is not None:
            return ActionResult(True, f"验证模板存在: {a.get('image')}")
        return ActionResult(False, f"验证模板不存在: {a.get('image')}")

    def _do_restart_app(self, a, ctx):
        ok = self.d.restart_app()
        return ActionResult(ok, "应用已重启" if ok else "应用重启失败")

    def _do_handle_popups(self, a, ctx):
        """步骤内显式触发弹窗处理"""
        if self.popups is None:
            return ActionResult(False, "PopupHandler 未注入")
        count = self.popups.handle(max_rounds=int(a.get("max_rounds", 3)))
        return ActionResult(True, f"处理弹窗 {count} 个")

    def _do_read_product(self, a, ctx):
        """只读商品信息（名称/金额/页面验证），不产生任何购买行为 — dry_run 安全"""
        details = []
        name_el = a.get("name_text")
        if name_el and self.detector.find_element({"text": name_el},
                                                  timeout=3)[0]:
            details.append(f"商品={name_el}")
        price_el = a.get("price_text")
        if price_el and self.detector.find_element({"text": price_el},
                                                   timeout=3)[0]:
            details.append(f"金额={price_el}")
        ok, _ = self.detector.wait_page(a.get("page", "TASK_PAGE"),
                                        timeout=a.get("timeout", 10))
        if not ok:
            return ActionResult(False, "商品页面验证失败")
        return ActionResult(True, "商品信息已读取(未购买): " + " ".join(details))

    def _do_click_payment(self, a, ctx):
        # 走到这里说明 _guard_payment 已放行（明确授权 + dry_run=false）
        sel = {k: v for k, v in a.items()
               if k in ("text", "desc", "resource_id") and v}
        if sel:
            ok, el = self.detector.find_element(sel, timeout=a.get("timeout", 5))
            if not ok:
                return ActionResult(False, f"支付按钮未找到: {sel}")
            el.click()
            return ActionResult(True, "已点击真实支付确认(明确授权)")
        ok = self.matcher.click(a.get("image", ""), self.d.click,
                                self.d.screenshot,
                                timeout=a.get("timeout", 10))
        return ActionResult(ok, "已点击真实支付确认(明确授权)" if ok
                            else "支付确认模板未找到")
