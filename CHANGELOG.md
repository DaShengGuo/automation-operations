# 更新日志

版本规则: Semantic Versioning — `v1.0.0` 第一版生产版本; `v1.0.1` 修复 BUG;
`v1.1.0` 新功能; `v2.0.0` 重大架构升级。

---

## v1.0.2 — 正式客户交付专项整改

修复:
- **CMD 黑框闪烁彻底消除**: 新增统一隐藏执行器(WindowsProcessRunner,
  STARTF_USESHOWWINDOW + SW_HIDE + CREATE_NO_WINDOW)并全局覆盖
  subprocess 调用点(含 uiautomator2/adbutils 库内部); 全部 ADB 命令
  隐藏执行, 禁止 shell=True/BAT 驱动运行流程
- **0 设备运行真实根因修复**: uiautomator2 资源(u2.jar/app-uiautomator.apk)
  此前未随程序发布 → 设备初始化全部失败 → 调度器 0 worker。
  现随安装包分发 + 冻结环境资源定位补丁
- **ADB 单一来源(AdbLocator)**: 捆绑 platform-tools 优先, 注入
  ADBUTILS_ADB_PATH/ADB_PATH 让 u2/adbutils 共用同一份 adb,
  devices 枚举带 start-server + 重试×3, 消除瞬时空窗误报
- **DeviceRegistry 单一设备状态源**: GUI/监控/调度器共享同一份设备事实,
  DEVICE_DISCOVERY 日志带 REJECT_REASON, 不再静默吞异常
- **DeviceMonitor 热插拔监控**: 5s 连接状态刷新 + 空闲 60s 硬件信息刷新
  (此前 GUI 每秒全量扫描打 getprop, 已消除)
- **GUI 设备计数拆三指标**: 检测到设备 / READY设备 / 运行中Worker;
  unauthorized/offline 等状态显示具体原因与操作提示
- 新增「复制诊断信息」按钮(ADB 来源/版本/设备注册表/环境自检一键复制)
- 新增环境自检: u2 资源校验 + VC++ 2015-2022 x64 运行库检测
  (缺失时内置安装器静默安装, 不要求客户装 Python/pip/ADB)
- 新增手机 VPN 检测: 每 120s 读 `dumpsys connectivity` 的
  VpnNetworkProvider 计数(兜底 tun/wg/ppp 接口枚举); 未检测到 VPN 时
  弹窗提醒(仅首次缺失/由开转关触发, 按钮「已开启VPN」), 运行前
  「确认并运行」/「开始运行」预检弹窗确认
- 修复 OCR 模型打包: rapidocr(PP-OCRv6) 与 rapidocr_onnxruntime
  (PP-OCRv4 兜底)的 default_models.yaml/config.yaml/models/
  arch_config.yaml 此前未随包发布 → 客户版 OCR 初始化即失败;
  现两套引擎资源完整打包
- 修复「停止全部」后 GUI 计数不归零: 调度器快照以 Worker 线程存活为准
  (此前 stop() 不清 runtime 状态残留,「运行中Worker」永久显示 1);
  实测停止 17s 内 Worker 退出, 计数即刻归零
- 修复 QQ 取号日志泄漏完整账号名: fetch_latest 打印账号改为
  mask_account 脱敏形态(如 Rk3\*\*\*658), 回归测试锁定
- 修复安装版 Worker 启动失败(Permission denied): 模块内
  `ControlConfig.load()` 默认 game_name 与桌面版注册的数据目录单例
  不匹配 → 另建实例 → 设备日志退回安装目录 `_internal\logs`
  (Program Files 只读) → 启动被 [Errno 13] 打死。现桌面版单例
  全局生效, 且设备日志创建失败仅告警不中断(自动化不得因日志
  不可写而启动失败), 回归测试锁定
- 修复 VPN 检测误报(机场/Clash 开着仍弹「未检测到 VPN」): root/
  内核模式 TUN(如 Clash Meta kernel tun)不注册 Android VpnService
  → `dumpsys connectivity` 的 VpnNetworkProvider 恒为 0, 但隧道
  真实存在(实测 tun0 UP + 全表路由接管全部 App 流量)。现系统
  计数为 0 时同样兜底查 tun*/wg*/ppp* 接口; 弹窗文案补充说明
  「机场/Clash 纯代理模式不算系统 VPN, 需开 TUN/虚拟网卡模式」。
  真机验证: `接口 tun0(VpnNetworkProvider=0; root/内核模式 TUN)`
  → vpn_ok=True, 不再误报

---

## v1.0.1

修复:
- PTC 登录: 密码保存弹窗在认证期间弹出会压死认证流程(75s 超时) — 提交验证
  循环先关弹窗再检查按钮, 弹窗关闭后表单仍在则自动重提一次
- PTC 登录: 弹窗遮住按钮时 OCR 误判「按钮消失=提交已生效」导致放行白等

---

## v1.0.0

首个客户交付版本(Windows 桌面软件)。

- 图形化控制窗口(欢迎使用宝可梦自动化购买脚本)
- 账号来源配置: QQ群聊 / Excel / CSV 文件
- 确认并运行 / 开始运行 / 停止全部 / 单设备停止
- 自动识别手机(品牌/型号/Serial/ADB 状态), 兼容任意设备数量
- 自动识别当前步骤 + 从指定步骤重新开始(真实页面前置校验)
- 停止后继续当前账号(真实页面优先恢复, 不重新登录)
- 实时运行日志 / 具体错误信息 / 错误现场截图留档
- 历史记录(今天/全部筛选 + Excel 导出) / 生产统计
- 关闭程序重置本次运行状态; 日志/数据库/历史永久保留
- 统一版本源(version.py) / SQLite schema 迁移 + 升级前自动备份
- 程序文件与客户数据分离(数据在 %LOCALAPPDATA%\PokemonAutomation)
- 正式安装包(桌面快捷方式 / 开始菜单 / 卸载程序 / 覆盖升级)
- 无需安装 Python — 双击安装包即可使用
