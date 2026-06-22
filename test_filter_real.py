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

logger.info("真机筛选测试 — Ctrl+C 停止")
logger.info(f"排除:{len(vf.exclude_keywords)} 目标:{len(vf.target_keywords)}")
logger.info("=" * 50)

stats = {"pass": 0, "fresh_skip": 0, "total": 0}

try:
    while True:
        # 滑动下一个视频
        sx, sy = int(360), int(1640 * 0.55)
        ex, ey = int(360), int(1640 * 0.25)
        ctrl.d.swipe(sx, sy, ex, ey, duration=0.3)
        time.sleep(random.uniform(1.5, 2.5))
        stats["total"] += 1

        # === 第0步: 检测直播 ===
        ss = ctrl.base.screenshot(f"filter_real_{int(time.time())}")
        try:
            quick_check = crop_and_ocr(ss, (0.02, 0.05, 0.25, 0.12))
            if any('直播' in t for t in quick_check):
                stats["total"] -= 1
                continue
        except: pass

        # === 第1步: 视频内容筛选(主要) ===
        try:
            texts = crop_and_ocr(ss, TITLE_REGION)
        except:
            texts = []
        result = vf.check_content(ss)
        ocr_preview = " | ".join(texts[:2]) if texts else "(无OCR)"

        if result == FilterResult.PASS:
            # 内容命中 → 直接算通过
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} ✅内容 | OCR:{ocr_preview[:60]}")
            continue

        # === 第2步: 内容未命中, 检查评论区新鲜度(兜底) ===
        ctrl.base._tap_ratio(*COMMENT_BTN)
        time.sleep(2)
        css = ctrl.base.screenshot(f"comments_real_{int(time.time())}")
        time_texts = crop_and_ocr(css, COMMENT_TIME_REGION)
        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
        score = vf.calc_freshness_score(times) if times else 0
        fresh = sum(1 for t in times if t <= 5)
        ctrl.base.d.press('back')
        time.sleep(1)

        if vf.should_comment(score) == FilterResult.PASS:
            # 新鲜度通过 → 也算目标
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} ✅新鲜 | 评论:{len(times)}条 新鲜:{fresh} 评分:{score:.2f} | OCR:{ocr_preview[:60]}")
        else:
            stats["fresh_skip"] += 1

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']}({stats['pass']*100//t}%) 跳过={stats['fresh_skip']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终: [{t}] PASS={stats['pass']}({stats['pass']*100//t}%) 跳过={stats['fresh_skip']}")
