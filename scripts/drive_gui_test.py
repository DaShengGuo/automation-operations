# -*- coding: utf-8 -*-
"""drive_gui_test.py — EXE 真机验证驱动(测试工具, 非运行期组件)。

用 pywinauto(UIA)点击正式 EXE 的 GUI: 等待设备 READY → 点「确认并运行」
→ 等「运行中Worker: 1」→ 保持 N 分钟 → 点「停止全部」→ 等 Worker 归零,
全程把界面文本快照与弹窗证据写入 JSON 结果文件。
同时配合 watch_flash2.py 做零闪窗实测。

弹窗查找注意事项(实测):
  - 模态弹窗打开期间, 父窗口 WS_VISIBLE 被清除(移出 UIA 顶层树)时,
    弹窗暴露为顶层元素 → 用 find_elements;
  - 父窗口保持可见时, QMessageBox 嵌套在父窗口 UIA 子树下(非顶层)
    → 必须用 win.child_window 找。
  两条路径都查, 才能覆盖「启动即弹窗」与「运行中弹窗」两种时机。

用法(venv): python scripts/drive_gui_test.py --hold-minutes 7 --out c:/temp/gui_drive_result.txt
"""
import argparse
import json
import sys
import time

from pywinauto import Application
from pywinauto.findwindows import ElementNotFoundError, find_elements

TITLE_RE = "欢迎使用宝可梦自动化购买脚本"
POLL_SECONDS = 3.0
STOP_WAIT_SECONDS = 150  # 停止全部是优雅停止(当前账号循环结束才退出), 实测 ~95s


def _texts(win, substr):
    """收集所有包含 substr 的控件文本(QLabel 在 UIA 里通常是 Text)。"""
    out = []
    try:
        desc = win.descendants()
    except Exception:
        return out
    for c in desc:
        try:
            t = c.window_text()
        except Exception:
            continue
        if t and substr in t:
            out.append(t)
    return out


def _click_button(win, name):
    for c in win.descendants(control_type="Button"):
        try:
            if c.window_text() == name:
                c.click()
                return True
        except Exception:
            continue
    return False


def _wait_text(win, substr, timeout, wanted=None):
    """轮询直到出现包含 substr 的文本; wanted 非空时要求其出现在某条文本里。"""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        texts = _texts(win, substr)
        if texts:
            last = texts[0]
            if wanted is None or any(wanted in t for t in texts):
                return last
        time.sleep(POLL_SECONDS)
    return last  # 超时返回最后见到的(可能为空)


def _find_dialog(win, title):
    """按标题找弹窗: 先顶层, 再主窗口子树(见模块注释)。win 可为 None。"""
    try:
        elems = find_elements(title_re=title, backend="uia",
                              visible_only=False)
        if elems:
            # 注意: connect(handle=...) 不能带 timeout (pywinauto<0.6.9 直接
            # 抛 ValueError: Timeout could be specified with path param only),
            # 异常又被上层吞掉 → 弹窗永远关不掉。timeout 仅限 path= 场景。
            app = Application(backend="uia").connect(
                handle=elems[-1].handle)
            return app.window(handle=elems[-1].handle)
    except Exception:
        pass
    if win is not None:
        try:
            spec = win.child_window(title_re=title, visible_only=False)
            if spec.exists():
                return spec.wrapper_object()
        except Exception:
            pass
    return None


def _click_dialog_button(dlg, names):
    for name in names:
        for c in dlg.descendants(control_type="Button"):
            try:
                if c.window_text() == name:
                    c.click()
                    return name
            except Exception:
                continue
    return None


def _dialog_body(dlg):
    """弹窗正文证据: 含 VPN/PTC 的文本。"""
    texts = []
    for c in dlg.descendants():
        try:
            t = c.window_text()
        except Exception:
            continue
        if t and ("VPN" in t or "PTC" in t):
            texts.append(t)
    return texts


def _dismiss_vpn_dialog(win, timeout=20.0):
    """关闭「VPN 未检测到」弹窗 → 点「仍然继续」(预检) 或「已开启VPN」。

    返回 (clicked, texts) — clicked 为点掉的按钮列表, texts 为弹窗正文(证据)。
    """
    clicked = []
    texts_seen = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        dlg = _find_dialog(win, "VPN 未检测到")
        if dlg is not None:
            for t in _dialog_body(dlg):
                if t not in texts_seen:
                    texts_seen.append(t)
            btn = _click_dialog_button(
                dlg, ("仍然继续", "已开启VPN", "OK", "确定"))
            if btn:
                clicked.append(btn)
        time.sleep(0.5)
    return clicked, texts_seen


def _dismiss_toast(win, timeout=5.0):
    """关闭通用「提示」弹窗(信息 toast), 返回点掉的按钮。"""
    clicked = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        dlg = _find_dialog(win, "提示")
        if dlg is not None:
            btn = _click_dialog_button(dlg, ("OK", "确定"))
            if btn:
                clicked.append(btn)
        time.sleep(0.5)
    return clicked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold-minutes", type=float, default=7.0)
    ap.add_argument("--out", default=r"c:\temp\gui_drive_result.txt")
    args = ap.parse_args()

    # 1. 连接 GUI 窗口(同名多窗口时取最新一个; 任何连接异常都重试)
    #    启动即弹窗时主窗口被模态隐藏 → 连接阶段也要像真人一样随手关弹窗。
    app = None
    hwnd = None
    vpn_texts_seen = []
    vpn_clicks = []
    toast_clicks = []
    deadline = time.time() + 90
    while time.time() < deadline:
        clicks, texts = _dismiss_vpn_dialog(None, timeout=0.3)
        vpn_clicks += clicks
        for t in texts:
            if t not in vpn_texts_seen:
                vpn_texts_seen.append(t)
        try:
            elems = find_elements(title_re=TITLE_RE, backend="uia",
                                  visible_only=False)
            if elems:
                hwnd = elems[-1].handle
                # timeout 仅限 path= (见 _find_dialog 注释)
                app = Application(backend="uia").connect(handle=hwnd)
                break
        except ElementNotFoundError:
            pass
        except Exception:
            pass
        time.sleep(2)
    if app is None:
        print(json.dumps({"result": "FAIL", "reason": "window not found"},
                         ensure_ascii=False))
        sys.exit(2)
    win = app.window(handle=hwnd)
    try:
        win.set_focus()
    except Exception:
        pass
    time.sleep(2)

    # 2. 等设备 READY(DeviceMonitor 硬件扫描完成; 期间顺带关闭弹窗)
    detected = " ".join(_texts(win, "检测到设备"))
    ready = ""
    deadline = time.time() + 150
    while time.time() < deadline:
        clicks, texts = _dismiss_vpn_dialog(win, timeout=0.3)
        vpn_clicks += clicks
        for t in texts:
            if t not in vpn_texts_seen:
                vpn_texts_seen.append(t)
        toast_clicks += _dismiss_toast(win, timeout=0.3)
        texts = _texts(win, "READY设备")
        if texts:
            ready = texts[0]
            break
        time.sleep(POLL_SECONDS)

    # 3. 点「确认并运行」(兜底: 按钮名不同则点「开始运行」)
    clicked = _click_button(win, "确认并运行") or \
        _click_button(win, "开始运行")
    time.sleep(2)
    # 预检弹窗(运行前 VPN 缺失 → 「仍然继续」)
    clicks, texts = _dismiss_vpn_dialog(win, timeout=8)
    vpn_clicks += clicks
    for t in texts:
        if t not in vpn_texts_seen:
            vpn_texts_seen.append(t)

    # 4. 等「运行中Worker: 1」—— 0 设备 BUG 端到端证据
    worker = _wait_text(win, "运行中Worker", 300, wanted=": 1")

    # 5. 保持运行(闪窗监测窗口内持续产生 adb 子进程)
    time.sleep(args.hold_minutes * 60)
    running = " ".join(_texts(win, "运行中Worker"))

    # 6. 停止全部 — 优雅停止(当前账号循环结束才退出, 实测 ~95s)
    stopped = _click_button(win, "停止全部")
    after_stop = " ".join(_texts(win, "运行中Worker"))
    deadline = time.time() + STOP_WAIT_SECONDS
    while time.time() < deadline:
        toast_clicks += _dismiss_toast(win, timeout=0.3)
        texts = _texts(win, "运行中Worker")
        after_stop = " ".join(texts)
        if any(": 0" in t for t in texts):
            break
        time.sleep(POLL_SECONDS)

    result = {
        "result": "PASS" if (worker and ": 1" in worker) else "FAIL",
        "detected": detected,
        "ready": ready,
        "confirm_clicked": clicked,
        "vpn_dialog_handled": vpn_clicks,
        "vpn_dialog_texts": vpn_texts_seen,
        "toast_dialog_handled": toast_clicks,
        "worker_after_start": worker,
        "worker_after_hold": running,
        "stop_clicked": stopped,
        "worker_after_stop": after_stop,
    }
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
