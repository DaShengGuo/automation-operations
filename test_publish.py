"""
发布功能测试 — 严格按步骤执行，5次循环
步骤：
  1. 打开评论区
  2. 点输入框 → 输入文案
  3. 点图片按钮 → 选2张对比图 → 下一步
  4. 点发送
  5. 等15秒 → 验证是否发布成功
"""
import sys
import time
import random
import logging
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from douyin_core import config as cfg
from douyin_core.adb_controller import DouyinController
from comment_bot.materials import MaterialManager

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("test_publish")

ctrl = DouyinController()
mm = MaterialManager()

# ── 推送图片到模拟器 ──
import subprocess
logger.info("推送图片到模拟器...")
dest = "/sdcard/DCIM/douyin_bot/"
result = subprocess.run(
    [cfg.ADB_EXECUTABLE, "-s", cfg.MUMU_ADB_ADDR, "shell", f"mkdir -p {dest}"],
    capture_output=True, timeout=10
)
result = subprocess.run(
    [cfg.ADB_EXECUTABLE, "-s", cfg.MUMU_ADB_ADDR, "push",
     str(cfg.MATERIALS_DIR / "images") + "/.", dest],
    capture_output=True, text=True, timeout=60
)
count = result.stdout.count(".jpg") + result.stdout.count(".png")
subprocess.run(
    [cfg.ADB_EXECUTABLE, "-s", cfg.MUMU_ADB_ADDR, "shell",
     "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://" + dest],
    capture_output=True, timeout=10
)
logger.info(f"推送完成 ~{count} 张图片")
time.sleep(2)

def step(name):
    logger.info(f"{'='*40}")
    logger.info(f">>> {name}")
    logger.info(f"{'='*40}")

for loop in range(1, 6):
    logger.info(f"\n{'#'*50}")
    logger.info(f"###  第 {loop}/5 次发布  ###")
    logger.info(f"{'#'*50}")

    # ── Step 1: 打开评论区 ──
    step("Step 1: 打开评论区")
    ctrl.nav.open_comments()
    time.sleep(2)

    # ── Step 2: 点输入框 + 输入文案 ──
    step("Step 2: 点输入框 + 输入文案")
    cw = mm.pick_copywriting()
    logger.info(f"  文案: {cw['content'][:50]}...")
    ctrl.comment.input_comment_text(cw['content'])
    time.sleep(2)

    # ── Step 3: 点图片按钮 + 选图 ──
    step("Step 3: 点图片按钮 → 选2张对比图")
    pair = mm.pick_image_pair()
    logger.info(f"  图片: {pair['before_path']} + {pair['after_path']}")
    ctrl.comment.add_comment_images([
        str(cfg.MATERIALS_DIR / pair['before_path']),
        str(cfg.MATERIALS_DIR / pair['after_path']),
    ])
    time.sleep(3)

    # ── Step 4: 点发送 ──
    step("Step 4: 点发送")
    ctrl.comment.submit_comment()
    time.sleep(2)

    # ── Step 5: 等2秒验证 ──
    step("Step 5: 等2秒 → 验证是否发布成功")
    time.sleep(2)
    verified = ctrl.comment.verify_comment_published()
    if verified:
        logger.info("  >>> 发布成功！<<<")
    else:
        logger.warning("  >>> 发布可能失败，检查截图 <<<")
        ss = ctrl.base.screenshot(f"publish_check_{loop}")
        logger.info(f"  截图保存: {ss}")

    # 关闭评论区，重置键盘状态，准备下一次
    ctrl.nav.close_comments()
    ctrl.comment.reset_keyboard_state()
    time.sleep(2)

logger.info("\n===== 5次发布测试完成 =====")
