"""
desktop/update_service.py
UpdateService — 在线更新抽象层(当前未配置真实更新服务器)。

架构预留(未来接入服务器时):
  当前版本 → 请求 update_endpoint → 获取最新 version
  → 比较版本 → 显示 release notes → 下载安装包
  → SHA256 校验(expected_hash == actual_hash 才允许运行安装程序)
  → 启动 Updater/Installer → 关闭主程序 → 安装 → 重启

当前行为: 未配置 update_endpoint 时, 检查更新如实提示
「当前未配置在线更新服务」, 不伪造连接成功。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from version import APP_VERSION


@dataclass
class UpdateInfo:
    version: str = ""
    release_notes: str = ""
    download_url: str = ""
    sha256: str = ""
    available: bool = False
    error: str = ""
    configured: bool = False


def compare_versions(a: str, b: str) -> int:
    """比较语义化版本。a>b→1, a<b→-1, 相等→0。"""
    def parts(v: str) -> list[int]:
        nums = []
        for p in v.lstrip("vV").split("."):
            try:
                nums.append(int(p))
            except ValueError:
                nums.append(0)
        return nums

    pa, pb = parts(a), parts(b)
    for x, y in zip(pa, pb):
        if x != y:
            return 1 if x > y else -1
    return 0 if len(pa) == len(pb) else (1 if len(pa) > len(pb) else -1)


def sha256_file(path: str) -> str:
    """计算文件 SHA256(安装包完整性校验)。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class UpdateService:
    """检查更新入口。endpoint 未配置时如实报告, 不伪装。"""

    def __init__(self, endpoint: str = "", current_version: str = APP_VERSION):
        self.endpoint = endpoint.strip()
        self.current_version = current_version

    def check(self) -> UpdateInfo:
        if not self.endpoint:
            return UpdateInfo(
                configured=False,
                error="当前未配置在线更新服务",
            )
        # 预留: 未来接入真实服务器时在此实现
        # 1. GET {endpoint}/latest.json
        # 2. compare_versions(current, latest)
        # 3. 下载后 sha256_file() 与 expected 比对
        # 4. 校验通过才允许启动安装程序
        return UpdateInfo(
            configured=True,
            error="更新服务接口已预留, 等待配置真实服务器",
        )
