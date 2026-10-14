# -*- coding: utf-8 -*-
"""
进程守护（阶段 E · 稳定性与自愈）

职责：
  1. 启动全部服务：5 个 ROS2 节点 + Web 界面
  2. 监控每个进程，崩溃自动重启（带退避）
  3. Ollama 看门狗：定期健康检查，挂了自动拉起
  4. 优雅停机：检测到 supervisor.stop 文件时终止所有子进程

运行（普通 Python，无需 ROS2；子进程各自 source ROS2）：
  python supervisor.py
一键启动 / 停止：
  start_all.bat  /  stop_all.bat
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = r'E:\ros_vision'
SETUP = r'E:\ros2\ros2-windows\setup.bat'
PY = sys.executable  # 复用当前 Python（已具备 rclpy/cv2/ollama）

# 摄像头源：默认真实摄像头 0；可用环境变量 CAMERA_SOURCE 覆盖（如 test_videos/03.mp4）
CAMERA_SOURCE = os.environ.get('CAMERA_SOURCE', '0')

STOP_FLAG = os.path.join(ROOT, 'supervisor.stop')

# 进程稳定运行这么久后，连续崩溃计数归零（避免永远退避）
STABLE_SECONDS = 60

# (名称, 命令列表, 是否需要 source ROS2 环境)
SERVICES = [
    ('video_publisher',
     ['python', 'video_publisher.py', '--ros-args', '-p', f"source:='{CAMERA_SOURCE}'", '-p', 'fps:=5.0'],
     True),
    ('vision_analysis',
     ['python', 'vision_analysis_node.py', '--ros-args',
      '-p', 'use_llava:=false', '-p', 'use_temporal_fall:=true',
      '-p', 'analyze_interval:=0.2'],
     True),
    ('risk_manager', ['python', 'elderly_monitor/nodes/risk_manager.py'], True),
    ('alert_delivery', ['python', 'alert_delivery_node.py'], True),
    ('review_agent', ['python', 'review_agent_node.py'], True),
    ('web_ui', ['python', 'web_ui.py', '--host', '0.0.0.0', '--port', '5000'], False),
]


def _build_cmd(cmd_list, needs_ros):
    # cmd_list 是「单个命令」的 argv（如 python xxx.py --arg v），参数之间用空格，
    # 只有命令与命令之间才用 && 串联。
    inner = ' '.join(cmd_list)
    if needs_ros:
        return ['cmd', '/c', f'call {SETUP} && cd /d {ROOT} && {inner}']
    return ['cmd', '/c', f'cd /d {ROOT} && {inner}']


def _ollama_alive() -> bool:
    try:
        urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3)
        return True
    except Exception:
        return False


class Supervisor:
    def __init__(self):
        self.procs = {}      # name -> Popen
        self.backoff = {}    # name -> 连续崩溃次数
        self.started_at = {}  # name -> 最近一次启动时间（稳定运行后清零崩溃计数）
        self._stop = False

    def launch(self, name, cmd):
        try:
            p = subprocess.Popen(
                cmd, cwd=ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self.procs[name] = p
            self.started_at[name] = time.time()
            print(f'[supervisor] 已启动 {name} (pid={p.pid})')
        except Exception as e:
            print(f'[supervisor] 启动 {name} 失败: {e}')

    def start_all(self):
        for name, cmd, needs_ros in SERVICES:
            self.launch(name, _build_cmd(cmd, needs_ros))

    def kill(self, name):
        p = self.procs.get(name)
        if not p:
            return
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    def restart(self, name, cmd):
        print(f'[supervisor] 重启 {name} ...')
        self.kill(name)
        time.sleep(1)
        self.launch(name, cmd)

    def _watch_ollama(self):
        down_count = 0
        while not self._stop:
            if not _ollama_alive():
                down_count += 1
                print(f'[supervisor] Ollama 无响应（第 {down_count} 次）')
                if down_count >= 3:
                    print('[supervisor] 尝试拉起 Ollama ...')
                    try:
                        subprocess.Popen(['ollama', 'serve'],
                                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                    except Exception as e:
                        print(f'[supervisor] 拉起 Ollama 失败: {e}')
                    down_count = 0
            else:
                down_count = 0
            time.sleep(20)

    def run(self):
        # 清理旧停机标记
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
        self.start_all()
        threading.Thread(target=self._watch_ollama, daemon=True).start()

        print('[supervisor] 全部服务已启动，进入守护模式（停止请运行 stop_all.bat）')
        cmds = {name: _build_cmd(cmd, needs_ros) for name, cmd, needs_ros in SERVICES}

        while not self._stop:
            # 停机标记检测
            if os.path.exists(STOP_FLAG):
                print('[supervisor] 收到停机标记，正在优雅关闭...')
                break

            # 崩溃检测与重启（带指数退避，稳定运行后清零计数）
            now = time.time()
            for name, p in list(self.procs.items()):
                if p.poll() is not None:  # 进程已退出
                    self.backoff[name] = self.backoff.get(name, 0) + 1
                    delay = min(5 * self.backoff[name], 60)
                    print(f'[supervisor] {name} 已退出（第 {self.backoff[name]} 次），{delay}s 后重启')
                    time.sleep(delay)
                    if self._stop or os.path.exists(STOP_FLAG):
                        break
                    self.restart(name, cmds[name])
                elif now - self.started_at.get(name, now) >= STABLE_SECONDS:
                    # 已稳定运行足够久，连续崩溃计数归零
                    if self.backoff.get(name, 0) != 0:
                        self.backoff[name] = 0
                        print(f'[supervisor] {name} 已稳定运行，崩溃计数清零')
            time.sleep(2)

        # 关闭所有子进程
        for name in list(self.procs.keys()):
            self.kill(name)
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
        print('[supervisor] 已全部关闭')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    Supervisor().run()


if __name__ == '__main__':
    main()
