"""
version.py
统一版本源 — GUI / 安装器 / 打包脚本 / 错误日志全部从这里读。

版本规则(Semantic Versioning):
  v1.0.0  第一版生产版本
  v1.0.1  修复卡住 BUG
  v1.0.2  修复设备识别
  v1.1.0  增加新功能
  v1.2.0  账号来源改为人工按设备输入账号密码队列
  v1.2.1  商城流程稳定性整改(状态缓存指纹碰撞/进店即滑/异常退出重进)
  v1.2.2  启动等待智能化(快速轮询分层/检查点日志/MAP二次确认防转场误判)
  v1.2.3  流程时间对齐人工实测 + 商城滑动状态锁 + 判底灰度通道修复
  v1.2.4  商城滑动误退出根因修复(MAIN_MENU误判商城+精确坐标滑动+BACK守卫)
  v1.2.9  GUI停止按钮真停止+商城误判二次确认+滑动禁识别
  v1.3.0  商城流程状态机修复(MAP误判根因min_hits=2+固定6次滑动+四条件退出证据)
  v2.0.0  重大架构升级
"""

APP_NAME = "宝可梦自动化购买脚本"
APP_NAME_EN = "PokemonAutomation"
APP_VERSION = "1.3.0"
APP_VERSION_TAG = f"v{APP_VERSION}"
APP_TITLE = "欢迎使用宝可梦自动化购买脚本"
APP_PUBLISHER = "PokemonAutomation"

# 安装包/EXE 文件名由打包脚本拼 APP_NAME + APP_VERSION_TAG,
# 禁止在打包脚本里再写死版本号。
