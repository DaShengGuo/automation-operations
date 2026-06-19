"""
tests/test_integration.py — 全系统集成测试（不连设备）
"""
from __future__ import annotations
import sys
import os
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestFullIntegration:
    """端到端集成测试：验证所有模块协同工作"""

    def test_all_imports(self):
        """验证所有模块可正常导入"""
        from douyin_core import config
        from douyin_core.adb_controller import (
            DouyinController, BaseActions, NavigateActions,
            CommentActions, UserActions
        )
        from douyin_core.ocr_engine import (
            parse_comment_time, region_to_pixels,
            crop_and_ocr, ocr_full_screen,
            extract_video_title_texts, extract_comment_times
        )
        from comment_bot.fsm import CommentFSM, CommentTask, FSMState
        from comment_bot.scheduler import TaskScheduler
        from comment_bot.interrupt import InterruptController, BotState
        from comment_bot.materials import MaterialManager
        from comment_bot.filter import VideoFilter, FilterResult
        from comment_bot.persistence import StateDB
        from comment_bot.dashboard import app, set_refs, update_stats
        from comment_bot.main import CommentBot

        assert config.SCREEN_WIDTH == 1080
        assert config.LIKE_WAIT_SEC == 300

    def test_fsm_full_lifecycle(self):
        """验证 FSM 完整生命周期"""
        from comment_bot.fsm import CommentFSM, CommentTask, FSMState

        task = CommentTask(video_id="v1", copywriting="测试文案")
        fsm = CommentFSM(task)

        # PENDING → POSTING → WAITING_LIKE
        assert fsm.state == FSMState.PENDING
        fsm.transition(FSMState.POSTING)
        fsm.mark_posted()
        assert fsm.state == FSMState.WAITING_LIKE

        # 有赞 → WAITING_REPLY
        fsm.check_likes(has_likes=True)
        assert fsm.state == FSMState.WAITING_REPLY

        # 有回复 → REPLYING
        fsm.check_replies(has_replies=True)
        assert fsm.state == FSMState.REPLYING

        # → FOLLOWING → DM_SEND → COMPLETED
        fsm.transition(FSMState.FOLLOWING)
        fsm.transition(FSMState.DM_SEND)
        fsm.mark_completed()
        assert fsm.state == FSMState.COMPLETED

    def test_fsm_no_likes_delete_retry(self):
        """验证无点赞→删除→重发流程"""
        from comment_bot.fsm import CommentFSM, CommentTask, FSMState

        task = CommentTask(video_id="v2", copywriting="test")
        fsm = CommentFSM(task)
        fsm.transition(FSMState.POSTING)
        fsm.mark_posted()
        fsm.check_likes(has_likes=False)
        assert fsm.state == FSMState.DELETING
        fsm.mark_deleted()
        assert fsm.state == FSMState.PENDING
        assert fsm.delete_count == 1

    def test_fsm_retry_limit(self):
        """验证发布失败重试上限"""
        from comment_bot.fsm import CommentFSM, CommentTask, FSMState

        task = CommentTask(video_id="v3", copywriting="test")
        fsm = CommentFSM(task)
        for _ in range(3):
            fsm.transition(FSMState.POSTING)
            fsm.mark_post_failed()
        assert fsm.state == FSMState.FAILED
        assert fsm.retry_count == 3

    def test_scheduler_priority(self):
        """验证调度器优先级排序"""
        from comment_bot.fsm import CommentFSM, CommentTask, FSMState
        from comment_bot.scheduler import TaskScheduler

        sched = TaskScheduler(max_active=5)

        # 添加 PENDING 任务 (P2)
        task_low = CommentTask(video_id="low", copywriting="low", image_paths=[])
        fsm_low = CommentFSM(task_low)
        sched.enqueue(fsm_low)

        # 添加 REPLYING 任务 (P0)
        task_high = CommentTask(video_id="high", copywriting="high", image_paths=[])
        fsm_high = CommentFSM(task_high)
        fsm_high.transition(FSMState.POSTING)
        fsm_high.mark_posted()
        fsm_high.check_likes(has_likes=True)
        fsm_high.check_replies(has_replies=True)
        sched.enqueue(fsm_high)

        ready = sched.get_ready_task()
        assert ready.task.video_id == "high"  # P0 优先

    def test_interrupt_pause_resume(self):
        """验证中断控制器暂停恢复"""
        from comment_bot.interrupt import InterruptController, BotState

        ic = InterruptController()
        assert ic.is_running

        ic.pause()
        assert ic.is_paused

        time.sleep(0.05)
        duration = ic.resume()
        assert duration > 0
        assert ic.is_running

        ic.stop()
        assert ic.state == BotState.STOPPED

    def test_interrupt_time_compensation(self):
        """验证时间补偿计算"""
        from comment_bot.interrupt import InterruptController

        tasks = {
            "v1": {"remaining": 300},  # 剩余 5 分钟
            "v2": {"remaining": 30},   # 剩余 30 秒
            "v3": {"remaining": 600},  # 剩余 10 分钟
        }
        immediate, delayed = InterruptController.compute_compensation(
            tasks, pause_duration=60
        )
        assert "v2" in immediate  # 30-60 < 0
        assert "v1" in delayed and delayed["v1"] == 240
        assert "v3" in delayed and delayed["v3"] == 540

    def test_persistence_roundtrip(self):
        """验证 SQLite 持久化往返"""
        from comment_bot.fsm import CommentFSM, CommentTask, FSMState
        from comment_bot.persistence import StateDB

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        db = StateDB(path)
        task = CommentTask(video_id="v_test", copywriting="hello",
                           image_paths=["a.jpg", "b.jpg"])
        fsm = CommentFSM(task)
        fsm.transition(FSMState.POSTING)
        fsm.mark_posted()
        db.save(fsm)

        loaded = db.load("v_test")
        assert loaded is not None
        assert loaded.task.video_id == "v_test"
        assert loaded.state == FSMState.WAITING_LIKE
        assert loaded.task.image_paths == ["a.jpg", "b.jpg"]

        db.delete("v_test")
        assert db.load("v_test") is None
        db.close()
        os.unlink(path)

    def test_materials_pick(self):
        """验证素材管理器选取"""
        from comment_bot.materials import MaterialManager

        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)

        mm = MaterialManager(path)
        cw = mm.pick_copywriting()
        assert cw is not None
        assert len(cw["content"]) > 0

        pair = mm.pick_image_pair()
        assert pair is not None

        dm = mm.pick_dm("默认")
        assert len(dm) > 0

        reply = mm.pick_reply("怎么治的")
        assert reply is not None

        # 无匹配关键词 → 默认表情包
        fallback = mm.pick_reply("完全不相关的问题")
        assert fallback is not None

        os.unlink(path)

    def test_filter_freshness(self):
        """验证视频筛选器时效计算"""
        from comment_bot.filter import VideoFilter

        vf = VideoFilter(
            exclude_keywords=["白癜风"],
            target_keywords=["白斑", "美白"],
        )

        # 全新鲜
        assert vf.calc_freshness_score([0, 1, 2, 3]) > 0.8

        # 全旧
        assert vf.calc_freshness_score([30, 60, 120]) == 0.0

        # 混合
        score = vf.calc_freshness_score([2] * 10 + [10] * 5 + [20] * 5)
        expected = 0.5 * 0.6 + 0.75 * 0.4
        assert abs(score - expected) < 0.01

    def test_ocr_time_parsing(self):
        """验证时间戳解析"""
        from douyin_core.ocr_engine import parse_comment_time

        assert parse_comment_time("刚刚") == 0
        assert parse_comment_time("3分钟前") == 3
        assert parse_comment_time("15分钟前") == 15
        assert parse_comment_time("2小时前") == 120
        assert parse_comment_time("3天前") == 4320
        assert parse_comment_time("30秒前") == 0
        assert parse_comment_time("乱码文本") == 99999

    def test_config_values(self):
        """验证关键配置值"""
        from douyin_core import config

        assert config.LIKE_WAIT_SEC == 300
        assert config.REPLY_WAIT_SEC == 900
        assert config.DM_DELAY_SEC == 60
        assert config.POST_RETRY_COUNT == 3
        assert config.MAX_ACTIVE_TASKS == 10
        assert config.FRESHNESS_THRESHOLD == 0.3
        assert len(config.VIDEO_EXCLUDE_KEYWORDS) > 0
        assert len(config.VIDEO_TARGET_KEYWORDS) > 0

    def test_main_import_and_bot_creation(self):
        """验证主入口类和 bot 实例化"""
        from comment_bot.main import CommentBot

        bot = CommentBot(no_dashboard=True, test_mode=True)
        assert bot.test_mode is True
        assert bot.no_dashboard is True
        assert bot.scheduler is not None
        assert bot.interrupt is not None
        assert bot.materials is not None
        assert bot.filter is not None
        assert bot.db is not None

    def test_dashboard_app(self):
        """验证 Dashboard Flask 应用"""
        from comment_bot.dashboard import app
        assert app is not None
        # 验证路由
        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
