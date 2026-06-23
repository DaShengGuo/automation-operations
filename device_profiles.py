"""
设备配置文件 — 不同手机+抖音版本的参数
"""
import json, os
from pathlib import Path

PROFILES_FILE = Path(__file__).parent / "device_profiles.json"

DEFAULTS = {
    "input_rids": ["erc", "ern", "ej3"],
    "img_btn_descs": ["插入图片", "image"],
    "img_btn_fallback": (0.078, 0.904),
    "plus_btn": (0.30, 0.76),
    "send_btn_coord": (0.89, 0.90),
    "circle1": (0.58, 0.23),
    "circle2": (0.91, 0.23),
}

PROFILES = {
    # 89U899UOYPAQXSCI — pond (Redmi) Android14 抖音38.1.0 720x1640
    "89U899UOYPAQXSCI": {
        "model": "pond",
        "android_sdk": 34,
        "douyin_version": "38.1.0",
        "resolution": [720, 1640],
        "plus_btn": (0.30, 0.76),
        "notes": "加号在缩略图正右边, 图片按钮desc=插入图片, 输入框rid=ej3"
    },
    # RS5XCI7XDYXWMFFI — pond Redmi14C 720x1640 抖音旧版
    "RS5XCI7XDYXWMFFI": {
        "model": "pond",
        "resolution": [720, 1640],
        "plus_btn": (0.30, 0.76),
        "notes": "抖音旧版, desc=插入图片, 输入框rid=ern"
    },
}

# Honor KOZ-AL00 720x1600 抖音37.8.0 — hierarchy无底部元素, 纯坐标
_HONOR = {
    "model": "KOZ-AL00",
    "resolution": [720, 1600],
    "douyin_version": "37.8.0",
    "plus_btn": (0.35, 0.93),
    "img_btn_coord": (0.525, 0.95),
    "send_btn_coord": (0.915, 0.95),
    "input_coord": (0.25, 0.95),
    "img_btn_descs": [],  # hierarchy无desc, 纯坐标
    "img_btn_fallback": (0.525, 0.95),
    "notes": "hierarchy无底部元素, 全部坐标定位"
}
for _s in ["AADE9X3919W01912","AADE9X3A13W01108","AADE9X3A20W00120","AADE9X3A21W05111"]:
    PROFILES[_s] = dict(_HONOR)

def get_profile(device_serial):
    """获取设备配置, 合并默认值"""
    profile = PROFILES.get(device_serial, {})
    cfg = dict(DEFAULTS)
    for k, v in profile.items():
        if k in cfg:
            cfg[k] = v
    return cfg

def save_profile(device_serial, **kwargs):
    """保存设备配置"""
    if device_serial in PROFILES:
        PROFILES[device_serial].update(kwargs)
    else:
        PROFILES[device_serial] = kwargs
