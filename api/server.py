"""
api/server.py
FastAPI 中控后台 — REST + WebSocket

启动:
  python main.py api          # 只启动 Web 后台
  python main.py run --web    # 自动化 + Web 后台
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.websocket import hub, router as ws_router
from core.logger import mask_account

logger = logging.getLogger(__name__)

# 调度器由进程注入（见 run_api / main.py）
_scheduler_holder: dict = {"scheduler": None}


def set_scheduler(scheduler):
    _scheduler_holder["scheduler"] = scheduler
    hub.scheduler = scheduler


def _sched():
    s = _scheduler_holder["scheduler"]
    if s is None:
        raise HTTPException(503, "调度器未启动")
    return s


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动广播任务
    import asyncio
    task = asyncio.create_task(hub.broadcast_loop())
    yield
    task.cancel()


app = FastAPI(title="Android 多设备游戏自动化中控", version="1.0.0",
              lifespan=lifespan)
app.include_router(ws_router)


class AccountsIn(BaseModel):
    """账号动态导入请求体"""
    accounts: list[dict]   # [{"account": "...", "password": "..."}]
    max_retry: Optional[int] = None


# ── 查询接口 ──

@app.get("/api/devices")
def list_devices():
    snap = _sched().snapshot()
    return {"devices": snap["devices"]}


@app.get("/api/accounts")
def list_accounts(status: str = ""):
    sched = _sched()
    accounts = sched.accounts.list_all(status or None)
    return {"accounts": [a.to_dict() for a in accounts]}


@app.get("/api/tasks")
def list_tasks(limit: int = 100, device: str = ""):
    sched = _sched()
    rows = sched.results.list(limit=limit, device_serial=device)
    for r in rows:
        if r.get("account"):
            r["account"] = mask_account(r["account"])
    return {"tasks": rows}


@app.get("/api/statistics")
def statistics():
    snap = _sched().snapshot()
    return {
        "accounts": snap["accounts"],
        "system": snap["system"],
        "devices_total": len(snap["devices"]),
        "devices_ready": sum(
            1 for d in snap["devices"] if d["status"] in ("READY", "RUNNING")),
        "devices_error": sum(
            1 for d in snap["devices"] if d["status"] == "DEVICE_ERROR"),
    }


# ── 系统控制 ──

@app.post("/api/system/start")
def system_start():
    result = _sched().start()
    return result


@app.post("/api/system/pause")
def system_pause():
    _sched().pause()
    return {"ok": True}


@app.post("/api/system/stop")
def system_stop():
    _sched().stop()
    return {"ok": True}


# ── 设备控制 ──

@app.post("/api/devices/{serial}/start")
def device_start(serial: str):
    return _sched().start_device(serial)


@app.post("/api/devices/{serial}/stop")
def device_stop(serial: str):
    return _sched().stop_device(serial)


@app.post("/api/devices/{serial}/restart")
def device_restart(serial: str):
    return _sched().restart_device(serial)


# ── 账号动态导入 ──

@app.post("/api/accounts")
def add_accounts(body: AccountsIn):
    """HTTP API 导入账号（供外部程序调用，不强耦合聊天软件）"""
    sched = _sched()
    items = []
    for row in body.accounts:
        account = str(row.get("account", "")).strip()
        if not account:
            continue
        items.append((account, str(row.get("password", ""))))
    max_retry = body.max_retry or sched.cfg.retry_for("account_max")
    added, skipped = sched.accounts.add_batch(items, max_retry=max_retry)
    return {"ok": True, "added": added, "skipped": skipped}


# ── 简易首页 ──

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <html><head><meta charset="utf-8">
    <title>自动化中控</title></head>
    <body style="font-family:sans-serif;background:#111;color:#eee;
        display:flex;flex-direction:column;align-items:center;padding-top:60px">
      <h1>🤖 Android 多设备游戏自动化中控</h1>
      <p>WebSocket: <code>/ws/status</code>（实时状态）</p>
      <p>REST 文档: <a href="/docs" style="color:#4af">/docs</a></p>
      <pre id="status" style="background:#1c1c1c;padding:16px;
        border-radius:8px;max-width:90%;overflow:auto">
        连接 /ws/status 后此处显示实时状态...</pre>
      <script>
        const ws = new WebSocket(
            (location.protocol === 'https:' ? 'wss://' : 'ws://')
            + location.host + '/ws/status');
        ws.onmessage = (e) => {
            document.getElementById('status').textContent =
                JSON.stringify(JSON.parse(e.data), null, 2);
        };
      </script>
    </body></html>
    """


def run_api(host: str = "127.0.0.1", port: int = 8900,
            scheduler=None, blocking: bool = False):
    """在独立线程中启动 uvicorn。blocking=True 时阻塞当前线程。"""
    import uvicorn
    if scheduler is not None:
        set_scheduler(scheduler)
    if blocking:
        uvicorn.run(app, host=host, port=port, log_level="warning")
        return None
    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=host, port=port,
                                   log_level="warning"),
        daemon=True, name="api-server")
    thread.start()
    return thread
