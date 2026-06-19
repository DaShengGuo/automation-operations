const socket = io();
let startTime = null, runTimer = null;

socket.on("connect", () => addLog("已连接到服务器"));
socket.on("status", data => updateUI(data));
socket.on("log", data => addLog(data.msg));
socket.on("resume_with_compensation", data => addLog(`时间补偿: 暂停了 ${data.duration.toFixed(0)} 秒`));

function updateUI(data) {
    const dot = document.getElementById("state-dot");
    const st = document.getElementById("state-text");
    dot.className = "dot";
    if (data.state === "RUNNING") {
        dot.classList.add("green"); st.textContent = "运行中";
        if (!startTime) startTime = Date.now();
        startRunTimer(); setButtons(true);
    } else if (data.state === "PAUSED") {
        dot.classList.add("yellow"); st.textContent = "已暂停";
        stopRunTimer(); setButtons(false);
    } else {
        dot.classList.add("red"); st.textContent = "已停止";
        stopRunTimer();
        document.querySelectorAll(".controls button").forEach(b => b.disabled = true);
    }
    const s = data.stats;
    document.getElementById("stat-comments").textContent = s.today_comments || 0;
    document.getElementById("stat-likes").textContent = s.today_likes || 0;
    document.getElementById("stat-replies").textContent = s.today_replies || 0;
    document.getElementById("stat-dms").textContent = s.today_dms || 0;
    document.getElementById("stat-active").textContent = data.active_count || 0;
    renderTasks(data.active_tasks || []);
}

function setButtons(running) {
    document.getElementById("btn-pause").disabled = !running;
    document.getElementById("btn-resume").disabled = running;
    document.getElementById("btn-stop").disabled = false;
}

function startRunTimer() {
    if (runTimer) return;
    runTimer = setInterval(() => {
        const e = Math.floor((Date.now() - startTime) / 1000);
        document.getElementById("run-time").textContent = `已运行: ${Math.floor(e/3600)}h ${Math.floor((e%3600)/60)}m`;
    }, 10000);
}

function stopRunTimer() { clearInterval(runTimer); runTimer = null; }

function renderTasks(tasks) {
    const c = document.getElementById("tasks-container");
    if (!tasks.length) { c.innerHTML = '<div class="empty-state">暂无活跃任务</div>'; return; }
    c.innerHTML = tasks.map(t => `
        <div class="task-card">
            <div class="task-state ${t.state}"></div>
            <div class="task-info">
                <div>🎬 ${t.video_id} — ${t.copywriting}</div>
                <div class="meta">重试:${t.retry_count} | 删除:${t.delete_count}</div>
            </div>
            <div class="task-timer">${t.state==='WAITING_LIKE' ? '⏱ '+Math.floor(t.remaining_like_wait/60)+'m' : ''}${t.state==='WAITING_REPLY' ? '⏱ '+Math.floor(t.remaining_reply_wait/60)+'m' : ''}</div>
        </div>`).join("");
}

function addLog(msg) {
    const c = document.getElementById("logs-container"), time = new Date().toLocaleTimeString("zh-CN");
    const e = document.createElement("div"); e.className = "log-entry";
    e.innerHTML = `<span class="time">[${time}]</span>${msg}`;
    c.prepend(e); if (c.children.length > 50) c.removeChild(c.lastChild);
}

function pauseBot() { socket.emit("pause"); }
function resumeBot() { socket.emit("resume"); }
function stopBot() { socket.emit("stop"); }
