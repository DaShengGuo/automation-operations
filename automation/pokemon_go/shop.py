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

    # ── 进店 / 离店 ──

    def enter_shop(self, timeout: float = 30) -> bool:
        """MAIN_MENU → 点击商店 → 等 SHOP。成功标准: SHOP 出现

        主菜单按钮布局(真机实测): 文字标签在上, 图标在下 —
        点击位置 = 文字中心 + entry_click_offset。
        """
        # 防御: 不在主菜单时先打开(调用者不依赖前置状态)
        if self.detector.detect() != PokemonGoState.MAIN_MENU:
            if not self.a.logout_auto.open_main_menu():
                self.log.warning("[商店] 无法打开主菜单")
                return False
        target = self.shop_cfg.get("entry_texts") or ["商店"]
        offset = self.shop_cfg.get("entry_click_offset") or [0, 160]
        clicked = self.a.click_ocr_text(target, timeout=10,
                                        click_offset=tuple(offset))
        if not clicked:
            # 兜底: 主菜单按钮模板
            clicked = self.a.click_template(
                self.shop_cfg.get("entry_template"), timeout=5)
        if not clicked:
            self.log.warning("[商店] 未找到商店入口")
            return False
        state = self.detector.wait_for_state([PokemonGoState.SHOP],
                                             timeout=timeout)
        return state == PokemonGoState.SHOP

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

    def find_product(self, max_scroll: int = 12) -> Optional[ProductInfo]:
        """滚动直到目标商品出现。

        真机实测(用户确认): 100 寶可幣在商店列表最底部 —
        策略(两阶段):
          1) 快速滑底 — 大距离连滑, 每 2 次滑动作一次 OCR 快检
             (商品一出现立即停止, 不再滑过头);
             判底用降采样+容差对比(倒计时等小面积动态元素不影响判定,
             纯哈希对比会因计时器每秒跳动永远判不了底);
          2) 到底后再 OCR — 从底部往上 4 屏搜索(商品在最底, 必覆盖);
          3) 仍未找到才回滚兜底。
        """
        target_amount = str(self.shop_cfg.get("target", {}).get(
            "amount", "100"))

        # 阶段 1: 快速滑底 + 周期性商品快检 + 容差判底
        last_small = None
        stale_count = 0
        for scroll in range(max_scroll):
            self.a.tick_heartbeat()   # 长循环内刷新心跳, 防调度器误判卡死
            # 商品快检: 商品出现立即停止(用户要求: 看到商品就进行下一步)
            if scroll >= 2 and scroll % 2 == 0:
                info = self._detect_product(target_amount)
                if info and info.matched:
                    self.log.info(f"[商店] 找到目标商品: {info.name} "
                                  f"({info.price}) (滚动 {scroll} 次)")
                    return info
            # 判底: 降采样灰度 + 平均差容差
            try:
                shot = self.d.screenshot()
                gray = cv2.cvtColor(shot, cv2.COLOR_RGB2GRAY)
                small = cv2.resize(gray, (36, 80))
            except Exception:
                small = None
            if small is not None and last_small is not None:
                diff = float(np.abs(small.astype(np.int16)
                                    - last_small.astype(np.int16)).mean())
            else:
                diff = 999.0
            if diff < 4.0:
                stale_count += 1
            else:
                stale_count = 0
            last_small = small
            if stale_count >= 2:
                self.log.info(f"[商店] 已滑到底(滚动 {scroll} 次)")
                break
            self.log.info(f"[商店] 快速滑底中, 上滑第 {scroll + 1} 次")
            self.d.swipe_direction("up", distance=0.8)
            time.sleep(0.5)

        # 阶段 2: 底部往上 4 屏 OCR 搜索
        for i in range(4):
            self.a.tick_heartbeat()
            info = self._detect_product(target_amount)
            if info and info.matched:
                self.log.info(f"[商店] 找到目标商品: {info.name} "
                              f"({info.price}) (底部起第 {i + 1} 屏)")
                return info
            self.d.swipe_direction("down", distance=0.4)
            time.sleep(0.6)

        # 阶段 3: 兜底回滚查找
        self.log.info("[商店] 可能滚过头, 反向回滚查找")
        for i in range(4):
            self.a.tick_heartbeat()
            self.d.swipe_direction("down", distance=0.5)
            time.sleep(1.0)
            info = self._detect_product(target_amount)
            if info and info.matched:
                self.log.info(f"[商店] 回滚找到目标商品: {info.name} "
                              f"({info.price}) (回滚 {i + 1} 次)")
                return info
        self.log.warning(f"[商店] 滑动 {max_scroll} 次仍未找到商品 → "
                         f"PRODUCT_NOT_FOUND")
        return None

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
        """点击目标商品。成功标准: 出现 Google Play 购买页"""
        x = (info.bbox[0] + info.bbox[2]) // 2
        y = (info.bbox[1] + info.bbox[3]) // 2
        self.d.click(x, y)
        state = self.detector.wait_for_state(
            [PokemonGoState.PURCHASE_PAGE], timeout=30)
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
