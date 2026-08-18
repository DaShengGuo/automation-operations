# -*- coding: utf-8 -*-
"""tests/test_vpn_check.py — VPN 检测解析纯函数测试。"""
from desktop.vpn_check import parse_tun_interfaces, parse_vpn_provider_count


def test_parse_vpn_provider_count_zero():
    text = ("Providers:\n"
            "  3: VpnNetworkProvider:0\n"
            "  4: TelephonyNetworkFactory\n")
    assert parse_vpn_provider_count(text) == 0


def test_parse_vpn_provider_count_one():
    text = "  3: VpnNetworkProvider:1\n"
    assert parse_vpn_provider_count(text) == 1


def test_parse_vpn_provider_count_absent():
    assert parse_vpn_provider_count(
        "ni{WIFI CONNECTED extra: } ...") is None


def test_parse_vpn_provider_count_empty():
    assert parse_vpn_provider_count("") is None


def test_parse_tun_interfaces():
    text = ("1: lo: <LOOPBACK,UP,LOWER_UP>\n"
            "12: tun0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP>\n"
            "13: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP>\n")
    assert parse_tun_interfaces(text) == ["tun0"]


def test_parse_tun_interfaces_wg_and_ppp():
    text = ("9: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP>\n"
            "14: ppp1: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP>\n")
    assert parse_tun_interfaces(text) == ["wg0", "ppp1"]


def test_parse_tun_interfaces_none():
    assert parse_tun_interfaces(
        "1: lo: <LOOPBACK>\n2: wlan0: <BROADCAST>\n") == []


# ── check_vpn 端到端(monkeypatch run_hidden_process) ──

class _FakeResult:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _fake_runner(dumpsys_text, ip_link_text="", ip_link_rc=0):
    """按参数分派: dumpsys → dumpsys_text; ip link → ip_link_text。"""
    from desktop import vpn_check

    def _run(args, **kwargs):
        if "ip" in args and "link" in args:
            return _FakeResult(ip_link_rc, ip_link_text)
        return _FakeResult(0, dumpsys_text)

    vpn_check.run_hidden_process = _run


def test_check_vpn_provider_one_true(monkeypatch):
    """系统计数 ≥1 直接判在线, 不查 tun。"""
    from desktop.vpn_check import check_vpn
    _fake_runner("  3: VpnNetworkProvider:1\n")
    ok, detail = check_vpn("adb", "SER")
    assert ok is True
    assert "VpnNetworkProvider=1" in detail


def test_check_vpn_zero_but_tun_up_true(monkeypatch):
    """实测场景(Clash Meta kernel tun): 计数 0 但 tun0 UP → 必须判在线。"""
    from desktop.vpn_check import check_vpn
    _fake_runner(
        "  3: VpnNetworkProvider:0\n",
        "27: tun0: <POINTOPOINT,UP,LOWER_UP>\n"
        "13: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP>\n")
    ok, detail = check_vpn("adb", "SER")
    assert ok is True
    assert "tun0" in detail


def test_check_vpn_zero_and_no_tun_false(monkeypatch):
    """计数 0 且无 tun 接口 → 判离线。"""
    from desktop.vpn_check import check_vpn
    _fake_runner(
        "  3: VpnNetworkProvider:0\n",
        "1: lo: <LOOPBACK>\n2: wlan0: <BROADCAST>\n")
    ok, detail = check_vpn("adb", "SER")
    assert ok is False
    assert "VpnNetworkProvider=0" in detail


def test_check_vpn_no_provider_line_tun_fallback(monkeypatch):
    """老系统无 provider 行 → tun 兜底(原语义保留)。"""
    from desktop.vpn_check import check_vpn
    _fake_runner(
        "ni{WIFI CONNECTED extra: }\n",
        "12: tun0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP>\n")
    ok, detail = check_vpn("adb", "SER")
    assert ok is True
    assert "tun0" in detail


def test_check_vpn_zero_ip_link_fails_false(monkeypatch):
    """计数 0 且 ip link 失败 → 离线(带原因)。"""
    from desktop.vpn_check import check_vpn
    _fake_runner("  3: VpnNetworkProvider:0\n", ip_link_rc=1)
    ok, detail = check_vpn("adb", "SER")
    assert ok is False
    assert "ip link 失败" in detail
