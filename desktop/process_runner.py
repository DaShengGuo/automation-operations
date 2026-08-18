"""
desktop/process_runner.py
WindowsProcessRunner — 统一隐藏进程执行器。

客户版硬性要求: 从启动到整个自动化运行过程, 不出现任何
CMD / PowerShell / console 黑框闪烁。

机制(双层保险):
  1. run_hidden_process(): 所有自有 subprocess 调用点统一入口,
     startupinfo.dwFlags |= STARTF_USESHOWWINDOW + SW_HIDE
     且 creationflags |= CREATE_NO_WINDOW。
  2. install_global_hidden_patch(): 包装 subprocess.run/Popen/
     check_output/check_call/call 的默认参数, 覆盖第三方库
     (uiautomator2/adbutils) 内部未带隐藏标志的裸调用。

约束:
  - 禁止 shell=True 启动 ADB(一律 list 形式参数)
  - 运行期禁止依赖 BAT/CMD/PowerShell; PowerShell 仅在自检时
    使用, 且必须 -NoProfile -NonInteractive -WindowStyle Hidden
  - 补丁幂等, 仅 Windows 生效; 测试进程(Linux CI)不受影响
"""
from __future__ import annotations

import functools
import subprocess
import sys
from typing import Any, Callable

# STARTF_USESHOWWINDOW + SW_HIDE 常量(避免依赖 msvcrt 名称差异)
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_CREATE_NO_WINDOW = 0x08000000

_installed = False


def is_windows() -> bool:
    return sys.platform == "win32"


def hidden_startupinfo() -> Any:
    """STARTUPINFO: 窗口隐藏显示(STARTF_USESHOWWINDOW + SW_HIDE)"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= _STARTF_USESHOWWINDOW
    si.wShowWindow = _SW_HIDE
    return si


def hidden_creationflags() -> int:
    """CREATE_NO_WINDOW — 子进程完全不创建控制台窗口"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) or _CREATE_NO_WINDOW
    return flags


def merge_hidden_kwargs(kwargs: dict) -> dict:
    """向 subprocess 调用的 kwargs 注入隐藏标志(不覆盖调用方已有设置)。"""
    if not is_windows():
        return kwargs
    merged = dict(kwargs)
    if "startupinfo" not in merged or merged["startupinfo"] is None:
        merged["startupinfo"] = hidden_startupinfo()
    if "creationflags" not in merged:
        merged["creationflags"] = hidden_creationflags()
    else:
        merged["creationflags"] = (merged["creationflags"] or 0) \
            | hidden_creationflags()
    return merged


def run_hidden_process(
    args: list[str],
    timeout: float = 15,
    check: bool = False,
    input_bytes: bytes | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """隐藏执行子进程(统一入口)。

    args 一律为 list 形式 — 禁止传入 shell 字符串。
    """
    if isinstance(args, str):
        raise TypeError(
            "run_hidden_process 禁止字符串命令(防 shell 注入/CMD 黑框), "
            "请传 list")
    call_kwargs = merge_hidden_kwargs(kwargs)
    if "capture_output" not in call_kwargs:
        call_kwargs["capture_output"] = True
    if input_bytes is not None:
        call_kwargs["input"] = input_bytes
    return subprocess.run(args, timeout=timeout, check=check, **call_kwargs)


def _wrap(name: str) -> None:
    """包装 subprocess.<name>, 默认注入隐藏标志。幂等。"""
    orig = getattr(subprocess, name)

    @functools.wraps(orig)
    def wrapped(*args, **kwargs):
        new_kwargs = merge_hidden_kwargs(kwargs)
        return orig(*args, **new_kwargs)

    setattr(subprocess, name, wrapped)


def install_global_hidden_patch() -> bool:
    """全局补丁: 让所有 subprocess.* 调用(含第三方库内部)默认隐藏窗口。

    返回 True=已安装(或此前已装), False=非 Windows 跳过。
    幂等: 重复调用不会叠加包装。
    """
    global _installed
    if _installed:
        return True
    if not is_windows():
        return False
    for name in ("run", "Popen", "check_output", "check_call", "call"):
        _wrap(name)
    _installed = True
    return True
