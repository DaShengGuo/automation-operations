"""
comment_bot/main.py
主入口 — 整合所有模块，运行评论运营主循环

使用方式：
    python -m comment_bot.main                    # 正常模式
    python -m comment_bot.main --no-dashboard     # 无 Dashboard 模式
    python -m comment_bot.main --test             # 测试模式（不连设备）
"""
from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from douyin_core import config as cfg
from douyin_core.adb_controller import DouyinController
from douyin_core.ocr_engine import crop_and_ocr, parse_comment_time, ocr_full_screen
from comment_bot.fsm import CommentFSM, CommentTask, FSMState
from comment_bot.scheduler import TaskScheduler
from comment_bot.interrupt import InterruptController
from comment_bot.materials import MaterialManager
from comment_bot.filter import VideoFilter, FilterResult
from comment_bot.persistence import StateDB
from comment_bot.dashboard import (start_dashboard, set_refs, update_stats, socketio)

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL),
    format=cfg.LOG_FORMAT,
    handlers=[
        logging.FileHandler(
            cfg.LOG_DIR / f"bot_{datetime.now():%Y-%m-%d}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("douyin_bot")


class CommentBot:
    def __init__(self, no_dashboard: bool = False, test_mode: bool = False):
        self.test_mode = test_mode
        self.ctrl: DouyinController = None
        self.scheduler = TaskScheduler()
        self.interrupt = InterruptController()
        self.materials = MaterialManager()
        self.filter = VideoFilter()
        self.db = StateDB(str(cfg.STATE_DB))
        self.no_dashboard = no_dashboard

    def setup(self):
        logger.info("=" * 50)
        logger.info("  抖音自动化评论运营系统 启动中...")
        logger.info("=" * 50)

        if not self.test_mode:
            self.ctrl = DouyinController()
            self.ctrl.open_douyin()
        else:
            logger.info("[测试模式] 跳过设备连接")

        # 恢复未完成任务
        active_ids = self.db.list_active()
        logger.info(f"[恢复] 找到 {len(active_ids)} 个未完成任务")
        for vid in active_ids:
            fsm = self.db.load(vid)
            if fsm and fsm.is_active:
                self.scheduler.active_tasks[vid] = fsm

        # 启动 Dashboard
        if not self.no_dashboard:
            set_refs(self.scheduler, self.interrupt)
            start_dashboard()
            logger.info(
                f"[Dashboard] http://{cfg.DASHBOARD_HOST}:{cfg.DASHBOARD_PORT}"
            )

        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)
        logger.info("[系统] 初始化完成，开始运行")

    def run(self):
        self.setup()
        self._main_loop()

    def _main_loop(self):
        video_index = 0
        video_count_since_rest = 0

        while self.interrupt.state.name != "STOPPED":
            if self.interrupt.is_paused:
                time.sleep(0.5)
                continue

            if not self._is_operating_hours():
                time.sleep(60)
                continue

            try:
                # 1. 检查定时器到期任务
                expired = self.scheduler.check_timers()
                for vid in expired:
                    self._handle_expired_timer(vid)

                # 2. 获取就绪任务
                ready_task = self.scheduler.get_ready_task()
                if ready_task:
                    self._execute_task(ready_task)
                    self.scheduler.cleanup_completed()
                    self._push_dashboard_update()
                    continue

                # 3. 刷视频
                if self.test_mode:
                    self._simulate_video_scan(video_index)
                else:
                    self._scan_video()
                video_index += 1
                video_count_since_rest += 1

                # 4. 定期休息
                if video_count_since_rest >= cfg.REST_EVERY_N_VIDEOS:
                    rest_sec = random.randint(
                        cfg.REST_DURATION_MIN, cfg.REST_DURATION_MAX
                    )
                    logger.info(
                        f"[休息] 刷了 {video_count_since_rest} 个视频，"
                        f"休息 {rest_sec} 秒"
                    )
                    time.sleep(rest_sec)
                    video_count_since_rest = 0

                self.scheduler.cleanup_completed()
                self._push_dashboard_update()

            except Exception as e:
                logger.error(f"[错误] {e}", exc_info=True)
                time.sleep(5)

        self._shutdown()

    def _scan_video(self):
        """
        真实模式：抖音推荐Tab视频自动连播。
        策略：立即截图 → OCR判断 → 目标则暂停连播评论 → 非目标则自然等自动跳转。
        不手动滑动！避免与自动连播冲突导致跳2个视频。
        """
        # 立即截图（不等视频播完，因为会自动跳下一个）
        time.sleep(random.uniform(0.5, 1.5))  # 等视频渲染稳定
        ss = self.ctrl.base.screenshot(f"video_scan_{int(time.time())}")

        # 检查验证码
        if self.ctrl.check_captcha():
            logger.warning("[验证码] 检测到验证码，暂停等待手动处理")
            self.interrupt.pause()
            return

        # OCR 判断视频内容
        result = self.filter.check_content(ss)

        if result != FilterResult.PASS:
            # 非目标视频 → 等待自动跳到下一个（抖音推荐Tab会自动连播）
            wait = random.uniform(cfg.VIDEO_WATCH_MIN, cfg.VIDEO_WATCH_MAX)
            time.sleep(wait)
            return

        # 目标视频 → 打开评论区（打开评论区会暂停自动连播）
        if not self.ctrl.nav.open_comments():
            # 打不开评论区，等自动跳转
            time.sleep(random.uniform(cfg.VIDEO_WATCH_MIN, cfg.VIDEO_WATCH_MAX))
            return

        # 评论区已打开，分析时效
        time.sleep(1.5)
        comment_ss = self.ctrl.base.screenshot(
            f"comments_{int(time.time())}"
        )
        time_texts = crop_and_ocr(comment_ss, (0.65, 0.25, 0.92, 0.85))
        times = [parse_comment_time(t) for t in time_texts]
        times = [t for t in times if t < 99999]

        if times:
            score = self.filter.calc_freshness_score(times)
            fres = self.filter.should_comment(score)
            if fres == FilterResult.PASS:
                self._create_comment_task(ss)

        # 关闭评论区，视频继续自动连播
        self.ctrl.nav.close_comments()

    def _simulate_video_scan(self, index: int):
        logger.info(f"[测试] 刷到视频 #{index}")
        time.sleep(1)
        if index % 3 == 0:
            self._create_comment_task(f"fake_ss_{index}")

    def _create_comment_task(self, video_ref: str):
        copywriting = self.materials.pick_copywriting()
        image_pair = self.materials.pick_image_pair()

        if not copywriting:
            logger.warning("[素材] 无可用文案")
            return

        image_paths = []
        if image_pair:
            base = cfg.MATERIALS_DIR
            image_paths = [
                str(base / image_pair.get("before_path", "")),
                str(base / image_pair.get("after_path", "")),
            ]
            image_paths = [p for p in image_paths if Path(p).exists()]

        task = CommentTask(
            video_id=f"video_{int(time.time())}",
            copywriting=copywriting["content"],
            image_paths=image_paths,
        )
        fsm = CommentFSM(task)
        self.scheduler.enqueue(fsm)
        self.db.save(fsm)
        update_stats(today_comments=self.db.get_today_count())
        logger.info(
            f"[新任务] {task.video_id} — {copywriting['content'][:30]}..."
        )

    def _execute_task(self, fsm: CommentFSM):
        state = fsm.state

        if state in (FSMState.PENDING, FSMState.POSTING):
            self._execute_post(fsm)

        elif state == FSMState.WAITING_LIKE:
            has_likes = self._check_likes()
            fsm.check_likes(has_likes)
            if has_likes:
                update_stats(today_likes=1)
                logger.info(f"[点赞] {fsm.task.video_id} 有点赞")
            else:
                logger.info(f"[无点赞] {fsm.task.video_id} 将删除重发")
            self.db.save(fsm)

        elif state == FSMState.WAITING_REPLY:
            has_replies = self._check_replies()
            fsm.check_replies(has_replies)
            if has_replies:
                update_stats(today_replies=1)
                logger.info(f"[回复] {fsm.task.video_id} 有回复")
            else:
                logger.info(f"[无回复] {fsm.task.video_id} 将删除重发")
            self.db.save(fsm)

        elif state == FSMState.DELETING:
            if not self.test_mode:
                self.ctrl.comment.delete_my_comment()
            fsm.mark_deleted()
            self.db.save(fsm)
            logger.info(f"[删除] {fsm.task.video_id} 已删除，准备重发")

        elif state == FSMState.REPLYING:
            self._execute_reply(fsm)

        elif state == FSMState.FOLLOWING:
            if not self.test_mode:
                self.ctrl.user.click_user_avatar()
                time.sleep(1.5)
                self.ctrl.user.follow_user()
                time.sleep(1.0)
            fsm.transition(FSMState.DM_SEND)
            self.db.save(fsm)
            logger.info(f"[关注] {fsm.task.video_id} 已关注用户")

        elif state == FSMState.DM_SEND:
            if not self.test_mode:
                self.ctrl.user.send_dm(fsm.task.dm_message)
            update_stats(today_dms=1)
            fsm.mark_completed()
            self.db.save(fsm)
            logger.info(f"[私信] {fsm.task.video_id} 已发送 → 完成")

        # 设置定时器（如果需要等待）
        if fsm.state == FSMState.WAITING_LIKE:
            self.scheduler.schedule_timer(fsm)
        elif fsm.state == FSMState.WAITING_REPLY:
            self.scheduler.schedule_timer(fsm)

    def _execute_post(self, fsm: CommentFSM):
        fsm.transition(FSMState.POSTING)

        if self.test_mode:
            fsm.mark_posted()
            self.scheduler.schedule_timer(fsm)
            logger.info(f"[测试发布] {fsm.task.video_id} 模拟发布成功")
        else:
            self.ctrl.nav.open_comments()
            time.sleep(1.0)
            self.ctrl.comment.input_comment_text(fsm.task.copywriting)
            if fsm.task.image_paths:
                self.ctrl.comment.add_comment_images(fsm.task.image_paths)
            posted = self.ctrl.comment.submit_comment()
            verified = self.ctrl.comment.verify_comment_published()

            if posted or verified:
                fsm.mark_posted()
                self.scheduler.schedule_timer(fsm)
                logger.info(f"[发布] {fsm.task.video_id} 发布成功")
            else:
                fsm.mark_post_failed()
                logger.warning(
                    f"[发布失败] {fsm.task.video_id} "
                    f"重试 {fsm.retry_count}/3"
                )
            self.ctrl.nav.close_comments()

        self.db.save(fsm)

    def _execute_reply(self, fsm: CommentFSM):
        # 获取用户评论文本
        user_comment = "怎么治的"
        if not self.test_mode and self.ctrl:
            ss = self.ctrl.base.screenshot(f"reply_ocr_{int(time.time())}")
            texts = ocr_full_screen(ss)
            user_comment = " ".join(texts) if texts else "怎么治的"

        reply_text = self.materials.pick_reply(user_comment)

        if not self.test_mode:
            self.ctrl.comment.reply_to_comment(reply_text)

        fsm.transition(FSMState.FOLLOWING)
        self.db.save(fsm)
        logger.info(f"[回复] {fsm.task.video_id} 已回复: {reply_text}")

        # 1 分钟后自动进入 FOLLOWING
        def delayed_follow():
            time.sleep(cfg.DM_DELAY_SEC)

        threading.Thread(target=delayed_follow, daemon=True).start()

    def _handle_expired_timer(self, vid: str):
        fsm = self.scheduler.active_tasks.get(vid)
        if fsm:
            self._execute_task(fsm)

    def _check_likes(self) -> bool:
        if self.test_mode:
            return random.random() > 0.4
        xml = self.ctrl.base.dump_hierarchy()
        return "点赞" in xml

    def _check_replies(self) -> bool:
        if self.test_mode:
            return random.random() > 0.5
        xml = self.ctrl.base.dump_hierarchy()
        return "回复" in xml

    @staticmethod
    def _is_operating_hours() -> bool:
        now = datetime.now()
        start = now.replace(hour=cfg.DAY_START_HOUR, minute=0, second=0)
        end = now.replace(
            hour=cfg.DAY_END_HOUR, minute=cfg.DAY_END_MINUTE, second=0
        )
        return start <= now <= end

    def _push_dashboard_update(self):
        if not self.no_dashboard:
            socketio.emit("status", {
                "state": self.interrupt.state.name,
                "stats": {"today_comments": self.db.get_today_count()},
                "active_count": self.scheduler.active_count,
                "active_tasks": self.scheduler.get_state_summary(),
            })

    def _handle_interrupt(self, signum, frame):
        logger.info("[中断] 收到退出信号")
        self.interrupt.stop()

    def _shutdown(self):
        logger.info("=" * 50)
        logger.info(f"  系统关闭 — 今日评论: {self.db.get_today_count()}")
        logger.info("=" * 50)
        if self.ctrl:
            self.ctrl.close_douyin()
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description="抖音自动化评论运营系统")
    parser.add_argument(
        "--no-dashboard", action="store_true", help="禁用 Web Dashboard"
    )
    parser.add_argument(
        "--test", action="store_true", help="测试模式（不连接设备）"
    )
    args = parser.parse_args()

    bot = CommentBot(no_dashboard=args.no_dashboard, test_mode=args.test)
    bot.run()


if __name__ == "__main__":
    main()
