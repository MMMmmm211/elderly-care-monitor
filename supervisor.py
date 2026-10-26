# -*- coding: utf-8 -*-
"""
进程守护（阶段 E · 稳定性与自愈）

职责：
  1. 先确保 Ollama 就绪（VLM 复核链路依赖它），再启动全部服务
  2. 启动全部服务：5 个 ROS2 节点 + Web 界面
  3. 监控每个进程，崩溃自动重启（非阻塞退避，不卡主循环）
  4. Ollama 看门狗：定期健康检查，挂了自动拉起并验证
  5. 每个服务的 stdout/stderr 落盘到 logs/<name>.log，出问题能查
  6. 优雅停机：检测到 supervisor.stop 文件时终止所有子进程

运行（普通 Python，无需 ROS2；子进程各自 source ROS2）：
  python supervisor.py
一键启动 / 停止：
  start_all.bat  /  stop_all.bat
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

ROOT = r'E:\ros_vision'
SETUP = r'E:\ros2\ros2-windows\setup.bat'
PY = sys.executable  # 复用当前 Python（已具备 rclpy/cv2/ollama）

# 摄像头源：默认真实摄像头 0；可用环境变量 CAMERA_SOURCE 覆盖（如 test_videos/03.mp4）
CAMERA_SOURCE = os.environ.get('CAMERA_SOURCE', '0')

STOP_FLAG = os.path.join(ROOT, 'supervisor.stop')
LOG_DIR = os.path.join(ROOT, 'logs')
OLLAMA_URL = 'http://localhost:11434/api/tags'
# Ollama 模型目录已迁到 E 盘，避免 C 盘告急；用户级环境变量已设，这里兜底
os.environ.setdefault('OLLAMA_MODELS', r'E:\ollama-models')

# 进程稳定运行这么久后，连续崩溃计数归零（避免永远退避）
STABLE_SECONDS = 60

# 单个服务日志超过这个体积就轮转，防止 vision 这种高频日志把磁盘吃满
MAX_LOG_BYTES = 5 * 1024 * 1024

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
    # 用当前解释器的绝对路径，避免 cmd 里 `python` 解析到另一个缺 rclpy 的环境。
    # 注意：整体拼成一个字符串交给 cmd /c，不能让 subprocess 再用列表去加引号
    # （否则 `&& "C:\...\python.exe"` 会被转义成 \" 而报「不是内部或外部命令」）。
    argv = list(cmd_list)
    argv[0] = f'"{PY}"' if ' ' in PY else PY
    inner = ' '.join(argv)
    if needs_ros:
        line = f'call {SETUP} && cd /d {ROOT} && {inner}'
    else:
        line = f'cd /d {ROOT} && {inner}'
    return f'cmd /c {line}'


def _ollama_alive() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _ollama_models() -> list:
    try:
        import json
        with urllib.request.urlopen(OLLAMA_URL, timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        return [m.get('name', '?') for m in data.get('models', [])]
    except Exception:
        return []


def _ollama_exe() -> str:
    exe = shutil.which('ollama')
    if exe:
        return exe
    cand = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Ollama', 'ollama.exe')
    return cand if os.path.exists(cand) else 'ollama'


class Supervisor:
    def __init__(self):
        self.procs = {}       # name -> Popen
        self.backoff = {}     # name -> 连续崩溃次数
        self.started_at = {}  # name -> 最近一次启动时间（稳定运行后清零崩溃计数）
        self.next_retry = {}  # name -> 下次允许重启的时间戳（非阻塞退避）
        self.logs = {}        # name -> 打开的日志文件句柄
        self.ollama_proc = None   # 仅当本进程拉起 Ollama 时才持有，停机时一起收
        self._stop = False

    # ---------- 日志 ----------
    def _open_log(self, name):
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f'{name}.log')
        # 轮转：超过 MAX_LOG_MB 就把旧日志改名成 <name>.log.1（只保留一份历史）
        try:
            if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
                bak = f'{path}.1'
                if os.path.exists(bak):
                    os.remove(bak)
                os.replace(path, bak)
        except Exception as e:
            print(f'[supervisor] 日志轮转失败 {name}: {e}')
        f = open(path, 'ab', buffering=0)
        f.write(f'\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 {name} =====\n'.encode('utf-8'))
        self.logs[name] = f
        return f

    def _close_log(self, name):
        f = self.logs.pop(name, None)
        if f:
            try:
                f.close()
            except Exception:
                pass

    # ---------- Ollama ----------
    def ensure_ollama(self, wait_s=90) -> bool:
        """启动节点前先把 Ollama 拉起来——否则 review_agent 一上来就没有复核能力。"""
        if _ollama_alive():
            models = _ollama_models()
            print(f'[supervisor] Ollama 已在运行，模型={models}')
            return True

        exe = _ollama_exe()
        print(f'[supervisor] Ollama 未运行，正在拉起：{exe}')
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log = open(os.path.join(LOG_DIR, 'ollama_serve.log'), 'ab', buffering=0)
            log.write(f'\n===== {datetime.now():%Y-%m-%d %H:%M:%S} ollama serve =====\n'.encode('utf-8'))
            self.ollama_proc = subprocess.Popen(
                [exe, 'serve'], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as e:
            print(f'[supervisor] 拉起 Ollama 失败: {e}')
            return False

        t0 = time.time()
        while time.time() - t0 < wait_s:
            if _ollama_alive():
                print(f'[supervisor] Ollama 就绪（{time.time() - t0:.1f}s），模型={_ollama_models()}')
                return True
            time.sleep(2)
        print(f'[supervisor] Ollama 启动超时（{wait_s}s），VLM 复核链路将不可用')
        return False

    def _watch_ollama(self):
        down_count = 0
        while not self._stop:
            if not _ollama_alive():
                down_count += 1
                print(f'[supervisor] Ollama 无响应（第 {down_count} 次）')
                if down_count >= 3:
                    print('[supervisor] 尝试拉起 Ollama ...')
                    if self.ensure_ollama(wait_s=60):
                        print('[supervisor] Ollama 已恢复')
                    down_count = 0
            else:
                if down_count:
                    print('[supervisor] Ollama 已恢复')
                down_count = 0
            time.sleep(20)

    # ---------- 服务进程 ----------
    def launch(self, name, cmd):
        try:
            logf = self._open_log(name)
            p = subprocess.Popen(
                cmd, cwd=ROOT,
                stdout=logf, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self.procs[name] = p
            self.started_at[name] = time.time()
            print(f'[supervisor] 已启动 {name} (pid={p.pid})，日志 logs/{name}.log')
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
        self._close_log(name)

    def restart(self, name, cmd):
        print(f'[supervisor] 重启 {name} ...')
        self.kill(name)
        time.sleep(1)
        self.launch(name, cmd)

    def run(self):
        # 清理旧停机标记
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)

        # 关键顺序：先 Ollama，再节点
        self.ensure_ollama()
        self.start_all()
        threading.Thread(target=self._watch_ollama, daemon=True).start()

        print('[supervisor] 全部服务已启动，进入守护模式（停止请运行 stop_all.bat）')
        cmds = {name: _build_cmd(cmd, needs_ros) for name, cmd, needs_ros in SERVICES}

        while not self._stop:
            # 停机标记检测
            if os.path.exists(STOP_FLAG):
                print('[supervisor] 收到停机标记，正在优雅关闭...')
                break

            # 崩溃检测与重启：只记录「何时重试」，不在主循环里 sleep，
            # 否则一个节点崩溃会把其他节点的检测和停机响应全部拖住。
            now = time.time()
            for name, p in list(self.procs.items()):
                if p.poll() is not None:  # 进程已退出
                    if name not in self.next_retry:
                        self.backoff[name] = self.backoff.get(name, 0) + 1
                        delay = min(5 * self.backoff[name], 60)
                        self.next_retry[name] = now + delay
                        print(f'[supervisor] {name} 已退出（第 {self.backoff[name]} 次），'
                              f'{delay}s 后重启')
                    elif now >= self.next_retry[name]:
                        del self.next_retry[name]
                        self.restart(name, cmds[name])
                else:
                    self.next_retry.pop(name, None)
                    if (now - self.started_at.get(name, now) >= STABLE_SECONDS
                            and self.backoff.get(name, 0) != 0):
                        self.backoff[name] = 0
                        print(f'[supervisor] {name} 已稳定运行，崩溃计数清零')
            time.sleep(2)

        # 关闭所有子进程
        for name in list(self.procs.keys()):
            self.kill(name)
        for name in list(self.logs.keys()):
            self._close_log(name)
        if self.ollama_proc and self.ollama_proc.poll() is None:
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.ollama_proc.pid)],
                               capture_output=True, timeout=10)
            except Exception:
                pass
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
