"""
真机筛选 — Redmi 14C (720×1640)
先看评论区数量(爆款), 再看新鲜度(最近评论), 达标→启动评论模块
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
FRESHNESS_THRESHOLD = 0.3

# ── 真机坐标 (720×1640) ──
COMMENT_BTN       = (0.931, 0.378)
COMMENT_TIME_REGION = (0.60, 0.25, 0.95, 0.85)

def parse_wan(s):
    """解析'3.3万' '588' '1.2万' → int"""
    s = s.strip().replace(',', '')
    if '万' in s:
        return int(float(s.replace('万', '')) * 10000)
    try: return int(s)
    except: return 0

def get_comment_count(xml):
    """从hierarchy提取评论数"""
    m = re.search(r'评论(\d+\.?\d*万?)', xml)
    if m: return parse_wan(m.group(1))
    # 尝试text
    for m in re.finditer(r'text="(\d+\.?\d*万?)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        x1 = int(m.group(2))
        if x1 > 550:  # 右侧
            return parse_wan(m.group(1))
    return 0

logger.info("真机筛选 — 爆款优先, Ctrl+C 停止")
logger.info("=" * 50)

stats = {"pass": 0, "skip": 0, "live": 0, "total": 0}

try:
    while True:
        # 先不滑动 — 看当前视频数据
        time.sleep(1)
        stats["total"] += 1

        # Dump hierarchy 看点赞/评论数
        xml = ctrl.base.dump_hierarchy()

        # 检测直播
        if '直播' in xml:
            stats["live"] += 1
            # 滑走
            ctrl.d.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
            time.sleep(random.uniform(1.5, 2.5))
            continue

        # 获取评论数
        cc = get_comment_count(xml)
        if cc < 50:  # 评论太少, 跳过
            stats["skip"] += 1
            ctrl.d.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
            time.sleep(random.uniform(1.5, 2.5))
            continue

        logger.info(f"  #{stats['total']} 评论数:{cc} → 检查新鲜度...")

        # 打开评论区, OCR时间戳
        ctrl.base._tap_ratio(*COMMENT_BTN)
        time.sleep(2)
        css = ctrl.base.screenshot(f"comments_real_{int(time.time())}")
        time_texts = crop_and_ocr(css, COMMENT_TIME_REGION)
        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]

        if not times:
            ctrl.base.d.press('back')
            time.sleep(1)
            ctrl.d.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
            time.sleep(random.uniform(1.5, 2.5))
            continue

        fresh5 = sum(1 for t in times if t <= 5)
        fresh15 = sum(1 for t in times if t <= 15)
        total = len(times)
        score = (fresh5/total)*0.6 + (fresh15/total)*0.4

        ctrl.base.d.press('back')
        time.sleep(1)

        if score >= FRESHNESS_THRESHOLD:
            stats["pass"] += 1
            logger.info(f"    ✅ PASS | 评论:{total}条 5min内:{fresh5} 15min内:{fresh15} 评分:{score:.2f}")
        else:
            stats["skip"] += 1
            logger.info(f"    ⏭ 新鲜度不足 评分:{score:.2f}")

        # 滑下一个
        ctrl.d.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
        time.sleep(random.uniform(1.5, 2.5))

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 跳过={stats['skip']} 直播={stats['live']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终: [{t}] PASS={stats['pass']}({stats['pass']*100//t}%) 跳过={stats['skip']} 直播={stats['live']}")
