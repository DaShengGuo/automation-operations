"""
真机筛选 — Redmi 14C
点评论区 → 确认打开 → 读评论时间 → 新鲜就标记
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
COMMENT_BTN = (0.931, 0.378)
TIME_REGION = (0.50, 0.25, 0.95, 0.90)  # 扩大OCR区域
FRESHNESS = 0.3

def swipe():
    ctrl.d.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
    time.sleep(random.uniform(1.5, 2.5))

logger.info("真机筛选 Ctrl+C 停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}

try:
    while True:
        swipe()
        stats["total"] += 1

        ctrl.base._tap_ratio(*COMMENT_BTN)
        time.sleep(2)

        css = ctrl.base.screenshot(f"c_{int(time.time())}")

        # 确认评论区打开了: OCR找"回复"或"评论"
        check = crop_and_ocr(css, (0.05, 0.20, 0.40, 0.50))
        check_text = ' '.join(check)
        has_comments = any(kw in check_text for kw in ['回复', '评论', '展开', '条回复'])

        if not has_comments:
            stats["nocomment"] += 1
            ctrl.base.d.press('back')
            time.sleep(0.5)
            continue

        # 评论区已打开, 读时间戳
        time_texts = crop_and_ocr(css, TIME_REGION)
        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
        total = len(times)

        # 也检查是否有"刚刚"文字(高新鲜度信号)
        has_just_now = any('刚刚' in t or '秒前' in t for t in time_texts)

        if total == 0 and not has_just_now:
            # OCR没读到时间, 但不代表不新鲜, 保守算过
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} ✅ 有评论(OCR未读到时间)")
        else:
            fresh5 = sum(1 for t in times if t <= 5)
            fresh15 = sum(1 for t in times if t <= 15)
            score = (fresh5/max(total,1))*0.6 + (fresh15/max(total,1))*0.4

            if score >= FRESHNESS or has_just_now:
                stats["pass"] += 1
                logger.info(f"  #{stats['total']} ✅ {total}条 5min:{fresh5} 15min:{fresh15} 评分:{score:.2f}")
            else:
                stats["stale"] += 1

        ctrl.base.d.press('back')
        time.sleep(0.5)

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 无评论={stats['nocomment']} 不新鲜={stats['stale']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终 [{t}]: PASS={stats['pass']} 无评论={stats['nocomment']} 不新鲜={stats['stale']}")
