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
from comment_bot.filter import VideoFilter, FilterResult

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
vf = VideoFilter()

# ── 真机坐标 (720×1640, 视觉分析) ──
COMMENT_BTN    = (0.931, 0.378)   # 评论按钮(右侧)
TITLE_REGION   = (0.04, 0.46, 0.99, 0.54)  # 视频标题OCR区域
COMMENT_TIME_REGION = (0.60, 0.25, 0.95, 0.85)  # 评论区时间戳

ctrl.nav.open_recommend_tab()
time.sleep(2)

logger.info("真机筛选测试 — Ctrl+C 停止")
logger.info(f"排除:{len(vf.exclude_keywords)} 目标:{len(vf.target_keywords)}")
logger.info("=" * 50)

stats = {"pass": 0, "excluded": 0, "irrelevant": 0, "total": 0}

try:
    while True:
        # 滑动下一个视频
        sx, sy = int(360), int(1640 * 0.55)
        ex, ey = int(360), int(1640 * 0.25)
        ctrl.d.swipe(sx, sy, ex, ey, duration=0.3)
        time.sleep(random.uniform(1.5, 2.5))
        stats["total"] += 1

        # 截图
        ss = ctrl.base.screenshot(f"filter_real_{int(time.time())}")

        # OCR 标题
        try:
            texts = crop_and_ocr(ss, TITLE_REGION)
        except:
            texts = []

        # 筛选
        result = vf.check_content(ss)

        ocr_preview = " | ".join(texts[:2]) if texts else "(无OCR)"
        if result == FilterResult.PASS:
            stats["pass"] += 1
            # 打开评论区检查新鲜度
            ctrl.base._tap_ratio(*COMMENT_BTN)
            time.sleep(2)
            css = ctrl.base.screenshot(f"comments_real_{int(time.time())}")
            time_texts = crop_and_ocr(css, COMMENT_TIME_REGION)
            times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
            score = vf.calc_freshness_score(times) if times else 0
            fresh = sum(1 for t in times if t <= 5)
            logger.info(f"  #{stats['total']} PASS | OCR:{ocr_preview[:60]} | 评论:{len(times)} 新鲜:{fresh} 评分:{score:.2f}")
            ctrl.base.d.press('back')  # 关评论区
            time.sleep(1)
        elif result == FilterResult.SKIP_EXCLUDED:
            stats["excluded"] += 1
            logger.info(f"  #{stats['total']} EXCL | OCR:{ocr_preview[:60]}")
        else:
            stats["irrelevant"] += 1

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']}({stats['pass']*100//t}%) EXCL={stats['excluded']} IREL={stats['irrelevant']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终: [{t}] PASS={stats['pass']}({stats['pass']*100//t}%) EXCL={stats['excluded']}")
