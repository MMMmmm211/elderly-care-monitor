# -*- coding: utf-8 -*-
"""
阶段 B 端到端记录器：订阅全链路话题，把带时间戳的原始数据落盘为 JSONL。
不参与业务逻辑，只做「取证」——用真实时间戳证明：
  1) /camera/image 帧率是否流畅
  2) /vision/analysis 分析结果（llava 原始输出）
  3) /monitor/state、/monitor/alert 状态机输出

运行（需先 source ROS2 环境）：
  python recorder_node.py --ros-args -p duration:=60.0
输出：E:\\ros_vision\\e2e_log.jsonl
"""
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

OUT = r'E:\ros_vision\e2e_log.jsonl'


class Recorder(Node):
    def __init__(self):
        super().__init__('recorder')
        self.declare_parameter('duration', 60.0)
        self.duration = float(self.get_parameter('duration').value)

        self.t0 = time.time()
        self.cam_frames = 0
        self.cam_times = []
        self._f = open(OUT, 'a', encoding='utf-8')
        # 清空旧文件，保证每次跑都是干净数据
        self._f.seek(0)
        self._f.truncate(0)

        self.create_subscription(Image, 'camera/image', self.cam_cb, 10)
        self.create_subscription(String, 'vision/analysis', self.analysis_cb, 10)
        self.create_subscription(String, 'monitor/state', self.state_cb, 10)
        self.create_subscription(String, 'monitor/alert', self.alert_cb, 10)

        self._tick = self.create_timer(1.0, self.tick)
        self.get_logger().info(f'[recorder] 启动，时长={self.duration}s，输出={OUT}')

    def _log(self, kind, payload):
        rec = {'t': round(time.time() - self.t0, 3), 'kind': kind, 'data': payload}
        self._f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self._f.flush()

    def cam_cb(self, msg):
        self.cam_frames += 1
        self.cam_times.append(time.time())
        if self.cam_frames % 25 == 0:
            self.get_logger().info(f'[camera] 已收 {self.cam_frames} 帧')

    def analysis_cb(self, msg):
        try:
            d = json.loads(msg.data)
        except Exception:
            d = {'raw': msg.data}
        self.get_logger().info(
            f'[analysis] activity={d.get("activity")} risk={d.get("possible_risk")} '
            f'conf={d.get("confidence")} followup={d.get("need_followup")}')
        self._log('analysis', d)

    def state_cb(self, msg):
        self.get_logger().info(f'[state] {msg.data}')
        try:
            d = json.loads(msg.data)
        except Exception:
            d = {'raw': msg.data}
        self._log('state', d)

    def alert_cb(self, msg):
        self.get_logger().warn(f'[alert] {msg.data}')
        try:
            d = json.loads(msg.data)
        except Exception:
            d = {'raw': msg.data}
        self._log('alert', d)

    def tick(self):
        dt = time.time() - self.t0
        if dt < self.duration:
            return
        # 收尾：统计帧率并退出
        if len(self.cam_times) >= 2:
            span = self.cam_times[-1] - self.cam_times[0]
            fps = (len(self.cam_times) - 1) / span if span > 0 else 0.0
        else:
            fps = 0.0
        summary = {
            'cam_frames': self.cam_frames,
            'fps': round(fps, 2),
            'duration': round(dt, 2),
        }
        self._log('summary', summary)
        self.get_logger().info(f'[summary] frames={self.cam_frames} fps={fps:.2f} duration={dt:.2f}s')
        self._f.close()
        self.destroy_timer(self._tick)
        rclpy.shutdown()


def main(args=None):
    rclpy.init(args=args)
    node = Recorder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node._f and not node._f.closed:
            node._f.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
