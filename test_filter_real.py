"""
真机筛选+发布 — hierarchy提取评论时间(全量), 新鲜就发布
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time
from comment_bot.materials import MaterialManager

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
D = ctrl.d
mm = MaterialManager()
FRESHNESS = 0.3

# 发布坐标(已确认)
IMG_BTN   = (0.089, 0.904)
PLUS_BTN  = (0.30, 0.76)
SEND_BTN  = (0.89, 0.90)
CIRCLE1   = (0.58, 0.23)
CIRCLE2   = (0.91, 0.23)
TIME_RE   = re.compile(r'(\d+分钟前|\d+小时前|\d+天前|刚刚|\d+秒前)')

def swipe():
    D.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
    time.sleep(random.uniform(1.5, 2.5))

def open_comments():
    try:
        el = D.xpath('//*[contains(@content-desc, "评论") and @clickable="true"]')
        if el.exists: el.click(); return
    except: pass
    ctrl.base._tap_ratio(0.931, 0.378)

def do_publish():
    try:
        open_comments(); time.sleep(2)
        cw = mm.pick_copywriting()
        ctrl.comment.input_comment_text(cw['content']); time.sleep(2)
        for desc in ["插入图片", "图片"]:
            el = D(description=desc)
            if el.exists: el.click(); break
        else: ctrl.base._tap_ratio(*IMG_BTN)
        time.sleep(3)
        ctrl.base._tap_ratio(*CIRCLE1); time.sleep(2)
        for t in ["下一步", "下一步(1)"]:
            if D(text=t).exists: D(text=t).click(); break
        time.sleep(3)
        ctrl.base._tap_ratio(*PLUS_BTN); time.sleep(3)
        ctrl.base._tap_ratio(*CIRCLE2); time.sleep(2)
        for t in ["下一步", "下一步(1)", "下一步(2)"]:
            if D(text=t).exists: D(text=t).click(); break
        time.sleep(2)
        for txt in ["发送", "发布"]:
            if D(text=txt).exists: D(text=txt).click(); break
        else: ctrl.base._tap_ratio(*SEND_BTN)
        time.sleep(2)
        ok = ctrl.comment.verify_comment_published()
        logger.info(f"  发布: {'成功' if ok else '待确认'}")
        D.press('back'); ctrl.comment.reset_keyboard_state(); time.sleep(1)
    except Exception as e:
        logger.error(f"发布异常: {e}")

logger.info("真机筛选+发布 Ctrl+C停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}

first = True
try:
    while True:
        if first:
            first = False
        else:
            swipe()
        stats["total"] += 1

        # 播放页先dump拿评论总数(在按钮desc里)
        pre_xml = D.dump_hierarchy()
        cc_m = re.search(r'评论(\d+\.?\d*万?|\d+)', pre_xml)
        cc = 0
        if cc_m:
            s = cc_m.group(1).replace(',','')
            cc = int(float(s.replace('万',''))*10000) if '万' in s else int(s)
        if cc < 500:
            stats["nocomment"] += 1
            continue

        open_comments()
        time.sleep(2)
        xml = D.dump_hierarchy()

        if '回复' not in xml:
            stats["nocomment"] += 1
            D.press('back'); time.sleep(0.5)
            continue

        time_texts = TIME_RE.findall(xml)[:200]
        if not time_texts:
            css = ctrl.base.screenshot(f"c_{int(time.time())}")
            ocr = crop_and_ocr(css, (0.50, 0.25, 0.95, 0.90))
            time_texts = [t for t in ocr if TIME_RE.match(t)]

        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
        total_t = len(times)
        has_now = any('刚刚' in t or '秒前' in t for t in time_texts)
        # 宽松判断: 只要有任何5分钟内的评论就通过
        has_recent = any(t <= 5 for t in times)

        if has_recent or has_now:
            stats["pass"] += 1
            fresh5 = sum(1 for t in times if t <= 5)
            logger.info(f"  #{stats['total']} {total_t}条时间 5min内:{fresh5} -> 发布")
            D.press('back'); time.sleep(0.5)
            do_publish()
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
