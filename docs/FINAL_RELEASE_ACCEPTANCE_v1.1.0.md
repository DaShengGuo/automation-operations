# FINAL RELEASE ACCEPTANCE — v1.1.0

生成时间: 2026-08-19 10:25 +0800
状态: **RELEASE CANDIDATE(重置功能单机真机验收 ✅; 干净机器与三机生产仍阻塞, 如实声明)**

本报告按"四项最终验收"逐项给出 ✅ / ⚠️ / ❌ 与证据。按验收规矩:
无干净机器不写 PASS、无真机不写重置真机 PASS、旧包不冒充 Tag 可复现
构建。

---

## A. Clean Machine(干净机器安装验收)

| 项 | 结论 |
|----|------|
| 测试方式 | ⚠️ **NOT TESTED** |
| 环境 | 本机 Windows 11 家庭中文版 10.0.26200 — **家庭版无 Windows Sandbox、无 Hyper-V**; 无第二台 Windows 电脑可用 |
| Setup | ⚠️ 未在干净机器实测, 按规矩不模拟 PASS |

与 v1.0.2 相同的自包含机制(捆绑 platform-tools/u2 资源/OCR 双引擎/
VC++ 运行库静默安装)未变; 本版本仅新增纯 Python 逻辑与两个 Qt 组件,
不引入新系统依赖 — 但不构成干净机器 PASS, 仍如实标 NOT TESTED。

**翻转条件**: Windows 专业版 Sandbox 或任意无开发环境电脑, 按交付指南
§二~§四 执行。

---

## B. 三设备生产压力测试

| 项 | 结论 |
|----|------|
| 设备数量 | ⚠️ **BLOCKED — 仍仅 1 台真机**(与 v1.0.2 相同阻塞) |
| 账号/凭据 | ⚠️ **BLOCKED — PTC 凭据未提供** |
| 3×3 / 3×5 / 3×10 | ❌ 未执行 |
| 5 分钟吞吐 | ❌ 无数据 — 不编造 |
| 多机重置互不影响 | ❌ 未真机执行(Mock 测试已覆盖, 见 C) |

**翻转条件**: 3 台真机 + PTC 有效凭据。

---

## C. 设备环境重置功能验收(本版本新功能)

| 项 | 结论 | 证据 |
|----|------|------|
| 自动化测试 | ✅ | `tests/test_device_reset.py` **17 条全部通过**; 全量 **258 passed** |
| Worker 停止+账号归还 | ✅(Mock) | 运行中重置 → Worker 退出, 在途账号 RETRY 且 `last_error` 含 DEVICE_RESET; 绝不误标 SUCCESS |
| 预取账号释放 | ✅(Mock) | 预取 LOCKED 账号释放回 PENDING, retry_count=0 不烧重试 |
| 单设备隔离 | ✅(Mock) | 重置 DEV-A 期间 DEV-B Worker 持续 RUNNING(调度器 join 移出锁外) |
| Runtime 清理 | ✅(Mock) | Checkpoint/resume 注入/临时运行状态清除, 仅本设备; SQLite 历史与日志文件保留 |
| 浏览器数据边界 | ✅(Mock) | 默认不执行浏览器清理; 高级选项仅当解析出真实浏览器才清; 解析不出/非浏览器(系统设置等)→ SKIPPED |
| pm clear 失败处理 | ✅(Mock) | RESET_FAILED + 步骤/原因/详细 + toast 弹窗; GAME_DATA 如实记录 CLEARED/FAILED/UNKNOWN |
| 重置日志 | ✅(Mock) | logs/device_reset.log 结构化字段齐全, REASON=MANUAL 恒定(无自动触发路径) |
| 防重复/防离线 | ✅(Mock) | 控制器 `_resetting` 集合防重复触发; 设备未连接拒绝重置 |
| 真机执行 | ✅ | Redmi M2012K11AC(Android 13) 完整流程一次通过: `pm clear` 真实执行 → ADB 重检/u2 重连/设备信息刷新 → 重新初始化 7 PASS + 1 WARN(屏幕未点亮, 非阻塞) → `detect_state()` 真实页面 = PTC_REDIRECTING(与手机截图核对一致, 未假设 RETURNING_PLAYER) → READY; `device_reset.log` RESULT=SUCCESS / GAME_DATA=CLEARED / BROWSER_DATA=NOT_TOUCHED / REASON=MANUAL / VERSION=1.1.0 |
| 多机并发重置 | ⚠️ **NOT TESTED** | 仅 1 台真机, 多台同时重置互不影响仍待三机环境 |
| GUI 人工点验 | ⚠️ **待目检** | 安装版已在本机启动运行(已升级 v1.1.0),「停止/重新识别/重置设备环境」三按钮与确认弹窗布局待人工点一遍 |

**单机已翻转** ✅(证据见上)。**剩余翻转条件**: 3 台真机环境验证多机并发重置互不影响。

---

## D. Git Release / Tag / 可复现构建

| 项 | 结论 | 证据 |
|----|------|------|
| 正式分支 | ✅ | master |
| Release Commit | ✅ | `3bca3c7` `release: device environment reset feature for v1.1.0`(16 文件, +1665/-31) |
| Tag | ✅ | `v1.1.0` 注解标签 → 指向 3bca3c7(master tip) |
| 全量测试 | ✅ | **258 passed**(v1.1.0 版本号下复跑; 含重置 17 例) |
| 可复现构建 | ✅ | PyInstaller `--clean`(日志 `[OK] Release 构建完成`, exe 10:18:17 重建) + ISCC `/DMyAppVersion=1.1.0`(`Successful compile (76.922 sec)`) |
| 安装包 | ✅ | `release\宝可梦自动化购买脚本_Setup_1.1.0.exe` |
| SHA256 | ✅ | `a8b326d485ded486307df9df0abf757eccea1d2ea81b6ba88fbc6143abfd549f` |
| 大小 / 构建时间 | ✅ | 183,337,093 bytes / 2026-08-19 10:21:43 +0800 |
| Release Manifest | ✅ | `release\release_manifest.json`(版本/commit/tag/SHA256/构建时间/258 tests) |
| Tag 构建包安装验证 | ✅ | 静默安装 exit 0 → FILEVER=1.1.0, 安装目录 exe 14,683,374 bytes 与新构建一致(首次因旧程序遗留 adb.exe 锁文件报「无法写入」, 结束 adb 服务器后重装成功); 安装版启动冒烟: 内置 ADB 定位 → 设备 e98bee5a 发现 → READY |
| 远程推送 | ✅ | `origin` master `81c4bbf..3bca3c7` + `* [new tag] v1.1.0` 均推送成功(https://github.com/DaShengGuo/automation-operations) |

---

## 最终判定

```text
✅ / ⚠️ / ❌  Clean Machine           →  ⚠️ NOT TESTED(无干净环境, 未伪造)
✅ / ⚠️ / ❌  三机生产测试            →  ⚠️ BLOCKED(1/3 手机 + PTC 凭据)
✅ / ⚠️ / ❌  重置功能 Mock 验收      →  ✅ 17/17 + 全量 258
✅ / ⚠️ / ❌  重置功能真机验收        →  ✅ Redmi M2012K11AC 单机全流程(多机待三机环境)
✅ / ⚠️ / ❌  Git 正式合并            →  ✅ master = 3bca3c7
✅ / ⚠️ / ❌  v1.1.0 Tag              →  ✅ 指向正式发布 commit
✅ / ⚠️ / ❌  Tag 可复现构建          →  ✅ PyInstaller --clean + ISCC 重建成功, SHA256 已固化
✅ / ⚠️ / ❌  最终安装包              →  ✅ 183,337,093 bytes, SHA256 见 D 表
```

**结论: RELEASE CANDIDATE** — Git/测试/构建/安装验证/重置单机真机 ✅;
干净机器与三机生产两项 ⚠️ 未翻转, 按验收规矩不得宣布 Production Release。
