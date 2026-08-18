"""
tests/test_device_pipeline.py
客户交付整改新增模块测试:
  - parse_devices_output: adb devices -l 解析(CRLF/Unicode/空格/编码)
  - DeviceRegistry: 单一设备状态源 + REJECT_REASON
  - process_runner: 隐藏执行器 + 全局补丁幂等
  - AdbLocator: 捆绑优先 + 环境变量注入
  - prerequisites: u2 资源 / VC++ 运行库自检
"""
import os
import subprocess
import sys

import pytest

from desktop.adb_locator import AdbLocator, parse_devices_output
from desktop.device_registry import DeviceRegistry
from desktop.process_runner import (install_global_hidden_patch,
                                    merge_hidden_kwargs,
                                    run_hidden_process)


# ── parse_devices_output ──

class TestParseDevicesOutput:
    def test_basic_device_line(self):
        out = "List of devices attached\r\n"
        out += "e98bee5a               device product:alioth "
        out += "model:M2012K11AC device:alioth transport_id:1\r\n"
        rows = parse_devices_output(out)
        assert rows == [{
            "serial": "e98bee5a", "state": "device",
            "product": "alioth", "model": "M2012K11AC",
            "device": "alioth", "transport_id": "1"}]

    def test_crlf_and_blank_lines(self):
        out = ("List of devices attached\r\n"
               "\r\n"
               "ABC123\tdevice\r\n"
               "\r\n")
        rows = parse_devices_output(out)
        assert len(rows) == 1
        assert rows[0]["serial"] == "ABC123"

    def test_unauthorized_offline_states(self):
        out = ("List of devices attached\n"
               "SER1 unauthorized usb:1-1 transport_id:2\n"
               "SER2 offline\n"
               "SER3 no permissions (user in plugdev group; "
               "are your udev rules wrong?)\n")
        rows = parse_devices_output(out)
        states = {r["serial"]: r["state"] for r in rows}
        assert states == {"SER1": "unauthorized", "SER2": "offline",
                          "SER3": "no"}

    def test_unicode_model_gbk_mojibake(self):
        # GBK 字节按 utf-8+replace 解码后的乱码模型名 — 解析不应崩溃,
        # serial/state 必须正确。
        out = ("List of devices attached\r\n"
               "X9A1 device product:gbk_�� model:���"
               " transport_id:3\r\n")
        rows = parse_devices_output(out)
        assert len(rows) == 1
        assert rows[0]["serial"] == "X9A1"
        assert rows[0]["state"] == "device"

    def test_empty_table(self):
        assert parse_devices_output("List of devices attached\n") == []
        assert parse_devices_output("") == []

    def test_ignores_daemon_lines_and_whitespace_only(self):
        out = ("* daemon not running; starting now at tcp:5037\n"
               "* daemon started successfully\n"
               "List of devices attached\n"
               "   \n"
               "REAL1 device\n")
        rows = parse_devices_output(out)
        assert [r["serial"] for r in rows] == ["REAL1"]

    def test_tab_separated_parts(self):
        out = "List of devices attached\r\nDEV1\tdevice\tproduct:p\tmodel:m\r\n"
        rows = parse_devices_output(out)
        assert rows[0]["model"] == "m"


# ── DeviceRegistry ──

class TestDeviceRegistry:
    def test_discovery_add_and_counts(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([
            {"serial": "A", "state": "device"},
            {"serial": "B", "state": "device"},
        ])
        c = reg.counts()
        assert c == {"detected": 2, "ready": 0, "running": 0}

    def test_unauthorized_sets_reject_reason(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([{"serial": "A", "state": "unauthorized"}])
        rec = reg.get("A")
        assert rec.reject_reason == "等待手机授权, 请点击允许USB调试"
        assert not rec.is_connected

    def test_offline_sets_reject_reason_and_clears_ready(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([{"serial": "A", "state": "device"}])
        reg.mark_ready("A", True, "ok")
        reg.refresh_from_adb([{"serial": "A", "state": "offline"}])
        rec = reg.get("A")
        assert rec.reject_reason == "ADB OFFLINE, 正在尝试重新连接"
        assert not rec.ready

    def test_device_removed_becomes_missing(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([{"serial": "A", "state": "device"}])
        reg.refresh_from_adb([])
        rec = reg.get("A")
        assert rec.adb_state == "missing"
        assert "断开" in rec.reject_reason
        assert reg.counts()["detected"] == 0

    def test_state_transition_tracked(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([{"serial": "A", "state": "device"}])
        summary = reg.refresh_from_adb([{"serial": "A", "state": "offline"}])
        assert summary["state_changed"] == ["A:device->offline"]
        assert summary["added"] == []

    def test_worker_and_ready_marks(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([{"serial": "A", "state": "device"}])
        reg.mark_ready("A", True, "u2 ok")
        reg.mark_worker("A", True, "LOGIN")
        c = reg.counts()
        assert c == {"detected": 1, "ready": 1, "running": 1}
        reg.mark_worker("A", False)
        assert reg.counts()["running"] == 0
        assert reg.get("A").worker_state == "-"

    def test_hardware_info_backfill(self):
        reg = DeviceRegistry()
        reg.refresh_from_adb([{"serial": "A", "state": "device"}])
        reg.update_hardware_info("A", model="M2012K11AC", brand="Redmi",
                                 resolution="1080x2400")
        rec = reg.get("A")
        assert rec.model == "M2012K11AC"
        assert rec.brand == "Redmi"


# ── process_runner ──

class TestProcessRunner:
    def test_merge_hidden_kwargs_injects_on_windows(self):
        if sys.platform != "win32":
            pytest.skip("Windows 专属")
        kw = merge_hidden_kwargs({})
        assert kw["startupinfo"] is not None
        assert kw["creationflags"] & 0x08000000  # CREATE_NO_WINDOW

    def test_merge_hidden_kwargs_keeps_caller_flags(self):
        if sys.platform != "win32":
            pytest.skip("Windows 专属")
        kw = merge_hidden_kwargs({"creationflags": 0x1})
        assert kw["creationflags"] & 0x1
        assert kw["creationflags"] & 0x08000000

    def test_run_hidden_rejects_string_command(self):
        with pytest.raises(TypeError):
            run_hidden_process("adb devices")

    def test_run_hidden_process_works(self):
        r = run_hidden_process(
            [sys.executable, "-c", "print('hidden-ok')"], timeout=30)
        assert r.returncode == 0
        out = r.stdout.decode() if isinstance(r.stdout, bytes) else r.stdout
        assert "hidden-ok" in (out or "")

    def test_global_patch_idempotent(self):
        if sys.platform != "win32":
            pytest.skip("Windows 专属")
        assert install_global_hidden_patch() is True
        # 重复安装不叠加/不报错
        assert install_global_hidden_patch() is True
        r = subprocess.run([sys.executable, "-c", "print('patched')"],
                           capture_output=True, timeout=30)
        assert "patched" in r.stdout.decode()


# ── AdbLocator ──

class TestAdbLocator:
    def test_bundled_priority(self):
        """捆绑 platform-tools 必须排第一(禁止裸依赖 PATH)。"""
        AdbLocator.reset_cache()
        cands = AdbLocator.candidates()
        assert cands, "候选链不能为空"
        assert "platform-tools" in cands[0].replace("\\", "/"), \
            f"捆绑 adb 应排第一, 实际: {cands}"

    def test_resolve_injects_env(self):
        AdbLocator.reset_cache()
        path = AdbLocator.resolve()
        assert path
        # adbutils/u2 内部 adb_path() 依赖这两个环境变量指向同一份 adb
        assert os.environ.get("ADBUTILS_ADB_PATH") == path
        assert os.environ.get("ADB_PATH") == path


# ── prerequisites ──

class TestPrerequisites:
    def test_u2_assets_check(self):
        from desktop.prerequisites import u2_assets_check
        name, ok, detail = u2_assets_check()
        assert name == "u2资源"
        # 开发机 site-packages 一定有 assets(u2.jar/apk)
        assert ok, detail

    def test_vc_runtime_check_tuple(self):
        from desktop.prerequisites import vc_runtime_check
        name, ok, detail = vc_runtime_check()
        assert name == "VC++运行库"
        assert isinstance(ok, bool) and detail
