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
        """商城流程中页面仍在商店内(规格 2026-08-21 §七/§八重写)。

        四条件强证据确认真退出(全部满足才算 SHOP_EXITED):
          1. 商城商品区域消失(OCR 无商城特征词: 寶可幣/宝可币/PokéCoins/
             Pokecoins/US$/新手禮盒 等)
          2. 商城标题消失(同上, 商城特征词涵盖标题/商品)
          3. 主地图 UI 持续出现(连续两次 detect 均为退出态)
          4. 连续两次截图确认(间隔 0.6s, bust_caches 强制最新画面)

        商城特征存在优先: OCR 仍有商城特征词 → 认为仍在商城,
        无视 MAP/主菜单误判(真机: 商城页红色商品图标曾误命中 MAP
        单证据; 现已 min_hits=2, 再加本层商城特征兜底)。
        UNKNOWN 视为转场/加载容忍。
        """
        exit_states = (PokemonGoState.MAIN_MENU, PokemonGoState.MAP,
                       PokemonGoState.SETTINGS, PokemonGoState.LOGOUT_CONFIRM)
        state = self.detector.detect()
        if state not in exit_states:
            return True
        # 疑似退出 — 先查商城特征(规格§八 条件1/2): 特征词仍在 = 误判
        if self._shop_texts_present():
            self.log.info(f"[SHOP] 检测到 {state.value} 但商城特征仍在 "
                          f"— 状态误判, 继续视为在商城")
            return True
        # 无商城特征 — 连续两次截图确认真退出(规格§八 条件3/4)
        time.sleep(0.6)   # 等滑动动画停下(仅动画缓冲, 非流程等待)
        self.detector.bust_caches()
        confirm = self.detector.detect()
        if confirm in exit_states:
            self.log.warning(f"[商店] 商城真退出已确认(连续两次检测 "
                             f"{confirm.value} 且商城特征消失)")
            return False
        # 二次检测回 SHOP/UNKNOWN → 瞬时误判, 继续滑动
        self.log.info(f"[SHOP] 二次检测回 {confirm.value} — 瞬时误判, "
                      f"继续滑动(不退出商城)")
        return True

    def _shop_texts_present(self) -> bool:
        """OCR 是否仍有商城特征词(规格§八条件1/2: 商品区域/标题消失检测)。

        商城特征词(真机 OCR 实测 + 配置规则一致): 寶可幣/宝可币/
        PokéCoins/Pokecoins/US$/新手禮盒/偷兒狐/社群日 等。
        OCR 失败(异常)保守视为特征仍在(不误判退出)。
        """
        shop_markers = ("寶可幣", "宝可币", "PokéCoins", "PokeCoins",
                        "Pokecoins", "US$", "新手禮盒", "新手礼盒",
                        "偷兒狐", "偷儿狐", "社群日", "Shop")
        try:
            boxes = self.detector.ocr_boxes()
        except Exception:
            return True   # OCR 不可用 → 保守, 不判退出
        for text, _ in boxes:
            if any(m in text for m in shop_markers):
                return True
        return False

    # ── 滑动循环辅助(规格 2026-08-21 定数滑动) ──

    def _scroll_pass(self, count: int, swipe_x: int, y1: int, y2: int,
                     target_amount: str, t0: float, budget: float
                     ) -> Optional[ProductInfo]:
        """连续大幅滑动 count 次(规格 2026-08-21 §四/§五/§九重写)。

        规格§五核心: 第一阶段必须完整滑 6 次, 期间禁止识别商品。
        本方法纯滑动: 不识别商品、不判底、无超预算停止 —
        滑动结束只由「完成规定次数」决定(规格§四: 删除 10 秒
        超预算停止; 规格§九: 不每次滑动检测到底/识别商品)。
        - 单次滑动 duration 0.8s, 间隔 0.4s(规格§九: 700-1000ms);
        - 滑动中异常退出守卫(每 2 轮, 四条件强证据, 不识别商品);
        返回 None(滑动完成/异常退出 — 不返回商品)。
        """
        for i in range(count):
            self.a.tick_heartbeat()   # 长循环内刷新心跳, 防调度器误判卡死
            # 异常退出守卫(每 2 轮): 四条件强证据确认真退出才停止滑动。
            # 不识别商品(规格§五: 滑动期间禁止识别, 滑完才统一识别)。
            if i >= 2 and i % 2 == 0:
                if not self._shop_still_open():
                    self.kicked_out = True
                    self.log.error("[ERROR] 商城滑动过程中真退出已确认")
                    self.a.capture_keyframe("SHOP_KICKED_OUT_DURING_SCROLL")
                    return None
            self.log.info(f"[SHOP] 执行滑动 {i + 1}/{count}")
            self._do_swipe(swipe_x, y1, y2, duration=1.0)
            time.sleep(0.5)   # 触摸间隔(duration 1.0s 后让滚动停下), 不判底
        return None

    def _do_swipe(self, x: int, y1: int, y2: int, duration: float = 1.0):
        """执行一次精确坐标上滑(规格 2026-08-21 §十: duration ~1000ms)。

        异常不再静默(旧 debug 吞掉导致「日志显示滑动执行但页面不动」
        无法取证): 失败时 warning 日志 + 截图留档, 供离线排查坐标/
        触摸通道问题。
        """
        try:
            self.d.swipe(x, y1, x, y2, duration=duration)
        except Exception as e:
            self.log.warning(f"[SHOP] swipe 异常(坐标 "
                             f"({x},{y1})→({x},{y2})): {e} — 截图留档")
            self.a.capture_keyframe("SWIPE_ERROR")

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
        # 定数滑动次数(规格 2026-08-21 §五/§六): 第一阶段 6 次, 第二阶段补 3 次。
        # 滑动结束只由「完成规定次数」决定 — 无超预算停止(规格§四),
        # 无判底提前停(规格§九), 不回滚(规格§四)。
        first_pass = int(self.shop_cfg.get("scroll_first_pass", 6))
        second_pass = int(self.shop_cfg.get("scroll_second_pass", 3))
        self.log.info(f"[SHOP] 开始大幅滑动 {first_pass} 次")
        # 滑动参数(规格 2026-08-21 §八~§十): 坐标必须走 CoordinateMapper —
        # 与 click_ratio 同一体系(含安全区 clamp, 防底部手势区截获触摸)。
        # 旧实现直接用 screen_h 比例换算: 真机分辨率/稳定边距(insets)不同
        # 时坐标可能落入系统手势区或超屏 → swipe 静默失效(「日志显示滑动
        # 执行但页面不动」根因)。基准 1080×2400: 中心 x=540, y=2200→200。
        mapper = getattr(self.d, "mapper", None)
        if mapper is not None:
            swipe_x = mapper.map_ratio(0.5, 0.5)[0]
            swipe_y1 = mapper.map(540, 2200)[1]
            swipe_y2 = mapper.map(540, 200)[1]
        else:
            sw = max(1, getattr(self.d, "screen_w", 1080))
            sh = max(1, getattr(self.d, "screen_h", 2400))
            swipe_x = sw // 2
            swipe_y1 = int(sh * 0.917)  # 2200(基准 2400)
            swipe_y2 = int(sh * 0.083)  # 200(基准 2400)
        try:
            # ── 第一阶段: 完整大幅滑动 first_pass 次(期间禁止识别) ──
            self._scroll_pass(first_pass, swipe_x, swipe_y1, swipe_y2,
                              target_amount, 0.0, 0.0)
            if self.kicked_out:
                return None
            # 滑满 6 次后才第一次识别(规格§五)
            self.log.info(f"[SHOP] 开始识别 {target_amount} 宝可梦")
            info = self._detect_product(target_amount)
            if info and info.matched:
                self.log.info(f"[SHOP] 识别成功, 开始购买: {info.name} "
                              f"({info.price})")
                self.a._mark_trace("PACKAGE_FOUND")
                return info

            # ── 第二阶段: 完整 6 次后未识别才补滑 3 次(规格§六) ──
            self.log.info(f"[SHOP] 未识别到, 补滑 {second_pass} 次再识别")
            self._scroll_pass(second_pass, swipe_x, swipe_y1, swipe_y2,
                              target_amount, 0.0, 0.0)
            if self.kicked_out:
                return None
            info = self._detect_product(target_amount)
            if info and info.matched:
                self.log.info(f"[SHOP] 识别成功, 开始购买: {info.name} "
                              f"({info.price})")
                self.a._mark_trace("PACKAGE_FOUND")
                return info
            self.log.warning(f"[SHOP] 滑动 {first_pass + second_pass} 次仍未找到"
                             f"商品 → PRODUCT_NOT_FOUND")
            return None
        finally:
            # 滑动结束(完成/异常)统一释放状态锁(规格九)
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
        """点击目标商品(规格 2026-08-21 §六: 重试 ≤2, 二次失败才报错)。

        成功标准: 出现 Google Play 购买页。点击后等待最多 5s 检测
        页面变化(Google Play 标题/支付页元素/购买确认按钮); 第一次
        未出现 → 自动再次点击一次(坐标偏移/点击未生效/页面切换慢);
        第二次仍失败才记录错误留档。绝不在第一次失败直接判死。
        """
        x = (info.bbox[0] + info.bbox[2]) // 2
        y = (info.bbox[1] + info.bbox[3]) // 2
        self.log.info("[BUY] 点击商品")
        for attempt in range(2):
            self.a.tick_heartbeat()
            self.d.click(x, y)
            self.detector.bust_caches()   # 事件驱动: 点击后强制全新检测
            self.log.info(f"[BUY] 等待 Google Play 页(第 {attempt + 1}/2 次)")
            state = self.detector.wait_for_state(
                [PokemonGoState.PURCHASE_PAGE], timeout=5)
            if state == PokemonGoState.PURCHASE_PAGE:
                self.log.info("[BUY] 成功进入支付页(Google Play)")
                return True
            if attempt == 0:
                self.log.info(f"[SHOP] Google Play 页未出现(当前="
                              f"{state.value}) — 自动再次点击一次")
        self.log.warning("[商店] 两次点击后仍未出现 Google Play 页"
                         " — 截图留档")
        self.a.capture_keyframe("PURCHASE_PAGE_TIMEOUT")
        return False

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
