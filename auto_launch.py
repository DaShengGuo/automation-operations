"""
自动启动 — 根据机型自动检测已连接设备并启动
用法: python auto_launch.py honor    # 启动所有已连接的荣耀
      python auto_launch.py redmi    # 启动所有已连接的红米
"""
import sys, subprocess, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from douyin_core.config import ADB_EXECUTABLE as adb
from device_profiles import PROFILES

# 机型分组
MODELS = {
    "honor": [s for s, p in PROFILES.items() if p.get("model") == "KOZ-AL00"],
    "redmi": [s for s, p in PROFILES.items() if p.get("model") == "pond"],
}

def get_online():
    r = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10)
    return [l.split()[0] for l in r.stdout.split("\n") if "\tdevice" in l]

if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "honor"
    known = MODELS.get(model, [])
    online = get_online()
    targets = [s for s in known if s in online]

    if not targets:
        print(f"未检测到在线{model}设备")
        print(f"已知设备: {known}")
        print(f"在线设备: {online}")
        sys.exit(1)

    print(f"检测到 {len(targets)} 台{model}设备: {targets}")

    # 每个设备启动一个进程
    procs = []
    for dev in targets:
        p = subprocess.Popen(
            ["python", "test_filter_real.py", dev],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        procs.append((dev, p))
        print(f"  启动: {dev} (PID {p.pid})")

    print(f"\n已启动 {len(procs)} 个进程. Ctrl+C 停止所有.")
    try:
        for _, p in procs:
            p.wait()
    except KeyboardInterrupt:
        for dev, p in procs:
            p.terminate()
            print(f"已停止: {dev}")
