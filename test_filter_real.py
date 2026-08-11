"""
真机筛选+发布
"""
import sys, time, random, logging, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("filter_real")

DEVICE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DOUYIN_DEVICE', '')
if not DEVICE:
    print("请指定设备序列号:")
    print("  python test_filter_real.py 设备序列号")
    print("  或设置环境变量: set DOUYIN_DEVICE=设备序列号")
    print("  运行 check-device.bat 查看已连接设备")
    sys.exit(1)
logger.info(f"设备: {DEVICE}")

from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import parse_comment_time
from comment_bot.materials import MaterialManager
from device_profiles import DeviceProfileManager

ctrl = DouyinController(DEVICE)
mm = MaterialManager()
D = ctrl.d
TIME_RE = re.compile(r'(\d+分钟前|\d+小时前|\d+天前|半小时前|刚刚|\d+秒前)')
SW, SH = ctrl.base.screen_w, ctrl.base.screen_h

# 自动解析设备配置（已验证 > 自动计算）
# 使用项目自带的 ADB 路径
ADB_PATH = os.path.join(os.path.dirname(__file__), 'adb', 'platform-tools', 'adb.exe')
DEVICE_PROFILE = DeviceProfileManager.resolve(DEVICE, ADB_PATH)
logger.info(
    f"分辨率: {DEVICE_PROFILE.width}x{DEVICE_PROFILE.height} "
    f"dpi={DEVICE_PROFILE.density} "
    f"已验证={DEVICE_PROFILE.is_verified} "
    f"置信度={DEVICE_PROFILE.confidence:.0%}"
)

def swipe():
    D.swipe(SW//2, int(SH*0.55), SW//2, int(SH*0.25), duration=0.3)
    time.sleep(random.uniform(1.5, 2.5))

def open_comments():
    try:
        el = D.xpath('//*[contains(@content-desc, "评论") and @clickable="true"]')
        if el.exists: el.click(); return
    except: pass
    ctrl.base._tap_ratio(0.931, 0.378)

# ====== 发布(完全复制 test_publish_real.py) ======
def do_publish():
    try:
        # Step1: 获取文案 — 无文案则报错暂停
        cw = mm.pick_copywriting()
        if not cw:
            logger.error("【致命】文案库为空！请在 materials/materials.xlsx 中添加评论文案后重新运行")
            return False

        # Step2: 获取图片 — 有则带图评论，无则纯文本
        pair = mm.pick_image_pair() if hasattr(mm, 'pick_image_pair') else None
        has_images = pair is not None

        # Step3: 打开评论区
        ctrl.nav.open_comments()
        time.sleep(2)

        # Step4: 输入文案
        text = cw['content']
        click_input()
        time.sleep(1); D.send_keys(text); time.sleep(3)

        if has_images:
            # Step5: 点图片按钮
            descs = DEVICE_PROFILE.img_btn_descs
            found_img = False
            if descs:
                for desc in descs:
                    el = D(description=desc)
                    if el.exists: el.click(); found_img = True; logger.info(f'  图片btn: desc={desc}'); break
            if not found_img:
                coord = DEVICE_PROFILE.img_btn_coord
                ctrl.base._tap_ratio(*coord)
                logger.info(f'  图片btn: 坐标{coord}')
            time.sleep(3)

            # Step6: 选第1张图+下一步
            ctrl.base._tap_ratio(*DEVICE_PROFILE.circle1); time.sleep(3)
            for t in ["下一步","下一步(1)","下一步(2)","下一步(3)","下一步(4)"]:
                el = D(text=t)
                if el.exists: el.click(); time.sleep(1); break
            else:
                ctrl.base._tap_ratio(0.50, 0.96); time.sleep(1)
            time.sleep(3)
            if '[图片]' in D.dump_hierarchy():
                logger.info('  第1张图已选')
            else:
                logger.warning('  第1张图未选上!')

            # Step7: 点+号选第2张图
            time.sleep(2)
            ctrl.base._tap_ratio(*DEVICE_PROFILE.plus_btn)
            time.sleep(3)
            ctrl.base._tap_ratio(*DEVICE_PROFILE.circle2); time.sleep(3)
            for t in ["下一步","下一步(1)","下一步(2)","下一步(3)","下一步(4)"]:
                el = D(text=t)
                if el.exists: el.click(); time.sleep(1); break
            else:
                ctrl.base._tap_ratio(0.50, 0.96); time.sleep(1)
            time.sleep(2)

            logger.info(f"  带图评论: {text[:30]}...")
        else:
            logger.info(f"  纯文本评论: {text[:30]}... (图片库无内容)")

        # Step8: 点发送
        if DEVICE_PROFILE.send_btn_coord:
            ctrl.base._tap_ratio(*DEVICE_PROFILE.send_btn_coord)
        else:
            for txt in ["发送","发布"]:
                el = D(text=txt)
                if el.exists: el.click(); break
        time.sleep(2)

        # Step9: 验证
        ok = ctrl.comment.verify_comment_published()
        logger.info(f"  发布:{'成功' if ok else '待确认'}")
        ctrl.nav.close_comments()
        ctrl.comment.reset_keyboard_state(); time.sleep(2)
        return ok
    except Exception as e:
        logger.error(f"发布异常:{e}"); return False

def click_input():
    """点击输入框 — 配置中的资源ID或坐标"""
    if DEVICE_PROFILE.input_coord:
        ctrl.base._tap_ratio(*DEVICE_PROFILE.input_coord); return
    for rid in DEVICE_PROFILE.input_rids:
        el = D(resourceId=f'com.ss.android.ugc.aweme:id/{rid}')
        if el.exists: el.click(); return
    ctrl.base._tap_ratio(0.35, 0.95)

def click_rid(name):
    """旧接口兼容"""
    click_input()
# ====== 发布结束 ======

logger.info("真机筛选+发布 Ctrl+C停止")
stats = {"pass": 0, "nocomment": 0, "stale": 0, "total": 0}
publish_count = 0
first = True

try:
    while True:
        if first: first = False
        else: swipe()
        stats["total"] += 1

        pre_xml = D.dump_hierarchy()
        cc = 0
        for m in re.finditer(r'content-desc="([^"]*)"', pre_xml):
            d = m.group(1)
            cm = re.search(r'评论(\d+\.?\d*万?|\d+)', d)
            if cm:
                s = cm.group(1).replace(',','')
                cc = int(float(s.replace('万',''))*10000) if '万' in s else int(s)
                break
        if cc < 500: continue

        open_comments(); time.sleep(2)

        all_times = []
        bad_dumps = 0
        for i in range(1, 7):
            # 先检查是否已找到足够新鲜评论
            if sum(1 for t in all_times if t <= 30) >= 2: break
            D.swipe(SW//2, int(SH*0.75), SW//2, int(SH*0.45), duration=0.3)
            time.sleep(0.8)
            xml = D.dump_hierarchy()
            if len(xml) < 30000 or '回复' not in xml:
                bad_dumps += 1
                if bad_dumps >= 3: break
                continue
            bad_dumps = 0
            texts = TIME_RE.findall(xml)
            ts = [parse_comment_time(t) for t in texts if parse_comment_time(t) < 99999]
            all_times.extend(ts)

        if not all_times:
            stats["nocomment"] += 1
            D.press('back'); time.sleep(0.5); continue

        times = all_times
        recent = [t for t in times if t <= 30]
        has_now = any(t <= 1 for t in times)

        if len(times) < 2:
            stats["nocomment"] += 1
            D.press('back'); time.sleep(0.5); continue

        if len(recent) >= 2 or has_now:
            stats["pass"] += 1
            logger.info(f"  #{stats['total']} {len(times)}条 30min内:{len(recent)} -> 发布({publish_count}/35)")
            D.press('back'); time.sleep(0.5)
            if do_publish():
                publish_count += 1
                if publish_count >= 20:
                    logger.info("=== 20条,暂停10分钟 ===")
                    time.sleep(600); publish_count = 0
        else:
            stats["stale"] += 1
            D.press('back')
        time.sleep(0.5)

        if stats["total"] % 10 == 0:
            t = stats["total"]
            logger.info(f"--- [{t}] PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")

except KeyboardInterrupt:
    t = max(stats["total"],1)
    logger.info(f"\n最终 [{t}]: PASS={stats['pass']} 无={stats['nocomment']} 旧={stats['stale']}")
