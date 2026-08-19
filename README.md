# Android 多设备游戏自动化中控系统

Windows 电脑 + 多台 Android 手机 = 全自动游戏任务流水线。

```
自动检测所有ADB设备 → 获取设备信息 → 初始化每台设备 → 建立独立 Worker
→ 从统一账号任务队列原子领取账号 → 启动目标游戏 → 识别当前页面
→ 自动执行登录流程 → 执行配置好的游戏任务 → 验证任务结果
→ 退出当前账号 → 领取下一个账号 → 循环执行
```

每台设备一个独立线程 + 独立 uiautomator2 会话，一台手机卡死不影响其他手机。

> 本仓库同时包含早期项目「抖音自动化评论运营系统」(`comment_bot/`、`douyin_core/`)，其使用方法见 [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md) 与本节末尾说明。中控系统的图像匹配器是统一实现，抖音项目已改为复用它。

---

## 一、项目架构

```text
main.py                  # 统一 CLI: doctor/devices/init/run/import-accounts/export-results/api
requirements-control.txt # 中控系统依赖
config/
  config.yaml            # 系统配置(并发数/超时/重试/支付安全)
  devices.yaml           # 设备级覆盖(可选)
  game.yaml              # 游戏适配配置(包名/页面识别/弹窗/登录/退出/任务步骤)
core/
  adb_manager.py         # ADB 封装(设备发现/属性/截图/输入) — 所有 subprocess 集中于此
  device_manager.py      # 设备扫描/初始化/DeviceController(每设备 u2 会话)
  device_worker.py       # DeviceWorker — 每设备一个线程的账号流水线
  task_scheduler.py      # 多设备并发调度 + 状态看板 + 卡死账号恢复
  account_manager.py     # AccountProvider: Excel/CSV/SQLite/HTTP 导入
  state_machine.py       # Worker 状态机(含超时 → RECOVERY)
  watchdog.py            # 异常监控 + 8 级恢复
  image_matcher.py       # 统一 OpenCV 模板匹配(threshold/ROI/scale/timeout)
  ui_detector.py         # 页面状态识别(UI层级 + 模板 + OCR可选)
  popup_handler.py       # 弹窗处理(公告/更新/权限/网络重试...)
  actions.py             # 动作执行器(click_text/click_image/verify/... + 支付护栏)
  coordinate.py          # 分辨率适配(基准1080×2400 → 任意分辨率)
  logger.py              # 统一日志(主日志 + 每设备独立日志 + 账号脱敏)
  exceptions.py          # 异常体系(对应恢复等级)
models/                  # Account / AndroidDevice / TaskResult / PageState
storage/                 # SQLite(runtime.db) + 账号队列/任务结果仓库
automation/
  base_game.py           # BaseGameAutomation 统一接口(launch/login/execute_task/...)
  target_game.py         # DouyinAutomation(参考适配) + 新游戏适配模板
api/                     # FastAPI 中控后台 + WebSocket 实时状态
scripts/                 # check_devices / init_devices / doctor / dump_hierarchy
templates/game/          # 游戏模板图片目录
screenshots/             # 失败现场截图(按 日期/设备/账号 归档)
logs/                    # 主日志 + device_<serial>.log
data/                    # accounts 导入源、runtime.db、results_*.xlsx(不入 Git)
tests/                   # 单元测试(Mock ADB, 与真机测试严格区分)
```

### 数据流

1. **账号进入队列**: `python main.py import-accounts data/accounts.xlsx`(或 CSV/SQLite/HTTP URL,或 `POST /api/accounts`)→ 写入 `data/runtime.db` 的 `accounts` 表,状态 PENDING
2. **设备领取账号**: 每个 DeviceWorker 通过 SQLite `BEGIN IMMEDIATE` 事务**原子领取**(PENDING/RETRY → LOCKED → RUNNING),并发下不可能两台设备领到同一账号
3. **Worker 运行**: CHECK_DEVICE → START_GAME → DETECT_PAGE → LOGIN → WAIT_HOME → HANDLE_POPUPS → EXECUTE_TASK → VERIFY_TASK → LOGOUT → CLEANUP → NEXT_ACCOUNT;任何状态超时/异常 → RECOVERY(Watchdog 8 级恢复);超过账号 max_retry → FAILED
4. **结果保存**: 每次执行写入 `task_results` 表 → `python main.py export-results` 导出 Excel
5. **崩溃恢复**: 程序意外退出后,卡在 LOCKED/RUNNING 的账号由启动时 + 周期清扫自动恢复

---

## 二、环境要求

| 组件 | 要求 |
|------|------|
| 操作系统 | Windows 10/11(其他平台只需替换 ADB 路径) |
| Python | 3.11+ |
| ADB | 任意版本(项目已捆绑 `adb/platform-tools/adb.exe`,也可用系统安装的) |
| 手机 | Android 7+, 开启 USB 调试 |
| 目标游戏 | 已安装(doctor 会检查) |

## 三、安装步骤

```bash
# 1. 下载项目
git clone https://github.com/DaShengGuo/automation-operations.git
cd automation-operations

# 2. 创建虚拟环境并安装依赖
py -3.11 -m venv .venv-control        # 或 py -3.13
.venv-control\Scripts\python.exe -m pip install -r requirements-control.txt
```

> 可选: `.env`(复制 `.env.example`)可覆盖 `ADB_PATH`、`LOG_LEVEL` 等。
> 可选 OCR: `pip install paddleocr paddlepaddle`(仅页面 OCR 识别需要,不装不影响核心功能)

## 四、手机连接

1. 手机打开「设置 → 开发者选项 → USB 调试」
2. 数据线连接电脑(需可传数据)
3. 手机弹窗点「**允许 USB 调试**」(可勾选始终允许)
4. 验证:

```bash
.venv-control\Scripts\python.exe main.py doctor    # 环境体检(应全部 PASS)
.venv-control\Scripts\python.exe main.py devices   # 查看手机
.venv-control\Scripts\python.exe main.py init      # 初始化手机(亮屏/解锁/截图/点击测试)
```

更多手机设置细节见 [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md)。

## 五、账号导入

账号文件格式(xlsx / csv 均可,列名支持 `account/账号/username/手机号` 与 `password/密码`):

| account | password |
|---------|----------|
| 13800138000 | abc123 |

```bash
# Excel / CSV / SQLite(accounts 表) / HTTP URL 均可
.venv-control\Scripts\python.exe main.py import-accounts data\accounts.xlsx
.venv-control\Scripts\python.exe main.py import-accounts data\accounts.csv
.venv-control\Scripts\python.exe main.py import-accounts http://127.0.0.1:9000/accounts
```

- 重复账号自动跳过;`--max-retry N` 设置账号最大失败重试次数
- 外部程序也可随时 `POST http://127.0.0.1:8900/api/accounts` 动态加账号(不强耦合任何聊天软件)
- 账号密码只存在本地 `data/runtime.db`(已加入 .gitignore),**日志与 API 输出全部脱敏**(`138***000` / `******`)

## 六、启动自动化

```bash
# 全部设备
.venv-control\Scripts\python.exe main.py run

# 指定设备 / 并发数 / Web 后台
.venv-control\Scripts\python.exe main.py run --device e98bee5a
.venv-control\Scripts\python.exe main.py run --workers 10
.venv-control\Scripts\python.exe main.py run --web          # 同时开 Web 后台
.venv-control\Scripts\python.exe main.py run --no-logout    # 测试用:跳过退出登录
```

运行中实时看板(每 5 秒刷新):

```text
======================================================================
Android 多设备游戏自动化中控
======================================================================
设备总数：12
在线：11
异常：1

设备 e98bee5a       RUNNING      tes***001        EXECUTE_TASK
设备 AADE9X38...    SUCCESS      -                IDLE
======================================================================
成功：105
失败：3
待执行：482
执行中：10
======================================================================
```

Ctrl+C 安全退出:Worker 会把执行中的账号归还队列(RETRY),不会卡死在 RUNNING。

## 七、Web 中控后台

```bash
.venv-control\Scripts\python.exe main.py api            # 仅后台
.venv-control\Scripts\python.exe main.py run --web      # 自动化 + 后台
# 打开 http://127.0.0.1:8900 (交互式文档 /docs)
```

| 接口 | 说明 |
|------|------|
| GET /api/devices / api/accounts / api/tasks / api/statistics | 查询 |
| POST /api/system/start / pause / stop | 系统控制 |
| POST /api/devices/{serial}/start / stop / restart | 单设备控制 |
| POST /api/accounts | 账号动态导入 |
| WS /ws/status | 每 2 秒推送全量状态 |

## 八、任务结果与日志

```bash
.venv-control\Scripts\python.exe main.py export-results
# → data/results_20260813.xlsx(账号/设备/开始/结束/耗时/结果/失败步骤/原因/重试/截图, 账号脱敏)
```

- **日志**: `logs/control_YYYY-MM-DD.log`(主日志)+ `logs/device_<serial>.log`(每设备独立)
  格式: `2026-08-13 12:11:10 [INFO] DEVICE=e98bee5a ACCOUNT=tes***001 STATE=LOGIN ACTION=CLICK_LOGIN RESULT=SUCCESS TIME=1.32s ...`
- **失败现场**: 任何失败自动截图 → `screenshots/2026-08-13/device_<serial>/account_<id>/<STATE>_<REASON>_<时间戳>.png`(+ 同名 .txt 记录异常详情),文件名含时间戳,永不覆盖

## 九、Pokémon GO 适配器

```bash
.venv-control\Scripts\python.exe main.py run --game pokemon_go --device e98bee5a
.venv-control\Scripts\python.exe main.py inspect --device e98bee5a --game pokemon_go   # 页面标定工具
```

- 完整业务循环: 已註冊的玩家 → PTC 登录方式 → **点击 PTC 后由 Android 系统自动调起该手机默认浏览器(任意品牌)** → PTC 网页登录(识别靠网页内容特征: `access.pokemon.com` / `Email or username` / `Password` / `Log In`)→ 认证 → 自动返回游戏 → 首次流程(存在则处理)→ MAP → 主菜单 → 商店 → 找目标商品(默认 `100寶可幣`)→ Google Play 购买页(**默认 manual 模式: 到支付页暂停, 人工完成后脚本自动检测结果**)→ 关商店 → 设置 → 登出 → 验证回到已註冊的玩家 → 下一账号
- **浏览器无关原则**: 不指定浏览器品牌、不维护浏览器白名单、不硬编码浏览器包名。五台设备分别用不同浏览器也能走同一套登录逻辑(已真机验证两种不同浏览器)
- **页面检测**: 游戏是 Unity 引擎(hierarchy 无业务文字)→ 检测 = 模板匹配 + OCR 关键词(多语言片段匹配, 容忍 OCR 对繁体字的误差); 浏览器网页 → hierarchy text/hint/desc
- **支付安全**: `game_pokemon_go.yaml → purchase.mode`: `manual`(默认) / `dry_run`(只读商品信息) / `sandbox`(测试环境+`.env` 双重开关)
- **QQ 群取号**: `config.yaml` 设 `account_provider: qq_ui` + `account_provider_qq_group: 游戏自动化购买` 后,队列为空时每台设备自动切到自己手机已登录的 QQ 群读取最新账号(上一条消息=账号,下一条=密码),读完切回游戏;账号入库自动去重。不配置则保持手动导入(Excel/CSV/HTTP)
- 真机验证状态与逐项验收见下方「十四、测试」与仓库维护记录

## 十、页面标定(新游戏适配必读)

所有游戏特定信息集中在 `config/game.yaml` + `automation/target_game.py`。当前适配的是抖音(参考实现)。换新游戏:

1. 手机连电脑,运行 `python scripts/dump_hierarchy.py <SERIAL>` 导出当前页面 UI 层级 XML
2. 在 XML 中找按钮的 `text` / `resource-id` / `content-desc`,填入 game.yaml 对应段:
   - `pages`: 每个页面的识别规则(文本/描述/资源ID/模板/OCR 关键词)
   - `popups`: 弹窗判定 + 关闭动作
   - `login`: 账号框/密码框/登录按钮选择器 + 错误文本分类
   - `logout`: 退出登录路径动作
   - `steps`: 任务步骤(动作类型见 [core/actions.py](core/actions.py) 顶部注释)
   - `verify`: 结果验证(text/image/page)
3. 找不到稳定控件的纯图形按钮 → 截图裁剪保存为 `templates/game/xxx.png`,在配置中按文件名引用(见 [templates/game/README.md](templates/game/README.md))
4. 复杂流程 YAML 表达不了的,在 `automation/` 新增 `BaseGameAutomation` 子类覆写对应方法,并注册到 `automation/__init__.ADAPTERS`

**UNKNOWN_SELECTOR 约定**: 未标定的选择器一律留空,运行时明确报错「选择器未标定」,绝不瞎猜坐标。
定位优先级: UI 控件 → resource-id → text/content-desc → 模板识别 → 比例坐标 → 固定坐标(最后兜底)。

## 十一、安全设计

- **支付护栏**: `config.yaml → payment.dry_run: true`(默认)。自动化只能读取商品信息(名称/金额/页面验证),**绝不自动点击真实支付确认**。双重开关:需同时 `dry_run: false` 且 `.env` 中 `CONTROL_CENTER_ALLOW_PAYMENT=1` 才会放行。
- **敏感信息**: 账号密码不写死源码、不入 Git(`data/runtime.db`、`data/accounts.*`、`.env` 均已 gitignore),日志/导出/API 全脱敏。
- 本系统不包含也不建议: 绕过反作弊、伪造设备身份、绕过登录限制等。

## 十二、测试

```bash
# 单元测试(Mock ADB, 不依赖真机; 共 5 个测试文件)
.venv-control\Scripts\python.exe -m pytest tests/test_coordinate_mapper.py tests/test_image_matcher.py tests/test_state_machine.py tests/test_account_queue.py tests/test_device_manager.py -q

# 抖音旧项目测试
python -m pytest tests/test_integration.py -q
```

**Mock 测试 ≠ 真机测试**。真机验证请运行:

```bash
.venv-control\Scripts\python.exe main.py doctor          # 环境体检(真机逐项实测)
.venv-control\Scripts\python.exe main.py doctor --compat # 设备兼容性报告(未实测标 NOT TESTED)
```

## 十三、错误排查

| 现象 | 处理 |
|------|------|
| doctor 报「ADB 设备 — 未检测到任何设备」 | 重插数据线;手机重新开关 USB 调试;`adb kill-server && adb devices` |
| 报「未授权」 | 手机上点「允许 USB 调试」 |
| uiautomator2 连接失败 | 手机保持亮屏;重新 `main.py init` |
| 设备执行中变 DEVICE_ERROR | 看 `logs/device_<serial>.log` 与 `screenshots/` 失败现场;`main.py init` 或 API restart 恢复 |
| 账号卡在 LOCKED/RUNNING | 程序自动清扫(默认 10 分钟无心跳即恢复);也可重启程序 |
| 任务一直失败 | `python scripts/dump_hierarchy.py <SERIAL>` 检查页面选择器是否随游戏版本变化,更新 game.yaml |
| 看板/日志中文乱码 | 用支持 UTF-8 的终端(Windows Terminal / VSCode);程序已强制 UTF-8 输出 |

## 十四、开发扩展

- **加新游戏**: 见「九、页面标定」,只需新增 Adapter + 一份 game.yaml
- **加账号来源**: `core/account_manager.py` 新增 `AccountProvider` 子类
- **加步骤动作**: `core/actions.py` 新增 `_do_xxx` 方法
- **改造旧抖音项目**: 抖音项目(`douyin_core/`、`comment_bot/`)运行方式不变(`start.bat` / `install.bat` / `check-device.bat`),其图像匹配已统一复用中控的 `core/image_matcher.py`

---

# 十五、客户版使用说明(Windows 桌面软件)

客户只需要一个文件: `宝可梦自动化购买脚本_Setup_v1.0.0.exe`。
不需要 Python / pip / 命令行 / 源码。

## 第一次安装

1. 双击 `宝可梦自动化购买脚本_Setup_v1.0.0.exe` → 下一步 → 安装
2. 桌面出现快捷方式「宝可梦自动化购买脚本」

## 连接手机

1. 手机开启「开发者选项 → USB 调试」
2. USB 数据线连接电脑
3. 手机上弹出「允许 USB 调试」→ 点允许
4. 若软件看不到手机: 确认 Windows 已安装该手机的 USB 驱动

## 启动软件

1. 双击桌面「宝可梦自动化购买脚本」
2. 标题栏显示「欢迎使用宝可梦自动化购买脚本」+ 当前版本
3. 软件打开后处于「已停止」状态 — 不会自动操作手机

## 输入群聊并运行

1. 账号来源选择「QQ群聊」
2. 输入接收账号的 QQ 群聊名称
3. 点击「确认并运行」— 保存配置 + 环境检测 + 扫描手机 + 开始生产

## 日常操作

- **开始运行**: 从当前真实页面继续(不重新启动游戏/登录)
- **停止全部**: 所有设备在安全检查点退出(手机停在当前页面)
- **单设备停止**: 每台设备卡片上的「停止」按钮
- **重新识别**: 每台设备卡片上的「重新识别」按钮 — 识别手机真实页面并
  就地提示建议步骤
- **重置设备环境**: 每台设备卡片上的「重置设备环境」按钮 — 二次确认后
  停止该设备自动化并清除手机端 Pokémon GO 应用数据, 重新初始化后按
  手机真实页面继续(用于测试环境恢复/游戏缓存异常/登录页混乱等故障
  恢复)。默认不清理浏览器数据(保护浏览器已有登录信息, 高级选项可
  单独勾选); 只影响当前这一台手机, 其他设备继续运行; 运行日志/
  历史记录/数据库一律保留
- **重新检测设备**: 手动重新扫描手机
- **自动识别当前步骤**: 识别手机真实页面 + 给出建议下一步
- **从选择步骤重新开始**: 选择步骤后校验手机页面, 匹配才继续, 不匹配会提示
- **历史记录**: 查看今天/全部执行记录, 可导出 Excel
- **打开日志目录**: 查看完整日志文件

## 软件升级

1. 开发者发布新版 `宝可梦自动化购买脚本_Setup_v1.0.1.exe`
2. 直接双击安装 → 自动覆盖旧版程序
3. 配置、日志、数据库、历史记录全部保留
4. 不需要卸载旧版, 不需要重新配置

## 软件卸载

控制面板卸载「宝可梦自动化购买脚本」。
卸载时询问是否删除历史数据(默认保留)。

---

# 十六、开发版发布说明

## 发布新版本流程

1. 修改代码 + 测试(`pytest tests/`)
2. 修改 `version.py` 的 `APP_VERSION`(唯一版本源, 禁止其他文件写死版本号)
3. 更新 `CHANGELOG.md`
4. 双击 `scripts\build_release.bat` → 生成 `dist\宝可梦自动化购买脚本\`
5. 双击 `scripts\build_installer.bat` → 生成
   `release\宝可梦自动化购买脚本_Setup_v{版本}.exe`
6. 测试覆盖升级(见下)
7. 发布 Setup 给客户

## 升级测试清单

```
安装 v1.0.0 → 运行产生日志/SQLite 历史/保存群聊设置 → 退出
→ 安装 v1.0.1 → 验证:
  ✅ 程序更新成功(GUI 显示 v1.0.1)
  ✅ 日志仍存在(%LOCALAPPDATA%\PokemonAutomation\logs)
  ✅ 数据库仍存在(数据库 schema 迁移 + 升级前自动备份)
  ✅ 历史记录仍存在
  ✅ 配置仍存在(群聊名称)
```

## 代码签名(可选)

有 Windows 代码签名证书时, 在 `packaging/installer.iss` 的 `[Setup]` 段加:

```
SignTool=mysigntool
```

并在 Inno Setup 的 IDE 里配置 Sign Tools。当前无证书, 构建时不伪造签名。

## 目录结构

```
desktop/          桌面应用层(GUI + Controller, 复用 core/automation)
packaging/        PyInstaller spec + Inno Setup 脚本 + 图标 + 中文语言文件
scripts/          构建脚本(build_release / build_debug / build_installer)
version.py        统一版本源
migrations.py     SQLite schema 版本化迁移
CHANGELOG.md      版本更新日志
```
