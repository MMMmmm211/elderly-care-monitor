"""
视觉分析节点（阶段 E · 混合分析）：订阅 /camera/image -> 混合分析 -> 发布 JSON 到 /vision/analysis

分析架构（双速）：
- 快速通路（姿态 + 决策，~几十 ms/帧）：MediaPipe 姿态检测 + 跌落时间特征 + 合并缓存的
  llava 上下文 -> 决策 -> 发布 PersonStatus。姿态是确定性的，保证「躺下/是否有人」高召回、低延迟。
- 慢速通路（llava 上下文，~秒/帧）：在独立后台线程低频率调用 llava，只问位置/表情/肢体，
  结果缓存供快速通路合并。llava 不再阻塞姿态/告警主链路。

线程模型：
- image_cb（rclpy 回调，ReentrantCallbackGroup）：只存最新帧 + 节流提交姿态任务
- ThreadPoolExecutor(1)：串行跑姿态检测（避免过载）
- 独立 daemon 线程：低频率跑 llava 上下文，失败不拖垮主链路
"""
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from sensor_msgs.msg import Image
from std_msgs.msg import String
from concurrent.futures import ThreadPoolExecutor

import json

from image_utils import imgmsg_to_numpy
from hybrid_analyzer import HybridAnalyzer


class VisionAnalysisNode(Node):
    def __init__(self):
        super().__init__('vision_analysis_node')

        self.declare_parameter('analyze_interval', 0.2)   # 秒，姿态分析节流间隔（快速通路，5fps 下每帧分析）
        self.declare_parameter('model', 'llava:7b')
        self.declare_parameter('use_llava', False)
        self.declare_parameter('llava_interval', 12.0)    # 秒，llava 上下文刷新间隔（慢速通路）
        self.declare_parameter('use_temporal_fall', False)  # 时间跌落特征默认关，需调阈值后开启

        self.interval = float(self.get_parameter('analyze_interval').value)
        model = self.get_parameter('model').value
        use_llava = bool(self.get_parameter('use_llava').value)
        self.llava_interval = float(self.get_parameter('llava_interval').value)
        use_fall = bool(self.get_parameter('use_temporal_fall').value)

        cb_group = ReentrantCallbackGroup()
        self.subscription = self.create_subscription(
            Image, 'camera/image', self.image_cb, 10, callback_group=cb_group)
        self.publisher = self.create_publisher(String, 'vision/analysis', 10)

        self.analyzer = HybridAnalyzer(model=model, use_llava=use_llava, use_temporal_fall=use_fall)
        self._pool = ThreadPoolExecutor(max_workers=1)  # 串行姿态分析
        self._busy = False
        self._last_analyze = 0.0

        # 最新帧槽（供姿态与 llava 两条通路共享）
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._frame_seq = 0

        # llava 慢速通路
        self._stop_llava = threading.Event()
        if use_llava:
            self._llava_thread = threading.Thread(target=self._llava_loop, daemon=True,
                                                  name='llava-context')
            self._llava_thread.start()

        self.get_logger().info(
            f'视觉分析节点启动(混合)：model={model} use_llava={use_llava} '
            f'use_temporal_fall={use_fall} 姿态节流={self.interval}s llava间隔={self.llava_interval}s')

    # ---------- 订阅回调：只存帧 + 节流提交姿态 ----------
    def image_cb(self, msg):
        img = imgmsg_to_numpy(msg)
        with self._frame_lock:
            self._latest_frame = img
            self._frame_seq += 1
            seq = self._frame_seq

        now = time.time()
        if self._busy or (now - self._last_analyze < self.interval):
            return
        self._busy = True
        self._last_analyze = now
        self._pool.submit(self._pose_analyze, img, seq)

    # ---------- 快速通路：姿态 + 决策 ----------
    def _pose_analyze(self, img, seq):
        try:
            t0 = time.time()
            result = self.analyzer.detect(img)   # 姿态 + 跌落特征 + 合并缓存上下文
            cost = time.time() - t0

            out = String()
            out.data = json.dumps(result, ensure_ascii=False)
            self.publisher.publish(out)

            self.get_logger().info(
                f'分析({cost*1000:.0f}ms) risk={result.get("possible_risk")} '
                f'activity={result.get("activity")} loc={result.get("location")} '
                f'fall={result.get("temporal_fall")} followup={result.get("need_followup")}')
        except Exception as e:
            self.get_logger().warn(f'姿态分析失败: {e}')
        finally:
            self._busy = False

    # ---------- 慢速通路：llava 上下文 ----------
    def _llava_loop(self):
        first = True
        while not self._stop_llava.is_set():
            if not first:
                self._stop_llava.wait(self.llava_interval)
                if self._stop_llava.is_set():
                    break
            first = False

            with self._frame_lock:
                img = self._latest_frame
                seq = self._frame_seq
            if img is None:
                continue
            try:
                t0 = time.time()
                self.analyzer.analyze_context(img)
                self.get_logger().info(f'llava 上下文刷新({time.time()-t0:.1f}s)')
            except Exception as e:
                self.get_logger().warn(f'llava 上下文刷新失败: {e}')

    # ---------- 关闭 ----------
    def shutdown(self):
        self._stop_llava.set()
        self._pool.shutdown(wait=False)


def main(args=None):
    rclpy.init(args=args)
    node = VisionAnalysisNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
