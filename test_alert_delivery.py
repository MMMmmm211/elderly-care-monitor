# -*- coding: utf-8 -*-
"""
阶段 D 快速自检：不跑 llava，直接向 alert_delivery_node 发布一条
「图片 + 分析 + 跌倒告警」，验证节点能正确截图、落库、尝试发信（SMTP 未配置则跳过）。
"""
import json
import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import cv2
import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

from image_utils import numpy_to_imgmsg
from alert_delivery_node import AlertDeliveryNode


class Publisher(Node):
    def __init__(self):
        super().__init__('test_pub')
        self.img_pub = self.create_publisher(Image, 'camera/image', 10)
        self.analysis_pub = self.create_publisher(String, 'vision/analysis', 10)
        self.alert_pub = self.create_publisher(String, 'monitor/alert', 10)
        self._fired = False
        self.create_timer(0.5, self.fire)

    def fire(self):
        if self._fired:
            return
        self._fired = True
        img = cv2.imread(r'E:\ros_vision\test_room.jpg')
        self.img_pub.publish(numpy_to_imgmsg(img, 'bgr8'))
        ana = {
            'activity': '躺下', 'location': '地面', 'expression': '平静',
            'body_state': '蜷缩', 'possible_risk': '高', 'need_followup': True,
            'confidence': 0.8,
        }
        self.analysis_pub.publish(String(data=json.dumps(ana, ensure_ascii=False)))
        alert = {
            'state': 'alert', 'alert_type': 'fall', 'risk_level': '高',
            'description': '检测到老人异常躺下，疑似跌倒或突发急症', 'duration_sec': 0.0,
        }
        self.alert_pub.publish(String(data=json.dumps(alert, ensure_ascii=False)))
        self.get_logger().info('已发布测试图片/分析/跌倒告警')


def main():
    rclpy.init()
    adn = AlertDeliveryNode()
    pub = Publisher()
    ex = MultiThreadedExecutor(num_threads=2)
    ex.add_node(adn)
    ex.add_node(pub)

    t0 = time.time()
    while time.time() - t0 < 6.0:
        ex.spin_once(timeout_sec=0.1)

    adn.destroy_node()
    pub.destroy_node()
    rclpy.shutdown()
    print('TEST DONE')


if __name__ == '__main__':
    main()
