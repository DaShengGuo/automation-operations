# -*- coding: utf-8 -*-
"""watch_flash2.py — 应用侧控制台活动检测器 v2(测试工具, 非运行期组件)。

v1(conhost 计数)的缺陷:
  1. conhost 父进程(一闪而过的 cmd/adb)死亡过快, 事后 CIM 查询拿不到
     父进程名 → 归因失败, 混入错误分类。
  2. 无法区分"可见黑框"与"隐藏 console"。

v2 设计(本机 Win11 26200 实测: 控制台进程不创建顶层 ConsoleWindowClass
窗口, 窗口枚举法失效; 改用 conhost 进程归因):
  1. 每 100ms 用 Toolhelp32 快照全量进程;
  2. 发现新 conhost PID 后立即连续补拍 2 张快照, 抢在其父进程死亡前
     解析出 (父进程名, 祖父…) 祖先链;
  3. 祖先链命中被测应用名(或 adb.exe 且其祖先为应用) → 应用侧;
  4. 同时枚举 ConsoleWindowClass 可见窗口(客户机 Win10/旧 Win11 上
     真实黑框会在此出现; 本机该计数恒为 0)。

用法(venv):
  python scripts/watch_flash2.py --minutes 10 --app-match "宝可梦" \
      --out c:/temp/flash2_result.txt
"""
import argparse
import ctypes
import json
import time
from ctypes import wintypes

# ── 进程快照(Toolhelp32) ──

TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE = ctypes.c_void_p(-1).value
kernel32 = ctypes.windll.kernel32


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def process_snapshot():
    """返回 {pid: (ppid, exe_name)}。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE:
        return {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        out = {}
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = (
                entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return out
    finally:
        kernel32.CloseHandle(snap)


def ancestor_chain(pid: int, snap: dict, depth: int = 12) -> list[str]:
    """pid 的祖先链(exe 名, 含自身), 防止环。"""
    chain = []
    seen = set()
    cur = pid
    while cur and cur in snap and cur not in seen and len(chain) < depth:
        seen.add(cur)
        ppid, name = snap[cur]
        chain.append(name)
        cur = ppid
    return chain


# ── 窗口枚举(次要证据: 客户机上可见黑框的客观判据) ──

user32 = ctypes.windll.user32
CONSOLE_CLASS = "ConsoleWindowClass"
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)


def enum_visible_console_windows():
    """返回 [(pid, title)] — 当前可见的控制台类顶层窗口。"""
    wins = []

    def cb(hwnd, _lparam):
        wins.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    out = []
    for hwnd in wins:
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value != CONSOLE_CLASS or not user32.IsWindowVisible(hwnd):
            continue
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        out.append((pid.value, title.value))
    return out


def classify(chain: list[str], app_match: str) -> str:
    """归因: 链条命中应用名 → APP; 有 adb/cmd/powershell 但无应用 →
    ORPHAN; 否则 NOISE。"""
    if any(app_match in name for name in chain):
        return "APP"
    if any(n.lower() in ("adb.exe", "cmd.exe", "powershell.exe")
           for n in chain):
        return "ORPHAN"
    return "NOISE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=8.0)
    ap.add_argument("--app-match", default="宝可梦")
    ap.add_argument("--out", default=r"c:\temp\flash2_result.txt")
    args = ap.parse_args()

    app_conhost = []      # conhost + 应用侧祖先链
    orphan_conhost = []   # conhost + adb/cmd 祖先链(非本应用)
    noise_conhost = []    # 其余环境噪声
    visible_console_wins = []  # 可见控制台窗口(客户机判据)
    seen_conhosts = {}

    deadline = time.time() + args.minutes * 60
    while time.time() < deadline:
        snap = process_snapshot()
        for pid, (ppid, name) in snap.items():
            if name.lower() != "conhost.exe":
                continue
            if pid in seen_conhosts:
                continue
            # 新 conhost: 立即补拍快照抢父进程(父可能 100ms 内死亡)
            chain = ancestor_chain(pid, snap)
            if len(chain) <= 1:
                time.sleep(0.02)
                snap2 = process_snapshot()
                chain2 = ancestor_chain(pid, snap2)
                if len(chain2) > len(chain):
                    chain = chain2
            seen_conhosts[pid] = chain
            kind = classify(chain, args.app_match)
            line = (f"{time.strftime('%H:%M:%S')} conhost={pid} "
                    f"chain={chain}")
            if kind == "APP":
                app_conhost.append(line)
            elif kind == "ORPHAN":
                orphan_conhost.append(line)
            else:
                noise_conhost.append(line)
        for pid, title in enum_visible_console_windows():
            visible_console_wins.append(
                f"{time.strftime('%H:%M:%S')} pid={pid} title={title!r}")
        time.sleep(0.1)

    result = {
        "APP_CONHOST_COUNT": len(app_conhost),
        "ORPHAN_CONHOST_COUNT": len(orphan_conhost),
        "NOISE_CONHOST_COUNT": len(noise_conhost),
        "VISIBLE_CONSOLE_WINDOW_COUNT": len(visible_console_wins),
        "APP_CONHOST_LINES": app_conhost[:60],
        "ORPHAN_CONHOST_LINES": orphan_conhost[:30],
        "NOISE_CONHOST_LINES": noise_conhost[:30],
        "VISIBLE_CONSOLE_WINDOW_LINES": visible_console_wins[:30],
    }
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
