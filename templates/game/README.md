# 游戏模板图片目录

模板图片放在本目录，`config/game.yaml` 中按**文件名(不含 .png)** 引用。

## 制作模板

1. 手机连接电脑后运行:

```bash
python scripts/dump_hierarchy.py <SERIAL>          # 导出 UI 层级 XML
python main.py run --device <SERIAL>                # 运行中会自动截图
adb -s <SERIAL> exec-out screencap -p > screen.png  # 手动截图
```

2. 用截图工具裁剪目标按钮/图标（越小越精确，推荐 40x40 ~ 200x200 px）
3. 保存为 `templates/game/<名字>.png`
4. 在 `config/game.yaml` 的 pages / popups / steps 中引用该名字

## 匹配参数

- `threshold`: 默认 0.8（config/game.yaml → image.threshold），越高越严格
- 匹配支持多尺度（1.0 / 0.9 / 1.1），不同分辨率手机无需重新制作模板
- 单次动作可指定 `roi` 缩小搜索区域提升速度与精度

## 提示

- 模板不要截入会变化的元素（时间、红点数字、头像）
- 优先 UI 层级(text/resource-id)定位；模板用于层级拿不到的纯图形按钮
