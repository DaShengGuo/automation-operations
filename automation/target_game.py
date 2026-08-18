"""
automation/target_game.py
目标游戏适配器 — 抖音(Douyin)参考实现 + 游戏页面适配区域

== 页面适配说明（新游戏必读） ==
所有游戏特定信息集中在:
  1. config/game.yaml  — 包名/pages 识别规则/popups/login/logout/steps
  2. 本文件的子类     — 仅放 YAML 表达不了的复杂流程

页面标定方法(拿到新页面素材后):
  1. `python main.py devices` 确认设备在线
  2. `python scripts/dump_hierarchy.py <serial>` 导出 UI 层级 XML
  3. 在 XML 中找 resource-id / text / content-desc 填入 game.yaml
  4. 找不到稳定控件时截图制作模板 → templates/game/xxx.png
  5. 把识别规则加入 game.yaml 的 pages / popups 段
  6. `python main.py run --device <serial>` 验证页面识别

UNKNOWN_SELECTOR 约定:
  game.yaml 中未标定的选择器一律不填写(null/缺失)。
  运行时遇到缺失选择器会明确报 "选择器未标定"，不会瞎猜坐标。
"""
from __future__ import annotations

import logging

from automation.base_game import BaseGameAutomation
from models.page_state import PageState

logger = logging.getLogger(__name__)


class DouyinAutomation(BaseGameAutomation):
    """抖音自动化适配器（参考实现）。

    抖音包名: com.ss.android.ugc.aweme
    页面识别/按钮全部来自 config/game.yaml，本类不硬编码任何 selector。
    本项目真机(Redmi K40 + 已登录抖音)可完整走通:
      launch → detect_page(HOME) → execute_task → verify → (logout 按配置)
    """

    def launch(self) -> bool:
        # 抖音启动后偶发弹「青少年模式」等遮罩，先处理再判页
        ok = super().launch()
        if ok:
            self.handle_popups()
        return ok

    def recover(self) -> bool:
        """抖音回首页：优先点底部「首页」Tab，失败则系统 Home 键重启"""
        from core.actions import ActionResult
        result = self.actions.execute({"action": "click_text", "text": "首页",
                                       "timeout": 3})
        if result.ok:
            return self.detector.wait_page(PageState.HOME.value, timeout=15)
        return super().recover()


class TargetGameAutomation(BaseGameAutomation):
    """新游戏适配模板。

    开发新游戏适配:
      1. 复制本类改名(如 MyGameAutomation)
      2. 在 automation/__init__.ADAPTERS 注册: {"mygame": MyGameAutomation}
      3. 新增 config/game_mygame.yaml 或在 game.yaml 中填好对应段
      4. 只覆写 launch/login/execute_task 中 YAML 表达不了的流程
    """

    def launch(self) -> bool:
        logger.warning("[target_game] 目标游戏未标定 — "
                       "请参考本文件顶部注释完成页面适配")
        return super().launch()
