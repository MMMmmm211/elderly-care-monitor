# -*- coding: utf-8 -*-
"""摄像头开关端到端验证：写数据库 camera_enabled=false，观察 video_publisher 暂停/恢复发布。"""
import os
import sys
import time
import subprocess

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, r'E:\ros_vision')
from elderly_monitor import database

ROOT = r'E:\ros_vision'
SETUP = r'E:\ros2\ros2-windows\setup.bat'
LOG = os.path.join(ROOT, 'e2e_logs', 'cam_toggle.log')


def main():
    # 确保初始为开启
    database.set_camera_enabled(True)

    cmd = (f'call {SETUP} && cd /d {ROOT} && python video_publisher.py '
           f'--ros-args -p source:=e2e_scenario.mp4 -p fps:=5.0')
    logf = open(LOG, 'w', encoding='utf-8')
    p = subprocess.Popen(['cmd', '/c', cmd], cwd=ROOT,
                         stdout=logf, stderr=subprocess.STDOUT)
    print(f'video_publisher 已启动 pid={p.pid}')

    time.sleep(6)  # 启动 + 开始发布
    database.set_camera_enabled(False)
    print('已写 camera_enabled=false')
    time.sleep(4)  # TTL 1s + 缓存过期
    database.set_camera_enabled(True)
    print('已写 camera_enabled=true')
    time.sleep(4)

    subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
    logf.close()

    print('\n=== video_publisher 日志（摄像头开关相关）===')
    with open(LOG, encoding='utf-8', errors='replace') as f:
        for line in f:
            if '摄像头' in line or '启动' in line:
                print('  ' + line.rstrip())


if __name__ == '__main__':
    main()
