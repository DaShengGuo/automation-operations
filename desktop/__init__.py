"""
desktop/
Windows 桌面应用层 — 现有自动化系统的控制层 + 展示层。

原则:
  - 不复制自动化逻辑: 复用 core/automation 现有模块
  - GUI 线程只做界面/事件/控制命令, 自动化在 DeviceWorker 线程执行
  - 程序文件与客户数据彻底分离: 数据全部走 AppPaths
"""
