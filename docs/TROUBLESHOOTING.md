# 故障排除

## adb devices 没有设备

**检查清单：**

1. USB 数据线是否支持数据传输？（很多线只能充电）
2. 手机 USB 调试是否打开？
3. 手机解锁看屏幕，是否弹出授权对话框？
4. 尝试更换 USB 端口
5. 尝试更换数据线

**终极方案：**

```bash
# 重启 ADB 服务
adb kill-server
adb start-server
adb devices
```

---

## 设备显示 unauthorized

**原因：**手机没有授权电脑的 USB 调试请求。

**解决：**
1. 看手机屏幕，点「允许」
2. 如果没弹窗：
   - 手机设置 → 开发者选项 → 撤销 USB 调试授权
   - 重新插拔 USB
   - 再次授权

---

## 设备显示 offline

**解决：**
1. 重新插拔 USB 线
2. 关闭再开启 USB 调试开关
3. 撤销 USB 调试授权后重新授权
4. 重启手机

---

## 手机连接后只有充电

**原因：**USB 模式不对。

**解决：**
- 下拉手机通知栏 → 点击 USB 通知 → 选择「传输文件」

---

## Windows 找不到 adb

**解决：**
- 运行 `install.bat`，它会自动配置

---

## Python 找不到

**解决：**
- 运行 `install.bat`，它会自动安装

---

## pip 安装失败

**常见原因：**网络问题。

**解决：**
- `install.bat` 优先使用清华镜像
- 如果仍失败，检查网络，或使用 VPN

---

## 虚拟环境激活失败

**原因：**PowerShell 执行策略限制。

**解决：**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

或直接用 `install.bat` / `start.bat` 运行（不需要手动激活）。

---

## 找不到 UI 元素 / 点击位置不对

**原因：**不同手机的屏幕分辨率不同。

**解决：**
1. 运行 `doctor.bat` 确认设备信息
2. 在 `device_profiles.py` 中添加你的设备配置
3. 运行 `calibrate_real.py` 校准坐标

---

## 手机自动化权限不足

部分品牌需要额外权限：

**小米：**
- 开发者选项 → USB 调试（安全设置）
- 开发者选项 → USB 安装

**vivo：**
- 开发者选项 → USB 模拟点击

**OPPO/Realme：**
- 开发者选项 → 禁止权限监控

---

## PaddleOCR 安装失败

PaddleOCR 依赖较大（约 500MB），安装可能较慢。

**解决：**
- 耐心等待
- 如果持续失败，尝试：
```bash
pip install paddlepaddle -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install paddleocr -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 运行中手机黑屏/锁屏

部分手机锁屏后 ADB 连接会断开。

**解决：**
- 设置 → 显示 → 休眠 → 设为最长（如 30 分钟）
- 或设置 → 开发者选项 → 不锁定屏幕

---

## 华为 HarmonyOS 特殊问题

- 需要同时开启「USB 调试」和「HDB」
- 如果 ADB 无法连接，尝试切换 USB 端口模式

---

## USB 驱动异常

**解决：**
1. 下载并安装手机品牌的官方 USB 驱动
2. 或在设备管理器中手动更新驱动为「Android Composite ADB Interface」

---

## 更多帮助

运行 `doctor.bat` 查看完整诊断信息。
