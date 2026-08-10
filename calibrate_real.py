"""
真机校准 — 每步截图，逐步确认坐标
运行后每一步都会截图保存，你描述屏幕上的实际位置，我来调整坐标
"""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from douyin_core.adb_controller import DouyinController

import os
DEVICE_SERIAL = os.environ.get('DOUYIN_DEVICE', '')
if not DEVICE_SERIAL:
    print("请设置环境变量 DOUYIN_DEVICE=你的设备序列号")
    print("或运行: set DOUYIN_DEVICE=设备序列号 && python calibrate_real.py")
    sys.exit(1)
ctrl = DouyinController(DEVICE_SERIAL)

# 启用 uiautomator2 键盘
try:
    import uiautomator2 as u2
    d = u2.connect(DEVICE_SERIAL)
    d.set_fastinput_ime(True)
except: pass

SDIR = os.path.join(os.path.dirname(__file__), 'data', 'screenshots')

def shot(name):
    path = ctrl.base.screenshot(name)
    print(f"[截图] {path}")
    return path

print("=== 真机校准脚本 ===\n")

# ==== Step A: 评论区（无输入）====
print("Step A: 打开评论区（不要点输入框）")
ctrl.nav.open_comments()
time.sleep(2)
shot("A_comments")
print(">>> 看 A_comments.png，描述评论区底部有哪些按钮（图片、@、表情等），各在什么位置？")
input("按 Enter 继续...")

# ==== Step B: 点输入框后 ====
print("\nStep B: 点击输入框")
el = ctrl.d(className='android.widget.EditText')
if el.exists:
    el.click()
    print("  已点击 EditText")
else:
    ctrl.base._tap_ratio(0.35, 0.96)
    print("  坐标点击输入框")
time.sleep(2)
ctrl.d.send_keys('test')
time.sleep(1)
shot("B_keyboard_open")
print(">>> 看 B_keyboard_open.png，描述键盘上方工具栏有什么按钮？图片按钮在哪个位置（大约几分之几）？")
input("按 Enter 继续...")

# ==== Step C: 点图片按钮进入相册 ====
print("\nStep C: 点击图片按钮进入相册")
# 尝试几个候选位置
candidates = [(0.08, 0.90), (0.05, 0.90), (0.10, 0.89), (0.08, 0.88)]
for i, (x, y) in enumerate(candidates):
    ctrl.base._tap_ratio(x, y)
    time.sleep(2)
    shot(f"C_picker_attempt{i}_{x}_{y}")
    # 快速检查是否在相册
    xml = ctrl.base.dump_hierarchy()
    import re
    if '下一步' in xml or '所有照片' in xml or '拍照' in xml:
        print(f"  >>> 尝试({x},{y}) 进入了相册!")
        break
    # 没进入相册，按back然后继续
    ctrl.base.d.press('back')
    time.sleep(1)

print(">>> 看 C_picker_attempt* — 哪张进入了相册？相册里图片排成几列？第一张图在什么位置？")
input("按 Enter 继续...")

# ==== Step D: 选一张图后 ====
print("\nStep D: 选第一张图")
# 点击屏幕上半部分（相册网格区域）
ctrl.base._tap_ratio(0.50, 0.30)
time.sleep(2)
shot("D_after_select")
# 找下一步
for t in ["下一步", "下一步(1)", "确认", "确定", "完成"]:
    el = ctrl.d(text=t)
    if el.exists:
        el.click()
        print(f"  点击了{t}")
        break
time.sleep(3)
shot("D_back_to_comment")
print(">>> 看 D_back_to_comment.png，评论区是否显示了1张图？+号在什么位置？")
input("按 Enter 结束...")

print("\n校准完成。把截图发给我，我根据实际位置修改坐标。")
