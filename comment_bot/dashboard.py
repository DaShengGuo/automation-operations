"""
comment_bot/dashboard.py
Flask Web Dashboard — 实时状态面板 + 暂停/恢复控制
"""
from __future__ import annotations

import threading
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

from douyin_core import config as cfg

app = Flask(__name__,
            template_folder=str(cfg.PROJECT_ROOT / "templates"),
            static_folder=str(cfg.PROJECT_ROOT / "templates" / "static"))
app.config["SECRET_KEY"] = "douyin-bot-dashboard"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_scheduler_ref = None
_interrupt_ref = None
_stats_ref = {"today_comments": 0, "today_likes": 0, "today_replies": 0, "today_dms": 0}
_dashboard_thread = None


def set_refs(scheduler, interrupt_controller):
    global _scheduler_ref, _interrupt_ref
    _scheduler_ref = scheduler
    _interrupt_ref = interrupt_controller


def update_stats(**kwargs):
    _stats_ref.update(kwargs)


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("connect")
def on_connect():
    emit("status", _get_full_status())


@socketio.on("request_status")
def on_request_status():
    emit("status", _get_full_status())


@socketio.on("pause")
def on_pause():
    if _interrupt_ref:
        ok = _interrupt_ref.pause()
        emit("status", _get_full_status())
        emit("log", {"msg": "⏸ 已暂停" if ok else "已在暂停状态"})


@socketio.on("resume")
def on_resume():
    if _interrupt_ref:
        duration = _interrupt_ref.resume()
        if duration > 0:
            emit("resume_with_compensation", {"duration": duration})
            emit("log", {"msg": f"▶ 已恢复 (暂停了 {duration:.0f} 秒)"})
        else:
            emit("log", {"msg": "已在运行状态"})


@socketio.on("stop")
def on_stop():
    if _interrupt_ref:
        _interrupt_ref.stop()
        emit("status", _get_full_status())
        emit("log", {"msg": "⏹ 已停止"})


def _get_full_status() -> dict:
    state = "STOPPED"
    active_tasks = []
    if _interrupt_ref:
        state = _interrupt_ref.state.name
    if _scheduler_ref:
        active_tasks = _scheduler_ref.get_state_summary()
    return {
        "state": state,
        "stats": {**_stats_ref},
        "active_count": len(active_tasks),
        "active_tasks": active_tasks,
    }


def start_dashboard():
    global _dashboard_thread
    if _dashboard_thread and _dashboard_thread.is_alive():
        return
    _dashboard_thread = threading.Thread(
        target=lambda: socketio.run(
            app, host=cfg.DASHBOARD_HOST, port=cfg.DASHBOARD_PORT,
            allow_unsafe_werkzeug=True, debug=False, use_reloader=False
        ),
        daemon=True,
        name="dashboard-thread",
    )
    _dashboard_thread.start()
