"""
真机筛选测试 — Redmi 14C (720×1640)
刷视频→OCR→关键词筛选→评论区新鲜度
坐标来自视觉分析
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
FRESHNESS_THRESHOLD = 0.3  # 新鲜度阈值

# ── 真机坐标 (720×1640, 视觉分析) ──
COMMENT_BTN    = (0.931, 0.378)
COMMENT_TIME_REGION = (0.60, 0.25, 0.95, 0.85)

logger.info("真机筛选 — 仅看评论区新鲜度, Ctrl+C 停止")
logger.info("=" * 50)

stats = {"pass": 0, "skip": 0, "live": 0, "total": 0}

def calc_freshness(times):
    if not times: return 0
    return (sum(1 for t in times if t<=5)/len(times))*0.6 + (sum(1 for t in times if t<=15)/len(times))*0.4

try:
    while True:
        # 滑动
        sx, sy = int(360), int(1640 * 0.55)
        ex, ey = int(360), int(1640 * 0.25)
        ctrl.d.swipe(sx, sy, ex, ey, duration=0.3)
        time.sleep(random.uniform(1.5, 2.5))
        stats["total"] += 1

        # 检测直播
        ss = ctrl.base.screenshot(f"filter_real_{int(time.time())}")
        try:
            qc = crop_and_ocr(ss, (0.02, 0.05, 0.25, 0.12))
            if any('直播' in t for t in qc):
                stats["live"] += 1
                continue
        except: pass

        # 打开评论区, 看新鲜度
        ctrl.base._tap_ratio(*COMMENT_BTN)
        time.sleep(2)
        css = ctrl.base.screenshot(f"comments_real_{int(time.time())}")
        time_texts = crop_and_ocr(css, COMMENT_TIME_REGION)
        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
        score = calc_freshness(times)
        fresh5 = sum(1 for t in times if t <= 5)
        ctrl.base.d.press('back')
        time.sleep(1)

        if score >= FRESHNESS_THRESHOLD:
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} ✅ | {len(times)}条评论 5min内:{fresh5} 评分:{score:.2f}")
        else:
            stats["skip"] += 1

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 跳过={stats['skip']} 直播={stats['live']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终: [{t}] PASS={stats['pass']}({stats['pass']*100//t}%) 跳过={stats['skip']} 直播={stats['live']}")
