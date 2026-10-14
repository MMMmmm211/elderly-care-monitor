# -*- coding: utf-8 -*-
"""端到端验证（阶段 E 收尾）：合成视频驱动整条链路。

链路：video_publisher -> /camera/image -> vision_analysis -> /vision/analysis
      -> risk_manager -> /monitor/alert -> alert_delivery -> 截图 + 落库

验证配置（仅测试用，不改生产默认）：
  - video_publisher: source=e2e_scenario.mp4, fps=30（实时播放）
  - vision_analysis: use_temporal_fall=true, use_llava=false（确定性，不依赖慢速 llava）

验证点：
  1. 无人 20s  -> risk_manager 进入 ABSENT
  2. 站立 8s   -> 恢复 NORMAL
  3. 跌倒+持续躺下 20s -> 触发 fall 告警 -> alert_delivery 截图 + 落库
"""
import os
import sys
import time
import glob
import sqlite3
import subprocess

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = r'E:\ros_vision'
SETUP = r'E:\ros2\ros2-windows\setup.bat'
LOG_DIR = os.path.join(ROOT, 'e2e_logs')
DB = os.path.join(ROOT, 'data', 'monitor.db')
ALERTS = os.path.join(ROOT, 'alerts')

WAIT_SEC = 100

# 二次审核模型：可用环境变量 REVIEW_MODEL 覆盖（如 llava:7b）
REVIEW_MODEL = os.environ.get('REVIEW_MODEL', 'qwen2.5vl:7b')


def build(script, params=None):
    inner = ['python', script]
    if params:
        inner += ['--ros-args'] + [f'-p {p}' for p in params]
    return f'call {SETUP} && cd /d {ROOT} && {" ".join(inner)}'


SERVICES = [
    ('video_publisher', build('video_publisher.py',
                              ['source:=e2e_scenario.mp4', 'fps:=30.0'])),
    ('vision_analysis', build('vision_analysis_node.py',
                              ['use_temporal_fall:=true', 'use_llava:=false'])),
    ('risk_manager', build('elderly_monitor/nodes/risk_manager.py')),
    ('alert_delivery', build('alert_delivery_node.py')),
    ('review_agent', build('review_agent_node.py', [f'model:={REVIEW_MODEL}'])),
]


def snapshot():
    n_alerts = len(glob.glob(os.path.join(ALERTS, '*.png')))
    conn = sqlite3.connect(DB)
    n_db = conn.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]
    conn.close()
    return n_alerts, n_db


def stop_all(procs):
    for name, p in procs.items():
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                           capture_output=True, timeout=15)
        except Exception as e:
            print(f'  [warn] 停止 {name} 失败: {e}')


def grep_log(name, *keys):
    path = os.path.join(LOG_DIR, f'{name}.log')
    if not os.path.exists(path):
        return []
    hits = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if any(k in line for k in keys):
                hits.append(line.rstrip())
    return hits


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(LOG_DIR, '*.log')):
        os.remove(old)

    n_alert0, n_db0 = snapshot()
    print(f'[基线] alerts 截图={n_alert0} db 告警={n_db0}')

    print(f'[启动] {len(SERVICES)} 个 ROS 节点 ...')
    procs = {}
    for name, cmd in SERVICES:
        logf = open(os.path.join(LOG_DIR, f'{name}.log'), 'w', encoding='utf-8')
        p = subprocess.Popen(['cmd', '/c', cmd], cwd=ROOT,
                             stdout=logf, stderr=subprocess.STDOUT)
        procs[name] = p
        print(f'  {name} pid={p.pid}')
        time.sleep(1.5)

    print(f'[运行] 等待 {WAIT_SEC}s（合成视频 48s + 启动缓冲）...')
    t0 = time.time()
    while time.time() - t0 < WAIT_SEC:
        alive = {n: (p.poll() is None) for n, p in procs.items()}
        elapsed = int(time.time() - t0)
        print(f'  {elapsed:3d}s 存活={ {k:v for k,v in alive.items() if v} }', flush=True)
        time.sleep(10)

    print('[停止] 关闭所有节点 ...')
    stop_all(procs)
    time.sleep(2)

    # ---------- 检查结果 ----------
    print('\n========== 验证结果 ==========')
    n_alert1, n_db1 = snapshot()

    print(f'[告警投递] 新增截图 {n_alert1 - n_alert0} 张，新增 db 告警 {n_db1 - n_db0} 条')
    for png in sorted(glob.glob(os.path.join(ALERTS, '*.png')))[-3:]:
        print(f'   {os.path.basename(png)}')

    print('\n[vision_analysis] 分析活动统计:')
    for line in grep_log('vision_analysis', '分析(')[-8:]:
        print('  ' + line)

    print('\n[risk_manager] 状态转移与告警:')
    for line in grep_log('risk_manager', '[状态转移]', '[告警]'):
        print('  ' + line)

    print('\n[alert_delivery] 投递动作:')
    for line in grep_log('alert_delivery', '[投递]', 'SMTP', '截图'):
        print('  ' + line)

    print('\n[数据库] 最新告警:')
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    for r in conn.execute(
            'SELECT id, ts, alert_type, risk_level, activity, location, email_status, '
            'review_status, review_result '
            'FROM alerts ORDER BY id DESC LIMIT 3'):
        print(f'  #{r["id"]} {r["ts"]} type={r["alert_type"]} risk={r["risk_level"]} '
              f'activity={r["activity"]} loc={r["location"]} email={r["email_status"]} '
              f'review={r["review_status"]} result={r["review_result"]}')
    conn.close()

    print('\n[review_agent] 二次审核动作:')
    for line in grep_log('review_agent', '[审核]'):
        print('  ' + line)


if __name__ == '__main__':
    main()
