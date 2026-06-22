"""
抖音自动化框架 — 全局配置
坐标基于 1080×1920 竖屏分辨率，按比例自适应
"""
from __future__ import annotations
import os
from pathlib import Path

# ── 项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MATERIALS_DIR = PROJECT_ROOT / "materials"
DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
LOG_DIR = DATA_DIR / "logs"
STATE_DB = DATA_DIR / "state.db"

# 确保运行时目录存在
for _d in [SCREENSHOT_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── 模拟器设置 ──
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920
DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
MUMU_ADB_ADDR = os.environ.get("MUMU_ADB_ADDR", "127.0.0.1:7555")
# ADB 可执行文件路径（系统 PATH 或 Android SDK）
_ADB_CANDIDATES = [
    os.environ.get("ADB_PATH", ""),
    str(Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe"),
    str(Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb"),
    "adb",  # fallback: 依赖 PATH
]
ADB_EXECUTABLE = next((p for p in _ADB_CANDIDATES if p and Path(p).exists()), "adb")

# ── 运营时间 ──
DAY_START_HOUR = 10
DAY_END_HOUR = 21
DAY_END_MINUTE = 30

# ── 评论生命周期时间（秒） ──
LIKE_WAIT_SEC = 300
REPLY_WAIT_SEC = 900
DM_DELAY_SEC = 60
POST_RETRY_COUNT = 3
POST_VERIFY_WAIT = 2
DELETE_GRACE_SEC = 10

# ── 视频筛选 ──
COMMENTS_TO_SAMPLE = 20
FRESHNESS_THRESHOLD = 0.3

# 排除关键词 — 已禁用(仅看评论区新鲜度)
VIDEO_EXCLUDE_KEYWORDS = []

# 目标关键词 — 已禁用(仅看评论区新鲜度)
VIDEO_TARGET_KEYWORDS = []

# ── 风控参数 ──
CLICK_DELAY_MIN = 0.5
CLICK_DELAY_MAX = 2.0
SWIPE_DURATION_MIN = 0.3
SWIPE_DURATION_MAX = 0.8
VIDEO_WATCH_MIN = 3
VIDEO_WATCH_MAX = 8
MAX_ACTIVE_TASKS = 10
REST_EVERY_N_VIDEOS = 30
REST_DURATION_MIN = 30
REST_DURATION_MAX = 90

# ── 手动介入关键词（检测到暂停等用户处理） ──
MANUAL_INTERVENTION_KEYWORDS = [
    "验证码", "滑块", "验证", "滑块拼图", "请完成验证",
    "登录失效", "重新登录", "账号异常",
]

# ── Dashboard ──
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5800

# ── AI 视频筛选 ──
AI_ENABLED = False  # 模型下载卡住, 先用关键词
AI_MODE = "hybrid"  # "keyword_only" | "ai_only" | "hybrid"
AI_USE_BLIP = False  # BLIP 标注(额外~500MB VRAM)
AI_USE_LLM = True    # LLM 最终决策
AI_LLM_MODEL_PATH = str(DATA_DIR / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf")
AI_BGE_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
AI_CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"
AI_QDRANT_PATH = str(DATA_DIR / "qdrant_data")
AI_QDRANT_USE_IN_MEMORY = True
AI_SEED_PATH = str(PROJECT_ROOT / "seeds" / "qdrant_seed.json")
AI_CONFIDENCE_THRESHOLD = 0.6  # 低于此值触发 LLM
AI_SIMILARITY_EXCLUDE_THRESHOLD = 0.5  # 高于此值跳过
AI_FALLBACK_TO_KEYWORD = True
AI_SAMPLE_EVERY_N = 1  # AI 每 N 个关键词通过运行一次

# ── 日志 ──
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
