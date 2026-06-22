"""
真机发布测试 — Redmi 14C (720×1640) 简单版
每一步都有截图反馈，方便定位问题
"""
import sys, time, logging, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("real")

from douyin_core import config as cfg
from douyin_core.adb_controller import DouyinController
from comment_bot.materials import MaterialManager

ctrl = DouyinController('AQV4TSDY9PCEIZ8L')
mm = MaterialManager()

# Enable uiautomator2 keyboard
try:
    import uiautomator2 as u2
    d = u2.connect('AQV4TSDY9PCEIZ8L')
    d.set_fastinput_ime(True)
except Exception:
    pass

logger.info("真机720x1640 — 每步截图到 data/screenshots/")


def step(n, desc):
    logger.info(f"--- Step{n}: {desc} ---")
    ctrl.base.screenshot(f"real_step{n}_{int(time.time())}")


for loop in range(1, 4):
    logger.info(f"\n{'#'*40}\n### 第{loop}/3次\n{'#'*40}")

    # Step1: 打开评论区
    step(1, "打开评论区")
    ctrl.nav.open_comments()
    time.sleep(3)

    # Step2: 点输入框 + 输入文案
    step(2, "输入文案")
    cw = mm.pick_copywriting()
    logger.info(f"文案: {cw['content'][:40]}")
    ctrl.comment.input_comment_text(cw['content'])
    time.sleep(2)

    # Step3: 点图片按钮 — 键盘打开时不能dump层级, 直接用坐标
    # 真机dump: [24,1439][88,1527] center=(56,1483) ratio=(0.078,0.904)
    step(3, "点图片按钮(坐标)")
    ctrl.base._tap_ratio(0.078, 0.904)
    time.sleep(4)

    # Step4: 选第1张图 → 下一步
    step(4, "选第1张图+下一步")
    ctrl.base._tap_ratio(0.50, 0.25)  # 点第一张图(真机网格)
    time.sleep(2)
    for t in ["下一步", "下一步(1)", "下一步(2)"]:
        el = ctrl.d(text=t)
        if el.exists:
            el.click()
            logger.info(f"点了{t}")
            break
    time.sleep(3)

    # Step5: 回到评论区, 点+号加第二张
    # 真机dump: +号在 [456,1512][536,1592] center=(496,1552) ratio=(0.689,0.946)
    step(5, "点+号加第二张")
    ctrl.base._tap_ratio(0.689, 0.946)
    time.sleep(4)

    # Step6: 选第2张图 → 下一步(2)
    step(6, "选第2张图+下一步")
    ctrl.base._tap_ratio(0.70, 0.25)
    time.sleep(2)
    for t in ["下一步", "下一步(1)", "下一步(2)"]:
        el = ctrl.d(text=t)
        if el.exists:
            el.click()
            break
    time.sleep(3)

    # Step7: 点发送
    step(7, "点发送")
    ctrl.comment.submit_comment()
    time.sleep(3)

    # Step8: 验证
    step(8, "验证发布")
    ok = ctrl.comment.verify_comment_published()
    logger.info(f"=> {'成功' if ok else '需检查'}")

    ctrl.nav.close_comments()
    ctrl.comment.reset_keyboard_state()
    time.sleep(2)

logger.info("\n真机测试完成")
