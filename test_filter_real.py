"""
真机筛选+发布 — hierarchy提取评论时间, 新鲜就发布
发布流程完全复制自 test_publish_real.py(已确认)
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
TIME_RE = re.compile(r'(\d+分钟前|\d+小时前|\d+天前|刚刚|\d+秒前)')

def swipe():
    D.swipe(360, int(1640*0.55), 360, int(1640*0.25), duration=0.3)
    time.sleep(random.uniform(1.5, 2.5))

def open_comments():
    try:
        el = D.xpath('//*[contains(@content-desc, "评论") and @clickable="true"]')
        if el.exists: el.click(); return
    except: pass
    ctrl.base._tap_ratio(0.931, 0.378)

# ====== 发布流程(复制自 test_publish_real.py 确认版) ======
def do_publish():
    try:
        open_comments(); time.sleep(2)
        cw = mm.pick_copywriting()
        el = D(className='android.widget.EditText')
        if el.exists: el.click()
        time.sleep(1); D.send_keys(cw['content']); time.sleep(2)
        for desc in ["插入图片", "图片"]:
            el = D(description=desc)
            if el.exists: el.click(); break
        else: ctrl.base._tap_ratio(0.089, 0.904)
        time.sleep(3)
        ctrl.base._tap_ratio(0.58, 0.23); time.sleep(2)
        for t in ["下一步","下一步(1)","下一步(2)","下一步(3)"]:
            el = D(text=t);
            if el.exists: el.click(); break
        time.sleep(3)
        ctrl.base._tap_ratio(0.30, 0.76); time.sleep(3)
        ctrl.base._tap_ratio(0.91, 0.23); time.sleep(2)
        for t in ["下一步","下一步(1)","下一步(2)","下一步(3)"]:
            el = D(text=t)
            if el.exists: el.click(); break
        time.sleep(3)
        for txt in ["发送","发布"]:
            el = D(text=txt)
            if el.exists: el.click(); break
        else: ctrl.base._tap_ratio(0.89, 0.90)
        time.sleep(2)
        ok = ctrl.comment.verify_comment_published()
        logger.info(f"  发布: {'成功' if ok else '待确认'}")
        ctrl.nav.close_comments()
        ctrl.comment.reset_keyboard_state()
        time.sleep(2)
    except Exception as e:
        logger.error(f"发布异常: {e}")
# ====== 发布流程结束 ======

logger.info("真机筛选+发布 Ctrl+C停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}
first = True

try:
    while True:
        if first: first = False
        else: swipe()
        stats["total"] += 1

        # 播放页拿评论数
        pre_xml = D.dump_hierarchy()
        cc_m = re.search(r'评论(\d+\.?\d*万?|\d+)', pre_xml)
        cc = 0
        if cc_m:
            s = cc_m.group(1).replace(',','')
            cc = int(float(s.replace('万',''))*10000) if '万' in s else int(s)
        if cc < 500:
            stats["nocomment"] += 1
            if cc > 0: logger.info(f"  #{stats['total']} 评论仅{cc}条,跳过")
            continue

        open_comments(); time.sleep(2)
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
        has_now = any('刚刚' in t or '秒前' in t for t in time_texts)
        has_recent = any(t <= 5 for t in times)

        if has_recent or has_now:
            stats["pass"] += 1
            fresh5 = sum(1 for t in times if t <= 5)
            logger.info(f"  #{stats['total']} 评论{cc} {len(times)}时间 5min内:{fresh5} -> 发布")
            D.press('back'); time.sleep(0.5)
            do_publish()
        else:
            stats["stale"] += 1
            logger.info(f"  #{stats['total']} 评论{cc} {len(times)}时间 无新鲜 -> 跳过")
            D.press('back')
        time.sleep(0.5)

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")

except KeyboardInterrupt:
    t = max(stats["total"], 1)
    logger.info(f"\n最终 [{t}]: PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")
