# 正式客户交付专项整改报告 — 2026-08-18 (v1.0.2)

整改范围: 客户交付专项(79 条要求)的两个真实 BUG + 整改过程中新发现并
修复的 6 个 BUG + 本轮新增 VPN 检测功能。全部修复已进
`release\宝可梦自动化购买脚本_Setup_1.0.2.exe` 并在安装目录实测。

---

## 一、BUG ①: GUI 打开与运行期间不断闪 CMD 黑色终端窗口

### 现象

客户机器上打开 GUI 和运行自动化期间, 屏幕不断闪现 CMD 黑色窗口。

### 真实根因(按层)

1. **裸 subprocess 调用点无隐藏标志**: 程序多处直接 `subprocess.run/Popen`
   调用 ADB 与其他工具, 未设置 `STARTF_USESHOWWINDOW/SW_HIDE`/
   `CREATE_NO_WINDOW`。Windows 对每个控制台子进程都会新建一个可见的
   conhost 窗口 → 每次 ADB 调用闪一次黑框。运行期 Worker 每几秒调 ADB
   (点击/截图/层级 dump), 故"不断闪"。
2. **第三方库内部同样裸调**: `adbutils`/`uiautomator2` 库内部
   `import subprocess` 后直接 `subprocess.run([adb, ...])` —
   不改库代码就无法在调用点逐个包住。
3. **历史遗留**: 存在 `cmd /k`、`pause`、BAT 驱动运行流程等调用形态,
   都会显式弹出控制台。

### 修复

- 新增统一隐藏执行器 [desktop/process_runner.py:70](desktop/process_runner.py#L70)
  `run_hidden_process(...)`:
  - `STARTF_USESHOWWINDOW(0x1) + SW_HIDE(0)` + `CREATE_NO_WINDOW(0x08000000)`;
  - 全部 ADB 调用走此执行器, **禁止 `shell=True` 作为普通 ADB 调用**;
  - PowerShell 一律 `-NoProfile -NonInteractive -WindowStyle Hidden`。
- **全局 monkeypatch** `subprocess.run/Popen/check_output/check_call/call`:
  adbutils/u2 等库在调用时 `import subprocess` 查模块属性, 全局替换后
  库内部调用同样隐藏 — 不修改任何第三方库源码即全覆盖。
- 删除运行期 `pause`/`cmd /k`/`cmd /c`, 运行流程不再由 BAT 驱动。

### 客观证据

`scripts/watch_flash2.py`(Toolhelp32 100ms 快照 + 20ms 重采样归属 conhost
父进程链)在自动化运行期间实测, 结果存
`c:\temp\flash2_result.txt`:

| 指标 | 数值 | 说明 |
|------|------|------|
| APP_CONHOST_COUNT | 345 | 归属程序的全部 conhost 父链均为 `conhost → adb.exe → 宝可梦自动化购买脚本.exe` — **零 cmd.exe/powershell.exe 在程序之下** |
| VISIBLE_CONSOLE_WINDOW_COUNT | 0 | 可见控制台窗口枚举 = 0(客户机验收口径) |
| ORPHAN | 3 | 均为 `cmd → claude.exe`(本机开发工具自身噪声, 与程序无关) |
| NOISE | 29 | clash-verge / MuMu / git / bash 等其他软件 |

**测量口径说明(如实声明)**: 本开发机 Win11 26200 用 ConPTY headless
conhost 承载控制台进程, 顶层可见窗口枚举天然恒为 0, 无法在本机复现
"可见黑框" — 故以两层口径交叉验证: ① 进程归属(程序之下只允许 adb.exe,
若出现 cmd/powershell 即失败) ② 可见控制台窗口枚举为 0(客户机上的
验收 oracle)。两层同时满足。

---

## 二、BUG ②: 手机已连接, GUI 显示"当前 0 个设备运行"

### 真实根因(ADB 层 / DeviceManager / Registry / Worker / GUI 各层)

| 层 | 根因 | 后果 |
|----|------|------|
| **ADB 层** | 程序依赖 PATH 里的 adb(或 adbutils 自行探测), 客户机未装/版本不符即失败; 异常被静默吞掉, 上层看到"0 设备"而无原因 | devices 枚举空 |
| **ADB 层** | 未先 `adb start-server` 就 `devices -l`; 无重试 — adb server 冷启动竞态产生瞬时空窗 | 启动瞬间误判 0 设备 |
| **DeviceManager** | `uiautomator2` 资源(`u2.jar`/`app-uiautomator.apk`)未随程序发布 → 设备初始化在 u2 连接阶段全部失败, 设备进了列表却永远不 READY | 0 个设备能起 Worker |
| **Registry** | 设备状态散落在多处(GUI 自扫 / 调度器自扫), 无单一事实源; 发现失败无 REJECT_REASON 日志 | 各层数字互相打架, 无法定位 |
| **Worker** | 无 Ready Gate — 初始化失败的设备也进调度, worker 起后立刻死, 计数瞬间回落 | 界面显示 0 运行 |
| **GUI** | 单一计数「0 个设备运行」, 检测到/READY/运行中混为一谈; 每秒全量扫描打 getprop(性能与状态抖动) | 用户看到"0"却无任何原因提示 |

**未做且明确禁止的假修复**: `if count == 0: count = 1` — 不改 GUI 数字,
逐层修真实根因(见上表)。

### 修复

- **AdbLocator 唯一 ADB 来源**([desktop/adb_locator.py](desktop/adb_locator.py)):
  捆绑 platform-tools 优先; 注入 `ADBUTILS_ADB_PATH`/`ADB_PATH` 让
  u2/adbutils 共用同一份 adb; `devices -l` 前先 `start-server`, 枚举
  失败自动重试 ×3。
- **捆绑发布**: platform-tools 与 u2 资源(u2.jar/app-uiautomator.apk/sync.sh)
  随安装包分发; 冻结环境资源定位补丁。
- **DeviceRegistry 单一设备状态源**([desktop/device_registry.py](desktop/device_registry.py)):
  GUI/监控/调度器共享同一份设备事实; `DEVICE_DISCOVERY` 日志带
  `REJECT_REASON`, 不再静默吞异常。
- **Ready Gate**: 只有初始化 PASS 的设备才允许起 Worker。
- **DeviceMonitor 热插拔监控**([desktop/device_monitor.py](desktop/device_monitor.py)):
  5s 连接刷新 + 空闲 60s 硬件信息刷新, 取代 GUI 每秒全量扫描。
- **GUI 计数拆三指标**: 检测到设备 / READY设备 / 运行中Worker;
  unauthorized/offline 显示具体原因与操作提示。
- **环境自检**: VC++ 2015-2022 x64 运行库检测 + 内置静默安装
  (客户机不装运行库时 u2/OCR 初始化即失败 — 同属"0 设备"环境根因)。

### 客观证据(端到端)

`c:\temp\gui_drive_result2.txt`(pywinauto UIA 驱动正式 EXE 实测):

```
检测到设备: 1 → READY设备: 1 → 点「确认并运行」→ 运行中Worker: 1
→ 保持运行 → 点「停止全部」→ 运行中Worker: 0   (result: PASS)
```

日志(`%LOCALAPPDATA%\PokemonAutomation\logs\control_2026-08-18.log`):

- 13:45:51 `[DEVICE_DISCOVERY] 新设备 e98bee5a state=device`
- 13:45:51 `[DEVICE_READY] e98bee5a ready=True ADB 正常 Redmi M2012K11AC 1080x2400`
- 22:58:44(安装版) `[ADB] 定位 adb: C:\Program Files\PokemonAutomation\_internal\adb\platform-tools\adb.exe`

---

## 三、整改中新发现并修复的 BUG

### 3.1 OCR 模型未打包 — 客户版 OCR 初始化即失败

- **现象/根因**: rapidocr(PP-OCRv6)与 rapidocr_onnxruntime(PP-OCRv4 兜底)
  的 `default_models.yaml`/`config.yaml`/`models/*.onnx`/`arch_config.yaml`
  未进安装包 → 客户版页面识别功能从启动起即不可用。
- **修复**: [packaging/pokemon_automation.spec](packaging/pokemon_automation.spec)
  完整打包两套引擎资源。
- **证据(同日志前后对照)**:
  - 14:14:07(修复前冻结版) `[OCR] rapidocr 不可用([Errno 2] ... default_models.yaml)` /
    `[OCR] rapidocr_onnxruntime 不可用(... config.yaml does not exist!)`
  - 22:10:09(修复后) `[OCR] 使用 rapidocr(PP-OCRv6)`

### 3.2 VPN 弹窗每 120s 重复弹出

- **根因**: [desktop/device_monitor.py](desktop/device_monitor.py) 弹窗条件写成
  `prev is not True` — Python 身份比较, `False is not True` 恒真 →
  已知缺失状态下每轮检测都再弹一次, 弹窗叠加。
- **修复**: 改为 `prev is not False`(仅首次缺失/由开转关才弹), 并加
  [desktop/main_window.py:433](desktop/main_window.py#L433) 重入保护。
- **证据**: 端到端实测全程只出现 1 个 VPN 弹窗(22:58:50 首次检测后
  持续多轮检测, 无重复弹窗)。

### 3.3 「停止全部」后 GUI「运行中Worker」计数不归零

- **现象**: 停止后 Worker 线程 17-95s 内已退出, GUI 计数仍显示 1。
- **根因**: [core/task_scheduler.py:253](core/task_scheduler.py#L253)
  `snapshot()` 读 `_runtimes[]` 残留状态 — `stop()` 清掉 `_workers`
  但不清 `_runtimes`, 快照把死 worker 的旧状态继续报为「运行中」。
- **修复**: 快照以 Worker 线程存活为准
  (`alive = d.serial in self._workers`), 死 worker 一律报 `-`。
- **证据**:
  - 修复前 `c:\temp\gui_drive_result.txt`: `worker_after_stop = "运行中Worker: 1"`(bug 复现)
  - 修复后 `c:\temp\gui_drive_result2.txt`: `worker_after_stop = "运行中Worker: 0"` ✅
  - 回归测试 `tests/test_task_scheduler.py`
    `test_stop_clears_snapshot_worker_state` /
    `test_stop_device_clears_snapshot_worker_state`; 全套 232 测试通过。

### 3.4 QQ 取号日志泄漏完整账号名

- **现象/根因**: [core/qq_provider.py:319](core/qq_provider.py#L319)
  `[QQ] 账号 {acc}` 直接打印完整账号 — 实测日志出现
  `[QQ] 账号 Rk3***658` / `[QQ] 账号 Dr3***820`, 违反账号脱敏要求。
- **修复**: 改打 `mask_account(acc)`(形态 `Rk3***658`); 回归测试
  `tests/test_pokemon_go.py::test_fetch_latest_masks_accounts`。

### 3.5 测试工具自身 BUG(不随客户版发布, 记录备查)

- GUI 验证驱动的 `pywinauto connect(handle=..., timeout=...)` 在
  pywinauto<0.6.9 上直接抛 `ValueError: Timeout could be specified with
  path param only`, 被 `except: pass` 吞掉 → 弹窗永远关不掉。
  已移除 `timeout`(仅 `path=` 场景可用), 并在 0.6.3/0.6.9 双版本验证。

### 3.6 安装版 Worker 启动失败: 设备日志写入安装目录 Permission denied

- **现象(安装版驱动实测, 本报告首次暴露)**: 终版安装包全新安装后
  启动 Worker, 日志报
  `23:18:26 [ERROR] desktop.controller: [桌面] 启动失败: [Errno 13]
  Permission denied: 'C:\Program Files\PokemonAutomation\_internal\
  logs\device_e98bee5a.log'` → Worker 永远到不了运行态(驱动 FAIL:
  运行中Worker 一直 0)。开发目录(Dist 直跑)测不出来 — 目录可写;
  只有 Program Files 只读安装位置才会炸。
- **真实根因**: [core/logger.py:152](core/logger.py#L152)
  `_attach_device_handler` 里 `ControlConfig.load()` 默认
  `game_name="game"`, 与桌面版经 `load_with_data_dirs(
  game_name="pokemon_go")` 注册的单例不匹配 → **另建了一个新配置
  实例** → `logs_dir` 退回默认 `project_root/logs` = 冻结版
  `_internal\logs`(Program Files 只读)→ FileHandler 抛
  PermissionError → 异常一路穿透到 Worker 启动 → 启动中止。
  即: 配置单例文档承诺"模块内 load() 指向本实例", 实现却因
  game_name 不一致而违约。
- **修复(双层)**:
  1. [core/config.py:262](core/config.py#L262) 桌面版注册数据目录
     单例时置 `_registered_with_data_dirs` 标志; 模块内默认 `load()`
     直接返回该实例(`reset()` 清标志, 测试/CLI 显式 game_name 语义
     不变)。
  2. [core/logger.py:145](core/logger.py#L145) `_attach_device_handler`
     容错: 日志文件创建失败仅告警「仅主日志记录」并返回 — **自动化
     不得因日志不可写而启动失败**(纵深防御, 即使路径再次出错也不
     再打死 Worker)。
- **回归测试**: `tests/test_logger.py` 4 例 — 默认 load() 返回注册
  单例 / 设备日志落在注入数据目录 / 创建失败不抛异常 / 显式
  game_name 语义不变; 全套 236 测试通过。
- **修复实测(安装版驱动, 同一日志文件前后对比)**:
  - 修复前 23:18:26 `[ERROR] 启动失败: [Errno 13] Permission denied:
    ...\Program Files\PokemonAutomation\_internal\logs\device_e98bee5a.log`;
  - 修复后 23:37:12 `[调度器] Worker 已启动: e98bee5a` →
    `启动完成: 1 台设备运行` → 23:37:26 `Worker 退出`;
  - 设备日志现落在 `%LOCALAPPDATA%\PokemonAutomation\logs\
    device_e98bee5a.log`(23:37:26), 不再触碰安装目录;
  - `gui_drive_result4.txt` result=PASS, worker_after_start=
    "运行中Worker: 1" → worker_after_stop="运行中Worker: 0"。

### 3.7 VPN 检测误报: 机场/Clash 开着仍弹「未检测到 VPN」

- **现象(用户现场)**: 手机上 Clash Meta Alpha 已运行、机场隧道在跑,
  GUI 仍弹「VPN 未检测到」, 详情 `VpnNetworkProvider=0`。
- **真机取证**(e98bee5a, Redmi M2012K11AC):
  - `dumpsys connectivity`: `3: VpnNetworkProvider:0`(系统计数恒 0);
  - `ip link`: `27: tun0: <POINTOPOINT,UP,LOWER_UP>` 存在;
  - `ip rule`: `12000: from all iif tun0 lookup 97` /
    `17000: from all iif lo oif tun0 uidrange 0-99999 lookup 1027`
    — 全部 App 流量被规则打进 tun0; 表 1027 内是整个 IPv4 网段
    (`1.0.0.0/8` … `255.255.255.254`)全指向 `dev tun0` → 隧道活且
    在接管流量;
  - 进程: `com.github.metacubex.clash.alpha` 主进程+后台进程均在跑。
- **真实根因**: Clash Meta 用 **root/内核模式 TUN** 创建隧道, 不走
  Android VpnService 注册 → ConnectivityService 永远不知道它 →
  `VpnNetworkProvider` 恒为 0。而 [desktop/vpn_check.py](desktop/vpn_check.py)
  看到计数为 0 就直接判"无 VPN"(兜底只在"完全没有 provider 行"时
  才触发)→ 开着机场也误报弹窗。**VPN 与机场代理不是一回事**:
  纯代理模式(HTTP/SOCKS)在系统层面确实不是 VPN, 且游戏/登录页
  流量不会自动走代理; 只有 TUN/虚拟网卡模式才构成系统级隧道。
- **修复**:
  1. `check_vpn`: 计数为 0(或无该行)时同样兜底 `ip link` 查
     tun*/wg*/ppp* 接口, 有接口即判在线(接口存在 ⇒ tun 持有进程
     存活 ⇒ 隧道活跃);
  2. 弹窗文案补充「机场/Clash 纯代理模式不算系统 VPN, 需开启
     TUN/虚拟网卡(VPN 模式)让隧道接管流量; 开启后几秒内生效」;
  3. 回归测试 5 例(计数 0+tun → 在线 / 0+无 tun → 离线 / 计数 1 →
     在线 / 无 provider 行 → tun 兜底 / ip link 失败 → 离线带原因)。
- **修复实测(真机)**: 同一台手机直接调用 `check_vpn` →
  `vpn_ok=True`, detail=`接口 tun0(VpnNetworkProvider=0; root/内核
  模式 TUN)`; 全套 241 测试通过。
- **修复实测(安装版端到端, `c:\temp\gui_drive_result5.txt`)**:
  - 日志 00:01:00 `[VPN] 设备 e98bee5a VPN=在线 (接口 tun0
    (VpnNetworkProvider=0; root/内核模式 TUN))`;
  - 00:01:11 `[VPN] 运行前预检 e98bee5a: ok=True` — 预检静默通过,
    **不再弹「VPN 未检测到」弹窗**(result 中 vpn_dialog_handled=[]);
  - 00:01:14 `Worker 已启动` → 运行中Worker: 1 → 停止后 0, result=PASS。

---

## 四、本轮新增功能: 手机 VPN 检测 + 弹窗提醒(用户要求)

- **检测**: 每 120s 读 `adb shell dumpsys connectivity` 的
  `VpnNetworkProvider` 计数(0=关, ≥1=开), 兜底 `ip link` 枚举
  tun*/wg*/ppp* 接口([desktop/device_monitor.py:140](desktop/device_monitor.py#L140))。
  **2026-08-19 增补**: root/内核模式 TUN(Clash Meta kernel tun 等)
  不注册 VpnService → VpnNetworkProvider 恒为 0 但隧道真实存在,
  故计数为 0 时同样兜底查 tun 接口(见 §3.7)。
- **弹窗**: 未检测到 VPN → 弹「VPN 未检测到」, 正文说明 PTC 登录跳转
  超时风险、检测详情, 并注明「机场/Clash 纯代理模式不算系统 VPN,
  需开 TUN/虚拟网卡(VPN 模式)」; 按钮为 **「已开启VPN」**(用户指定
  文案), 另有「关闭」; 运行前预检弹窗([desktop/main_window.py:413](desktop/main_window.py#L413))
  提供「仍然继续/取消运行」。
- **实测证据**(`c:\temp\gui_drive_result2.txt` + 日志):
  - 弹窗正文: `手机 e98bee5a 未检测到 VPN 连接。... 检测详情: VpnNetworkProvider=0`
  - 按钮点击: `已开启VPN`(监控弹窗)、`仍然继续`(预检弹窗)均被验证;
  - 日志 21:56:23 `[VPN] 设备 e98bee5a VPN=未检测到 (VpnNetworkProvider=0)`;
    22:09:52 `[VPN] 运行前预检 e98bee5a: ok=False (VpnNetworkProvider=0)`。
- **与「卡在游戏登录界面」的关联(此前会话已定位)**: 手机无 VPN 时
  PTC 提交后系统跳转超时 → 自动化卡登录界面反复重试。本弹窗在
  运行前/运行中直接提示运营开 VPN, 从源头避免该卡死。

---

## 五、正式客户安装包与升级

- **安装包**: `release\宝可梦自动化购买脚本_Setup_1.0.2.exe`
  (Inno Setup 6, `packaging\installer.iss`; 版本号唯一源 version.py,
  `build_installer.bat` 字面量提取 `APP_VERSION = ` 防止被
  `APP_VERSION_TAG` 行污染)。
- **升级机制**: 固定 AppId + 同目录覆盖; 客户数据在
  `%LOCALAPPDATA%\PokemonAutomation`(升级/卸载均不触碰, 卸载默认保留)。
- **升级实测**: 1.0.0 → 1.0.2 静默升级(`/VERYSILENT /SUPPRESSMSGBOXES
  /NORESTART`)成功; 安装目录 `_internal` 含 rapidocr 两套模型、
  `adb\platform-tools\adb.exe`、u2 资源(u2.jar/app-uiautomator.apk);
  数据目录 backups/config/database/error_reports/exports/logs/runtime/
  screenshots 全部保留。
- **安装版运行实测**: 从 `C:\Program Files\PokemonAutomation` 启动 →
  ADB 定位到安装目录内置 adb → 设备 READY → VPN 检测与弹窗正常 →
  界面计数 检测到设备: 1 / READY设备: 1 / 运行中Worker: 0。
- **冒烟验证(安装版)**: `c:\temp\smoke_installed.py` 实测
  `{"result": "PASS", "window_found": true, "vpn_clicks": ["已开启VPN"],
  "detected": "检测到设备: 1", "ready": "READY设备: 1",
  "worker": "运行中Worker: 0", "close_sent": true}`。
- **终版驱动验证(含 §3.6 修复的最终安装包)**: `c:\temp\
  gui_drive_result4.txt` result=PASS — 检测到设备: 1 / READY设备: 1 /
  运行中Worker: 1 → 0; VPN 弹窗与「仍然继续」预检、toast OK 全部
  实测点击, 证据见清单 #19。

---

## 六、验收清单

| # | 要求 | 状态 | 证据 |
|---|------|------|------|
| 1 | CMD 黑框彻底消除(运行中零可见控制台窗口) | ✅ | `c:\temp\flash2_result.txt`: APP_CONHOST_COUNT=345 全为 adb.exe 链, VISIBLE_CONSOLE_WINDOW_COUNT=0 |
| 2 | 统一 run_hidden_process(STARTF_USESHOWWINDOW+SW_HIDE+CREATE_NO_WINDOW) | ✅ | [desktop/process_runner.py:70](desktop/process_runner.py#L70) + 全局 monkeypatch |
| 3 | 禁止 shell=True 作为普通 ADB 调用; 无运行期 BAT; 无 pause/cmd /k | ✅ | 代码复核; flash 证据中程序之下零 cmd.exe |
| 4 | PowerShell -NoProfile -NonInteractive -WindowStyle Hidden | ✅ | process_runner 统一封装 |
| 5 | 唯一 AdbLocator + 捆绑 platform-tools 优先 | ✅ | 日志: `[ADB] 定位 adb: ..._internal\adb\platform-tools\adb.exe`(安装版为 Program Files 路径) |
| 6 | devices -l 前 start-server + 重试 ×3 | ✅ | adb_locator 实现; 日志 22:17:18 显示枚举失败重试 |
| 7 | DeviceRegistry 单一设备状态源 | ✅ | [desktop/device_registry.py](desktop/device_registry.py); 各层消费同一注册表 |
| 8 | DEVICE_DISCOVERY 带 REJECT_REASON | ✅ | 日志: `[DEVICE_DISCOVERY] 新设备 e98bee5a state=device` |
| 9 | GUI 计数拆三指标(检测到/READY/运行中) | ✅ | `gui_drive_result2.txt`: 检测到设备: 1 / READY设备: 1 / 运行中Worker: 1→0 |
| 10 | 禁止 `if count==0: count=1` 假修复 | ✅ | 未出现; 逐层真根因修复(见第二节表) |
| 11 | 手机已连接时 GUI 正确显示设备 | ✅ | `gui_drive_result2.txt` result=PASS |
| 12 | 停止全部后计数归零 | ✅ | `gui_drive_result2.txt` worker_after_stop="运行中Worker: 0" |
| 13 | 账号日志脱敏 | ✅ | qq_provider mask_account 修复 + 回归测试; 日志仅 Rk3\*\*\*658 形态 |
| 14 | VPN 检测 + 未检测到弹窗 | ✅ | gui_drive_result2.txt 弹窗正文/按钮点击证据; 日志 [VPN] 行; §3.7 增补 root 模式 TUN 兜底 + 真机实测 vpn_ok=True(修复 Clash 误报) |
| 15 | 弹窗按钮文案「已开启VPN」 | ✅ | 实测点击记录 `"已开启VPN"` |
| 16 | OCR 模型随包发布 | ✅ | 日志 22:10:09 `[OCR] 使用 rapidocr(PP-OCRv6)`; 安装目录含两套模型 |
| 17 | 正式客户安装包(Inno Setup, 桌面/开始菜单/卸载) | ✅ | `release\宝可梦自动化购买脚本_Setup_1.0.2.exe` |
| 18 | 升级保留客户数据 | ✅ | 升级实测: LOCALAPPDATA 全目录保留 |
| 19 | 安装版从 Program Files 启动可用 | ✅ | `gui_drive_result4.txt` result=PASS: 运行中Worker 1→0; 日志 23:37:12 Worker 已启动(修复前 23:18:26 同场景 Permission denied) |
| 20 | 双 BUG 根因报告 | ✅ | 本文档 |
| 21 | 干净机器(无 Python/无 ADB)全新安装测试 | ⚠️ | 本机已装开发环境, 未在干净机器实测; 环境自检/内置 VC++/捆绑 ADB 已实现, 逻辑上自包含, 但如实声明未实测 |
| 22 | 生产 3 循环测试(3 账号完整跑通) | ⚠️ | 阻塞于运营侧: 手机 VPN 未开 + PTC 凭据待核对(见 2026-08-16 报告), 代码侧无阻塞 |
| 23 | 真实购买链路 | ⚠️ | purchase.mode=dry_run 阻断真实支付(安全设计); 自动支付需 CONTROL_CENTER_ALLOW_PAYMENT=1 双重开关, 未实测 |

---

## 七、⚠️ NOT TESTED(如实声明)

- **干净机器全新安装**: 未在无 Python/ADB 环境实测(见清单 #21)。
- **生产 3 循环端到端**: 手机 VPN 与 PTC 凭据为运营侧问题, 3 循环
  测试继续挂起(记忆: remediation-2026-08-16)。
- **3 设备并发 / 真实购买**: 本机 1 台真机; 支付双重开关未实测。

## 八、结论

两个交付级 BUG(CMD 闪窗 / 0 设备)均已找到真实根因、修复并经
端到端客观证据验证; 整改中额外发现并修复 6 个 BUG(OCR 打包、
VPN 弹窗重复、停止计数不归零、账号日志泄漏、安装版设备日志
Permission denied、VPN 检测对 root 模式 TUN 误报); 新增 VPN 检测
弹窗功能; 正式安装包 1.0.2 已构建并完成升级安装 + 安装版运行
验证。剩余 ⚠️ 项均为运营侧/环境侧阻塞, 与代码无关, 已如实标注。
