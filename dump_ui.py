"""
Dump 抖音评论区 UI 层级，找出所有可交互元素。
运行前确保：模拟器在抖音推荐Tab某个视频上。
"""
import sys, time
sys.path.insert(0, '.')
from douyin_core.adb_controller import DouyinController

ctrl = DouyinController()
print("设备已连接")
time.sleep(1)

# 1. 打开评论区
print("\n>>> 打开评论区...")
ctrl.nav.open_comments()
time.sleep(2)

# 2. 点输入框（让键盘弹出来）
print(">>> 点击输入框...")
try:
    el = ctrl.d(className="android.widget.EditText")
    if el.exists:
        el.click()
        print("  找到 EditText，已点击")
    else:
        print("  未找到 EditText")
except Exception as e:
    print(f"  错误: {e}")
time.sleep(2)

# 3. Dump 完整层级
print("\n>>> Dumping UI hierarchy...")
xml = ctrl.d.dump_hierarchy()
path = "data/comment_ui_dump.xml"
with open(path, "w", encoding="utf-8") as f:
    f.write(xml)
print(f"  已保存到 {path} ({len(xml)} 字符)")

# 4. 找出所有可点击的元素
print("\n>>> 查找关键元素:")
import re
# 找所有带 resource-id 且可点击的元素
clicks = re.findall(r'resource-id="([^"]*)"[^>]*text="([^"]*)"[^>]*clickable="true"', xml)
for rid, txt in clicks[:30]:
    if txt.strip():
        print(f"  [{rid}] text='{txt.strip()}'")

# 找 content-desc 的元素
descs = re.findall(r'content-desc="([^"]+)"[^>]*clickable="true"', xml)
print(f"\n>>> content-desc 可点击元素:")
for d in descs[:20]:
    if d.strip():
        print(f"  desc='{d.strip()}'")

# 找所有 EditText
edits = re.findall(r'class="android.widget.EditText"[^/]*/>', xml)
print(f"\n>>> EditText 元素: {len(edits)} 个")
for e in edits[:5]:
    rid = re.search(r'resource-id="([^"]*)"', e)
    txt = re.search(r'text="([^"]*)"', e)
    print(f"  resource-id={rid.group(1) if rid else 'N/A'} text={txt.group(1) if txt else 'N/A'}")

# 找 ImageView/ImageView 按钮（图片按钮可能在工具栏）
imgs = re.findall(r'class="android.widget.ImageView"[^/]*/>', xml)
print(f"\n>>> ImageView 元素: {len(imgs)} 个")
for img in imgs[:15]:
    rid = re.search(r'resource-id="([^"]*)"', img)
    desc = re.search(r'content-desc="([^"]*)"', img)
    bounds = re.search(r'bounds="([^"]*)"', img)
    if rid or desc:
        print(f"  {rid.group(1) if rid else 'N/A'} | desc={desc.group(1) if desc else 'N/A'} | {bounds.group(1) if bounds else 'N/A'}")

print("\n>>> 完成。查看 data/comment_ui_dump.xml 获取完整层级。")
