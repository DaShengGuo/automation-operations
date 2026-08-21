"""
automation/pokemon_go/web_context.py
ExternalWebContext — 浏览器无关的 PTC 网页登录上下文

原则:
  - 不感知浏览器品牌, 不维护浏览器白名单
  - 业务状态只有: PTC_REDIRECTING / PTC_LOGIN_PAGE / PTC_LOGIN_SUBMITTING
    / PTC_LOGIN_ERROR / AUTHORIZING / RETURNING_TO_GAME
  - 网页识别靠「网页内容特征」(hierarchy text/hint/desc + OCR)
  - 填充优先级: u2 set_text → 剪贴板粘贴 → adb input

真实网页特征(录屏 + 真机验证):
  URL: access.pokemon.com
  标题: POKÉMON TRAINER CENTRAL
  输入框: Email or username / Password
  按钮: Log In
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from automation.pokemon_go.detector import PokemonGoPageDetector
from automation.pokemon_go.states import PgoLoginResult, PokemonGoState

logger = logging.getLogger(__name__)


class FillResult:
    def __init__(self, ok: bool, method: str = "", detail: str = ""):
        self.ok = ok
        self.method = method
        self.detail = detail


class ExternalWebContext:
    """外部网页上下文 — 由 Android 系统自行调起该手机现有浏览器"""

    def __init__(self, detector: PokemonGoPageDetector, ptc_config: dict,
                 controller, log=None):
        self.detector = detector
        self.d = controller
        self.ptc = ptc_config or {}
        self.log = log or logging.getLogger(__name__)

    # ── 跳转等待 ──

    def wait_leave_game(self, timeout: float = 60) -> bool:
        """点击 PTC 后等待系统跳转到外部网页(浏览器)"""
        ok = self.detector.wait_external_context(timeout)
        if ok:
            self.log.info("[PTC] 系统已打开外部网页(浏览器无关)")
        else:
            self.log.warning(f"[PTC] 等待系统跳转超时({timeout}s)")
        return ok

    def wait_ptc_login_page(self, timeout: float = 60) -> bool:
        """轮询等待 PTC 登录表单出现(允许网页先白屏再加载)。

        错误页/WAF 拦截页作为终态提前返回 False(不等满超时)。
        """
        state = self.detector.wait_for_state(
            [PokemonGoState.PTC_LOGIN_PAGE, PokemonGoState.PTC_LOGIN_ERROR],
            timeout=timeout)
        if state == PokemonGoState.PTC_LOGIN_PAGE:
            return True
        if state == PokemonGoState.PTC_LOGIN_ERROR:
            self.log.warning("[PTC] 网页错误页(网络/服务/WAF 拦截)")
        else:
            self.log.warning(f"[PTC] 登录页等待超时({timeout}s), "
                             f"最后状态={state.value}")
        return False

    # ── 输入框定位(浏览器无关) ──

    def _find_inputs(self) -> list:
        """hierarchy 中找网页输入框(EditText)。浏览器不同但类名一致。"""
        try:
            sel = self.d.device(className="android.widget.EditText")
            try:
                els = sel.all()  # u2 3.6+
            except AttributeError:
                els = list(sel)  # 旧版迭代
        except Exception:
            return []
        inputs = []
        for i, el in enumerate(els):
            try:
                info = el.info or {}
            except Exception:
                info = {}
            inputs.append((i, el, info))
        return inputs

    def _locate_username_input(self):
        """用户名框: 第一个 EditText, 或 hint 含 email/username"""
        inputs = self._find_inputs()
        if not inputs:
            return None
        # 优先 hint 匹配
        for idx, el, info in inputs:
            hint = (info.get("hint") or "")
            if any(k in hint.lower() for k in
                   ("email", "username", "user")):
                return el, "hint"
        return inputs[0][1], "first_edittext"

    def _locate_password_input(self):
        """密码框: 第二个 EditText 或 hint 含 password"""
        inputs = self._find_inputs()
        if len(inputs) < 2:
            return None
        for idx, el, info in inputs:
            hint = (info.get("hint") or "").lower()
            if "pass" in hint:
                return el, "hint"
        return inputs[1][1], "second_edittext"

    # ── 填充 ──

    def fill_username(self, username: str) -> FillResult:
        located = self._locate_username_input()
        if located is None:
            return FillResult(False, "", "未找到用户名输入框(EditText)")
        el, how = located
        return self._fill(el, username, field="username", how=how)

    def fill_password(self, password: str) -> FillResult:
        located = self._locate_password_input()
        if located is None:
            return FillResult(False, "", "未找到密码输入框(EditText)")
        el, how = located
        return self._fill(el, password, field="password", how=how)

    def _fill(self, el, text: str, field: str, how: str) -> FillResult:
        """三级填充: u2 set_text → 剪贴板粘贴 → adb input"""
        # 1. uiautomator2 set_text(内部先清空)
        try:
            el.click()
            time.sleep(0.8)
            el.set_text(text)
            time.sleep(0.5)
            if self._verify_filled(field, text):
                return FillResult(True, "set_text", how)
        except Exception as e:
            self.log.debug(f"[PTC] set_text 失败: {e}")

        # 2. 剪贴板粘贴(KEYCODE_PASTE=279, 兼容 u2 无 paste 命名键)
        try:
            self.d.device.set_clipboard(text)
            self.d.device.press(279)
            time.sleep(0.8)
            if self._verify_filled(field, text):
                return FillResult(True, "clipboard", how)
        except Exception as e:
            self.log.debug(f"[PTC] 剪贴板失败: {e}")

        # 3. adb input text(仅 ASCII)
        if text.isascii():
            try:
                self.d.adb.input_text(self.d.serial, text)
                time.sleep(0.5)
                if self._verify_filled(field, text):
                    return FillResult(True, "adb_input", how)
            except Exception as e:
                self.log.debug(f"[PTC] adb input 失败: {e}")
        return FillResult(False, "", f"{field} 填充失败(三种方式均失败)")

    def _verify_filled(self, field: str, text: str) -> bool:
        """验证输入已生效: 该字段输入框文本非空"""
        try:
            if field == "username":
                located = self._locate_username_input()
            else:
                located = self._locate_password_input()
            if located is None:
                return False
            info = located[0].info or {}
            value = info.get("text") or ""
            # 密码框通常被遮罩, 但长度>0 即可; 用户名框检查包含
            if field == "password":
                return len(value) > 0
            return len(value) > 0 and (value in text or text in value
                                       or len(value) >= len(text))
        except Exception:
            return False

    # ── 提交 ──

    def submit_login(self, timeout: float = 20) -> bool:
        """点击 Log In 按钮。

        真机实测:
          - u2 的 el.click()/bounds 点击对 Custom Tab 按钮无效, OCR 坐标有效
          - 密码框聚焦时键盘遮挡 + 网页可能启用防截屏(截图失败)
            → 提交前先点击表单上方空白处收起键盘/失焦
        """
        # 0. 收起键盘(点击网页上部空白, 不触发表单按钮)
        try:
            self.d.click(int(self.d.screen_w * 0.5), int(self.d.screen_h * 0.22))
            time.sleep(1.5)
        except Exception:
            pass
        # 1. OCR 定位「Log In」→ 坐标点击
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                box = self.detector.find_text_box(["Log", "In"],
                                                  require_all=True)
                if box is not None:
                    x = (box[0] + box[2]) // 2
                    y = (box[1] + box[3]) // 2
                    self.d.click(x, y)
                    self.log.info(f"[PTC] 已提交登录(OCR @{x},{y})")
                    # 验证点击生效: 提交成功后按钮随页面跳转消失。键盘收起
                    # 导致布局位移会让点击落空(真机 run 实测: 点了但表单
                    # 仍在, 白等 75s 超时) → 按钮仍在则重定位再点一次
                    for _ in range(4):
                        time.sleep(2)
                        # 密码保存弹窗压住浏览器会遮住按钮 → OCR 找不到
                        # Log/In 误判「按钮消失=提交已生效」放行(真机 5/5
                        # 实测: 弹窗在认证期间出现即 75s 死锁超时)。检查
                        # 按钮前先关弹窗。
                        if self._dismiss_password_save_dialog():
                            time.sleep(1)
                        try:
                            if self.detector.find_text_box(
                                    ["Log", "In"],
                                    require_all=True) is None:
                                return True   # 按钮消失 = 提交已生效
                        except Exception:
                            return True
                    self.log.warning("[PTC] 提交点击未生效(按钮仍在), "
                                     "重新定位再点一次")
                    try:
                        box2 = self.detector.find_text_box(
                            ["Log", "In"], require_all=True)
                        if box2 is not None:
                            self.d.click((box2[0] + box2[2]) // 2,
                                         (box2[1] + box2[3]) // 2)
                    except Exception:
                        pass
                    return True
            except Exception as e:
                self.log.debug(f"[PTC] OCR 定位 Log In 异常: {e}")
            time.sleep(2.5)
        self.log.warning("[PTC] 未找到 Log In 按钮(OCR 重试超时)")
        return False

    def classify_error(self) -> Optional[PgoLoginResult]:
        """登录后检测网页错误分类"""
        try:
            xml = self.d.dump_hierarchy()
        except Exception:
            xml = ""
        err_map = {
            "INVALID_CREDENTIALS": self.ptc.get("error_texts", {}).get(
                "invalid_credentials", []),
            "NETWORK_ERROR": ["无法访问", "無法連上", "网络错误", "網路錯誤",
                              "offline", "timed out"],
            "WEB_ERROR": ["Page not found", "404", "ERR_", "白屏"],
        }
        for kind, keywords in err_map.items():
            if any(k in xml for k in (keywords or [])):
                return PgoLoginResult(kind)
        # OCR 兜底(网页错误可能不在 hierarchy)
        try:
            texts = self.detector._ocr_texts(self.d.screenshot())
            joined = " ".join(texts)
            for kw in ["用户名或密码", "密码错误", "Invalid username",
                       "incorrect password", "账号或密码"]:
                if kw.lower() in joined.lower():
                    return PgoLoginResult.INVALID_CREDENTIALS
        except Exception:
            pass
        return None

    def wait_game_return(self, timeout: float = 120) -> bool:
        """等待网页认证完成后系统自动返回 Pokémon GO

        规格 §四: Login→主页人工 ≥30s 资源加载, 不误判卡死。0.5s 快速
        轮询(命中即返) + 每 5s 检查点进度日志 + 错误早失败(不烧满超时)。
        """
        deadline = time.time() + timeout
        t0 = time.time()
        last_log = 0.0
        last_err_check = 0.0
        resubmitted = False  # 弹窗死锁后重提一次(有界)
        while time.time() < deadline:
            # 心跳(Worker 注入): 认证等待可长达 75-120s, 期间告知调度器未卡死
            cb = getattr(self, "heartbeat_cb", None)
            if cb:
                cb()
            # 停止检查(2026-08-21): stop_event 置位即中断认证等待
            stop_cb = getattr(self, "stop_cb", None)
            if stop_cb and stop_cb():
                from core.stop_error import WorkerStopRequested
                raise WorkerStopRequested("停止指令生效, 中断认证等待")
            if self.detector.is_game_foreground():
                self.log.info(f"[PTC] 已返回游戏 (用时 "
                              f"{time.time() - t0:.0f}s)")
                return True
            # 检查点进度日志(规格 §四): 每 5s 一次, 让 ≥30s 等待节奏可视
            now = time.time()
            if now - last_log >= 5.0:
                self.log.info(f"[步骤] 等待游戏返回 (已等 "
                              f"{now - t0:.0f}s, "
                              f"外部上下文={self.detector.is_external_context()})")
                last_log = now
            # 网页上可能还有继续/授权按钮(正常授权步骤)
            self._handle_web_continue_buttons()
            # MIUI 密码保存弹窗压住浏览器会停滞认证(run 12 实测根因)
            if self._dismiss_password_save_dialog():
                time.sleep(1)
                # 弹窗压住浏览器期间认证死锁(真机 5/5: 弹窗在认证期间
                # 出现=超时, 弹窗延迟到返回游戏后=成功)。关闭弹窗后若
                # 表单仍在, 主动重提一次 — 密码管理器同会话通常不再
                # 重复弹窗, 重提可走完认证。
                if (not resubmitted
                        and not self.detector.is_game_foreground()
                        and self._login_form_visible()):
                    resubmitted = True
                    self.log.info("[PTC] 密码弹窗关闭后表单仍在 — 重提一次")
                    self.submit_login(timeout=10)
            # 错误可能提交后很快出现(用户名或密码错误等) — 早失败,
            # 不烧满 120s(run 13 实测: 错误已显示在屏幕, 仍白等整窗)
            now = time.time()
            if now - last_err_check >= 5.0:
                last_err_check = now
                err = self.classify_error()
                if err is not None:
                    self.log.warning(f"[PTC] 认证提前失败: {err.value}")
                    return False
            time.sleep(0.5)
        self.log.warning(f"[AUTH_RETURN_TIMEOUT] 等待返回游戏超时({timeout}s)")
        return False

    def _login_form_visible(self) -> bool:
        """登录表单是否仍在(Log In 按钮可见)"""
        try:
            return self.detector.find_text_box(["Log", "In"],
                                               require_all=True) is not None
        except Exception:
            return False

    def _dismiss_password_save_dialog(self) -> bool:
        """关闭系统密码保存弹窗(MIUI 智能密码管理)。

        提交登录后系统询问「是否将账号密码存储在智能密码管理中」,
        弹窗压在浏览器上导致认证流程停滞(真机 run 12 实测
        AUTH_RETURN_TIMEOUT 根因)。必须点「取消」— 绝不点「存储」
        (账号密码不入系统密码库)。
        """
        try:
            xml = self.d.dump_hierarchy()
        except Exception:
            return False
        if not any(k in xml for k in ("智能密码管理", "自动保存账号密码",
                                      "智能密码")):
            return False
        # 1. OCR 定位取消按钮
        try:
            box = self.detector.find_text_box(["取消"], require_all=False)
            if box is not None:
                x = (box[0] + box[2]) // 2
                y = (box[1] + box[3]) // 2
                self.d.click(x, y)
                self.log.info("[PTC] 关闭系统密码保存弹窗(取消, 不入密码库)")
                return True
        except Exception as e:
            self.log.debug(f"[PTC] OCR 关闭密码弹窗失败: {e}")
        # 2. u2 兜底
        try:
            el = self.d.device(text="取消")
            if el.exists:
                el.click()
                self.log.info("[PTC] 关闭系统密码保存弹窗(u2 取消)")
                return True
        except Exception as e:
            self.log.debug(f"[PTC] u2 关闭密码弹窗失败: {e}")
        return False

    def _handle_web_continue_buttons(self):
        """处理网页认证流程中可能出现的继续/授权按钮"""
        for text in ("Continue", "Allow", "OK", "繼續"):
            try:
                el = self.d.device(text=text)
                if el.exists:
                    el.click()
                    self.log.info(f"[PTC] 处理网页授权按钮: {text}")
                    time.sleep(1)
            except Exception:
                pass

    def back_to_game(self, timeout: float = 30) -> bool:
        """网页失败恢复: BACK → 检测回到游戏/登录方式页(与浏览器无关)"""
        try:
            self.d.press("back")
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.detector.is_game_foreground():
                return True
            time.sleep(1.5)
        return False
