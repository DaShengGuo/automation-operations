"""
纯筛选测试 — 刷视频+AI筛选+评论区分析, 不发布。
每个视频: OCR → 关键词/CLIP/BGE → PASS/SKIP → PASS的检查评论区质量
"""
import sys, time, random, logging, os
os.makedirs('c:/temp/douyin-framework/data/logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("filter_test")

import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time
from comment_bot.filter import VideoFilter, FilterResult
from douyin_core import config as cfg

ctrl = DouyinController()
vf = VideoFilter()

# AI 状态
ai_status = "AI(CLIP+BGE)" if cfg.AI_ENABLED else "关键词"
logger.info(f"纯筛选测试开始 [{ai_status}] — Ctrl+C 停止")
logger.info(f"排除: {len(vf.exclude_keywords)}个 | 目标: {len(vf.target_keywords)}个")
logger.info("=" * 60)

stats = {
    "pass": 0, "pass_comment_good": 0, "excluded": 0,
    "irrelevant": 0, "error": 0, "total": 0,
}
TITLE_REGION = (0.05, 0.78, 0.95, 0.90)

try:
    while True:
        ctrl.nav.swipe_next_video()
        stats["total"] += 1
        time.sleep(random.uniform(1.5, 2.5))

        ss = ctrl.base.screenshot(f"filter_test_{int(time.time())}")

        # OCR 文字
        try:
            texts = crop_and_ocr(ss, TITLE_REGION)
        except Exception:
            texts = []

        # 筛选
        result = vf.check_content(ss)

        # 日志
        ocr_preview = " | ".join(texts[:2]) if texts else "(无OCR)"
        icon = {"PASS": "✅", "SKIP_EXCLUDED": "❌", "SKIP_IRRELEVANT": "⏭", "SKIP_INACTIVE": "💤", "SKIP_ERROR": "⚠"}

        if result == FilterResult.PASS:
            stats["pass"] += 1

            # PASS → 检查评论区质量
            if ctrl.nav.open_comments():
                time.sleep(1.5)
                css = ctrl.base.screenshot(f"comments_{int(time.time())}")
                time_texts = crop_and_ocr(css, (0.65, 0.25, 0.92, 0.85))
                times = [parse_comment_time(t) for t in time_texts if parse_comment_time(t) < 99999]

                fresh = sum(1 for t in times if t <= 5)
                score = vf.calc_freshness_score(times) if times else 0
                if vf.should_comment(score) == FilterResult.PASS:
                    stats["pass_comment_good"] += 1

                logger.info(
                    f"  #{stats['total']} ✅ PASS | OCR: {ocr_preview[:80]} | "
                    f"评论:{len(times)}条 新鲜:{fresh} 评分:{score:.2f}"
                )
                ctrl.nav.close_comments()
            else:
                logger.info(f"  #{stats['total']} ✅ PASS | OCR: {ocr_preview[:80]}")

        elif result == FilterResult.SKIP_EXCLUDED:
            stats["excluded"] += 1
            logger.info(f"  #{stats['total']} ❌ EXCLUDED | OCR: {ocr_preview[:80]}")
        elif result == FilterResult.SKIP_IRRELEVANT:
            stats["irrelevant"] += 1
        else:
            stats["error"] += 1

        # 每 10 个统计
        if stats["total"] % 10 == 0:
            total = stats["total"]
            logger.info(
                f"--- [{total}] PASS={stats['pass']}({stats['pass']*100//total}%) "
                f"EXCL={stats['excluded']} IREL={stats['irrelevant']} ERR={stats['error']} "
                f"评论优={stats['pass_comment_good']}"
            )

except KeyboardInterrupt:
    total = max(stats["total"], 1)
    logger.info("\n" + "=" * 60)
    logger.info(
        f"最终统计 [{total}个视频]: "
        f"PASS={stats['pass']}({stats['pass']*100//total}%) "
        f"EXCL={stats['excluded']}({stats['excluded']*100//total}%) "
        f"IRREL={stats['irrelevant']}({stats['irrelevant']*100//total}%) "
        f"评论优质={stats['pass_comment_good']}"
    )
    logger.info("测试结束")
