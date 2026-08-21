"""
automation/pokemon_go/shop.py
商店与购买流程 — 滚动寻商品 / 点击校验 / Google Play 购买(安全模式)

真实商品数据(录屏验证):
  100寶可幣 IDR 5,000 / 550寶可幣 IDR 25,000 / 1,200寶可幣 IDR 50,000
  2,500寶可幣 IDR 101,000 / 5,200寶可幣 IDR 200,000 / 14,500寶可幣 IDR 505,000

安全护栏(purchase.mode):
  manual (默认): 进入 Google Play 购买页 → 暂停, 等待人工完成 → 检测结果
  dry_run:       只读商品信息, 不点击购买
  sandbox:       仅测试环境 + 双重开关时自动执行
禁止绕过 Google Play 安全验证/支付确认。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from automation.pokemon_go.states import (PokemonGoState, PurchaseMode)

logger = logging.getLogger(__name__)


@dataclass
class ProductInfo:
    """已识别商品信息(点击前校验用)"""
    name: str = ""               # OCR 原始识别文本(如 100寶可幣)
    price: str = ""              # 显示价格(如 IDR 5,000)
    bbox: tuple = (0, 0, 0, 0)
    matched: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "price": self.price,
                "bbox": list(self.bbox), "matched": self.matched}


class ShopAutomation:
    """商店业务: 进店 → 滚动找目标商品 → 点击前校验 → 购买(模式化)"""

    def __init__(self, adapter):
        self.a = adapter                    # PokemonGoAdapter
        self.d = adapter.d                  # DeviceController
        self.detector = adapter.detector
        self.sel = adapter.sel
        self.shop_cfg = self.sel.shop
        self.purchase_cfg = self.sel.purchase
        self.log = adapter.log
        # 商城异常退出标记(§四): 滑动中检测到首页 UI 出现 → True,
        # find_product 提前返回 None, 调用方据此重进商城(≤2 次)
        self.kicked_out = False
        # 商城滑动状态保护锁(规格九): 滑动期间置 True, 禁止外部状态机
        # 介入返回主页/登录/下一账号/APP重启逻辑 — 只有商城到底或异常
        # 才能改变状态。find_product 进入时置 True, 退出(到底/异常/找到)置 False。
        self.scrolling = False

    # ── 进店 / 离店 ──

    def enter_shop(self, timeout: float = None, max_wrong_page_retries: int = 1
                   ) -> bool:
        """MAIN_MENU → 点击商店 → 等 SHOP。成功标准: SHOP 出现(§三)。

        主菜单按钮布局(真机实测): 文字标签在上, 图标在下 —
        点击位置 = 文字中心 + entry_click_offset。

        步级预算 shop_entry(默认 15s)。点击后只接受三种去向:
          SHOP      → 成功
          SETTINGS  → 防误入设置守卫(记录 → BACK 回主菜单 → 重试)
          MAIN_MENU → 点击未生效 → 重新点击
          超时(加载中/UNKNOWN) → 截图 + 日志 + 暖启动 + 重试(§五)

        真机教训(2026-08): 旧实现点击失败后在重试循环里调
        open_main_menu(), 其比例坐标点击 (0.5,0.94) 在商城页恰好命中
        底部 X 关闭按钮 → 误退商城(客户「自动返回主页面」根因)。
        现在: 已检测到 SHOP 直接返回(重进场景); 重试循环内绝不调
        open_main_menu, 只在地图(精灵球安全位)才允许开菜单。
        """
        target = self.shop_cfg.get("entry_texts") or ["商店"]
        offset = self.shop_cfg.get("entry_click_offset") or [0, 160]
        timeout = timeout or self.a._step_budget("shop_entry", 15)
        menu_timeout = self.a._step_budget("menu_open", 15)
        self.log.info("[SHOP] 点击商城")
        # 已在商城(异常退出后重进场景) → 无需再点
        if self.detector.detect() == PokemonGoState.SHOP:
            self.log.info("[SHOP] 商城页面确认(已在商城, 直接滑动)")
            return True
        for attempt in range(max_wrong_page_retries + 1):
            state = self.detector.detect()
            if state == PokemonGoState.SHOP:
                return True
            if state != PokemonGoState.MAIN_MENU:
                if state == PokemonGoState.MAP:
                    # 地图 → 开主菜单(精灵球点击是安全位, 与商城 X 无关)
                    if not self.a.logout_auto.open_main_menu(
                            timeout=menu_timeout):
                        self.log.warning("[商店] 无法打开主菜单")
                        return False
                else:
                    # 转场/未知页: 先等稳定(绝不点比例坐标 — 可能正
                    # 处于检测未识别的商城页, 比例坐标会点中 X 关闭)
                    state = self.detector.wait_for_state(
                        [PokemonGoState.MAIN_MENU, PokemonGoState.SHOP,
                         PokemonGoState.MAP], timeout=min(menu_timeout,
                                                          timeout))
                    if state == PokemonGoState.SHOP:
                        return True
                    if state == PokemonGoState.MAP:
                        if not self.a.logout_auto.open_main_menu(
                                timeout=menu_timeout):
                            return False
                    if state != PokemonGoState.MAIN_MENU:
                        self.log.warning(f"[商店] 未回到主菜单"
                                         f"(当前={state.value}) — 进店失败")
                        return False
            if attempt > 0:
                self.log.info(f"[商店] 错误页面恢复后重试进店 "
                              f"第 {attempt}/{max_wrong_page_retries} 次")
            clicked = self.a.click_ocr_text(target, timeout=10,
                                            click_offset=tuple(offset))
            if not clicked:
                # 兜底: 主菜单按钮模板
                clicked = self.a.click_template(
                    self.shop_cfg.get("entry_template"), timeout=5)
            if not clicked:
                self.log.warning("[商店] 未找到商店入口")
                return False
            self.detector.bust_caches()   # 事件驱动: 点击后强制全新检测
            # 点击后等待页面变化, 识别实际去向(不假设点击=进店成功)
            state = self.detector.wait_for_state(
                [PokemonGoState.SHOP, PokemonGoState.SETTINGS,
                 PokemonGoState.MAIN_MENU], timeout=timeout)
            if state == PokemonGoState.SHOP:
                self.log.info("[SHOP] 商城页面确认 — 立即开始滑动")
                self.a._mark_trace("SHOP_ENTERED")
                return True
            if state == PokemonGoState.SETTINGS:
                # 点偏进了设置 — 记录证据, BACK 回菜单重试, 绝不继续
                self.log.warning("[商店] WRONG_PAGE_DETECTED: 点击商店后"
                                 "进入设置页 — BACK 回主菜单重试")
                self.a.capture_keyframe("WRONG_PAGE_SETTINGS")
                self.d.press("back")
                self.detector.bust_caches()
                self.detector.wait_for_state([PokemonGoState.MAIN_MENU],
                                             timeout=menu_timeout)
                continue
            if state == PokemonGoState.MAIN_MENU:
                # 点击未生效(可能加载慢/点击被吞) — 重新点击,
                # 绝不调 open_main_menu(其比例坐标会点中商城 X 关闭)
                self.log.info("[商店] 点击商店后仍在主菜单 — 重新点击")
                continue
            # 超预算(§五): 截图 + 日志 + 重启APP(暖启动保会话) + 重执行
            self.a.capture_keyframe("SHOP_ENTRY_TIMEOUT")
            self.log.warning(f"[商店] 进入商城超时({timeout}s 预算), "
                             f"当前={state.value} — 截图留档, "
                             f"暖启动后重试({attempt + 1}/"
                             f"{max_wrong_page_retries + 1})")
            if attempt < max_wrong_page_retries:
                try:
                    self.d.app_start(self.a.package, self.a.activity)
                except Exception:
                    pass
                time.sleep(3)
                self.detector.bust_caches()
                continue
        self.log.error("[商店] 进店失败(多次误入设置或超时)")
        return False

    def close_shop(self, timeout: float = 20) -> bool:
        """点击商店关闭按钮(X) → 回主菜单/地图。成功标准: 状态验证

        真机实测: 青绿色圆形 X 按钮固定在底部中央(模板 pgo_shop_close,
        中心比例 0.5, 0.92), OCR 无法识别 × 符号。
        """
        clicked = self.a.click_template(
            self.shop_cfg.get("close_template", "pgo_shop_close"),
            timeout=2)   # 真机模板几乎总失败(渲染延迟), 快速落比例坐标
        if not clicked:
            self.d.click_ratio(0.5, 0.92)
            self.log.info("[商店] 用比例坐标点击关闭按钮")
        state = self.detector.wait_for_state(
            [PokemonGoState.MAIN_MENU, PokemonGoState.MAP],
            timeout=timeout)
        if state in (PokemonGoState.MAIN_MENU, PokemonGoState.MAP):
            return True
        # BACK 兜底 — 同样等待状态稳定(转场截图会短暂 UNKNOWN)
        self.d.press("back")
        state = self.detector.wait_for_state(
            [PokemonGoState.MAIN_MENU, PokemonGoState.MAP],
            timeout=timeout)
        return state in (PokemonGoState.MAIN_MENU, PokemonGoState.MAP)

    # ── 滚动寻找商品 ──

    def _shop_still_open(self) -> bool:
        """商城流程中页面仍在商店内(§四)。防误判退出(规格§七 2026-08-21)。

        检测到首页 UI/主菜单/设置/登出确认 = 疑似商城异常退出。
        但 OCR/模板检测可能瞬时误判(滑动动画帧) — 单次 MAP 绝不直接判退出。
        规格§七: 连续两次确认 MAP 且商城特征消失, 才认为真退出。

        UNKNOWN 视为转场/加载容忍(商店 OCR 未识别的中间态)。
        """
        exit_states = (PokemonGoState.MAIN_MENU, PokemonGoState.MAP,
                       PokemonGoState.SETTINGS, PokemonGoState.LOGOUT_CONFIRM)
        state = self.detector.detect()
        if state not in exit_states:
            return True
        # 疑似退出 — 二次确认(规格§七): 短等 + bust_caches 强制最新画面重检。
        # 滑动动画帧可能瞬时误判 MAP, 确认仍 MAP 且非 SHOP 才算真退出。
        self.log.warning(f"[SHOP] 疑似商城退出(检测到 {state.value}) "
                         f"— 二次确认中")
        time.sleep(0.6)   # 等滑动动画停下(规格: 不 sleep 几十秒, 仅动画缓冲)
        self.detector.bust_caches()
        confirm = self.detector.detect()
        if confirm in exit_states and confirm != PokemonGoState.SHOP:
            self.log.warning(f"[商店] 商城异常退出已确认(两次检测均为 "
                             f"{confirm.value}) — 触发恢复")
            return False
        # 二次确认回 SHOP/UNKNOWN → 上次是瞬时误判, 继续滑动
        self.log.info(f"[SHOP] 二次确认回 {confirm.value} — 瞬时误判, "
                      f"继续滑动(不退出商城)")
        return True

    # ── 滑动循环辅助(规格 2026-08-21 定数滑动) ──

    def _scroll_pass(self, count: int, swipe_x: int, y1: int, y2: int,
                     target_amount: str, t0: float, budget: float
                     ) -> Optional[ProductInfo]:
        """连续滑动 count 次(规格§五/§六/§七)。

        规格§五核心: 第一阶段必须完整滑动 6 次, 期间禁止识别商品
        (禁止"第2次滑动后识别"/提前进入补滑)。本方法纯滑动不识别 —
        商品识别由调用方在滑动完成后统一做。
        - 单次滑动 duration 0.7s, 间隔 0.4s(规格§七: 600-800ms / 300-500ms);
        - 静帧判底(连续 2 帧无变化)提前停 — 到底优化, 非回滚;
        - 滑动中异常退出守卫(kicked_out, 每 2 轮) — 保留但不识别商品;
        - 超预算停。
        返回 None(滑动完成/到底/超预算/异常退出 — 不返回商品)。
        """
        last_still = None
        stale_count = 0
        for i in range(count):
            self.a.tick_heartbeat()   # 长循环内刷新心跳, 防调度器误判卡死
            # 异常退出守卫(每 2 轮): 商城被踢出立即停止滑动。
            # 不识别商品(规格§五: 滑动期间禁止识别, 滑完才统一识别)。
            if i >= 2 and i % 2 == 0:
                if not self._shop_still_open():
                    self.kicked_out = True
                    self.log.error("[ERROR] 商城滑动过程中退出 "
                                   f"(当前状态={self.detector.detect().value})")
                    self.a.capture_keyframe("SHOP_KICKED_OUT_DURING_SCROLL")
                    return None
            if time.time() - t0 > budget:
                self.log.warning(f"[SHOP] 滑动超预算({budget}s) — 停止滑动")
                self.a.capture_keyframe("SHOP_SCROLL_BUDGET_EXCEEDED")
                break
            self.log.info(f"[SHOP] 开始快速滑动 {i + 1}/{count}")
            self._do_swipe(swipe_x, y1, y2, duration=0.7)
            time.sleep(0.4)   # 让滚动停下判底, 不长等待(规格§七)
            still = self._downsample_gray()
            if last_still is not None and not self._frame_changed(last_still, still):
                stale_count += 1
            else:
                stale_count = 0
            last_still = still
            if stale_count >= 2:
                self.log.info(f"[SHOP] 检测到底 (连续两次静帧无变化, "
                              f"第 {i + 1} 次) — 停止滑动")
                self.a._mark_trace("SHOP_BOTTOM_REACHED")
                break
        return None

    def _downsample_gray(self):
        """截图并降采样为灰度小图(36×80), 用于前后帧比对判变化。
        截图统一 BGR(见 device_manager), 用 BGR2GRAY 与 detector 一致。
        失败返回 None。"""
        try:
            shot = self.d.screenshot()
            gray = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
            return cv2.resize(gray, (36, 80))
        except Exception:
            return None

    @staticmethod
    def _frame_changed(a, b) -> bool:
        """两帧降采样灰度图是否发生变化(平均差容差 < 4.0 视为未变)。
        None 任一 → 视为变化(保守, 不误判到底)。"""
        if a is None or b is None:
            return True
        diff = float(np.abs(a.astype(np.int16)
                            - b.astype(np.int16)).mean())
        return diff >= 4.0

    def _do_swipe(self, x: int, y1: int, y2: int, duration: float = 0.7):
        """执行一次精确坐标上滑(规格§七: duration 600-800ms)。
        异常吞掉(滑动失败由后续静帧比对兜底)。"""
        try:
            self.d.swipe(x, y1, x, y2, duration=duration)
        except Exception as e:
            self.log.debug(f"[SHOP] swipe 异常: {e}")

    def find_product(self, max_scroll: int = 12) -> Optional[ProductInfo]:
        """定数滑动找商品(规格 2026-08-21 §四~§八重写)。

        真机实测(用户确认): 100 寶可幣在商店列表最底部 —
        策略(两阶段定数滑动, 不回滚):
          1) 第一阶段连续滑动 first_pass(默认 6) 次 → 识别商品;
          2) 未识别 → 第二阶段补滑 second_pass(默认 3) 次 → 再识别。
        删除旧"滚过头回滚/反向查找"逻辑 — 目标是滑到底再识别,
        不是精确定位(规格§四: 禁止 rollback / reverse swipe)。
        判底(连续 2 帧静帧无变化)是到底提前停优化, 不是回滚。
        滑动参数: duration 0.7s + 间隔 0.4s(规格§七)。

        异常退出守卫: 滑动中检测到商城外页面立即停止(kicked_out=True),
        调用方据此重进商城(≤2 次)。滑动期间状态锁 scrolling(规格§八:
        禁止滚过头判断/自动回滚/返回主页/重置流程)。
        """
        self.kicked_out = False
        self.scrolling = True   # 商城滑动状态保护锁(规格九): 滑动期间禁止外部状态机介入
        target_amount = str(self.shop_cfg.get("target", {}).get(
            "amount", "100"))
        scroll_budget = self.a._step_budget("shop_scroll", 10)
        find_budget = self.a._step_budget("shop_find", 40)
        # 定数滑动次数(规格 2026-08-21 §五/§六): 第一阶段 6 次, 第二阶段补 3 次。
        # 不再"判滚过头回滚" — 目标是滑到底再识别, 不是精确定位。
        first_pass = int(self.shop_cfg.get("scroll_first_pass", 6))
        second_pass = int(self.shop_cfg.get("scroll_second_pass", 3))
        self.log.info(f"[SHOP] 开始快速滑动 {first_pass} 次到底部")
        t0 = time.time()
        # 滑动参数(规格 §七): duration 600-800ms, 间隔 300-500ms, 不长等待。
        #   start_y=1800→end_y=400(基准 2400 高, ratio 0.75→0.167)。
        sw = max(1, getattr(self.d, "screen_w", 1080))
        sh = max(1, getattr(self.d, "screen_h", 2400))
        swipe_x = sw // 2
        swipe_y1 = int(sh * 0.75)   # 1800(基准)
        swipe_y2 = int(sh * 0.167)  # 400(基准)
        try:
            # ── 第一阶段: 连续滑动 first_pass 次 + 静帧判底提前停 ──
            # (规格§五: 直接滑 6 次; 判底连续2帧无变化是优化提前停, 非回滚)
            info = self._scroll_pass(first_pass, swipe_x, swipe_y1, swipe_y2,
                                     target_amount, t0, scroll_budget)
            if info is not None:
                return info
            if self.kicked_out:
                return None
            # 第一阶段后识别商品(规格§五: 6 次后识别 100 宝可梦)
            self.log.info(f"[SHOP] 第一次识别 {target_amount} 宝可梦")
            info = self._detect_product(target_amount)
            if info and info.matched:
                self.log.info(f"[SHOP] 发现目标商品: {info.name} "
                              f"({info.price}) — 开始购买")
                self.a._mark_trace("PACKAGE_FOUND")
                return info

            # ── 第二阶段: 补滑 second_pass 次再识别(规格§六) ──
            self.log.info(f"[SHOP] 第一次未识别, 补滑 {second_pass} 次再识别")
            info = self._scroll_pass(second_pass, swipe_x, swipe_y1, swipe_y2,
                                     target_amount, t0, find_budget)
            if info is not None:
                return info
            if self.kicked_out:
                return None
            info = self._detect_product(target_amount)
            if info and info.matched:
                self.log.info(f"[SHOP] 补滑后发现目标商品: {info.name} "
                              f"({info.price}) — 开始购买")
                self.a._mark_trace("PACKAGE_FOUND")
                return info
            self.log.warning(f"[SHOP] 滑动 {first_pass + second_pass} 次仍未找到"
                             f"商品 → PRODUCT_NOT_FOUND")
            return None
        finally:
            # 滑动结束(到底/异常/找到/超预算)统一释放状态锁(规格九)
            self.scrolling = False

    @staticmethod
    def _price_re(text: str) -> bool:
        """文本是否像价格(US$/IDR/Rp/￥/$ + 数字)"""
        import re
        return bool(re.search(
            r"(US\$|USD|IDR|Rp|NT\$|HK\$|¥|￥)\s?[\d,.]+|"
            r"^\$?[\d,]+\.\d{2}$", text.strip()))

    def _detect_product(self, amount: str) -> Optional[ProductInfo]:
        """当前屏幕 OCR 识别目标商品。

        真机实测: OCR 对「100寶可幣」常识别为「100寶可」(幣字丢失) —
        所以商品判定 = 数量精确匹配 + 邻近行价格证据(货币无关)。
        邻近价格取「距离最近」的一条(同一商品卡片, 避免相邻商品误配)。
        """
        boxes = self.detector.ocr_boxes()
        for text, bbox in boxes:
            if not self._amount_equals(text, amount):
                continue
            x1, y1, x2, y2 = bbox
            cy = (y1 + y2) / 2
            # 同列价格(真机布局: 商品横排三列, 价格在名称正下方)。
            # 过滤条件: x 轴重叠 + 垂直距离 < 200px, 取垂直最近
            candidates = []
            for t, b in boxes:
                if not self._price_re(t):
                    continue
                bx1, by1, bx2, by2 = b
                x_overlap = min(x2, bx2) - max(x1, bx1) > 0
                dy = abs(((by1 + by2) / 2) - cy)
                if x_overlap and dy < 200:
                    candidates.append((t, b, dy))
            if not candidates:
                continue
            best = min(candidates, key=lambda c: c[2])
            self.log.debug(f"[商店] 商品 {amount} 文本={text!r} bbox={bbox} "
                           f"价格={best[0]!r}(同列, dy={best[2]:.0f})")
            return ProductInfo(name=text, price=best[0], bbox=bbox,
                               matched=True)
        return None

    @staticmethod
    def _amount_equals(text: str, amount: str) -> bool:
        """'100' 精确匹配文本中的数量(排除 1,200 等)。

        真机实测 OCR 会把 0 识别成 o: 「100 PokeCoins」→「1ooPokeCoins」。
        归一化 o/O→0 后再匹配。
        """
        t = text.replace(",", "").replace(" ", "")
        t = t.replace("o", "0").replace("O", "0")
        a = amount.replace("o", "0").replace("O", "0")
        if t == a:
            return True
        # "100寶可幣" 形式(后一位不是数字才算, 排除 1000/1,200)
        if t.startswith(a) and not t[len(a):len(a) + 1].isdigit():
            return True
        return False

    @staticmethod
    def _extract_price(texts: list) -> str:
        for t in texts:
            if "IDR" in t or "Rp" in t or "￥" in t or "$" in t:
                return t
        return ""

    # ── 点击与校验 ──

    def click_product(self, info: ProductInfo) -> bool:
        """点击目标商品。成功标准: 出现 Google Play 购买页(§五 步级预算)"""
        x = (info.bbox[0] + info.bbox[2]) // 2
        y = (info.bbox[1] + info.bbox[3]) // 2
        self.d.click(x, y)
        self.detector.bust_caches()   # 事件驱动: 点击后强制全新检测
        state = self.detector.wait_for_state(
            [PokemonGoState.PURCHASE_PAGE],
            timeout=self.a._step_budget("purchase_page", 20))
        if state != PokemonGoState.PURCHASE_PAGE:
            self.log.warning(f"[商店] 点击商品后未出现 Google Play 页"
                             f"(当前={state.value}, 预算已耗) — 截图留档")
            self.a.capture_keyframe("PURCHASE_PAGE_TIMEOUT")
        return state == PokemonGoState.PURCHASE_PAGE

    def verify_product_on_purchase_page(self, info: ProductInfo,
                                        retries: int = 4,
                                        interval: float = 2.5) -> bool:
        """Google Play 页校验商品名/价格一致性(不匹配 → PRODUCT_MISMATCH)

        真机实测:
          - OCR 将「100 PokéCoins」识别为「100PokeCoins」(无空格、
            é 丢失、大小写变化) — 匹配必须大小写不敏感
          - Google Play 页打开后内容渲染有延迟 — 轮询重试直到
            内容就绪或次数用尽
        """
        target_amount = str(self.shop_cfg.get("target", {}).get("amount", "100"))
        currencies = (self.shop_cfg.get("target", {}).get("currencies")
                      or ["寶可幣", "PokéCoins", "Pokecoins"])
        for attempt in range(retries):
            self.a.tick_heartbeat()
            boxes = self.detector.ocr_boxes()
            joined = " ".join(t for t, _ in boxes).lower()
            currency_ok = any(c.lower() in joined for c in currencies)
            amount_ok = self._amount_equals(joined, target_amount) or \
                any(self._amount_equals(t, target_amount) for t, _ in boxes)
            price_ok = not info.price or \
                any(info.price.split()[-1].replace(",", "").lower() in
                    t.replace(",", "").lower() for t, _ in boxes)
            ok = amount_ok and currency_ok
            self.log.info(f"[购买校验#{attempt+1}] 商品={target_amount} "
                          f"数量识别={amount_ok} 货币识别={currency_ok} "
                          f"价格={info.price} 页面命中={price_ok}")
            if ok:
                return True
            if attempt < retries - 1:
                time.sleep(interval)
        self.log.error(f"[PRODUCT_MISMATCH] Google Play 页商品与目标不符"
                       f"(重试 {retries} 次)")
        self.a.capture_keyframe("PRODUCT_MISMATCH")
        return False

    # ── 购买执行(安全模式) ──

    def handle_purchase(self, account) -> str:
        """按 purchase.mode 处理购买。返回结果标记。"""
        mode = PurchaseMode(str(self.purchase_cfg.get("mode", "manual")))
        self.log.info(f"[购买] mode={mode.value}")

        if mode == PurchaseMode.DRY_RUN:
            self.log.info("[购买] dry_run: 只读商品信息, 不进入支付")
            self.d.press("back")   # 关闭 Google Play 页
            return "DRY_RUN"

        if mode == PurchaseMode.SANDBOX:
            if not self.a.cfg.payment_allowed:
                self.log.warning("[购买] sandbox 需要明确授权"
                                 "(CONTROL_CENTER_ALLOW_PAYMENT=1)")
                return "BLOCKED"
            return self._auto_purchase()

        # manual: 暂停等待人工完成(默认)
        return self._manual_purchase(account)

    def _manual_purchase(self, account) -> str:
        """到支付页暂停。人工在手机上完成购买后, 脚本自动检测结果。"""
        self.a.capture_keyframe("PURCHASE_PAGE")
        self.log.warning(f"[购买] 已到 Google Play 支付页 — 等待人工完成购买"
                         f"(账号 {account.masked() if account else '?'})")
        timeout = float(self.purchase_cfg.get("manual_timeout", 300))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.a.tick_heartbeat()
            state = self.detector.detect()
            if state == PokemonGoState.PURCHASE_SUCCESS:
                return "SUCCESS"
            if state == PokemonGoState.PURCHASE_FAILED:
                return "FAILED"
            if state == PokemonGoState.SHOP and \
                    not self.detector.is_external_context():
                # 回到商店 = 支付页关闭(可能已购买或人工取消)
                return "BACK_TO_SHOP"
            time.sleep(3)
        self.log.warning("[购买] 人工确认超时")
        return "MANUAL_TIMEOUT"

    def _auto_purchase(self) -> str:
        """自动购买(sandbox/auto 模式, 需 CONTROL_CENTER_ALLOW_PAYMENT 双重授权)。

        真机实测的两种购买操作形态:
          1. 「一键购买/购买」按钮 → 点击后自动回到游戏商城
          2. 「滑動即可購買」→ 向右滑动后自动回到游戏商城
        购买完成信号: 自动回到游戏商城(SHOP) — 用户确认的业务行为特征。
        """
        buy_texts = self.purchase_cfg.get("auto_buy_texts") or \
            ["一键购买", "购买", "Buy"]
        slide_texts = self.purchase_cfg.get("auto_slide_texts") or \
            ["滑動即可購買", "滑动即可购买"]
        action = ""

        # ── 自动识别购买操作形态 ──
        # 1) 滑动购买条(「滑動即可購買」本身是滑动形态特征)
        box = self.detector.find_text_box(slide_texts)
        if box is not None:
            y = (box[1] + box[3]) // 2
            x1, x2 = box[0] + 20, box[2] - 20
            if x2 - x1 < 50:
                x1 = int(self.d.screen_w * 0.25)
                x2 = int(self.d.screen_w * 0.75)
            self.d.swipe(x1, y, x2, y, duration=0.8)
            action = f"向右滑动购买@({x1},{y})→({x2},{y})"
        else:
            # 2) 点击购买按钮(排除长句如「购买须遵守许可条款」:
            #    按钮文本是短文本, 长度 ≤ 8)
            box = None
            for text, bbox in self.detector.ocr_boxes():
                if any(b in text for b in buy_texts) and len(text) <= 8:
                    box = bbox
                    self.log.debug(f"[购买] 按钮候选: {text!r} {bbox}")
                    break
            if box is None:
                self.log.error("[购买] 未识别到购买按钮或滑动条形态")
                self.a.capture_keyframe("NO_PURCHASE_BUTTON")
                return "NO_PURCHASE_BUTTON"
            x = (box[0] + box[2]) // 2
            y = (box[1] + box[3]) // 2
            self.d.click(x, y)
            action = f"点击购买按钮@({x},{y})"

        self.log.warning(f"[购买] 已执行自动购买操作: {action}")
        return self._wait_purchase_result(action)

    def _wait_purchase_result(self, action: str,
                              timeout: float = None) -> str:
        """自动购买后等待结果。

        购买完成信号(真机业务特征): 自动回到游戏商城(SHOP)。
        Google Play 处理中/停留支付页 → 继续等待, 超时报 PURCHASE_TIMEOUT。
        """
        timeout = timeout or float(self.purchase_cfg.get("result_timeout", 120))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.a.tick_heartbeat()
            state = self.detector.detect()
            if state == PokemonGoState.PURCHASE_SUCCESS:
                self.log.info("[购买] 检测到购买成功提示")
                return "SUCCESS"
            if state == PokemonGoState.PURCHASE_FAILED:
                self.log.warning("[购买] 检测到购买失败提示")
                return "FAILED"
            # 回到游戏商城 = 购买完成(用户确认: 一键购买后自动回商城)
            if state == PokemonGoState.SHOP and \
                    self.detector.is_game_foreground():
                self.log.info("[购买] 已自动回到游戏商城 — 购买完成")
                return "SUCCESS"
            time.sleep(3)
        self.log.warning(f"[购买] 结果等待超时({timeout}s), 操作={action}")
        return "PURCHASE_TIMEOUT"
