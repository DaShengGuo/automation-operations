"""
desktop/checkpoint.py
RuntimeCheckpoint — 每台设备的运行恢复点(临时, 存 AppPaths.runtime)。

注意: Checkpoint 只是辅助。恢复时以「手机当前真实页面」优先
(优先级: actual_state = detector.detect_state() > RuntimeCheckpoint)。

应用正常关闭 → clean_runtime() 清除; 手机页面本身不会变, 下次
运行靠真实页面识别恢复, 不依赖本文件。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from version import APP_VERSION


@dataclass
class RuntimeCheckpoint:
    device_serial: str = ""
    account_id: Optional[int] = None
    masked_account: str = ""
    current_state: str = ""          # WorkerState
    detected_page: str = ""          # 手机真实页面(PokemonGoState)
    last_completed_state: str = ""
    next_state: str = ""
    last_action: str = ""
    last_success_action: str = ""
    state_enter_time: float = 0.0
    account_start_time: float = 0.0
    app_version: str = APP_VERSION
    saved_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "RuntimeCheckpoint":
        data = json.loads(text)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CheckpointStore:
    """runtime/ 目录下的临时恢复点文件(每设备一个)。"""

    def __init__(self, runtime_dir: Path):
        self.dir = Path(runtime_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, serial: str) -> Path:
        # serial 只含安全字符, 直接作文件名
        safe = "".join(c for c in serial if c.isalnum() or c in "-_")
        return self.dir / f"checkpoint_{safe or 'unknown'}.json"

    def save(self, cp: RuntimeCheckpoint) -> None:
        cp.saved_at = time.time()
        try:
            self._path(cp.device_serial).write_text(
                cp.to_json(), encoding="utf-8")
        except OSError:
            pass  # 临时文件写失败不阻断运行

    def load(self, serial: str) -> Optional[RuntimeCheckpoint]:
        try:
            text = self._path(serial).read_text(encoding="utf-8")
            return RuntimeCheckpoint.from_json(text)
        except (OSError, ValueError):
            return None

    def clear_all(self) -> None:
        for p in self.dir.glob("checkpoint_*.json"):
            try:
                p.unlink()
            except OSError:
                pass
