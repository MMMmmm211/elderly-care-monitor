# -*- coding: utf-8 -*-
"""supervisor 自愈冒烟测试：启动 supervisor，杀掉 vision_analysis，观察是否自动重启。"""
import os
import re
import sys
import time
import subprocess

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = r'E:\ros_vision'
LOG = os.path.join(ROOT, 'e2e_logs', 'supervisor.log')
STOP_FLAG = os.path.join(ROOT, 'supervisor.stop')


def main():
    if os.path.exists(STOP_FLAG):
        os.remove(STOP_FLAG)

    env = dict(os.environ)
    env['CAMERA_SOURCE'] = 'e2e_scenario.mp4'

    logf = open(LOG, 'w', encoding='utf-8')
    p = subprocess.Popen(['python', '-u', 'supervisor.py'], cwd=ROOT, env=env,
                         stdout=logf, stderr=subprocess.STDOUT)
    print(f'supervisor 已启动 pid={p.pid}')

    time.sleep(20)  # 等 5 个服务全部启动

    with open(LOG, encoding='utf-8', errors='replace') as f:
        content = f.read()
    m = re.search(r'已启动 vision_analysis \(pid=(\d+)\)', content)
    if not m:
        print('[FAIL] 未在 supervisor 日志中找到 vision_analysis pid')
    else:
        vpid = m.group(1)
        print(f'杀掉 vision_analysis pid={vpid} ...')
        subprocess.run(['taskkill', '/F', '/T', '/PID', vpid], capture_output=True)
        time.sleep(14)  # 等 supervisor 检测退出 + 退避 + 重启

    # 停止 supervisor
    open(STOP_FLAG, 'w').write('stop')
    time.sleep(10)
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True, timeout=10)
    except Exception:
        pass
    logf.close()

    print('\n=== supervisor 日志（自愈相关）===')
    with open(LOG, encoding='utf-8', errors='replace') as f:
        for line in f:
            if any(k in line for k in ('已启动 vision_analysis', 'vision_analysis 已退出',
                                       '重启 vision_analysis', '已全部关闭')):
                print('  ' + line.rstrip())


if __name__ == '__main__':
    main()
