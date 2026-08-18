"""
desktop/frozen_compat.py
PyInstaller 冻结环境兼容补丁(仅 frozen 生效)。

Bug②(0 设备运行)的打包层根治: uiautomator2 通过
importlib.resources 定位 assets/u2.jar 与 assets/app-uiautomator.apk。
spec 已把 assets 捆绑进 _internal/uiautomator2/assets, 此处再补一层
确定性保障 — 冻结运行时直接优先读捆绑目录, 不依赖 PyInstaller
对 importlib.resources 的支持行为。

调用: desktop/app.py main() 最早期, 任何设备操作之前。
"""
from __future__ import annotations

import contextlib
import logging
import sys

logger = logging.getLogger(__name__)


def apply_frozen_patches() -> None:
    if not getattr(sys, "frozen", False):
        return

    # uiautomator2 资源定位 → 优先捆绑目录(_internal/uiautomator2/assets)
    try:
        from desktop.app_paths import resource_root
        from pathlib import Path
        assets_dir = Path(resource_root()) / "uiautomator2" / "assets"

        import uiautomator2.utils as _u2utils
        _orig = _u2utils.with_package_resource

        @contextlib.contextmanager
        def _with_bundled_resource(filename: str):
            local = assets_dir / filename
            if local.exists():
                yield local
                return
            with _orig(filename) as f:
                yield f

        # u2 各模块以 `from uiautomator2.utils import ...` 方式持有引用,
        # 需逐模块替换; utils 本身也替换, 覆盖后续新引用。
        _u2utils.with_package_resource = _with_bundled_resource
        for mod_name in ("uiautomator2.core", "uiautomator2._input"):
            try:
                import importlib
                mod = importlib.import_module(mod_name)
                mod.with_package_resource = _with_bundled_resource
            except Exception as e:
                logger.debug("[Frozen] %s 补丁跳过: %s", mod_name, e)
        logger.info("[Frozen] uiautomator2 资源定位补丁已安装: %s",
                    assets_dir)
    except Exception as e:
        logger.warning("[Frozen] uiautomator2 资源补丁失败: %s", e)
