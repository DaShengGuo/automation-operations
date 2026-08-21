# 更新日志

版本规则: Semantic Versioning — `v1.0.0` 第一版生产版本; `v1.0.1` 修复 BUG;
`v1.1.0` 新功能; `v2.0.0` 重大架构升级。

---

## v1.2.6 — 快速连续滑动恢复 + 全流程等待节点压缩 (2026-08-21)

用户规格 v2「保持快速连续滑动逻辑, 不要一次滑动一次确认」+ 全流程
减少无意义等待:

1. **商城滑动恢复快速连续(规格§七核心)**: v1.2.5 的单步确认模式
   (每轮 before→swipe→0.8s→after→无变化再重试) 每轮 ~1.6s, 10s 预算
   只够 6 轮, 页面未到底就被预算打断 — 这正是「卡住没到底」的新根因。
   恢复快速连续滑动: 连续 swipe 不做前后帧确认, 每轮仅短停 0.3s 让
   页面滚动停下, 判底改为「滑后静帧 vs 上一静帧, 连续 2 帧无变化」。
   3 秒可滑 ~6 轮, 与人工节奏一致。
2. **中央站渐进轮询(规格§三)**: 点已注册后等中央站, 旧 sleep(2) 粗轮询
   → 0.3/0.5/1/2s 渐进(中央站人工 <1s 出现, 页面一出现立即点击);
   同时不再嵌套 wait_external_context(2) 加 2s 颗粒, 直接 is_external_context
   即时判断。
3. **注册页 L1 上限 30→15s(规格§二)**: 内部 wait_for_state 0.2s 快速轮询
   命中即返, 15s 纯上限(人工 ~9s 出现 + 余量)。
4. **Login→主页检查点日志(规格§四)**: wait_game_return 0.5s 快速轮询
   (已有早失败) + 每 5s 进度日志 + 返回成功打用时, 让 ≥30s 资源加载
   等待节奏全程可视、不误判卡死。

- **测试**: 全量 360 条通过(滑动逻辑回归 25 条全过)。

## v1.2.5 — 商城滑动单步重写: 滑动完成确认 + 无变化重试防卡死 (2026-08-21)

用户实况「滑动第1/2/3次正常, 某次滑动后停止响应, 页面没到底, 脚本卡住」。
已确认非加载问题。重点修复 swipe 连续执行逻辑:

1. **单步滑动循环重写(规格§一)**: 旧实现连续 swipe()+短间隔, 判底在滑动
   前截图(逻辑错位 — 比的是两次滑动前的图, 不是滑动前后)。改为严格单步:
   每轮 `截图(before) → 滑一次 → 等0.8s触摸完成 → 截图(after) → 比对前后帧`。
   不连续快速 swipe()swipe()swipe() — 每次滑动后等触摸事件完成再判。
2. **滑动完成确认(规格§二)**: 滑动后等 0.8s 让页面渲染稳定, 再截图比对
   前后帧判断是否变化。
3. **无变化重试防卡死(规格§二核心)**: 滑动后页面无变化时不直接判底 —
   重试一次滑动再判。重试后页面变了 → 之前是触摸未生效, 继续正常滑动;
   重试仍无变化 → 确认到底。防止「触摸事件未完成/滑动未生效」被误判为
   到底导致脚本卡住(客户「某次滑动后停止响应」根因)。
4. **辅助方法抽出**: `_downsample_gray`(BGR2GRAY 降采样 36×80)、
   `_frame_changed`(平均差容差<4.0 视为未变, None 保守视为变化)、
   `_do_swipe`(异常吞掉, 失败由前后帧比对兜底)。

- **测试**: 新增 test_scroll_retry_on_no_change_then_bottom(无变化重试机制
  存在) + test_scroll_no_change_does_not_infinite_loop(页面钉底快速判底
  不无限滑)。全量 360 条通过。

## v1.2.4 — 商城滑动误退出根因修复: MAIN_MENU 误判 + 滑动参数 (2026-08-21)

用户实况「进商城后滑几次就异常退出商城」+ 重点排查方向「不是商城加载慢,
而是滑动期间被状态机误判强制退出」。定位真正根因并修复:

1. **MAIN_MENU 误判商城页(核心根因)**: MAIN_MENU 旧含 OCR 规则
   `[Shop, Settings]` — 商城页也有 "Shop" 文字, 滑动中某帧 OCR 识别到
   Shop 即命中 MAIN_MENU → `_shop_still_open` 触发 kicked_out → 滑动
   中止 + 重进商城(客户「滑几次异常退出」根因)。且 DETECT_ORDER 里
   MAIN_MENU 在 SHOP 之前, 商城页被抢判。修复: ① 删除 `[Shop, Settings]`
   组(主菜单已有圖鑑/對戰/Pokédex/Battle 等独有词, 不需要此组);
   ② DETECT_ORDER 把 SHOP 提到 MAIN_MENU 之前(商城优先判定)。
2. **滑动参数对齐人工实测(规格)**: 旧用 `swipe_direction("up", 0.8)`
   大幅度方向滑动。改精确坐标上滑 start_y=1800→end_y=400(基准 2400,
   ratio 0.75→0.167, 自动适配分辨率), duration=500ms, 间隔 0.4s
   (规格 0.3~0.8s) — 人工实测 3 秒到底。进店后第一次循环直接滑动,
   不等待(规格: 进店即滑)。
3. **商城滑动 BACK 守卫(规格§九)**: back_safe 新增 scrolling 标记旁路 —
   shop_auto.scrolling=True 时一律拒绝 BACK。滑动中状态可能短暂 UNKNOWN
   (OCR 未识别商品文字), 若此时恢复链路按 BACK 会退出商城。滑动期间禁止
   任何退出逻辑(返回主页/登录/切账号/重启)。
4. **商城流程日志(规格§十一)**: enter_shop + find_product 全程 [SHOP] 日志
   (点击商城/商城页面确认/开始第N次滑动/检测到底/找到目标商品)。

- **测试**: 新增 test_back_guard_rejected_during_scroll(滑动中 BACK 拒绝)
  + test_shop_not_misdetected_as_main_menu(商城含 Shop 文字不误判主菜单);
  TestShopFastScrollToBottom 2 条适配精确坐标滑动(swipe mock);
  ShopCtrl/StabilityCtrl swipe mock 同步累加 up_swipes。全量 358 条通过。

## v1.2.3 — 流程时间对齐人工实测 + 商城滑动状态锁 (2026-08-21)

以人工实测耗时为基准(启动→注册页 ~9s / 点已注册→中央站 <1s /
点中央站→账密页 ~3s / Login→主页 ≥30s / 进店+滑底 ~3s)重调全流程
超时, 消除「等待时间与真实加载不匹配」:

1. **超时值对齐人工实测(config/timeouts + budgets)**:
   - ptc_provider 60s→8s(点已注册→中央站, 人工 <1s)
   - ptc_redirect 60s→15s(点中央站→跳浏览器, 人工 ~3s)
   - ptc_page_loading 60s→12s(网页账密页, 人工 ~3s)
   - auth_return 75s→50s(Login→主页资源加载, 人工 ≥30s, 50s 超时恢复)
   - game_loading 120s→60s; shop_scroll 15s→10s(进店+滑底人工 ~3s)
   - adapter.login 代码内写死的默认值同步更新(60/60/120→8/12/50)。
2. **商城滑动状态保护锁(规格§九)**: ShopAutomation.scrolling 标记,
   find_product 进入置 True、退出(到底/异常/找到/超预算)finally 释放。
   滑动期间锁定 SHOP_SCROLLING, 禁止外部状态机介入返回主页/登录/
   下一账号/APP重启 — 只有商城到底或异常才能改变状态。
3. **判底灰度通道修复**: shop.find_product 旧用 cv2.COLOR_RGB2GRAY,
   与全局截图 BGR 通道约定不一致(detector 用 BGR2HSV) — 改 BGR2GRAY,
   判底 diff 计算与检测器统一。
4. **检查点日志(规格§十一)**: adapter 新增 _phase_t0/_elapsed/_checkpoint,
   launch/login/execute_task 全流程打 [MM:SS] 相对时间戳日志
   (启动游戏/等待注册页面/已点击已注册/检测到中央站/账号密码页面就绪/
   已提交 Login 等待资源加载/登录完成/打开主菜单/点击商城/商城加载成功/
   找到目标商品/购买完成/关闭商城/主页面恢复)。滑动中异常退出补
   [ERROR] 日志 + SHOP_KICKED_OUT_DURING_SCROLL 截图; 滑底超预算补
   SHOP_SCROLL_BUDGET_EXCEEDED 截图。

- **测试**: 新增 test_shop_scroll_state_lock_released_on_exit(到底释放锁)
  + test_shop_scroll_state_lock_released_on_exception(异常路径 finally 释放),
  全量 356 条通过。

## v1.2.2 — 启动等待智能化: 检查点日志 + MAP 二次确认 (2026-08-21)

针对「游戏已进入主页但脚本仍等几十秒」的启动等待优化(在 v1.2.1 步级
预算基础上, 对齐智能页面检测等待规格):

1. **wait_home 快速轮询分层**: 前 3s 用 0.5s 快速轮询(页面一出现立即
   返回, 快手机不等、慢手机不超时), 之后降频 2s(减少 OCR 空转)。
   home_wait 预算 20s → 30s(对齐规格检查点序列, 慢设备留余量)。
2. **检查点进度日志**: 每 5s 打一次「[步骤] 第N次检测主页(已等Xs,
   state=..., 第R轮)」, 启动等待全程节奏可视(规格日志要求)。
3. **MAP 二次确认(多特征评分)**: 首次命中 MAP/首次流程页后隔 0.8s
   再检一次(强制 bust_caches 用最新截图), 两次都是主页状态才算真正
   进入 — 防转场动画/黑屏/加载闪帧瞬时误判(规格: 避免加载动画/登录
   界面/黑屏误判)。转场瞬时命中被否决后继续等待, 不误报成功。
4. **删除 launch 残留固定 sleep(2)**: is_app_running 分支拉前台后
   原固定睡 2s 才进检测, 改为直接进 wait_for_state(0.2s 快速轮询,
   第一个 tick 命中即返回 — 规格游戏启动后不再固定等待)。
5. **wait_home 循环补 tick_heartbeat**: 等待期间心跳不停摆, 防调度器
   误判 WORKER_STALLED 重建 Worker。

- **测试**: 新增 test_wait_home_rejects_transient_home(瞬时 MAP 必须
  被二次确认否决, 稳定 MAP 才返回 True), 全量 354 条通过。

## v1.2.1 — 商城流程稳定性整改 (2026-08-20)

客户实况「主页面卡几十秒 → 点商城停留几十秒 → 没滑动自动回主页面 →
又卡几十秒」。用 8-13/8-16 真机截图 + 日志离线取证, 定位四个根因:

1. **状态缓存指纹碰撞(核心根因)**: 检测器状态缓存用 8×4 灰度感知哈希
   判断「截图未变复用上次状态」, 实测主菜单/商店/设置等暗色全屏页指纹
   大量碰撞(5 组标签共指纹) → 商城主菜单互相复用幻影状态 → 在菜单页
   「成功进店」空滑几十秒, 或商城页被判主菜单重复点击误中底部 X 关闭按钮
   → 自动回主页面。修复: 指纹加入 4×4 色块均值网格, 不同页面必然区分
   (真机语料 28 张关键帧零跨页碰撞)。
2. **SHOP 检测规则漏检**: 旧规则 `[寶可幣,IDR]` 要求 IDR 同现, 美区
   商店价格是 US$0.99(真机 PRODUCT_FOUND 截图实测) → 商城页永远检测
   失败, 进店白等 30s+10s。修复: 寶可幣/宝可币/PokéCoins/PokeCoins/
   Pokecoins/US$ 独立成组(主菜单/设置页无这些词, 实测无冲突)。
3. **MAP 检测单一证据**: 只有精灵球模板。新增红色像素占比色块证据
   (地图 0.048 vs 其它页 ≤0.013, 阈值 0.025 两侧 2 倍余量) + OCR
   [目前位置](真机地图顶部状态栏)。真机地图三条证据同时命中。
4. **固定等待/状态机断裂**: 按步级看门狗预算(主页 20s/进商城 15s/
   滑底 15s/购买页 20s, 全部 yaml 可配)重做 — wait_home 超时截图
   HOME_TIMEOUT+暖启动重入(两轮上限, Worker 两轮失败立即 RECOVERY);
   enter_shop 点击未生效→重新点击(重试循环内绝不点比例坐标 — 那会
   命中商城 X 关闭); 已在商城直接返回(重进场景); 滑动中检测到首页
   UI 出现 → 标记商城异常退出 → 停止滑动 → 重进商城(≤2 次), 绝不
   静默进入下一步; 滑底靠连续两次截图无变化判定(禁止到底后继续滑)。
   全程 [步骤] 时间戳日志(检测主页成功/点击商城/商城加载成功/开始
   滑动到底部/商城到底/购买完成), 失败附错误截图路径。

- **测试**: 新增 `tests/test_shop_stability.py` 10 条(重试不点比例坐标/
  进店超时暖启动/滑动中异常退出提前停止/重进≤2 找到商品/wait_home 超时
  重入/两轮失败交 RECOVERY/红色色块兜底/指纹防碰撞/Worker 两轮失败
  RECOVERY), 全量 353 条通过。

---

## v1.2.0 — 人工按设备账号密码队列 (2026-08-20)

账号来源彻底改造: 移除「QQ群自动取号」生产入口, 改为人工按手机设备输入
账号密码队列。原有自动化核心(购买/登录/风控处理)全部保持不变。

主要变更:
- **账号来源**: 新增 `ManualDeviceQueueProvider` 为默认账号源, 队列以
  ADB Serial 为 Key(绝不按型号), 每台手机完全独立 FIFO 队列, 严格
  禁止跨设备偷号; 全局执行锁保证同一账号绝不同时在两台设备上运行
- **GUI 设备卡片**: 每台设备带独立队列表格(账号/状态/加入时间/重试),
  单号添加(密码默认隐藏 + 👁 查看)、批量添加(支持 `----` > Tab > 逗号
  三种分隔符, 实时逐行校验缺少密码/账号为空/重复, 预览确认后入队),
  待执行账号支持 编辑/删除/上移/下移/插到队首/清空待执行(仅「等待」可
  编辑, 「运行中」受保护, 删除/清空需二次确认且保留运行中与已中断)
- **队列语义**: 停止保留等待队列, 重启优先恢复被打断的当前账号
  (INTERRUPTED 不烧重试); 运行中动态加号不打断当前账号, 空队列显示
  「等待账号」, 新账号加入毫秒级自动唤醒 Worker; 断线/重连/重置环境
  队列全部保留, 仅应用关闭时清空(关闭前提示待执行数量)
- **安全**: 密码绝不出现在 GUI 表格/日志/SQLite 历史中, 关闭确认与
  删除确认默认「取消」
- **QQ 清理**: GUI 移除 QQ 群入口, 旧配置字段(qq_group_name 等)静默
  兼容不再报错; 全局共享队列(GLOBAL_AUTO_SCHEDULER)留作后续版本
- **统计**: 每设备(等待/成功/失败)+ 全局(设备/运行中/等待账号/当前
  执行/本次完成/失败), 队列变化事件驱动刷新
- **布局**: 设备卡片新增队列表格+加号行后高度显著增加, 日志区限高并把
  纵向空间优先给设备区(视口 ≥360px), 保证「账号/密码/添加/批量添加」
  行开机即见, 不再被折叠进滚动区
- **视觉加固**: 加号行外加蓝色边框 + 「➕ 添加账号到本设备队列」标题,
  「添加」按钮绿底白字加粗; 空队列表格显式白底+网格线(避免空表 viewport
  深灰底被误认为"没有添加功能")。针对 v1.2.0 首发用户反馈"里面没有账号
  密码添加功能"——根因是加号行被低对比占位符与空表深色块淹没, 非控件缺失
- **测试**: 新增 7 个测试文件 74 条(队列核心/并发/重复防护/设备绑定/
  停止恢复/断线重连/批量解析), 全量 332 条通过。并发消费保护: 心跳
  重建时旧 Worker 未归还的任务绝不静默丢失(INTERRUPTED 收回)

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
  控制器防重复防离线), 全量 258 条通过。真机验收已执行:
  Redmi M2012K11AC(Android 13) 单机全流程 PASS(pm clear → 重连/
  重初始化 7 PASS + 1 WARN → 真实页面检测 PTC_REDIRECTING →
  READY; device_reset.log RESULT=SUCCESS), 见
  FINAL_RELEASE_ACCEPTANCE_v1.1.0 §C。多机并发重置仍待三机环境

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
