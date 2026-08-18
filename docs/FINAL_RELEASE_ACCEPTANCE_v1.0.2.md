# FINAL RELEASE ACCEPTANCE — v1.0.2

生成时间: 2026-08-19 00:20 +0800
状态: **RELEASE CANDIDATE(两项验收环境阻塞, 一项全部完成; 如实声明)**

本报告按"三项最终验收"逐项给出 ✅ / ⚠️ / ❌ 与证据。按验收规矩:
无干净机器不写 PASS、只有 1 台手机不写 3 机 PASS、旧包不冒充
Tag 可复现构建。

---

## A. Clean Machine(干净机器安装验收)

| 项 | 结论 |
|----|------|
| 测试方式 | ⚠️ **NOT TESTED** |
| 环境 | 本机 Windows 11 家庭中文版 10.0.26200 — **家庭版无 Windows Sandbox(仅 Pro/Enterprise 有)、无 Hyper-V**; 未安装 VirtualBox/VMware; 无第二台 Windows 电脑可用 |
| Setup | ⚠️ 未在干净机器实测, 按规矩不模拟 PASS |

已具备但未能在干净机器验证的机制(如实列出, 不构成 PASS):

- 安装包自包含: 捆绑 platform-tools(实测日志 `[ADB] 定位 adb: C:\Program
  Files\PokemonAutomation\_internal\adb\platform-tools\adb.exe`)、
  u2 资源、OCR 双引擎模型、VC++ 2015-2022 x64 运行库检测 + 内置静默安装;
- 程序不依赖系统 Python/pip/ADB(运行期无任何 python 解释器调用);
- 安装版在 Program Files 只读位置运行、数据落 `%LOCALAPPDATA%\
  PokemonAutomation\`(本开发机实测, 与干净机器只有"预装环境差异"这一
  项不同, 该差异恰由自包含机制覆盖 — 但按规矩仍如实标 NOT TESTED)。

**翻转条件**: 在 Windows 专业版开启 Windows Sandbox, 或任意一台无开发
环境的 Windows 电脑, 按验收清单 §五~§十五 执行。

---

## B. 三设备生产压力测试

| 项 | 结论 |
|----|------|
| 设备数量 | ⚠️ **BLOCKED — 当前仅 1 台真机在线**(e98bee5a, Redmi M2012K11AC) |
| 账号/凭据 | ⚠️ **BLOCKED — PTC 凭据未提供**(运营侧, 见 docs/REMEDIATION_2026-08-16.md) |
| 3×3 / 3×5 / 3×10 | ❌ 未执行(硬件与凭据不具备) |
| 5 分钟吞吐 | ❌ 无数据 — 不编造 |
| 3 机日志隔离/并发/账号原子领取 | ❌ 未执行 |

**已完成的单机验证(不能代替三机测试, 仅如实记录)**:

- 安装版(Tag 构建)驱动实测 `c:\temp\gui_drive_result6.txt`:
  result=PASS, 检测到设备: 1 / READY设备: 1 / 运行中Worker: 1 → 0;
- VPN 检测真机实测通过(`接口 tun0(VpnNetworkProvider=0; root/内核模式
  TUN)` → 在线), Clash 误报已修复(§3.7 根因报告);
- 账号日志脱敏、Worker 计数归零、设备日志落 LocalAppData 均实测。

**翻转条件**: 3 台真机 + PTC 有效凭据; 按验收清单 §十六~§三十四 执行。

---

## C. Git Release / Tag / 可复现构建

| 项 | 结论 | 证据 |
|----|------|------|
| 正式分支 | ✅ | master |
| Release Commit | ✅ | `9c23fad902955fa649edbe1cdd5be35fc9840352` `release: finalize Windows installer and production device runtime for v1.0.2` |
| 合并方式 | ✅ | fast-forward: adaptive-device-support → master(244 文件: 112 新增/126 删除/6 修改) |
| Tag | ✅ | `v1.0.2` 注解标签 → 指向 9c23fad(master tip, 非旧 commit) |
| 敏感信息扫描 | ✅ | 真实账号名已全部脱敏(代码注释/文档/测试改为 Rk3\*\*\*658 形态与合成账号); 无硬编码密码/Token/API Key; 无本机绝对路径; .gitignore 覆盖 logs/截图/DB/.env/dist/build/release |
| 运行数据隔离 | ✅ | runtime.db、release/ 安装包等不入库(.gitignore 验证 `git check-ignore` 命中) |
| 全量测试 | ✅ | **241 passed**(合并前全套; 含 VPN 修复 5 例、设备日志 4 例、账号脱敏回归) |
| 可复现构建 | ✅ | checkout v1.0.2(干净源码树), build/dist/release 旧产物移开(零缓存复用), PyInstaller `--clean` + ISCC `/DMyAppVersion=1.0.2` 重建成功 |
| 安装包 | ✅ | `release\宝可梦自动化购买脚本_Setup_1.0.2.exe` |
| SHA256 | ✅ | `5b244bec9427d824bb3a01663e1727e093cbfab27329bb62eae32e4285b4433c` |
| 大小 / 构建时间 | ✅ | 183,324,771 bytes / 2026-08-19 00:15:20 |
| Release Manifest | ✅ | `release\release_manifest.json`(版本/commit/tag/SHA256/构建时间/测试数) |
| Tag 构建包安装验证 | ✅ | 静默安装 exit 0 → FILEVER=1.0.2 → 驱动冒烟 PASS(`gui_drive_result6.txt`: 检测到设备 1 / READY 1 / Worker 1→0; 日志 00:16:23 内置 ADB、00:16:46 Worker 已启动; 设备日志落 `%LOCALAPPDATA%\PokemonAutomation\logs\`) |
| 远程推送 | ✅ | `origin` master `a5132e0..9c23fad` + `* [new tag] v1.0.2` 均推送成功(https://github.com/DaShengGuo/automation-operations) |
| Working Tree | ✅ | `nothing to commit, working tree clean`(master; 旧构建产物备份 build.rc-before-tag 等经 .git/info/exclude 本地排除) |

---

## 最终判定

```text
✅ / ⚠️ / ❌  Clean Machine           →  ⚠️ NOT TESTED(无干净环境, 未伪造)
✅ / ⚠️ / ❌  三机生产测试            →  ⚠️ BLOCKED(1/3 手机 + PTC 凭据)
✅ / ⚠️ / ❌  Git 正式合并            →  ✅ master = 9c23fad, 已推送
✅ / ⚠️ / ❌  v1.0.2 Tag              →  ✅ 指向正式发布 commit
✅ / ⚠️ / ❌  Tag 可复现构建          →  ✅ 干净重建 + 安装冒烟 PASS
✅ / ⚠️ / ❌  最终安装包              →  ✅ SHA256 已固化
```

**结论: 仍为 RELEASE CANDIDATE** — Git/构建/安装包三项 ✅;
干净机器与三机生产测试两项环境阻塞 ⚠️, 翻转条件见上。
按验收规矩(§58)不得在两项 ⚠️ 未翻转时宣布 Production Release。
