"""
真机筛选+发布 — 评论区有2条以上5分钟内评论就发布
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import parse_comment_time
from comment_bot.materials import MaterialManager

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
D = ctrl.d
mm = MaterialManager()
TIME_RE = re.compile(r'(\d+分钟前|\d+小时前|\d+天前|半小时前|刚刚|\d+秒前)')

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
    """发布(已确认流程)"""
    try:
        open_comments(); time.sleep(2)
        cw1 = mm.pick_copywriting()
        cw2 = mm.pick_copywriting()
        text = cw1['content'] + '\n' + cw2['content']
        el = D(className='android.widget.EditText')
        if el.exists: el.click()
        time.sleep(1); D.send_keys(text); time.sleep(2)
        for desc in ["插入图片","图片"]:
            el = D(description=desc)
            if el.exists: el.click(); break
        else: ctrl.base._tap_ratio(0.089, 0.904)
        time.sleep(3)
        ctrl.base._tap_ratio(0.58, 0.23); time.sleep(3)
        for t in ["下一步","下一步(1)","下一步(2)","下一步(3)","下一步(4)"]:
            el = D(text=t)
            if el.exists: el.click(); time.sleep(1); break
        else:
            ctrl.base._tap_ratio(0.50, 0.96)
        time.sleep(3)
        ctrl.base._tap_ratio(0.30, 0.76); time.sleep(3)
        ctrl.base._tap_ratio(0.91, 0.23); time.sleep(3)
        for t in ["下一步","下一步(1)","下一步(2)","下一步(3)","下一步(4)"]:
            el = D(text=t)
            if el.exists: el.click(); time.sleep(1); break
        else:
            ctrl.base._tap_ratio(0.50, 0.96)  # 坐标兜底
            time.sleep(1)
        time.sleep(3)
        for txt in ["发送","发布"]:
            el = D(text=txt)
            if el.exists: el.click(); break
        else: ctrl.base._tap_ratio(0.89, 0.90)
        time.sleep(2)
        ok = ctrl.comment.verify_comment_published()
        logger.info(f"  发布:{'成功' if ok else '待确认'}")
        ctrl.nav.close_comments()
        ctrl.comment.reset_keyboard_state()
        time.sleep(2)
    except Exception as e:
        logger.error(f"发布异常:{e}")

logger.info("真机筛选+发布 Ctrl+C停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}
first = True

try:
    while True:
        if first: first = False
        else: swipe()
        stats["total"] += 1

        open_comments(); time.sleep(2)

        # 滚动评论区6次
        for _ in range(6):
            D.swipe(360, int(1640*0.75), 360, int(1640*0.45), duration=0.3)
            time.sleep(0.8)

        xml = D.dump_hierarchy()

        # 健康检查: hierarchy太短说明服务断了, 重启
        if len(xml) < 30000:
            logger.warning(f"hierarchy异常({len(xml)}chars), 重启u2服务...")
            try: D.press('back')
            except: pass
            try:
                import uiautomator2 as u2
                u2.connect('AQV4TSDY9PCEIZ8L').reset_uiautomator()
                time.sleep(3)
            except: pass
            continue

        if '回复' not in xml:
            stats["nocomment"] += 1
            D.press('back'); time.sleep(0.5)
            continue

        # 提取时间, 统计30分钟内的
        time_texts = TIME_RE.findall(xml)
        times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]
        recent = [t for t in times if t <= 30]
        has_now = any('刚刚' in t or '秒前' in t for t in time_texts)

        if len(recent) >= 2 or has_now:
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} {len(times)}条时间 30min内:{len(recent)} -> 发布")
            D.press('back'); time.sleep(0.5)
            do_publish()
        else:
            stats["stale"] += 1
            logger.info(f"  #{stats['total']} {len(times)}条时间 30min内:{len(recent)} 跳过")
            D.press('back')
        time.sleep(0.5)

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")

except KeyboardInterrupt:
    t = max(stats["total"],1)
    logger.info(f"\n最终 [{t}]: PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")
