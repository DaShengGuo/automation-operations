"""
main.py
Android 多设备游戏自动化中控系统 — 统一命令行入口

用法:
  python main.py doctor                    检查环境
  python main.py devices [--report]        查看手机 / 生成兼容性报告
  python main.py init [--device SERIAL]    初始化手机
  python main.py run [--device SERIAL] [--workers N] [--web]  启动自动化
  python main.py import-accounts <源>      导入账号(xlsx/csv/db/http)
  python main.py export-results [--out]    导出任务结果 Excel
  python main.py api [--port]              启动 Web 中控后台

推荐使用项目虚拟环境:
  py -3.13 -m venv .venv-control
  .venv-control\\Scripts\\python.exe main.py doctor
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ControlConfig
from core.logger import setup_logging
from scripts.check_devices import check_devices
from scripts.doctor import run_doctor
from scripts.init_devices import init_devices


# ── 中控看板 ──

def print_board(scheduler) -> None:
    """实时打印中控运行状态（每 5 秒刷新）"""
    snap = scheduler.snapshot()
    sys_info = snap["system"]
    devices = snap["devices"]
    accounts = snap["accounts"]

    online = sum(1 for d in devices if d["status"] not in ("OFFLINE",))
    errors = sum(1 for d in devices if d["status"] == "DEVICE_ERROR")

    print("\033[2J\033[H", end="")  # 清屏(终端支持时)
    print("=" * 70)
    print("Android 多设备游戏自动化中控"
          + (" [已暂停]" if sys_info["paused"] else ""))
    print("=" * 70)
    print(f"\n设备总数：{len(devices)}")
    print(f"在线：{online}")
    print(f"异常：{errors}\n")
    for d in devices:
        state = d["worker_state"] if d["status"] != "DEVICE_ERROR" else "ERROR"
        account = d["account"] or "-"
        print(f"设备 {d['serial'][:16]:<16} {d['status']:<12} "
              f"{account:<16} {state:<16} {d['page']}")
    print("\n" + "=" * 70)
    print(f"成功：{accounts.get('SUCCESS', 0)}")
    print(f"失败：{accounts.get('FAILED', 0)}")
    print(f"待执行：{accounts.get('PENDING', 0) + accounts.get('RETRY', 0)}")
    print(f"执行中：{accounts.get('RUNNING', 0) + accounts.get('LOCKED', 0)}")
    print("=" * 70)
    print("按 Ctrl+C 停止")


def board_loop(scheduler, stop_event):
    """看板刷新线程"""
    while not stop_event.is_set():
        try:
            print_board(scheduler)
        except Exception as e:
            logging.getLogger(__name__).debug(f"看板刷新异常: {e}")
        stop_event.wait(5)


# ── 子命令 ──

def cmd_doctor(args) -> int:
    if getattr(args, "compat", False):
        cfg = ControlConfig.load()
        from core.device_manager import DeviceManager
        manager = DeviceManager(cfg)
        report = manager.compat_report()
        out = Path(args.out) if getattr(args, "out", "") else \
            cfg.data_dir / "device_compat_report.md"
        out.write_text(report, encoding="utf-8")
        print(report)
        print(f"\n报告已保存: {out}")
        return 0
    return run_doctor()


def cmd_devices(args) -> int:
    if getattr(args, "report", False):
        return cmd_doctor(argparse.Namespace(compat=True, out=""))
    return check_devices()


def cmd_init(args) -> int:
    return init_devices(getattr(args, "device", ""))


def cmd_run(args) -> int:
    cfg = ControlConfig.load(game_name=getattr(args, "game", "") or None)
    setup_logging(cfg.logs_dir, cfg.log_level)
    logger = logging.getLogger("control.main")
    logger.info(f"[游戏] 适配配置: {cfg.game_name} "
                f"(adapter={cfg.game_adapter})")
    if getattr(args, "no_logout", False):
        cfg.system["logout_required"] = False
        logger.warning("--no-logout: 本次运行跳过退出登录")

    from core.task_scheduler import TaskScheduler
    scheduler = TaskScheduler(cfg)

    result = scheduler.start(
        serials=[args.device] if args.device else None,
        max_workers=args.workers if args.workers else None)
    logger.info(f"调度器启动结果: {result}")

    # Web 后台(可选)
    api_thread = None
    if args.web:
        from api.server import run_api
        port = args.web_port or int(cfg.get("api", {}).get("port", 8900))
        api_thread = run_api(port=port, scheduler=scheduler)
        logger.info(f"[Web] 中控后台: http://127.0.0.1:{port} "
                    f"(WebSocket: /ws/status)")

    # 看板线程
    import threading
    board_stop = threading.Event()
    board_thread = threading.Thread(target=board_loop,
                                    args=(scheduler, board_stop), daemon=True)
    board_thread.start()

    try:
        while scheduler.running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，安全退出中...")
    finally:
        board_stop.set()
        scheduler.stop()
        logger.info("系统已安全退出")
    return 0


def cmd_import(args) -> int:
    cfg = ControlConfig.load()
    setup_logging(cfg.logs_dir, cfg.log_level)
    logger = logging.getLogger("control.main")

    from storage.database import Database
    from storage.repositories import AccountRepository
    from core.account_manager import import_accounts

    db = Database(cfg.db_path)
    repo = AccountRepository(db)
    try:
        result = import_accounts(args.source, repo,
                                 max_retry=args.max_retry)
        print(f"账号导入完成: 新增 {result['added']}, "
              f"跳过 {result['skipped']}, 共 {result['total']} 条")
        stats = repo.stats()
        print(f"当前队列: {stats}")
        return 0
    except Exception as e:
        logger.error(f"账号导入失败: {e}")
        print(f"[FAIL] 账号导入失败: {e}")
        return 1
    finally:
        db.close()


def cmd_inspect(args) -> int:
    """检查设备当前页面: 截图/层级/包名/Activity/分辨率/游戏状态识别"""
    cfg = ControlConfig.load(game_name=getattr(args, "game", "") or None)
    from core.device_manager import DeviceManager
    from automation import create_automation

    manager = DeviceManager(cfg)
    devices = manager.scan()
    targets = [d for d in devices
               if (not args.device or d.serial == args.device)]
    if not targets:
        print("未找到设备" + (f": {args.device}" if args.device else ""))
        return 1

    out_dir = Path(args.out) if args.out else cfg.data_dir / "inspect"
    out_dir.mkdir(parents=True, exist_ok=True)

    for d in targets:
        if not d.is_adb_healthy:
            print(f"[SKIP] {d.serial} adb={d.adb_state}")
            continue
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = out_dir / f"{d.serial}_{ts}"
        controller = manager.create_controller(d.serial)
        controller.connect()
        shot = controller.save_screenshot(base.with_suffix(".png"))
        xml = controller.dump_hierarchy()
        (base.with_suffix(".xml")).write_text(xml, encoding="utf-8")
        pkg = controller.adb.current_app(d.serial)
        print("=" * 60)
        print(f"[inspect] {d.serial} {d.brand} {d.model} "
              f"{d.resolution}")
        print(f"  截图: {shot}")
        print(f"  层级: {base.with_suffix('.xml')}")
        print(f"  前台包: {pkg}")

        # 游戏状态识别
        automation = create_automation(cfg.game_adapter, controller, cfg)
        if automation is not None:
            try:
                state = automation.detect_state()
                print(f"  Pokémon 状态: {state.value}")
                if state.is_external_web_state:
                    # PTC 网页检查(与浏览器品牌无关)
                    web = automation.web
                    print(f"  [网页] 用户名框: "
                          f"{'找到' if web._locate_username_input() else '未找到'}")
                    print(f"  [网页] 密码框: "
                          f"{'找到' if web._locate_password_input() else '未找到'}")
                    log_btn = None
                    try:
                        log_btn = controller.device(text="Log In")
                        print(f"  [网页] Log In 按钮: "
                              f"{'找到' if log_btn.exists else '未找到'}")
                    except Exception:
                        print("  [网页] Log In 按钮: 未找到")
            except Exception as e:
                print(f"  状态识别失败: {e}")
    return 0


def cmd_export(args) -> int:
    cfg = ControlConfig.load()
    from storage.database import Database
    from storage.repositories import TaskResultRepository

    db = Database(cfg.db_path)
    repo = TaskResultRepository(db)
    out = Path(args.out) if args.out else \
        cfg.data_dir / f"results_{time.strftime('%Y%m%d')}.xlsx"
    path = repo.export_xlsx(out)
    db.close()
    print(f"任务结果已导出: {path}")
    return 0


def cmd_api(args) -> int:
    cfg = ControlConfig.load()
    setup_logging(cfg.logs_dir, cfg.log_level)
    from api.server import run_api
    from core.task_scheduler import TaskScheduler

    scheduler = TaskScheduler(cfg)
    port = args.port or int(cfg.get("api", {}).get("port", 8900))
    print(f"[Web] 中控后台: http://127.0.0.1:{port}  (Ctrl+C 退出)")
    run_api(host=args.host, port=port, scheduler=scheduler, blocking=True)
    scheduler.stop()
    return 0


# ── 入口 ──

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Android 多设备游戏自动化中控系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="检查环境")
    p_doctor.add_argument("--compat", action="store_true",
                          help="生成设备兼容性报告")
    p_doctor.add_argument("--out", default="", help="报告输出路径")

    p_devices = sub.add_parser("devices", help="查看手机")
    p_devices.add_argument("--report", action="store_true",
                           help="生成设备兼容性报告")

    p_init = sub.add_parser("init", help="初始化手机")
    p_init.add_argument("--device", default="", help="指定设备 serial")

    p_run = sub.add_parser("run", help="启动全部自动化任务")
    p_run.add_argument("--device", default="", help="只运行指定设备")
    p_run.add_argument("--game", default="pokemon_go",
                       help="游戏适配配置名(默认 pokemon_go)")
    p_run.add_argument("--workers", type=int, default=0,
                       help="最大并发设备数(默认取 config.yaml)")
    p_run.add_argument("--web", action="store_true",
                       help="同时启动 Web 中控后台")
    p_run.add_argument("--web-port", type=int, default=0, help="Web 端口")
    p_run.add_argument("--no-logout", action="store_true",
                       help="本次运行不执行退出登录(测试/冒烟用)")

    p_inspect = sub.add_parser(
        "inspect", help="检查设备当前页面: 截图+hierarchy+状态识别")
    p_inspect.add_argument("--device", default="", help="指定设备 serial")
    p_inspect.add_argument("--game", default="pokemon_go",
                           help="游戏适配配置名(默认 pokemon_go)")
    p_inspect.add_argument("--out", default="",
                           help="输出目录(默认 data/inspect)")

    p_import = sub.add_parser("import-accounts", help="导入账号")
    p_import.add_argument("source", help="账号文件(xlsx/csv/db)或 HTTP URL")
    p_import.add_argument("--max-retry", type=int, default=3,
                          help="账号最大失败重试次数")

    p_export = sub.add_parser("export-results", help="导出任务结果 Excel")
    p_export.add_argument("--out", default="", help="输出文件路径")

    p_api = sub.add_parser("api", help="只启动 Web 中控后台")
    p_api.add_argument("--port", type=int, default=0, help="监听端口")
    p_api.add_argument("--host", default="127.0.0.1", help="监听地址")

    p_desktop = sub.add_parser("desktop", help="启动桌面图形界面")
    return parser


def cmd_desktop(args) -> int:
    from desktop.app import main as desktop_main
    return desktop_main()


def main() -> int:
    # 客户端/开发 CLI 同样要求全程无 CMD 黑框(desktop 内另有幂等调用)
    from desktop.process_runner import install_global_hidden_patch
    install_global_hidden_patch()
    args = build_parser().parse_args()
    handlers = {
        "doctor": cmd_doctor,
        "devices": cmd_devices,
        "init": cmd_init,
        "run": cmd_run,
        "inspect": cmd_inspect,
        "import-accounts": cmd_import,
        "export-results": cmd_export,
        "api": cmd_api,
        "desktop": cmd_desktop,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
