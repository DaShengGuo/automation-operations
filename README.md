# 抖音自动化评论运营系统

以自动化运营专员身份，在 MuMu 模拟器上运行抖音，自动刷视频、筛选、评论、互动。

## 环境要求

- Python 3.10+
- MuMu 模拟器（开启 ADB 调试）
- 抖音 App 已安装并登录

## 快速开始

1. 安装依赖: `pip install -r requirements.txt`
2. 启动 MuMu 模拟器，开启 ADB 调试
3. 准备素材: 编辑 `materials/materials.xlsx`（首次运行自动创建模板）
4. 运行: `python -m comment_bot.main`
5. 打开 Dashboard: http://localhost:5800

## 目录结构

- `douyin_core/` — 通用抖音自动化框架
- `comment_bot/` — 评论运营引擎
- `materials/` — 素材库
- `data/` — 运行时数据
