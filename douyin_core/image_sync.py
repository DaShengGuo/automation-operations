"""
image_sync.py — ADB 图片同步到模拟器
放入 /sdcard/Pictures/douyin_bot/ 并触发媒体索引
"""
from __future__ import annotations

import subprocess
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEST = "/sdcard/DCIM/douyin_bot/"


def sync_images_to_emulator(adb_path: str, device_addr: str, images_dir: str) -> int:
    """
    将本地图片推送到模拟器 Pictures 目录并触发媒体索引。
    返回推送数量。
    """
    src = Path(images_dir)
    if not src.exists():
        logger.warning(f"[图库同步] 目录不存在: {src}")
        return 0

    # 1. 创建目标目录
    subprocess.run(
        [adb_path, "-s", device_addr, "shell", f"mkdir -p {DEST}"],
        capture_output=True, timeout=10,
    )

    # 2. 批量推送所有图片
    logger.info(f"[图库同步] 推送 {src} → {DEST}")
    result = subprocess.run(
        [adb_path, "-s", device_addr, "push", str(src) + "/.", DEST],
        capture_output=True, text=True, timeout=120,
    )
    pushed = result.stdout.count(".jpg") + result.stdout.count(".png")
    logger.info(f"[图库同步] 推送完成 ~{pushed} 张")

    # 3. 逐文件触发媒体扫描（Android 11+ 需要）
    files_out = subprocess.run(
        [adb_path, "-s", device_addr, "shell", f"ls {DEST}"],
        capture_output=True, text=True, timeout=10,
    )
    files = [f.strip() for f in files_out.stdout.split("\n") if "." in f]
    logger.info(f"[图库同步] 触发 {len(files)} 个文件的媒体索引...")

    scanned = 0
    for fname in files:
        path = DEST + fname
        r = subprocess.run(
            [adb_path, "-s", device_addr, "shell",
             "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
             f"-d file://{path}"],
            capture_output=True, text=True, timeout=5,
        )
        if "result=" in r.stdout:
            scanned += 1
    logger.info(f"[图库同步] 已索引 {scanned}/{len(files)} 个文件")
    time.sleep(2)
    return pushed
