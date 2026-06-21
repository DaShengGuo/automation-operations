"""
运行时机: 评论区已有1张图选中后, 手动运行此脚本
"""
import sys, time, re
sys.path.insert(0, '.')
from douyin_core.adb_controller import DouyinController
ctrl = DouyinController()
time.sleep(1)

xml = ctrl.d.dump_hierarchy()
with open("data/after_1img.xml", "w", encoding="utf-8") as f:
    f.write(xml)

print("=== 底部区域(y>1700)所有元素 ===")
# 找所有 bounds 在底部区域的元素
elems = re.findall(
    r'(<[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*/>)',
    xml
)
for raw, x1, y1, x2, y2 in elems:
    if int(y1) > 1700 or int(y2) > 1700:
        desc = re.search(r'content-desc="([^"]*)"', raw)
        text = re.search(r'text="([^"]*)"', raw)
        cls = re.search(r'class="([^"]*)"', raw)
        rid = re.search(r'resource-id="([^"]*)"', raw)
        clickable = 'clickable="true"' in raw
        cx = (int(x1)+int(x2))//2; cy = (int(y1)+int(y2))//2
        parts = []
        if rid: parts.append(f'id={rid.group(1)[-30:]}')
        if desc: parts.append(f'desc="{desc.group(1)[:40]}"')
        if text: parts.append(f'text="{text.group(1)[:40]}"')
        if cls: parts.append(f'class={cls.group(1)[-20:]}')
        if clickable: parts.append('CLICKABLE')
        print(f'  [{x1},{y1}][{x2},{y2}] r=({cx/1080:.3f},{cy/1920:.3f}) | {", ".join(parts)}')

print("\n=== 所有 desc='插入图片' ===")
for m in re.finditer(r'content-desc="插入图片"[^/]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
    x1,y1,x2,y2 = m.group(1),m.group(2),m.group(3),m.group(4)
    cx=(int(x1)+int(x2))//2; cy=(int(y1)+int(y2))//2
    clickable = 'clickable="true"' in xml[m.start():m.end()+50]
    print(f'  [{x1},{y1}][{x2},{y2}] r=({cx/1080:.3f},{cy/1920:.3f}) clickable={clickable}')

print("\n保存到 data/after_1img.xml")
