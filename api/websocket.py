"""
api/websocket.py
WebSocket 实时状态推送 — /ws/status 每 2 秒广播调度器快照
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


class StatusHub:
    """连接管理 + 广播任务（由 server 启动）"""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.scheduler = None
        self.interval = 2.0

    async def broadcast_loop(self):
        while True:
            await asyncio.sleep(self.interval)
            if not self.clients or self.scheduler is None:
                continue
            snapshot = self.scheduler.snapshot()
            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send_json(snapshot)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)


hub = StatusHub()


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await websocket.accept()
    hub.clients.add(websocket)
    try:
        # 连上先推一次快照
        if hub.scheduler is not None:
            await websocket.send_json(hub.scheduler.snapshot())
        while True:
            # 心跳（客户端消息丢弃）
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"ws 断开: {e}")
    finally:
        hub.clients.discard(websocket)
