# 更新日志

版本规则: Semantic Versioning — `v1.0.0` 第一版生产版本; `v1.0.1` 修复 BUG;
`v1.1.0` 新功能; `v2.0.0` 重大架构升级。

---

## v1.3.6 — 启动状态机精简 + 商城滑动坐标动态化 (2026-08-22)

两份强制方案「启动状态机精简修复」+「商城滑动坐标优化」:

1. **删 RETURNING_PLAYER → DETECT_PAGE → LOGIN 中转(启动快乐路径)**:
   真机日志「启动完成, 当前=RETURNING_PLAYER」后仍空转
   `状态: DETECT_PAGE 超时=30.0s` → `状态: LOGIN 超时=90.0s` 两个
   状态。现在: launch 确认登录入口页(RETURNING_PLAYER)后 START_GAME
   内**直接执行登录流程**(注册页等待→点已註冊→中央站→账密页→
   游戏加载→MAP 全部由登录内部步级预算推进), 不再经 DETECT_PAGE/
   LOGIN 状态中转, 也不是调超时参数(按规格: 直接删错误状态跳转)。
   LOGIN 状态保留: 仅重试/HANDLE_POPUPS/WAIT_HOME 分流/RECOVERY
   回检等恢复路径使用; HOME(残留会话归属检查)/弹窗/未知页仍走
   DETECT_PAGE 安全路由。预期日志: `启动完成, 当前=RETURNING_PLAYER`
   → `[REGISTER] 检测到登录入口页 — 进入登录流程` → `[00:01]
   等待注册页面(已註冊/未註冊)`。状态机迁移表 START_GAME 新增
   WAIT_HOME 合法后继。
2. **商城滑动坐标动态比例(多设备兼容)**: 由写死基准坐标改为
   屏幕比例动态计算(绝不硬编码像素, 适配 720P/1080P/1440P):
   `x = 屏宽25%`(1080→270, 左四分之一区, 远离底部中央 X 关闭按钮
   真机实测 (514,2158)); `起点 = 屏高82%`(2400→1968); `终点 =
   屏高15%`(2400→360); duration 800~1200ms 随机(避免固定节奏)。
   坐标仍走 CoordinateMapper(insets/clamp/底部手势区防御不变,
   fallback 分支 round 修正浮点截断 0.82×2400→1967 问题)。
   新增调试日志: `[SHOP] 屏幕尺寸: 1080x2400` + `[SHOP] 滑动坐标:
   (270,1968) → (270,360) duration=0.8~1.2s`。

- **测试**: +6 条(启动直达登录流程快乐路径无 DETECT_PAGE/LOGIN
  状态; 残留 HOME 仍走 DETECT_PAGE 安全路由; 滑动坐标 1080/720P/
  mapper 1440P 三档比例 + 起点避开 X 按钮带 + 调试日志断言)。
  全量 377 条通过。

## v1.3.5 — 砍掉商城滑动期间退出判断错误分支 (2026-08-22)

强制方案「商城真退出已确认误伤正常滑动」— 砍掉错误分支:

1. **删滑动中退出守卫(核心)**: `_scroll_pass` 内每 2 轮的
   `_shop_still_open()` 守卫彻底删除 — 真机证实滑动中 OCR 暂时匹配
   不到商城特征词(动画帧/渲染抖动) → 误判 MAIN_MENU →「商城真退出
   已确认」中断滑动(客户日志所见)。SHOP_SCROLLING 期间严格锁定:
   禁止商品识别/退出判断/MAIN_MENU 检测/MAP 检测/补滑/点击。
2. **删 `_shop_still_open`/`_shop_texts_present` 死代码**: 退出判断
   只在非滑动状态允许(规格§六), 当前流程不判断退出 — 识别不到 →
   PRODUCT_NOT_FOUND 交上层正常处理。
3. **删 kicked_out 标记**: 属性/赋值/分支全部移除, 状态锁 scrolling
   (back_safe 守卫依赖)保留。
4. **最终状态流转(规格§八)**: CLICK_SHOP → SHOP → SHOP_SCROLLING
   (滑动1/6..6/6, 无任何检测) → SHOP_SEARCH_PRODUCT(识别) → BUY;
   未识别 → 补滑3次 → 再识别。日志只有 [SHOP] 滑动 N/6 与
   [SHOP] 开始识别100宝可梦, 无「第一次未识别/商城真退出」分支。

- **测试**: 重写 test_find_product_kicked_out_stops_early →
  test_scroll_ignores_exit_signal_during_scrolling(滑动期间退出信号
  被忽略, 完整滑满 9 次)。全量 371 条通过。

## v1.3.4 — 删 MAP→HANDLE_POPUPS 状态跳转 + 滑动起点避开 X 关闭按钮 (2026-08-22)

强制修复方案 t9k4m「旧状态机没有删除 + 商城滑动坐标设计错误」:

1. **删 MAP→HANDLE_POPUPS→EXECUTE_TASK 链路(§一~§七, 状态机级)**: 旧
   主页检测成功后进 HANDLE_POPUPS 状态(15s 超时显示)再 EXECUTE_TASK —
   真机曾 49 秒才点菜单。修改: Worker WAIT_HOME 成功后**直接**
   EXECUTE_TASK(状态机迁移表 WAIT_HOME 加入 EXECUTE_TASK 合法后继),
   HANDLE_POPUPS 状态不再出现在主页后链路。弹窗处理改为任务内部
   快速执行(execute_task 开头 handle_popups, 非阻塞 <0.3s, 无独立
   弹窗状态)。HANDLE_POPUPS 状态保留但只用于登录流程中弹窗遮挡
   (DETECT_PAGE 路由)。日志 [MAP] 主页检测成功 — 直接进入任务。
2. **滑动起点避开 X 关闭按钮(§八~§十一, 「滑动无效」根因)**: 旧滑动
   起点 (540, 2200) 正落在商城底部中央 X 关闭按钮(真机实测
   (514,2158))上 — 按下即被按钮消费: 列表不动, 甚至误触关闭。
   改为横向偏移区域: x=360(左三分之一, 远离中心), 起点 y=2100
   (按钮上方), 终点 y=300, duration 1.0s。保留 CoordinateMapper
   映射 + 底部手势区 clamp。
3. **EXECUTE_TASK 说明(§六)**: Worker 状态机必须有任务执行状态 —
   商城购买流程在 EXECUTE_TASK 内执行(MAP→点菜单→商城→购买),
   fsm 超时 v1.3.1 已收紧 90s(用户日志 180s 为旧版本)。

- **测试**: +1 生命周期断言(主页成功后不得经过 HANDLE_POPUPS)。
  全量 371 条通过。

## v1.3.3 — 滑动手势区防御 + 三段滑动日志 + 注册页检测提速 (2026-08-21)

技术方案「状态机精简 + 商城滑动修复」落地(不改状态机结构 —
DETECT_PAGE/LOGIN 是真实页面恢复机制, 删除会破坏停止后继续/
已登录跳过登录/认证失败重登, 且它们本就是快速检测非固定等待):

1. **滑动手势区防御(§七, 「日志执行滑动但页面不动」根因深化)**:
   v1.3.2 已走 CoordinateMapper, 本轮再叠加防御 — 滑动起点
   (基准 2200, 屏幕底部附近)在映射后 clamp 到
   `screen_h - insets.bottom - 80px` 安全线上。从系统底部手势区/
   导航条开始的触摸会被系统截获(游戏收不到 → 页面不动), 旧
   裸换算 2200 起点在部分机型正好落入该区域。终点 200→100
   (规格: 顶部附近)。滑动坐标日志落盘(真实坐标可离线复核)。
2. **三段滑动日志(§七)**: [SHOP] 准备滑动 N/6 / 执行滑动 N/6 /
   滑动完成 N/6 — 「日志完成但页面不动」时可直接判定坐标/输入
   通道问题(swipe 异常仍有 SWIPE_ERROR 截图)。
3. **注册页检测提速(§五)**: DETECT_PAGE 超时 60→30s(GAME_SPLASH
   →注册页人工 ~9s, 30s 上限足够; 页面出现即转态, 超时仅保险)。
   检测到登录入口页时打 [REGISTER] 日志。
4. **说明(方案§三/§六)**: 「删除 DETECT_PAGE/LOGIN 状态」「删除
   HANDLE_POPUPS/EXECUTE_TASK」未按字面执行 — 前者是停止后恢复/
   真实页面检测机制(快速轮询命中即转, 非固定等待), 后者在 v1.3.1
   已收紧为 3s/90s。用户日志中的 15s/180s 为旧版本配置。

- **测试**: 全量 371 条通过。

## v1.3.2 — 启动拦截同步化 + VPN 只检一次 + 滑动坐标走映射器 (2026-08-21)

规格「账号检查未拦截启动」「滑动日志执行但页面不动」:

1. **启动账号检查同步化(规格§一/§二重点)**: 检查从后台线程
   `_start_scheduler_worker` 提前到 `controller.start()` 同步第一步 —
   无账号可执行(等待+运行中=0)立即返回 error「当前没有可执行账号,
   请先添加账号密码」(GUI QMessageBox 弹窗), 状态保持 STOPPED,
   不创建调度器/Worker/线程/不控制手机。日志 [START] 读取账号列表
   — 账号数量: N / 发现账号数量: N — 开始启动。后台线程内检查
   保留作双保险。
2. **VPN 只启动检测一次(规格§七)**: 删除 device_monitor 的 120s
   周期 VPN 检测(_check_vpn/相关状态字段), 运行期间不再触发 VPN
   检测/弹窗。VPN 只在 GUI 点击启动时由 preflight_vpn 执行一次。
   vpn_check 核心函数保留(启动预检仍用)。
3. **滑动坐标走 CoordinateMapper(规格§八~§十)**: 商城滑动坐标从
   `screen_h 比例换算` 改为 mapper.map/map_ratio — 与 click_ratio
   同一坐标体系(含安全区 clamp)。旧裸换算在真机分辨率/稳定边距
   不同时坐标可能落入底部系统手势区或超屏 → 触摸被系统截获 →
   「日志显示滑动执行但页面不动」根因。swipe 异常不再静默(旧
   debug 吞掉无法取证): 改 warning + SWIPE_ERROR 截图留档。
   日志 [SHOP] 执行滑动 N/6。
4. **状态机确认(规格§三~§六)**: v1.3.1 已收紧 HANDLE_POPUPS 3s/
   EXECUTE_TASK 90s(config.yaml, state_timeout 映射 handle_popups→
   popup/execute_task→task 已核实生效); 商城流程状态锁 SHOP_SCROLLING
   + 四条件退出证据 + 无自动重进已在 v1.3.0 落地, 本轮无重复改动。

- **测试**: +1 start() 同步拒绝(不切 STARTING/不建调度器)。
  全量 371 条通过。

## v1.3.1 — 流程提速 + 滑动加长 + 商品点击重试 + 启动账号检查 (2026-08-21)

规格六项(删状态等待/滑动幅度/点击验证/启动检查):

1. **状态超时收紧(规格§一~§四)**: HANDLE_POPUPS 只做快速检查 —
   fsm 超时 15→3s, 仍有弹窗时复检 0.5s(旧 2s)。EXECUTE_TASK 超时
   180→90s(商城全流程内部预算 ~60s, 不设 180 秒长兜底)。主页检测
   成功后 3 秒内完成「弹窗检查+点菜单」(handle_popups 已非阻塞
   <0.3s, 弹窗命中才处理)。
2. **商城滑动幅度增大(规格§五)**: start_y 1800→2200(0.917), end_y
   400→200(0.083), duration 0.8→1.0s(规格 800-1200ms)。每次滑动
   覆盖 2000px 长距离, 6 次覆盖列表全程。
3. **商品点击重试≤2(规格§六)**: click_product 点击后等最多 5s 检测
   Google Play 页, 第一次未出现(坐标偏移/点击未生效/切换慢)自动
   再次点击, 第二次仍失败才记录错误截图。绝不在第一次失败直接判死。
   日志 [BUY] 点击商品/等待 Google Play 页/成功进入支付页。
4. **启动前账号检查(规格§七)**: GUI 点击启动时, 队列模式无账号可执行
   (等待+运行中均为 0, 含 INTERRUPTED 待恢复) → 弹窗「当前没有可
   执行账号, 请先添加账号密码」+ 状态回 STOPPED, 禁止启动 Worker。
   停止后重启恢复场景(INTERRUPTED 在队列)不受影响。

- **测试**: +2 启动检查(无账号拦截/有账号放行) +2 商品点击重试
  (一次重试成功/两次失败留档)。全量 370 条通过。

## v1.3.0 — 商城流程状态机修复: MAP 误判根因 + 固定6次滑动 (2026-08-21)

客户实况「商城滑动中检测到 MAP 但实际没退出商城」+ 规格五条硬约束
(删 VPN 阻塞/删超预算停止/完整6次/删错误退出判断):

1. **MAP 误判根因修复(核心)**: 所有状态 min_hits=1 — MAP 规则的红像素
   占比(red_ratio)单证据命中即判 MAP。商城页满屏红色寶可幣商品图标
   红占比超阈值(0.025) → 商城滑动中被误判 MAP → 触发错误退出恢复。
   修复: MAP `min_hits: 1→2`(至少两条证据) — 真机实测地图三证据同时
   命中无漏检; 商城页仅 red_ratio 单证据不再判 MAP。
2. **商城退出四条件强证据(规格§七/§八)**: `_shop_still_open` 重写 —
   商城特征存在优先(OCR 仍有 寶可幣/PokéCoins/US$/新手禮盒 等词 →
   视为仍在商城, 无视 MAP 误判); 特征消失 + 连续两次 detect 退出态
   才确认真退出。删除「自动重新进入商城」逻辑(_find_product_with_guards
   改为单次 find_product 透传 — 真机证实重进判断不可靠, 商城没退出
   却反复重进)。
3. **固定 6 次大幅滑动(规格§四/§五/§九)**: 滑动结束只由「完成规定
   次数」决定 — 删除滑动超预算 10s 停止、删除判底提前停、删除滑动中
   商品识别。第一阶段完整滑 6 次 → 统一识别 → 未中补 3 次 → 再识别。
   滑动参数 duration 0.8s(start_y=1800→end_y=400)。
4. **日志对齐规格§十**: [MAP] 点击菜单 / [MENU] 点击商城 / [SHOP]
   商城确认成功, 立即开始大幅滑动 / 开始大幅滑动 N/6 / 开始识别
   100宝可梦 / 识别成功, 开始购买。删「滑动超预算」「疑似商城退出」
   类噪音日志。
5. **VPN 检测澄清(规格§一)**: VPN 检测位于 GUI 层(运行前预检弹窗 +
   device_monitor 120s 周期后台检测), 与 Worker 任务流程并行, 不在
   MAP→菜单路径上, 不阻塞主页→商城流程(客户日志看到的 VPN 检测是
   后台线程输出, 非流程步骤)。运行前预检是 v1.0.2 客户要求
   (PTC 登录必须 VPN), 保留不删。

- **测试**: 更新 MAP 双证据契约(red_ratio 单证据不判 MAP)/固定 6 次
  无判底提前停/guards 不自动重进。全量 366 条通过。

## v1.2.9 — GUI 停止按钮真停止 + 商城误判二次确认 + 滑动禁识别 (2026-08-21)

规格 v5「停止只弹提示不停止实际任务」+「商城误判退出」+「滑动中禁识别」:

1. **GUI 停止按钮真停止(规格§一重点, 事故级根因)**: 旧实现 stop_event
   只在 DeviceWorker.run() 循环头检查, 但进入 execute_task/login/
   wait_home 后是单个长调用, 期间绝不检查 — GUI 点停止弹"已停止",
   后台仍继续控制手机(客户实测)。修复协作式中断:
   - 新增 core/stop_error.py WorkerStopRequested(BaseException);
   - worker _wire_heartbeat 注入 stop_cb = stop_event|_local_stop;
   - adapter.tick_heartbeat + detector._hb + web_context.wait_game_return
     每轮检查 stop_cb, 置位即抛 WorkerStopRequested;
   - run() 顶层专门捕获(BaseException 穿透各层 except Exception 不被吞)。
   长循环每轮间隔 ≤2s(滑动 0.4s/登录 0.5-2s), 保证 1 秒内停止手机操作。
2. **商城误判退出二次确认(规格§七)**: _shop_still_open 旧单次检测到
   MAP/MAIN_MENU 即判退出 — 滑动动画帧可能瞬时误判。改: 首次检测到
   退出状态 → sleep(0.6) 等动画停下 + bust_caches 强制最新画面重检,
   二次确认仍退出状态且非 SHOP 才算真退出; 回 SHOP/UNKNOWN 视为瞬时
   误判继续滑动。一次误识别不再直接退出商城。
3. **滑动期间禁止识别商品(规格§五)**: _scroll_pass 移除滑动中商品快检
   (旧 i>=2 偶数轮调 _detect_product — 规格判为"第2次滑动后识别"错误)。
   第一阶段完整滑 6 次(到底判停除外) → 统一识别; 未中补 3 次 → 再识别。
   滑动中只保留异常退出守卫(不识别商品)。

- **测试**: 新增停止链路 2 条(stop_cb 注入 + BaseException 穿透),
  更新 TestShopFastScrollToBottom 2 条对齐"滑完才统一识别"契约。
  全量 365 条通过。

## v1.2.8 — 弹窗识别非阻塞化 + 商城定数滑动(删回滚) (2026-08-21)

规格 v4「主页识别后等 42 秒才点球」+「商城滚过头回滚逻辑错误」:

1. **弹窗识别非阻塞化(42s 等待根因之一, 速度优化核心)**:
   `PopupHandler._matches` 旧用 `find_element(timeout=1.5)` → u2
   `el.wait()` 阻塞轮询, 无弹窗时每个 popup 配置睡满 1.5s。yaml 登记
   3 个通用 popup, 每次 `handle_popups` 调用吃 4.5s; 主页→点球路径
   (HANDLE_POPUPS 态 + execute_task 开头) 多次调用 → 累积十几秒,
   叠加 `_handle_unknown_popup` 入口 sleep(1.5) → 接近 42s。
   改 `d.exists(text=..., timeout=0)` 瞬时判断(一次 dump + 内存匹配,
   不阻塞)。无弹窗时 handle_popups 从 4.5s 降到 <0.3s。
2. **`_handle_unknown_popup` 二次确认 1.5s→0.3s**: 转场动画通常 <0.5s,
   旧 sleep(1.5) 过长。改 0.3s + bust_caches 读最新画面, 仍 UNKNOWN
   才走关闭策略。
3. **商城滑动删回滚, 改定数 6+3(规格§四~§八)**:
   旧三阶段(滑底 + 底部4屏搜索 + 反向回滚4屏)含"滚过头回滚"逻辑 —
   规格判定该逻辑错误(目标是滑到底再识别, 不是精确定位)。重写为
   两阶段定数滑动: 第一阶段 6 次 → 识别 → 未中补 3 次 → 再识别。
   删除所有 rollback/reverse swipe。判底(连续2帧静帧无变化)保留为
   到底提前停优化。滑动参数 duration 0.7s + 间隔 0.4s(规格§七)。
   新增 `_scroll_pass` 辅助 + yaml `scroll_first_pass/scroll_second_pass`。
4. **测试更新**: 替换 2 条 v1.2.5 单步确认机制测试为定数滑动契约
   (不回滚无 down swipe / 钉底快速判底); test_3 契约 <=6→<=9(允许补滑)。

- **测试**: 全量 363 条通过。

## v1.2.7 — GUI 实时日志不显示根因 + 弹窗即关 + 点球重试 (2026-08-21)

三块独立修复(用户规格 v3):

1. **GUI 实时日志空白根因(规格§五重点, 证据链闭合)**:
   `desktop/app.py` 旧时序先挂 `QtLogHandler` 到 root logger, 再建
   `DesktopAppController` — 其 `__init__` 调 `setup_logging()`, 内部
   `root.handlers.clear()` 把刚挂的 QtLogHandler 清掉。之后日志只进
   控制台/文件, GUI 日志区永远空白(`console=False` 打包后控制台也
   看不见)。文件日志正常反证断点在 QtLogHandler 被清。
   修复双保险: ① app.py 调整顺序(先 controller 后挂 handler);
   ② logger.py clear 前保留类型名 == QtLogHandler 的外部 handler
   (防未来时序回归, 按类型名判断避免 core→desktop 依赖)。
   + 回归测试 test_setup_logging_preserves_qt_log_handler(模拟旧时序)。
2. **弹窗即关不睡满(规格§一/§二)**: handle_popups 各分支点击后旧
   盲 `sleep(1.5~2)` 睡满 — 弹窗已消失仍等, 累积延迟。改为
   `_wait_popup_gone(trigger_words)`: 0.4s 间隔验证轮询(每轮 bust_caches
   清 OCR 缓存读最新画面), 触发词消失立即返回, 最多 2s。命中弹窗
   通常 1 次确认即消失(0.4s), 比旧 sleep(2) 快 1.6s。
3. **点球重试 ≤2(规格§四)**: open_main_menu 旧单次点击无重试, 模板
   timeout=2s 浪费(渲染延迟常失败)。改模板快试 0.5s 失败立即比例
   坐标 + wait_for_state MAIN_MENU 失败重试 1 次, 最多 2 次不无限等。
   + 回归测试 2 条(重试成功 / 两次失败放弃)。

- **测试**: 全量 363 条通过(+1 日志 +2 点球)。

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
