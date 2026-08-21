"""core.logger 设备日志路径与容错回归测试

背景(实测生产事故): 桌面版经 load_with_data_dirs(game_name="pokemon_go")
注册配置单例, 但模块内 ControlConfig.load() 默认 game_name="game" 与单例
不匹配 → 另建实例 → logs_dir 退回安装目录(_internal\\logs) → Program Files
只读 → [Errno 13] Permission denied 打死 Worker 启动。
"""
import logging

import pytest


@pytest.fixture(autouse=True)
def _cleanup():
    from core.config import ControlConfig
    import core.logger as cl

    ControlConfig.reset()
    yield
    ControlConfig.reset()
    with cl._device_handlers_lock:
        for serial, handler in list(cl._device_handlers.items()):
            handler.close()
            cl._device_handlers.pop(serial, None)


def _register(tmp_path):
    from core.config import ControlConfig

    project = tmp_path / "app"          # 模拟安装目录(客户机器上只读)
    data_root = tmp_path / "userdata"   # 模拟客户数据目录(可写)
    logs_dir = data_root / "logs"
    cfg = ControlConfig.load_with_data_dirs(
        project_root=project,
        game_name="pokemon_go",
        data_dirs={"data_dir": data_root / "data",
                   "screenshots_dir": data_root / "screenshots",
                   "logs_dir": logs_dir})
    return project, logs_dir, cfg


def test_load_returns_registered_desktop_instance(tmp_path):
    """桌面版注册数据目录单例后, 模块内默认 load() 必须返回该实例,
    而不是按 game_name="game" 另建(另建即路径退回安装目录)。"""
    from core.config import ControlConfig

    project, logs_dir, cfg = _register(tmp_path)
    assert ControlConfig.load() is cfg, \
        "默认 load() 未返回桌面版注册的单例"
    assert ControlConfig.load().logs_dir == logs_dir
    assert ControlConfig.load().project_root == project


def test_device_log_written_to_injected_logs_dir(tmp_path):
    """设备日志文件必须落在注入的客户数据目录, 绝不落在安装目录。"""
    from core.config import ControlConfig
    import core.logger as cl

    project, logs_dir, cfg = _register(tmp_path)
    log = cl.get_logger("test.device", device_serial="TEST-SER")
    log.info("hello")

    assert "TEST-SER" in cl._device_handlers
    handler = cl._device_handlers["TEST-SER"]
    assert handler.baseFilename == str(logs_dir / "device_TEST-SER.log"), \
        f"设备日志写到了 {handler.baseFilename} (预期 {logs_dir})"
    assert str(project) not in handler.baseFilename


def test_device_log_creation_failure_does_not_raise(tmp_path, caplog):
    """日志文件不可写(如目录被同名路径占位)时只告警, 不得中断调用方 —
    自动化不得因日志不可写而启动失败。"""
    from core.config import ControlConfig
    import core.logger as cl

    project, logs_dir, cfg = _register(tmp_path)
    # 占位一个同名"目录", FileHandler 打开必失败
    (logs_dir / "device_TEST-FAIL.log").mkdir(parents=True, exist_ok=True)

    with caplog.at_level(logging.WARNING, logger="core.logger"):
        log = cl.get_logger("test.device2", device_serial="TEST-FAIL")
        log.info("still alive")

    assert "TEST-FAIL" not in cl._device_handlers, \
        "失败路径不应登记设备日志 handler"
    assert any("设备日志文件创建失败" in r.message
               for r in caplog.records), f"未输出降级告警: {caplog.records}"


def test_load_with_explicit_game_name_still_works(tmp_path):
    """注册数据目录单例后, 显式 game_name 的 load() 仍走原有语义
    (CLI/测试路径不受影响)。"""
    from core.config import ControlConfig

    _, _, _ = _register(tmp_path)
    cli_cfg = ControlConfig.load("pokemon_go")
    assert cli_cfg.game_name == "pokemon_go"


def test_setup_logging_preserves_qt_log_handler(tmp_path):
    """实时日志回归(2026-08-21): setup_logging() 的 root.handlers.clear()
    不得清掉 GUI 的 QtLogHandler — 否则 GUI 日志区永远空白(实测事故)。
    模拟 app.py 旧时序: 先挂 QtLogHandler 再 setup_logging, handler 必须存活。
    """
    import core.logger as cl

    # 模拟 QtLogHandler(不依赖 PySide6, 只需类名匹配)
    class QtLogHandler(logging.Handler):
        def emit(self, record):
            pass

    root = logging.getLogger()
    qt = QtLogHandler()
    root.addHandler(qt)
    try:
        cl.setup_logging(tmp_path / "logs")
        # QtLogHandler 必须仍在 root handlers 里
        assert any(type(h).__name__ == "QtLogHandler" for h in root.handlers), \
            "setup_logging 清掉了 QtLogHandler, GUI 实时日志会丢失"
    finally:
        root.handlers.clear()

