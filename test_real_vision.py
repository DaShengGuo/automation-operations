"""
真机测试 — 每步截图后用视觉 AI 定位按钮
需要 zhipu-vision MCP (已配置)
"""
import sys, time, logging, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("vision")

from douyin_core.adb_controller import DouyinController
from comment_bot.materials import MaterialManager

DEVICE = os.environ.get('DOUYIN_DEVICE', '')
if not DEVICE:
    print("请设置 DOUYIN_DEVICE 环境变量")
    sys.exit(1)
ctrl = DouyinController(DEVICE)
mm = MaterialManager()

screenshot_dir = os.path.join(os.path.dirname(__file__), 'data', 'screenshots')

def shot(name):
    path = ctrl.base.screenshot(name)
    logger.info(f"截图: {path}")
    return path

# === Step 1: 打开评论区 ===
logger.info("Step1: 打开评论区")
ctrl.nav.open_comments()
time.sleep(2)
shot("step1_comments")

# === Step 2: 输入文案 ===
logger.info("Step2: 输入文案")
cw = mm.pick_copywriting()
ctrl.comment.input_comment_text(cw['content'])
time.sleep(2)
shot("step2_text")

# === Step 3: 点图片按钮 ===
logger.info("Step3: 找图片按钮")
# 尝试多种位置
for attempt, (x, y) in enumerate([
    (0.078, 0.904),  # 真机dump位置
    (0.05, 0.90),     # 靠近左边
    (0.10, 0.94),     # 稍下
]):
    ctrl.base._tap_ratio(x, y)
    logger.info(f"  尝试({x:.3f},{y:.3f})")
    time.sleep(2)
    shot(f"step3_attempt{attempt}")
logger.info("请查看截图 step3_attempt* — 哪张打开了相册?")
