# 更新日志

版本规则: Semantic Versioning — `v1.0.0` 第一版生产版本; `v1.0.1` 修复 BUG;
`v1.1.0` 新功能; `v2.0.0` 重大架构升级。

---

## v1.1.0 — 设备环境重置 / 故障恢复 (2026-08-19)

新功能:
- **设备环境重置 / 故障恢复**: 每台设备操作区新增「重置设备环境」按钮,
  设备操作区最终布局 `[停止] [重新识别] [重置设备环境]`(重新识别
  结果就地显示在卡片提示位)。重置前二次确认弹窗: 设备型号 / 脱敏账号
  / 当前步骤 / 影响范围清单。
  流程: 停止本机 Worker(在途账号归还 RETRY, 原因 DEVICE_RESET, 绝不
  误标成功; 预取账号释放回 PENDING 不烧重试) → 释放账号锁 → 清理
  RuntimeCheckpoint / resume 注入配置 → `pm clear` 游戏数据 → 重检 ADB
  → 重连 uiautomator2 → 重新获取设备信息 → 重新初始化 →
  `detect_state()` 按手机真实页面恢复 READY(不假设任何状态, 清数据后
  的权限页/欢迎页/首次启动由既有 Popup/InitialPage handler 处理)。
  失败显示 RESET_FAILED + 具体步骤/原因/详细输出, 绝不笼统报错
- **浏览器数据独立高级选项**: 默认不清理(浏览器无关原则, 保护浏览器
  Cookie/登录状态/网站数据); 勾选后解析系统默认浏览器再执行,
  解析不出或结果不像浏览器(系统设置/应用商店等)时按规格跳过 —
  无法安全确认影响范围绝不执行
- **重置硬边界**: 仅人工明确触发(禁止按账号数量/风控检测自动清理,
  不实现平台限制规避 — 检测到限制仍由既有 Watchdog 截图取证停止);
  单台设备执行, 其他设备 Worker 继续运行不受影响(调度器 join 移出
  锁外); 运行日志/SQLite 历史/错误记录/账号执行历史/设备历史一律
  不删; 每步结果持久化 `logs/device_reset.log`
  (DEVICE/MODEL/ACTION/REASON/PREV_ACCOUNT/PREV_STATE/GAME_DATA/
  BROWSER_DATA/DETECTED_STATE/RESULT/STEP/ERROR/DETAIL)
- 测试: `tests/test_device_reset.py` 17 条(Worker 停止/DEVICE_RESET
  原因/预取释放/检查点单设备清理/浏览器三态/失败详情/历史保留/
  控制器防重复防离线), 全量 258 条通过。真机测试待交付设备就绪后
  按规格 §18 单独执行

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
