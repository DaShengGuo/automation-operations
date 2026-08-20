"""
automation/pokemon_go/detector.py
Pokémon GO 页面检测器

检测优先级(综合多证据, 命中数达到 min_hits 才算):
  1. 前台 app 判断 → 游戏内 / 外部网页上下文 / Google Play / 系统选择器
  2. UI hierarchy 文本(hierarchy 可读时 — 浏览器网页/系统UI)
  3. OpenCV 模板(图形特征, 语言无关)
  4. OCR 关键词(带缓存, 应对 Unity 游戏 hierarchy 无业务文字)

浏览器品牌完全透明: 外部上下文中只看「内容特征」判断 PTC 登录页。
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional, Sequence

from automation.pokemon_go.selectors import PokemonGoSelectors
from automation.pokemon_go.states import PokemonGoState

logger = logging.getLogger(__name__)


class PokemonGoPageDetector:
    """每台设备一个实例(线程隔离)"""

    # 检测顺序: 先具体后通用
    DETECT_ORDER = [
        PokemonGoState.PTC_LOGIN_ERROR,
        PokemonGoState.LOGOUT_CONFIRM,
        PokemonGoState.PURCHASE_PROCESSING,
        PokemonGoState.PURCHASE_SUCCESS,
        PokemonGoState.PURCHASE_FAILED,
        PokemonGoState.PRODUCT_FOUND,
        PokemonGoState.INITIAL_PROMPT,
        PokemonGoState.PROFESSOR_DIALOG,
        PokemonGoState.WELCOME_PAGE,
        PokemonGoState.SETTINGS,
        PokemonGoState.MAIN_MENU,
        PokemonGoState.SHOP,
        PokemonGoState.LOGIN_FAILED_DIALOG,  # 弹窗含「中央站」描述文本, 必须先于 LOGIN_PROVIDER
        PokemonGoState.LOGIN_PROVIDER,
        PokemonGoState.RETURNING_PLAYER,
        PokemonGoState.GAME_SPLASH,
        PokemonGoState.GAME_LOADING,
        PokemonGoState.MAP,   # 兜底: 底部 Poké Ball 图形(语言无关)
    ]

    def __init__(self, controller, selectors: PokemonGoSelectors,
                 ocr_cache_sec: float = 4.0):
        self.d = controller
        self.sel = selectors
        self.matcher = getattr(controller, "matcher", None)
        self.ocr_cache_sec = ocr_cache_sec
        self._ocr_cache = {"hash": "", "ts": 0.0,
                           "texts": [], "boxes": []}
        self.last_evidence: str = ""
        # 状态级缓存: 截图无变化时直接复用上次检测结果(省 OCR/模板)
        self._state_cache = {"fp": "", "state": PokemonGoState.UNKNOWN}
        # 性能缓存: 前台包名(ADB dumpsys 慢)与游戏内 hierarchy(无业务文字)
        self._pkg_cache = {"pkg": "", "ts": 0.0}
        self._xml_cache = {"xml": "", "ts": 0.0}
        # 游戏内状态是否需要 hierarchy(检查规则表, 通常全空 → 跳过 dump)
        self._need_hierarchy_in_game = any(
            (r.hierarchy_texts or r.hierarchy_descs)
            for r in (self.sel.rules.values()))

    # ── 前台应用 ──

    def bust_caches(self):
        """清空全部检测缓存 — 恢复流程要求以最新截图/dump 重新检测。

        OCR/XML/包名/状态指纹全部强制下轮重取, 避免「截图早已变化
        但旧缓存仍在复用」导致的假 UNKNOWN(真机卡点)。
        """
        self._ocr_cache.update(hash="", ts=0.0, texts=[], boxes=[])
        self._xml_cache.update(xml="", ts=0.0)
        self._pkg_cache.update(pkg="", ts=0.0)
        self._state_cache.update(fp="", state=PokemonGoState.UNKNOWN)

    def current_package(self) -> str:
        """当前前台包名(2 秒缓存, 避免每 tick 打一次 dumpsys)"""
        import time as _time
        now = _time.time()
        if now - self._pkg_cache["ts"] < 2.0:
            return self._pkg_cache["pkg"]
        try:
            pkg = self.d.current_package()
        except Exception:
            pkg = self._pkg_cache["pkg"]
        self._pkg_cache.update(pkg=pkg, ts=now)
        return pkg

    def is_game_foreground(self) -> bool:
        return self.current_package() == self.d.package

    def is_external_context(self) -> bool:
        """已离开游戏(浏览器/Google Play/系统弹窗)"""
        pkg = self.current_package()
        return pkg != "" and pkg != self.d.package

    # ── 主检测 ──

    def detect(self) -> PokemonGoState:
        """综合判断当前页面状态

        性能: 游戏内(Unity hierarchy 无业务文字)跳过 dump_hierarchy,
        只在外部网页上下文需要时读取 — 实测每轮检测省 1-3 秒。
        """
        shot = None
        xml = ""

        # 1. 外部上下文
        pkg = self.current_package()
        if pkg != self.d.package:
            return self._detect_external(pkg)

        # 2. hierarchy — 仅当游戏内规则确实用到时(缓存 15 秒)
        if self._need_hierarchy_in_game:
            import time as _time
            now = _time.time()
            if now - self._xml_cache["ts"] > 15.0:
                try:
                    self._xml_cache.update(xml=self.d.dump_hierarchy(),
                                           ts=now)
                except Exception:
                    pass
            xml = self._xml_cache["xml"]

        # 3. 模板通道(快) + 状态缓存(截图指纹无变化 → 直接复用上次结果)
        try:
            shot = self.d.screenshot()
        except Exception:
            pass
        if shot is not None:
            from core.perf import screen_fingerprint
            fp = screen_fingerprint(shot, shrink=4)
            if fp and fp == self._state_cache["fp"] and \
                    self._state_cache["state"] != PokemonGoState.UNKNOWN:
                return self._state_cache["state"]
            self._state_cache["fp"] = fp

        # 4. 按顺序判定游戏内状态
        for state in self.DETECT_ORDER:
            rule = self.sel.rule(state)
            if rule is None:
                continue
            hits = 0
            evidence = []
            if rule.hierarchy_texts or rule.hierarchy_descs:
                h = rule.match_hierarchy(xml)
                if h:
                    hits += h
                    evidence.append(f"hierarchy×{h}")
            if rule.templates and shot is not None and self.matcher:
                for t in rule.templates:
                    # ROI 优化: 只匹配模板所在区域(底部 Poké Ball 等)
                    roi = (0.3, 0.78, 0.7, 1.0) if state == PokemonGoState.MAP \
                        else None
                    if self.matcher.exists(t, shot,
                                           threshold=rule.template_threshold,
                                           roi=roi):
                        hits += 1
                        evidence.append(f"tpl:{t}")
                        break
            if rule.red_ratio_threshold is not None and shot is not None:
                if self._red_ratio_hit(shot, rule):
                    hits += 1
                    evidence.append("red_ratio")
            if rule.ocr_rules:
                texts = self._ocr_texts(shot)
                o = rule.match_ocr(texts)
                if o:
                    hits += o
                    evidence.append(f"ocr×{o}")
            if hits >= rule.min_hits:
                self.last_evidence = " + ".join(evidence)
                logger.debug(f"[检测] {state.value} ({self.last_evidence})")
                self._state_cache["state"] = state
                return state

        self.last_evidence = "no rule matched"
        self._state_cache["state"] = PokemonGoState.UNKNOWN
        return PokemonGoState.UNKNOWN

    # ── 外部上下文检测 ──

    def _detect_external(self, pkg: str) -> PokemonGoState:
        xml = ""
        try:
            xml = self.d.dump_hierarchy()
        except Exception:
            pass

        # A. Google Play 支付上下文(com.android.vending / 支付特征)
        if pkg.startswith("com.android.vending") or \
                self._xml_has_any(xml, ["滑動即可購買", "Google Play",
                                        "購買", "购买"]):
            if self._xml_has_any(xml, ["正在處理", "正在处理", "Processing"]):
                return PokemonGoState.PURCHASE_PROCESSING
            return PokemonGoState.PURCHASE_PAGE

        # B. Android 系统「选择打开方式」(ResolverActivity/Chooser)
        if "chooser" in pkg.lower() or "resolver" in pkg.lower() or \
                self._xml_has_any(xml, ["选择打开方式", "開啟方式",
                                        "仅此一次", "僅此一次", "始終", "始终"]):
            logger.warning("[BROWSER_CHOOSER_REQUIRED] 系统弹出选择器,"
                           "需人工选择一次(或设置默认浏览器)")
            return PokemonGoState.PTC_REDIRECTING

        # C. PTC 登录页(网页内容特征, 与浏览器品牌无关)
        ptc_texts = self.sel.ptc.get("page_texts", []) or []
        if self._xml_has_any(xml, ptc_texts):
            return PokemonGoState.PTC_LOGIN_PAGE
        # 网页错误特征
        if self._xml_has_any(xml, ["无法访问", "無法連上", "找不到伺服器",
                                   "Page not found", "404", "ERR_"]):
            return PokemonGoState.PTC_LOGIN_ERROR
        # WAF/反爬拦截页(真机实测: Pardon Our Interruption)
        if self._xml_has_any(xml, ["Pardon Our Interruption",
                                   "we think you were a bot",
                                   "Access Denied", "captcha",
                                   "Just a moment"]):
            logger.warning("[PTC] 网页被 WAF 拦截(网络出口被风控), "
                           "建议检查设备网络/VPN 配置")
            return PokemonGoState.PTC_LOGIN_ERROR
        # 白屏/加载中 → 交给 wait_ptc_login_page 超时判定
        return PokemonGoState.PTC_REDIRECTING

    @staticmethod
    def _red_ratio_hit(shot, rule) -> bool:
        """色块证据: ROI 内红色像素占比超过阈值(截图统一 BGR 通道)。

        MAP 底部精灵球兜底 — 真机实测红色占比: 地图 0.048,
        商店/主菜单/设置 ≤0.013, 阈值 0.025 两侧均有 2 倍余量。
        """
        try:
            import cv2
            import numpy as np
            h, w = shot.shape[:2]
            x1, y1, x2, y2 = rule.red_ratio_roi
            roi = shot[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            red = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170)) & \
                  (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 80)
            return float(red.mean()) > rule.red_ratio_threshold
        except Exception:
            return False

    @staticmethod
    def _xml_has_any(xml: str, keywords: Sequence[str]) -> bool:
        if not xml:
            return False
        return any(k in xml for k in keywords)

    # ── OCR 通道(带缓存) ──

    def _ocr_texts(self, shot) -> list[str]:
        if shot is None:
            return []
        h = hashlib.md5(shot.tobytes()).hexdigest()
        now = time.time()
        if h == self._ocr_cache["hash"] and \
                now - self._ocr_cache["ts"] < self.ocr_cache_sec:
            return self._ocr_cache["texts"]
        from core.ocr import ocr_with_boxes
        boxes = ocr_with_boxes(shot)
        if boxes:
            # 只缓存成功结果 — 空结果不缓存, 避免截图偶发失败
            # 后重试仍命中空缓存(真机实测卡点)
            self._ocr_cache.update(hash=h, ts=now,
                                   texts=[t for t, _ in boxes], boxes=boxes)
        else:
            self._ocr_cache.update(hash="", ts=0.0, texts=[], boxes=[])
        return self._ocr_cache["texts"]

    def ocr_boxes(self, shot=None) -> list:
        """返回 [(text, bbox)] — 供点击定位"""
        if shot is None:
            try:
                shot = self.d.screenshot()
            except Exception:
                return []
        self._ocr_texts(shot)  # 触发缓存
        if hashlib.md5(shot.tobytes()).hexdigest() == self._ocr_cache["hash"]:
            return self._ocr_cache["boxes"]
        return []

    def find_text_box(self, keywords: Sequence[str],
                      require_all: bool = False
                      ) -> Optional[tuple[int, int, int, int]]:
        """OCR 查找包含关键词的文本块, 返回 (x1,y1,x2,y2)。

        require_all=True:  优先返回单个文本块含全部关键词的;
                           否则回退到含关键词最多的块(要求所有关键词
                           至少散布在整屏 OCR 文本中) — 应对 OCR 把
                           「已註冊的玩家」拆成多块导致整块匹配失败
                           (真机卡点: 注册页按钮永远点不到)。
        require_all=False: 任一关键词命中(候选变体, 如 [商店,Shop])
        """
        boxes = self.ocr_boxes()
        if not require_all:
            for text, bbox in boxes:
                if any(k in text for k in keywords):
                    return bbox
            return None
        # require_all: 单块全含优先, 否则整屏散布 + 最多命中块
        best = None
        best_hits = 0
        all_present = True
        for k in keywords:
            if not any(k in t for t, _ in boxes):
                all_present = False
                break
        if not all_present:
            return None
        for text, bbox in boxes:
            hits = sum(1 for k in keywords if k in text)
            if hits > best_hits:
                best_hits = hits
                best = bbox
                if hits == len(keywords):
                    return bbox
        return best

    # ── 等待类 ──

    def _hb(self):
        """心跳回调(Worker 注入): 长等待循环内周期性告知调度器线程未卡死。

        真机 run 实测: 登录等待 120s 期间心跳停摆, 调度器误判
        WORKER_STALLED 重建 Worker, 登录重试被打断 + 账号白冷却 2 分钟。
        """
        cb = getattr(self, "heartbeat_cb", None)
        if cb:
            cb()

    def wait_for_state(self, states: Sequence[PokemonGoState],
                       timeout: float, interval: float = 0.2,
                       screenshot_each: bool = False,
                       on_snapshot=None,
                       or_states: Sequence[PokemonGoState] = ()
                       ) -> PokemonGoState:
        """等待进入任一状态(快速轮询: 页面一出现立即返回)。

        or_states: 「已经越过目标」的合法状态(目标的后继页)。
        等待 RETURNING_PLAYER 时页面已到 LOGIN_PROVIDER(人工点过
        已注册等场景) → 立即返回, 不再白等整段超时后判失败。
        """
        t0 = time.time()
        deadline = t0 + timeout
        last = PokemonGoState.UNKNOWN
        fast_phase = min(3.0, timeout / 2)
        while time.time() < deadline:
            self._hb()
            last = self.detect()
            if last in states:
                return last
            if last in or_states:
                return last
            if on_snapshot:
                on_snapshot(last)
            # 前几秒快速轮询, 之后降频(避免长时间等待空转 OCR)
            iv = interval if (deadline - time.time()) > \
                (timeout - fast_phase) else max(interval, 0.8)
            time.sleep(iv)
        # 等待超时诊断(§十六): >5s 的等待记录现场, 供事后优化
        waited = time.time() - t0
        if waited > 5.0:
            logger.warning(f"[等待诊断] 等待 {[s.value for s in states]} "
                           f"超时: 耗时={waited:.1f}s "
                           f"当前={last.value} "
                           f"证据={self.last_evidence}")
        return last

    def wait_game_foreground(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._hb()
            if self.is_game_foreground():
                return True
            time.sleep(1.5)
        return False

    def wait_external_context(self, timeout: float) -> bool:
        """等待离开游戏(系统跳转到浏览器)"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._hb()
            if self.is_external_context():
                return True
            time.sleep(1.0)
        return False
