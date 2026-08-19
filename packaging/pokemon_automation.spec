# -*- mode: python ; coding: utf-8 -*-
"""
packaging/pokemon_automation.spec
PyInstaller 打包配置 — 宝可梦自动化购买脚本 (Release windowed)

产物: dist/宝可梦自动化购买脚本/宝可梦自动化购买脚本.exe
原则:
  - 客户无需安装 Python
  - ADB(adb.exe/AdbWinApi.dll/AdbWinUsbApi.dll) 随程序发布
  - 程序资源(config/templates/OCR模型) 打包进 _internal
  - 运行数据绝不写入 _MEIPASS(只读资源目录)
"""
import sys
from pathlib import Path

import version as ver

PROJECT_ROOT = Path.cwd()  # spec 由根目录运行
ADB_DIR = PROJECT_ROOT / "adb" / "platform-tools"

# rapidocr 模型(wheel 内置, 离线可用) — 客户版 OCR 关闭根因:
# 只打了 models, 缺 default_models.yaml/config.yaml → 初始化即失败。
# 两个引擎都完整打包(rapidocr 主包 PP-OCRv6 优先, onnxruntime 兜底)。
import rapidocr_onnxruntime
import os as _os
_RAPIDOCR_ONNX_PKG = Path(_os.path.dirname(rapidocr_onnxruntime.__file__))
import rapidocr as _rapidocr
_RAPIDOCR_PKG = Path(_os.path.dirname(_rapidocr.__file__))

# uiautomator2 资源(u2.jar/app-uiautomator.apk) — 必须随程序发布:
# 缺失会导致 u2.connect 抛 "Resource assets/u2.jar not found",
# 设备初始化全部失败 → GUI 显示 0 台设备运行(客户版 BUG 根因)。
import uiautomator2 as _u2
U2_ASSETS = Path(_os.path.dirname(_u2.__file__)) / "assets"

datas = [
    (str(PROJECT_ROOT / "config"), "config"),
    (str(PROJECT_ROOT / "templates"), "templates"),
    # rapidocr 主包(PP-OCRv6): yaml + 模型 + 网络架构配置
    (str(_RAPIDOCR_PKG / "default_models.yaml"), "rapidocr"),
    (str(_RAPIDOCR_PKG / "config.yaml"), "rapidocr"),
    (str(_RAPIDOCR_PKG / "models"), "rapidocr/models"),
    (str(_RAPIDOCR_PKG / "inference_engine" / "pytorch" / "networks"
         / "arch_config.yaml"),
     "rapidocr/inference_engine/pytorch/networks"),
    # rapidocr_onnxruntime 兜底引擎(PP-OCRv4): yaml + 模型
    (str(_RAPIDOCR_ONNX_PKG / "config.yaml"), "rapidocr_onnxruntime"),
    (str(_RAPIDOCR_ONNX_PKG / "models"), "rapidocr_onnxruntime/models"),
]
if ADB_DIR.exists():
    datas.append((str(ADB_DIR), "adb/platform-tools"))
if U2_ASSETS.exists():
    datas.append((str(U2_ASSETS), "uiautomator2/assets"))

# 可选: VC++ 运行库随包分发(存在时打包, 客户机缺失时静默安装)
VC_REDIST = PROJECT_ROOT / "packaging" / "redist" / "vc_redist.x64.exe"
if VC_REDIST.exists():
    datas.append((str(VC_REDIST), "redist"))

hiddenimports = [
    # 项目内部
    "automation.pokemon_go.adapter",
    "automation.pokemon_go.detector",
    "automation.pokemon_go.logout",
    "automation.pokemon_go.recovery",
    "automation.pokemon_go.selectors",
    "automation.pokemon_go.shop",
    "automation.pokemon_go.states",
    "automation.pokemon_go.web_context",
    "core.adb_manager",
    "core.device_manager",
    "core.device_worker",
    "core.task_scheduler",
    "core.qq_provider",
    "core.watchdog",
    # v1.2.0 人工按设备账号队列
    "core.account_queues",
    "core.bulk_parser",
    "desktop.app",
    "desktop.controller",
    "desktop.main_window",
    "desktop.history_dialog",
    "desktop.widgets.device_card",
    "desktop.widgets.batch_dialog",
    "desktop.widgets.edit_dialog",
    # 设备整改模块(动态导入/依赖链, 显式声明防漏)
    "desktop.process_runner",
    "desktop.adb_locator",
    "desktop.device_registry",
    "desktop.device_monitor",
    "desktop.prerequisites",
    "desktop.frozen_compat",
    "migrations",
    "version",
    # 依赖库动态加载部分
    "uiautomator2",
    "adbutils",
    "rapidocr_onnxruntime",
    "yaml",
    "pandas",
    "openpyxl",
]

a = Analysis(
    [str(PROJECT_ROOT / "desktop" / "app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 桌面版不需要 Web API/测试框架
        "api", "fastapi", "uvicorn", "starlette",
        "pytest", "paddleocr", "paddle",
        "tkinter", "matplotlib",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=ver.APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                # Release: 不显示 CMD
    icon=str(PROJECT_ROOT / "packaging" / "icon.ico"),
    version=str(PROJECT_ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=ver.APP_NAME,            # dist/宝可梦自动化购买脚本/
)
