"""
真机筛选+发布 — 评论区有2条以上5分钟内评论就发布
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

# 设备序列号: 命令行参数 > 环境变量 > 默认值
DEVICE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DOUYIN_DEVICE', 'AQV4TSDY9PCEIZ8L')
logger.info(f"设备: {DEVICE}")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import parse_comment_time
from comment_bot.materials import MaterialManager

ctrl = DouyinController(DEVICE)
D = ctrl.d
mm = MaterialManager()
TIME_RE = re.compile(r'(\d+分钟前|\d+小时前|\d+天前|半小时前|刚刚|\d+秒前)')
SW, SH = ctrl.base.screen_w, ctrl.base.screen_h
logger.info(f"分辨率: {SW}x{SH}")

def swipe():
    D.swipe(SW//2, int(SH*0.55), SW//2, int(SH*0.25), duration=0.3)
    time.sleep(random.uniform(1.5, 2.5))

def open_comments():
    try:
        el = D.xpath('//*[contains(@content-desc, "评论") and @clickable="true"]')
        if el.exists: el.click(); return
    except: pass
    ctrl.base._tap_ratio(0.931, 0.378)

def do_publish():
    """发布(评论区已打开, 直接发布)"""
    try:
        cw1 = mm.pick_copywriting()
        cw2 = mm.pick_copywriting()
        while cw2['content'] == cw1['content']:
            cw2 = mm.pick_copywriting()
        text = cw1['content'] + '\n' + cw2['content']
        el = D(className='android.widget.EditText')
        if el.exists: el.click()
        time.sleep(1); D.send_keys(text); time.sleep(2)
        ctrl.base._tap_ratio(0.089, 0.904)  # 图片按钮坐标
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
        return ok
    except Exception as e:
        logger.error(f"发布异常:{e}")
        return False

logger.info("真机筛选+发布 Ctrl+C停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}
publish_count = 0
first = True

try:
    while True:
        if first: first = False
        else: swipe()
        stats["total"] += 1

        # 播放页: 从content-desc提取评论数
        pre_xml = D.dump_hierarchy()
        cc = 0
        for m in re.finditer(r'content-desc="([^"]*)"', pre_xml):
            d = m.group(1)
            cm = re.search(r'评论(\d+\.?\d*万?|\d+)', d)
            if cm:
                s = cm.group(1).replace(',','')
                cc = int(float(s.replace('万',''))*10000) if '万' in s else int(s)
                break
        if cc < 500:
            continue

        open_comments(); time.sleep(2)

        # 滚动+dump+累加时间 (每次滚动都dump, 收集所有评论时间)
        all_times = []
        for i in range(1, 7):
            D.swipe(SW//2, int(SH*0.75), SW//2, int(SH*0.45), duration=0.3)
            time.sleep(0.8)
            xml = D.dump_hierarchy()
            # 健康检查
            if len(xml) < 30000:
                if i == 1:
                    logger.warning(f"hierarchy异常({len(xml)}chars)")
                continue
            if '回复' not in xml:
                continue
            texts = TIME_RE.findall(xml)
            ts = [parse_comment_time(t) for t in texts if parse_comment_time(t) < 99999]
            all_times.extend(ts)

        if not all_times:
            stats["nocomment"] += 1
            D.press('back'); time.sleep(0.5)
            continue

        # 统计30分钟内的
        times = all_times
        recent = [t for t in times if t <= 30]
        has_now = any(t <= 1 for t in times)  # 1分钟内有评论=高新鲜

        # 时间太少=评论区不活跃, 跳过
        if len(times) < 6:
            stats["nocomment"] += 1
            D.press('back'); time.sleep(0.5)
            continue

        if len(recent) >= 6 or has_now:
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} {len(times)}条时间 30min内:{len(recent)} -> 发布(已发布{publish_count}次)")
            if do_publish():
                publish_count += 1
                logger.info(f"  累计发布: {publish_count}/35")
                if publish_count >= 35:
                    logger.info(f"  === 已达35条, 暂停10分钟 ===")
                    time.sleep(600)
                    publish_count = 0
                    logger.info(f"  === 暂停结束, 继续 ===")
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
