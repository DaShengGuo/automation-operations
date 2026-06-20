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
        self._stop_event = threading.Event()

    def setup(self):
        logger.info("=" * 50)
        logger.info("  抖音自动化评论运营系统 启动中...")
        logger.info("=" * 50)

        if not self.test_mode:
            self.ctrl = DouyinController()
            self._sync_images_to_emulator()
            self.ctrl.open_douyin()
            time.sleep(3)
            # 强制回到推荐Tab首页（抖音可能启动到朋友页或其他Tab）
            self.ctrl.nav.open_recommend_tab()
            time.sleep(2)
            logger.info("[导航] 已切换到推荐Tab首页")
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

        logger.info("[系统] 初始化完成，开始运行（Ctrl+C 停止）")

    def _sync_images_to_emulator(self):
        """
        启动时通过 ADB 将对比图推送到模拟器相册。
        推送到 /sdcard/DCIM/douyin_bot/ → 触发媒体扫描 → 相册中可见。
        """
        import subprocess
        images_dir = cfg.MATERIALS_DIR / "images"
        if not images_dir.exists():
            logger.warning("[图库同步] images 目录不存在，跳过")
            return

        dest = "/sdcard/DCIM/douyin_bot/"
        adb = cfg.ADB_EXECUTABLE

        try:
            subprocess.run([adb, "-s", cfg.MUMU_ADB_ADDR, "shell",
                           f"mkdir -p {dest}"],
                          capture_output=True, timeout=10)
            files = list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png"))
            pushed = 0
            for f in files:
                result = subprocess.run(
                    [adb, "-s", cfg.MUMU_ADB_ADDR, "push", str(f), dest + f.name],
                    capture_output=True, timeout=15
                )
                if result.returncode == 0:
                    pushed += 1
            logger.info(f"[图库同步] 推送 {pushed}/{len(files)} 张图片到模拟器")

            # 触发媒体扫描
            subprocess.run(
                [adb, "-s", cfg.MUMU_ADB_ADDR, "shell",
                 "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                 f"-d file://{dest}"],
                capture_output=True, timeout=10
            )
            time.sleep(2)
        except Exception as e:
            logger.warning(f"[图库同步] 失败: {e}（继续运行）")

    def run(self):
        self.setup()
        self._main_loop()

    def _main_loop(self):
        video_index = 0
        video_count_since_rest = 0
        last_badge_check = time.time()

        while (self.interrupt.state.name != "STOPPED"
               and not self._stop_event.is_set()):
            if self.interrupt.is_paused:
                time.sleep(0.5)
                continue

            if not self._is_operating_hours():
                time.sleep(60)
                continue

            try:
                # 1. 定期检查消息Tab红点（每20秒），有回复立即处理
                if time.time() - last_badge_check > 20:
                    last_badge_check = time.time()
                    self._check_message_badge_and_handle()

                # 2. 检查定时器到期任务
                expired = self.scheduler.check_timers()
                for vid in expired:
                    self._handle_expired_timer(vid)

                # 3. 获取就绪任务
                ready_task = self.scheduler.get_ready_task()
                if ready_task:
                    self._execute_task(ready_task)
                    self.scheduler.cleanup_completed()
                    self._push_dashboard_update()
                    continue

                # 4. 刷视频
                if self.test_mode:
                    self._simulate_video_scan(video_index)
                else:
                    self._scan_video()
                video_index += 1
                video_count_since_rest += 1

                # 4. 每20个视频校准一次：确保还在推荐Tab（防止误触跳转到朋友/消息页）
                if video_index > 0 and video_index % 20 == 0 and not self.test_mode:
                    self.ctrl.nav.open_recommend_tab()
                    time.sleep(1.0)

                # 5. 定期休息
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
        真实模式：手动滑动刷视频（效率优先）。
        抖音推荐Tab会自动连播，但我们主动手动滑可以控制节奏、更快跳过不相关内容。
        策略：立即截图 → OCR判断 → 非目标直接滑走 → 目标则打开评论区。
        """
        # 等视频渲染稳定后截图
        time.sleep(random.uniform(0.8, 1.5))
        ss = self.ctrl.base.screenshot(f"video_scan_{int(time.time())}")

        # 检查验证码
        if self.ctrl.check_captcha():
            logger.warning("[验证码] 检测到验证码，暂停等待手动处理")
            self.interrupt.pause()
            return

        # OCR 判断视频内容
        result = self.filter.check_content(ss)

        if result != FilterResult.PASS:
            # 非目标 → 手动滑走（比等自动连播快）
            time.sleep(random.uniform(0.5, 1.0))
            self.ctrl.nav.swipe_next_video()
            return

        # 目标视频 → 打开评论区（暂停连播）
        if not self.ctrl.nav.open_comments():
            self.ctrl.nav.swipe_next_video()
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

        # 关闭评论区，继续手动滑下一个
        self.ctrl.nav.close_comments()
        self.ctrl.nav.swipe_next_video()

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
            # 滚动到底部确保输入栏完全可见
            self.ctrl.base._swipe_up(0.05)  # 轻滑确保输入栏露出
            time.sleep(0.5)
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

    def _check_message_badge_and_handle(self):
        """
        检查底部消息Tab红点 → 有则进入消息流处理回复。
        这是主要的回复检测方式（比逐条评论区检查更可靠）。
        """
        if self.test_mode:
            return

        if not self.ctrl.nav.check_message_badge():
            return  # 无红点，跳过

        logger.info("[消息] 检测到消息Tab红点，进入互动消息处理...")

        # 保存当前状态，处理完消息后恢复刷视频
        try:
            # 1. 打开消息Tab
            self.ctrl.nav.open_messages_tab()

            # 2. 打开互动消息
            if not self.ctrl.nav.open_interaction_messages():
                logger.info("[消息] 未找到互动消息入口，返回首页")
                self.ctrl.nav.close_messages_to_home()
                return

            # 3. 打开第一条回复详情（最新回复在最上面）
            if not self.ctrl.nav.open_first_reply_detail():
                logger.info("[消息] 无法打开回复详情")
                self.ctrl.nav.close_messages_to_home()
                return

            # 4. OCR 识别回复内容
            ss = self.ctrl.base.screenshot(f"msg_reply_{int(time.time())}")
            texts = ocr_full_screen(ss)
            user_comment = " ".join(texts) if texts else "怎么治的"
            logger.info(f"[消息] 回复内容OCR: {user_comment[:80]}")

            # 5. 根据回复内容生成回复
            reply_text = self.materials.pick_reply(user_comment)

            # 6. 在详情页直接回复
            self.ctrl.nav.reply_in_message_detail(reply_text)
            logger.info(f"[消息] 已回复: {reply_text}")

            # 7. 更新统计数据
            update_stats(today_replies=1)

            # 8. 等待1分钟后关注+私信
            time.sleep(cfg.DM_DELAY_SEC)

            # 9. 点击用户头像 → 关注 → 私信
            self.ctrl.user.click_user_avatar()
            time.sleep(1.5)
            if self.ctrl.user.follow_user():
                logger.info("[消息] 已关注用户")
            time.sleep(1.0)
            self.ctrl.user.send_dm(self.materials.pick_dm())
            update_stats(today_dms=1)
            logger.info("[消息] 已发送私信")

            # 10. 返回首页继续刷视频
            self.ctrl.nav.close_messages_to_home()

            # 将对应的 WAITING_REPLY 任务标记为已处理
            for vid, fsm in list(self.scheduler.active_tasks.items()):
                if fsm.state == FSMState.WAITING_REPLY:
                    fsm.transition(FSMState.REPLYING)
                    fsm.transition(FSMState.FOLLOWING)
                    fsm.transition(FSMState.DM_SEND)
                    fsm.mark_completed()
                    self.db.save(fsm)
                    break  # 一次处理一条

        except Exception as e:
            logger.error(f"[消息处理异常] {e}", exc_info=True)
            # 尝试恢复到首页
            try:
                self.ctrl.nav.close_messages_to_home()
            except Exception:
                pass

    def _check_likes(self) -> bool:
        if self.test_mode:
            return random.random() > 0.4
        xml = self.ctrl.base.dump_hierarchy()
        return "点赞" in xml

    def _check_replies(self) -> bool:
        """
        检查是否有回复。优先使用消息Tab红点检测（更可靠）。
        测试模式用随机；真实模式检查消息Tab红点。
        """
        if self.test_mode:
            return random.random() > 0.5
        return self.ctrl.nav.check_message_badge()

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
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("\n[中断] 收到 Ctrl+C，正在安全退出...")
        bot._stop_event.set()
        bot.interrupt.stop()
        bot._shutdown()
        logger.info("[中断] 已安全退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
