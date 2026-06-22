"""
真机发布测试 — Redmi 14C (720×1640)
使用 resource-id 定位 (dump确认和模拟器一样):
  图片按钮: rid=iv_image (desc=插入图片) 或 rid=h66 (底部左上)
  +号: 已选图后 rid=iv_image
  发送: rid=evj 或 text=发送
  输入框: rid=erc (EditText)
"""
import sys, time, os, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("real")

from douyin_core.adb_controller import DouyinController
from comment_bot.materials import MaterialManager

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
mm = MaterialManager()
D = ctrl.d  # uiautomator2 device

try:
    import uiautomator2 as u2
    u2.connect('AQV4TSDY9PCEIZ8L').set_fastinput_ime(True)
except: pass

# Resource IDs (真机dump确认)
RID = "com.ss.android.ugc.aweme:id"

def click_rid(name, timeout=3):
    """通过 resource-id 点击元素"""
    el = D(resourceId=f"{RID}/{name}")
    if el.exists:
        el.click()
        time.sleep(1)
        return True
    return False

def find_and_click(*rids):
    """依次尝试多个 resource-id"""
    for name in rids:
        if click_rid(name):
            logger.info(f"  点击 rid={name}")
            return True
    return False


for loop in range(1, 6):
    logger.info(f"\n### 第{loop}/5次 ###")

    # Step1: 打开评论区
    logger.info("Step1: 打开评论区")
    ctrl.nav.open_comments()
    time.sleep(2)

    # Step2: 点输入框 + 输入文案
    logger.info("Step2: 输入文案")
    cw = mm.pick_copywriting()
    click_rid("erc")  # EditText
    time.sleep(1)
    D.send_keys(cw['content'])
    time.sleep(2)

    # Step3: 点图片按钮 (desc=插入图片, 无rid, 坐标0.09,0.90)
    logger.info("Step3: 点图片按钮")
    found = False
    for desc in ["插入图片", "图片"]:
        el = D(description=desc)
        if el.exists: el.click(); found = True; break
    if not found:
        ctrl.base._tap_ratio(0.09, 0.90)  # 真机dump坐标
    time.sleep(3)

    # Step4: 选第1张图 (col1=0.50, row≈0.28 右上角圆圈 +0.08,-0.05)
    logger.info("Step4: 选第1张图+下一步")
    ctrl.base._tap_ratio(0.58, 0.23)  # 第一张图右上角圆圈
    time.sleep(1.5)
    for t in ["下一步", "下一步(1)", "下一步(2)"]:
        if D(text=t).exists: D(text=t).click(); break
    time.sleep(3)

    # Step5: 点+号 (rid=iv_image, 坐标0.69,0.95)
    logger.info("Step5: 点+号")
    if not find_and_click("iv_image"):
        ctrl.base._tap_ratio(0.69, 0.95)
    time.sleep(3)

    # Step6: 选第2张图 (col2=0.83, row≈0.28 右上角圆圈)
    logger.info("Step6: 选第2张图+下一步")
    ctrl.base._tap_ratio(0.91, 0.23)  # 第二张图右上角圆圈
    time.sleep(1.5)
    for t in ["下一步", "下一步(1)", "下一步(2)"]:
        if D(text=t).exists: D(text=t).click(); break
    time.sleep(3)

    # Step7: 点发送
    logger.info("Step7: 点发送")
    for txt in ["发送", "发布"]:
        el = D(text=txt)
        if el.exists: el.click(); break
    else:
        ctrl.base._tap_ratio(0.89, 0.90)  # evj位置
    time.sleep(2)

    # Step8: 验证
    logger.info("Step8: 验证")
    ok = ctrl.comment.verify_comment_published()
    logger.info(f"=> {'成功' if ok else '需检查'}")

    ctrl.nav.close_comments()
    ctrl.comment.reset_keyboard_state()
    time.sleep(2)

logger.info("\n===== 5次真机测试完成 =====")
