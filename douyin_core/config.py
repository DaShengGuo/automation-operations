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
POST_VERIFY_WAIT = 3
DELETE_GRACE_SEC = 10

# ── 视频筛选 ──
COMMENTS_TO_SAMPLE = 20
FRESHNESS_THRESHOLD = 0.3

# 排除关键词 — 命中任一则跳过（病种/医疗/违规相关）
VIDEO_EXCLUDE_KEYWORDS = [
    # 病种名称
    "白癜风", "银屑病", "牛皮癣", "红斑狼疮", "硬皮病",
    "皮肤病", "皮炎", "湿疹", "荨麻疹", "鱼鳞病",
    # 医疗相关
    "皮肤科", "皮肤科医生", "皮肤医院", "皮肤病医院",
    "挂号", "就诊", "处方", "医保", "医院", "诊所",
    "确诊", "检查", "病理", "药膏", "激素", "抗生素",
    "手术", "移植", "激光治疗", "光疗仪",
    # 药物相关
    "他克莫司", "卡泊三醇", "卤米松", "激素药",
    "中药", "偏方", "祖传秘方",
]

# 目标关键词 — 需命中任一才考虑评论
# 自动关联生成：围绕白斑/美白/康复/上岸/对比/变化等主题
VIDEO_TARGET_KEYWORDS = [
    # 白斑相关（核心）
    "白斑", "白点", "白块", "白斑病",
    # 美白淡斑
    "美白", "祛斑", "淡斑", "色斑", "去斑", "消斑",
    "皮肤白", "变白", "白了", "白回来了",
    # 肤色相关
    "皮肤暗沉", "肤色不均", "色素", "暗黄", "肤色",
    "脸上斑", "祛痘印", "痘印", "黑斑", "黄褐斑",
    # 康复上岸（用户文案高频词）
    "康复", "上岸", "恢复", "好了", "痊愈",
    "好转", "改善", "变化", "对比", "效果",
    "惊喜", "值得", "心愿", "了却",
    # 皮肤问题相关
    "皮肤问题", "皮肤困扰", "皮肤瑕疵", "皮肤差",
    "不敢露", "遮遮掩掩", "被歧视", "被孤立",
    "自卑", "自信", "抬不起头", "不敢出门",
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
