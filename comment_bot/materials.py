"""
comment_bot/materials.py
Excel 素材管理器 — 文案/图片/私信/回复 的读取和随机选取
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Optional

import openpyxl

from douyin_core import config as cfg


class MaterialManager:
    def __init__(self, excel_path: str = None):
        path = Path(excel_path or cfg.MATERIALS_DIR / "materials.xlsx")
        if not path.exists():
            self._create_template(path)
        try:
            self.wb = openpyxl.load_workbook(path)
        except Exception:
            self._create_template(path)
            self.wb = openpyxl.load_workbook(path)
        self._excel_path = path
        self._daily_counters: dict[str, dict[int, int]] = {
            "copywriting": {},
            "images": {},
        }
        self._last_pick_day: str = ""

    def _create_template(self, path: Path):
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "评论文案"
        ws1.append(["id", "category", "content", "priority", "daily_limit", "enabled"])
        ws1.append([1, "效果展示", "用了这个方法，白斑真的淡了！前后对比太明显了", "high", 20, "✅"])
        ws1.append([2, "经验分享", "我也是白斑困扰多年，终于找到了方法", "high", 15, "✅"])
        ws1.append([3, "互动引导", "有同样困扰的姐妹看过来，这个方法真的有用", "medium", 10, "✅"])
        ws2 = wb.create_sheet("对比图片")
        ws2.append(["id", "name", "before_path", "after_path", "category", "daily_limit", "enabled"])
        ws2.append([1, "手部对比", "images/before_hand.jpg", "images/after_hand.jpg", "手部", 15, "✅"])
        ws2.append([2, "面部对比", "images/before_face.jpg", "images/after_face.jpg", "面部", 15, "✅"])
        ws2.append([3, "腿部对比", "images/before_leg.jpg", "images/after_leg.jpg", "腿部", 10, "✅"])
        ws3 = wb.create_sheet("私信模板")
        ws3.append(["id", "trigger", "content", "enabled"])
        ws3.append([1, "默认", "看到你评论问我，我是吃药加照光弄好的", "✅"])
        ws3.append([2, "追问", "具体就是口服药+定期照光，坚持了半年左右，你可以试试", "✅"])
        ws4 = wb.create_sheet("回复模板")
        ws4.append(["id", "trigger_keywords", "reply_type", "content", "enabled"])
        ws4.append([1, "怎么治,如何治,什么方法", "text", "吃药加照光", "✅"])
        ws4.append([2, "真的吗,有用吗,效果", "text", "真的！坚持就会有效果", "✅"])
        ws4.append([3, "*", "emoji", "😊👍", "✅"])
        path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)

    def _reset_daily_counters_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if self._last_pick_day != today:
            self._daily_counters = {"copywriting": {}, "images": {}}
            self._last_pick_day = today

    def _read_sheet(self, sheet_name: str) -> list[dict]:
        if sheet_name not in self.wb.sheetnames:
            return []
        ws = self.wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            return []
        headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
        result = []
        for row in rows[1:]:
            item = dict(zip(headers, row))
            enabled = str(item.get("enabled", "✅")).strip()
            if enabled not in ("✅", "True", "true", "1", "是"):
                continue
            result.append(item)
        return result

    def get_copywritings(self) -> list[dict]:
        return self._read_sheet("评论文案")

    def get_image_pairs(self) -> list[dict]:
        return self._read_sheet("对比图片")

    def get_dm_templates(self) -> list[dict]:
        return self._read_sheet("私信模板")

    def get_reply_templates(self) -> list[dict]:
        return self._read_sheet("回复模板")

    def pick_copywriting(self) -> Optional[dict]:
        self._reset_daily_counters_if_new_day()
        items = self.get_copywritings()
        if not items:
            return None
        weights = {"high": 5, "medium": 3, "low": 1}
        available = [
            it for it in items
            if self._daily_counters["copywriting"].get(it["id"], 0)
               < int(it.get("daily_limit", 999))
        ]
        if not available:
            self._daily_counters["copywriting"] = {}
            available = items
        chosen = random.choices(
            available,
            weights=[weights.get(str(it.get("priority", "medium")), 3)
                     for it in available],
            k=1
        )[0]
        cid = chosen["id"]
        self._daily_counters["copywriting"][cid] = \
            self._daily_counters["copywriting"].get(cid, 0) + 1
        return chosen

    def pick_image_pair(self) -> Optional[dict]:
        self._reset_daily_counters_if_new_day()
        items = self.get_image_pairs()
        if not items:
            return None
        available = [
            it for it in items
            if self._daily_counters["images"].get(it["id"], 0)
               < int(it.get("daily_limit", 999))
        ]
        if not available:
            self._daily_counters["images"] = {}
            available = items
        chosen = random.choice(available)
        cid = chosen["id"]
        self._daily_counters["images"][cid] = \
            self._daily_counters["images"].get(cid, 0) + 1
        return chosen

    def pick_dm(self, trigger: str = "默认") -> str:
        templates = self.get_dm_templates()
        for t in templates:
            if str(t.get("trigger", "")).strip() == trigger:
                return str(t.get("content", ""))
        if templates:
            return str(templates[0].get("content", ""))
        return "看到你评论问我，我是吃药加照光弄好的"

    def pick_reply(self, user_comment: str) -> str:
        templates = self.get_reply_templates()
        for t in templates:
            keywords = str(t.get("trigger_keywords", "")).split(",")
            if "*" in keywords:
                continue
            for kw in keywords:
                if kw.strip() in user_comment:
                    return str(t.get("content", ""))
        for t in templates:
            if "*" in str(t.get("trigger_keywords", "")):
                return str(t.get("content", ""))
        return "😊👍"

    def reload(self):
        self.wb = openpyxl.load_workbook(self._excel_path)
