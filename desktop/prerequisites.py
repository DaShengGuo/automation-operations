"""
desktop/prerequisites.py
EnvironmentDoctor / PrerequisiteChecker — 客户机环境自检。

客户机承诺: 无需安装 Python / pip / ADB。唯一可能缺的系统组件是
VC++ 2015-2022 x64 运行库(部分精简版 Windows 缺失会导致 EXE 启动
即崩溃)。

  - vc_runtime_check(): 读注册表检测运行库; 满足 → PASS, 不弹任何窗;
    缺失 → 若打包内置 vc_redist.x64.exe 则静默安装(隐藏执行),
    否则给出明确提示(不假装已修复)。
  - u2_assets_check(): 校验 uiautomator2 资源(u2.jar/app-uiautomator.apk)
    是否随程序发布 — 缺失是「0 设备运行」的直接根因。
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# VC++ 2015-2022 运行库注册表位置(x64 / x86 视图)
_VC_KEYS = [
    (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", "Version"),
    (r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
     "Version"),
    (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86", "Version"),
    (r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x86",
     "Version"),
]


def _registry_query(key_path: str, value: str) -> str:
    """读注册表(64/32 双视图), 失败返回空串。"""
    try:
        import winreg
    except ImportError:
        return ""
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY, 0):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path,
                                0, winreg.KEY_READ | view) as key:
                val, _ = winreg.QueryValueEx(key, value)
                return str(val)
        except OSError:
            continue
    return ""


def _vc_runtime_version() -> str:
    """检测 VC++ 运行库版本字符串, 未安装返回空。"""
    for key_path, value in _VC_KEYS:
        v = _registry_query(key_path, value)
        if v:
            return v
    return ""


def vc_runtime_check() -> tuple[str, bool, str]:
    """→ (名称, ok, 详情)。不弹窗。"""
    ver = _vc_runtime_version()
    if ver:
        return ("VC++运行库", True, f"已安装 (v{ver})")
    redist = _find_bundled_redist()
    if redist is not None:
        if install_vc_runtime_silent(redist):
            ver = _vc_runtime_version()
            if ver:
                return ("VC++运行库", True, f"已自动安装 (v{ver})")
        return ("VC++运行库", False,
                "缺失且自动安装失败 — 请手动安装 VC++ 2015-2022 x64")
    return ("VC++运行库", False,
            "未检测到 VC++ 2015-2022 x64 运行库 — "
            "请安装后重试(EXE 可能无法启动)")


def _find_bundled_redist() -> str | None:
    """打包内置的 VC++ 安装器(如存在): _internal/redist/vc_redist.x64.exe。"""
    candidates = []
    try:
        from desktop.app_paths import resource_root
        candidates.append(resource_root() / "redist" / "vc_redist.x64.exe")
    except Exception:
        pass
    candidates.append(
        Path(__file__).resolve().parent.parent / "packaging" / "redist"
        / "vc_redist.x64.exe")
    for p in candidates:
        if Path(p).exists():
            return str(p)
    return None


def install_vc_runtime_silent(redist: str) -> bool:
    """静默安装 VC++ 运行库(/install /quiet /norestart, 隐藏执行)。

    需要管理员权限; 失败不弹窗, 由自检项报告。
    """
    from desktop.process_runner import run_hidden_process
    try:
        r = run_hidden_process(
            [redist, "/install", "/quiet", "/norestart"], timeout=300)
        return r.returncode in (0, 3010)  # 3010 = 需要重启, 已装成功
    except Exception as e:
        logger.warning("[EnvDoctor] VC++ 静默安装失败: %s", e)
        return False


def u2_assets_check() -> tuple[str, bool, str]:
    """校验 uiautomator2 资源文件随程序发布。

    缺失 = 设备初始化 u2_connect 必失败 → 0 台设备运行。
    """
    assets = _find_u2_assets_dir()
    if assets is None:
        return ("u2资源", False,
                "uiautomator2 资源缺失(u2.jar/app-uiautomator.apk) — "
                "设备无法初始化, 请重新安装")
    missing = [f for f in ("u2.jar", "app-uiautomator.apk")
               if not (Path(assets) / f).exists()]
    if missing:
        return ("u2资源", False, f"uiautomator2 资源不完整: {missing}")
    return ("u2资源", True, f"u2.jar/app-uiautomator.apk 已就绪({assets})")


def _find_u2_assets_dir() -> str | None:
    """定位 uiautomator2 assets 目录(frozen: _internal 下; dev: site-packages)。"""
    try:
        from desktop.app_paths import resource_root
        p = resource_root() / "uiautomator2" / "assets"
        if p.exists():
            return str(p)
    except Exception:
        pass
    try:
        import uiautomator2
        p = Path(uiautomator2.__file__).resolve().parent / "assets"
        if p.exists():
            return str(p)
    except Exception:
        pass
    return None
