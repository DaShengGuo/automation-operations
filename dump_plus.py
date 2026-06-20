"""
自动执行到"已选1张图"状态 → dump + 号元素
"""
import sys, time, re
sys.path.insert(0, '.')
from douyin_core.adb_controller import DouyinController
from comment_bot.materials import MaterialManager

ctrl = DouyinController()
mm = MaterialManager()

# 1. 打开评论区
print("1. 打开评论区...")
ctrl.nav.open_comments()
time.sleep(2)

# 2. 点输入框 + 输入文字
print("2. 输入文字...")
el = ctrl.d(className="android.widget.EditText")
if el.exists:
    el.click()
time.sleep(1.5)
ctrl.d.send_keys("测试")
time.sleep(1)

# 3. 点图片按钮 → 选第1张图
print("3. 点图片按钮 → 选第1张...")
el_img = ctrl.d(description="插入图片")
if el_img.exists:
    el_img.click()
else:
    ctrl.base._tap_ratio(0.045, 0.938)
time.sleep(3)

# 选第一张图
ctrl.base._tap_ratio(0.499, 0.248)
time.sleep(3)

# 4. 现在应该回到了评论区（有1张图选中）, dump!
print("4. Dump评论区+1张图状态...")
xml = ctrl.d.dump_hierarchy()
with open("data/comment_plus_btn.xml", "w", encoding="utf-8") as f:
    f.write(xml)
print(f"   Saved ({len(xml)} chars)")

# 找所有带描述的可点击元素
clicks = re.findall(r'content-desc="([^"]*)"[^>]*clickable="true"[^/]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
print("\n=== 可点击+有描述的元素 ===")
for desc, x1, y1, x2, y2 in clicks:
    d = desc.strip()
    if d:
        cx = (int(x1)+int(x2))//2; cy = (int(y1)+int(y2))//2
        print(f'  desc="{d[:80]}" bounds=[{x1},{y1}][{x2},{y2}] ratio=({cx/1080:.3f},{cy/1920:.3f})')

# 找包含 添加/加/更多/+ 的
print("\n=== 找+号相关 ===")
for desc, x1, y1, x2, y2 in clicks:
    d = desc.strip()
    if any(kw in d for kw in ['添加', '加', '更多', '+', '继续', '再选']):
        cx = (int(x1)+int(x2))//2; cy = (int(y1)+int(y2))//2
        print(f'  *** desc="{d}" bounds=[{x1},{y1}][{x2},{y2}] ratio=({cx/1080:.3f},{cy/1920:.3f})')

# 找所有有文字的
texts = re.findall(r'text="([^"]+)"[^>]*clickable="true"[^/]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
print("\n=== 可点击+有文字 ===")
for t, x1, y1, x2, y2 in texts[:20]:
    cx = (int(x1)+int(x2))//2; cy = (int(y1)+int(y2))//2
    print(f'  text="{t[:50]}" bounds=[{x1},{y1}][{x2},{y2}] ratio=({cx/1080:.3f},{cy/1920:.3f})')

# 找 ImageView 带描述的
imgs = re.findall(r'class="android.widget.ImageView"[^/]*content-desc="([^"]*)"[^/]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
print(f"\n=== ImageView+desc ({len(imgs)}个) ===")
for desc, x1, y1, x2, y2 in imgs:
    d = desc.strip()
    if d and len(d) > 0:
        cx = (int(x1)+int(x2))//2; cy = (int(y1)+int(y2))//2
        print(f'  desc="{d[:80]}" bounds=[{x1},{y1}][{x2},{y2}] ratio=({cx/1080:.3f},{cy/1920:.3f})')

print("\n完成。查看 data/comment_plus_btn.xml")
