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

# ── 运营时间 ──
DAY_START_HOUR = 10
DAY_END_HOUR = 21
DAY_END_MINUTE = 30

# ── 评论生命周期时间（秒） ──
LIKE_WAIT_SEC = 300
REPLY_WAIT_SEC = 900
DM_DELAY_SEC = 60
POST_RETRY_COUNT = 3
POST_VERIFY_WAIT = 3
DELETE_GRACE_SEC = 10

# ── 视频筛选 ──
COMMENTS_TO_SAMPLE = 20
FRESHNESS_THRESHOLD = 0.3

# 排除关键词 — 命中任一则跳过（病种/医疗相关）
VIDEO_EXCLUDE_KEYWORDS = [
    "白癜风", "银屑病", "皮肤病医院", "皮肤科", "皮肤科医生",
    "挂号", "就诊", "处方", "医保", "医院", "诊所",
    "确诊", "检查", "病理", "药膏", "激素",
]

# 目标关键词 — 需命中任一才考虑评论
VIDEO_TARGET_KEYWORDS = [
    "白斑", "美白", "祛斑", "淡斑", "色斑", "去斑",
    "皮肤暗沉", "肤色不均", "色素", "暗黄", "肤色",
    "变白", "脸上斑", "祛痘印", "痘印",
]

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

# ── 日志 ──
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
