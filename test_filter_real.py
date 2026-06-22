"""
真机筛选 — 元素定位评论按钮, 不再猜坐标
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
D = ctrl.d
FRESHNESS = 0.3
TIME_REGION = (0.50, 0.25, 0.95, 0.90)

def swipe():
    D.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
    time.sleep(random.uniform(1.5, 2.5))

def open_comments():
    """用元素定位点评论按钮"""
    # 方法1: XPath contains 评论
    try:
        el = D.xpath('//*[contains(@content-desc, "评论") and @clickable="true"]')
        if el.exists:
            el.click()
            return True
    except: pass
    # 方法2: description 子串匹配
    for d in D(descriptionMatches=".*评论.*"):
        try:
            d.click()
            return True
        except: pass
    # 方法3: 坐标兜底
    ctrl.base._tap_ratio(0.931, 0.378)
    return True

logger.info("真机筛选 — 元素定位, Ctrl+C 停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}

try:
    while True:
        swipe()
        stats["total"] += 1

        open_comments()
        time.sleep(2)

        css = ctrl.base.screenshot(f"c_{int(time.time())}")

        # 确认评论区打开了
        check = crop_and_ocr(css, (0.05, 0.20, 0.40, 0.50))
        check_text = ' '.join(check)
        has_comments = any(kw in check_text for kw in ['回复', '评论', '展开', '条回复'])

        if not has_comments:
            stats["nocomment"] += 1
            D.press('back')
            time.sleep(0.5)
            continue

        # 读时间戳
        time_texts = crop_and_ocr(css, TIME_REGION)
        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
        total = len(times)
        has_just_now = any('刚刚' in t or '秒前' in t for t in time_texts)

        if total == 0 and not has_just_now:
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} ✅ 有评论(OCR未读到时间)")
        else:
            fresh5 = sum(1 for t in times if t <= 5)
            fresh15 = sum(1 for t in times if t <= 15)
            score = (fresh5/max(total,1))*0.6 + (fresh15/max(total,1))*0.4
            if score >= FRESHNESS or has_just_now:
                stats["pass"] += 1
                logger.info(f"  #{stats['total']} ✅ {total}条 5min:{fresh5} 评分:{score:.2f}")
            else:
                stats["stale"] += 1

        D.press('back')
        time.sleep(0.5)

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终 [{t}]: PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")
