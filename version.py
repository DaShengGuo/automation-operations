"""
version.py
统一版本源 — GUI / 安装器 / 打包脚本 / 错误日志全部从这里读。

版本规则(Semantic Versioning):
  v1.0.0  第一版生产版本
  v1.0.1  修复卡住 BUG
  v1.0.2  修复设备识别
  v1.1.0  增加新功能
  v2.0.0  重大架构升级
"""

APP_NAME = "宝可梦自动化购买脚本"
APP_NAME_EN = "PokemonAutomation"
APP_VERSION = "1.0.2"
APP_VERSION_TAG = f"v{APP_VERSION}"
APP_TITLE = "欢迎使用宝可梦自动化购买脚本"
APP_PUBLISHER = "PokemonAutomation"

# 安装包/EXE 文件名由打包脚本拼 APP_NAME + APP_VERSION_TAG,
# 禁止在打包脚本里再写死版本号。
