"""
desktop/vpn_check.py
VPN 在线检测 — PTC 网页登录的运营前置条件。

背景: PTC 登录需要科学上网。手机未开 VPN 时登录页能加载, 但提交
账号后系统跳转超时(实测 60s), 自动化会卡在游戏登录界面反复重试。
本模块通过 `adb shell dumpsys connectivity` 的 VpnNetworkProvider 计数
判断 VPN 是否在线:

  VpnNetworkProvider:0  → 无 VPN(任何 VPNService 都未注册网络)
  VpnNetworkProvider:1+ → 有 VPN

老系统/无该行时用 `ip link` 的 tun*/wg*/ppp* 接口兜底。
注意: root/内核模式 TUN(Clash Meta kernel tun 等)不注册 VpnService,
VpnNetworkProvider 恒为 0 但隧道真实存在 → 计数为 0 时同样兜底
查 tun 接口(实测: tun0 UP + 全表路由, 系统计数仍为 0)。
所有 adb 调用经 run_hidden_process, 无 CMD 黑框。
"""
from __future__ import annotations

import logging
import re

from desktop.process_runner import run_hidden_process

logger = logging.getLogger(__name__)

# dumpsys connectivity 的 provider 列表行, 如 "  3: VpnNetworkProvider:0"
VPN_PROVIDER_RE = re.compile(r"VpnNetworkProvider:(\d+)")

# ip link 接口行, 如 "12: tun0: <POINTOPOINT,MULTICAST,NOARP,UP..."
TUN_IFACE_RE = re.compile(r"^\d+:\s+(tun\d+|wg\d+|ppp\d+)[:@]", re.M)


def parse_vpn_provider_count(dumpsys_text: str) -> int | None:
    """从 `dumpsys connectivity` 输出解析 VPN 网络数量(纯函数, 可测)。

    返回 None 表示输出中没有该行(老系统/命令失败)。
    """
    m = VPN_PROVIDER_RE.search(dumpsys_text)
    if m:
        return int(m.group(1))
    return None


def parse_tun_interfaces(ip_link_text: str) -> list[str]:
    """从 `ip link` 输出提取 VPN 类接口名(纯函数, 可测)。"""
    return TUN_IFACE_RE.findall(ip_link_text)


def check_vpn(adb_path: str, serial: str, timeout: float = 20.0) \
        -> tuple[bool, str]:
    """检测设备 VPN 是否在线。返回 (vpn_ok, detail)。"""
    args = [adb_path, "-s", serial, "shell", "dumpsys", "connectivity"]
    try:
        r = run_hidden_process(args, timeout=timeout,
                               encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"检测失败: {e}"
    if r.returncode != 0:
        return False, f"dumpsys 失败(rc={r.returncode})"

    count = parse_vpn_provider_count(r.stdout)
    if count and count >= 1:
        return True, f"VpnNetworkProvider={count}"

    # 计数为 0(或老系统无该行)时兜底查 tun 接口。
    # 关键实测: root/内核模式 TUN(如 Clash Meta kernel tun)不注册
    # VpnService → VpnNetworkProvider 恒为 0, 但隧道真实存在
    # (tun0 UP + 全表路由接管全部 App 流量)。只看系统计数会把
    # 开着机场的用户误报为"无 VPN"。
    prefix = f"VpnNetworkProvider={count}; " if count is not None else ""
    try:
        r2 = run_hidden_process(
            [adb_path, "-s", serial, "shell", "ip", "link"],
            timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"{prefix}tun 接口检测失败: {e}"
    if r2.returncode == 0:
        tuns = parse_tun_interfaces(r2.stdout)
        if tuns:
            return True, f"接口 {','.join(tuns)}({prefix}root/内核模式 TUN)"
        return False, f"{prefix}且无 tun 接口"
    return False, f"{prefix}ip link 失败(rc={r2.returncode})"
