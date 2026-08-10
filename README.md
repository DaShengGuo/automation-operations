# 抖音自动化评论运营系统

Windows 电脑 + Android 手机 = 全自动抖音评论运营

## 你需要什么

- 💻 **Windows 10/11 电脑**
- 📱 **Android 手机**（已安装抖音并登录）
- 🔌 **USB 数据线**（能传数据的，不是纯充电线）

## 5 步快速开始

### 第 1 步：下载项目

```bash
git clone https://github.com/DaShengGuo/automation-operations.git
cd automation-operations
```

或下载 ZIP 解压。

### 第 2 步：一键安装

**双击 `install.bat`**

脚本会自动完成：
- ✅ 检测/安装 Python
- ✅ 创建虚拟环境
- ✅ 安装所有依赖
- ✅ 初始化配置

### 第 3 步：设置手机

查看详细教程：**[docs/PHONE_SETUP.md](docs/PHONE_SETUP.md)**

简要步骤：
1. 开启开发者选项
2. 开启 USB 调试
3. USB 连接电脑，手机屏幕弹窗点「允许」

### 第 4 步：检测设备

**双击 `check-device.bat`**

确认显示「设备就绪」。

### 第 5 步：启动

**双击 `start.bat`**

脚本开始自动运行。按 `Ctrl+C` 停止。

## 以后每次使用

```text
1. 连接手机
2. 双击 start.bat
```

## 故障排除

- 设备检测失败 → 运行 `check-device.bat` 看提示
- 环境有问题 → 运行 `doctor.bat` 诊断
- 详细帮助 → [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Android 设备兼容性

本项目采用**运行时设备检测 + 自适应坐标系统**，原则上支持
大多数启用 ADB 调试的 Android 设备（Android 10+）。

连接新手机后，程序自动：
- 读取屏幕分辨率、DPI、品牌型号
- 根据屏幕参数自动计算归一化坐标
- 优先使用 UI 元素定位（resource-id / text / desc），坐标只作兜底

**以下设备经作者实际测试验证：**

| 设备 | 状态 |
|------|------|
| Redmi 14C (pond) | ✅ 已验证 |
| Honor KOZ-AL00 | ✅ 已验证 |
| 其他 Android 10+ | 🔵 自动检测适配 |

> 如遇新设备坐标偏移，运行 `init-device.bat` 完成一次自动校准即可。

## 项目结构

```
automation-operations/
├── douyin_core/          # 抖音自动化核心框架
│   ├── adb_controller.py # ADB + uiautomator2 混合控制
│   ├── config.py         # 全局配置
│   ├── ocr_engine.py     # PaddleOCR 文字识别
│   └── image_sync.py     # 图片推送到手机
├── comment_bot/          # 评论运营引擎
│   ├── main.py           # 完整生命周期主程序
│   ├── fsm.py            # 评论状态机
│   ├── scheduler.py      # 任务调度器
│   ├── materials.py      # 素材管理
│   ├── filter.py         # 视频筛选
│   ├── dashboard.py      # Web 监控面板
│   └── ai_filter/        # AI 筛选管线 (可选)
├── materials/            # 素材库 (文案/图片)
├── adb/platform-tools/   # ADB 工具 (自带)
├── install.bat           # 一键安装
├── start.bat             # 一键启动
├── check-device.bat      # 设备检测
├── doctor.bat            # 环境诊断
└── docs/                 # 文档
```

## 安全说明

- 本项目运行在你的本地电脑上，不向任何服务器发送数据
- 请合理使用，遵守抖音平台规则
- 建议控制使用频率，避免账号风险

## License

[MIT](LICENSE)
