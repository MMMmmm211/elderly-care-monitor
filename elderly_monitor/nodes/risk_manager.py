"""
风险状态机节点：订阅 /vision/analysis -> 状态机 -> 发布 /monitor/alert 与 /monitor/state

参数（可用 --ros-args -p 覆盖）：
  watch_sec / alert_sec / fall_sec / absent_sec / absent_alert_sec / recover_sec / cooldown_sec
"""
import os
import sys

# 让 elderly_monitor 包可被导入（尚未 colcon 安装，先手动加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from elderly_monitor.messages import PersonStatus
from elderly_monitor.state_machine import RiskStateMachine


class RiskManager(Node):
    def __init__(self):
        super().__init__('risk_manager')

        # 阈值统一从 config.yaml 的 risk 段读取；命令行 -p 仍可覆盖
        from app_config import section
        cfg = section('risk', {
            'watch_sec': 10.0,
            'alert_sec': 20.0,
            'fall_sec': 2.0,
            'squat_sec': 15.0,
            'absent_sec': 15.0,
            'absent_alert_sec': 30.0,
            'recover_sec': 30.0,
            'cooldown_sec': 300.0,
            'night_check_hour': 20.0,
        })
        for k, v in cfg.items():
            self.declare_parameter(k, float(v))

        def g(name):
            return float(self.get_parameter(name).value)

        self.sm = RiskStateMachine(
            watch_sec=g('watch_sec'),
            alert_sec=g('alert_sec'),
            fall_sec=g('fall_sec'),
            squat_sec=g('squat_sec'),
            absent_sec=g('absent_sec'),
            absent_alert_sec=g('absent_alert_sec'),
            recover_sec=g('recover_sec'),
            cooldown_sec=g('cooldown_sec'),
            night_check_hour=g('night_check_hour'),
        )

        self.sub = self.create_subscription(String, 'vision/analysis', self.on_analysis, 10)
        self.alert_pub = self.create_publisher(String, 'monitor/alert', 10)   # 真正的告警
        self.state_pub = self.create_publisher(String, 'monitor/state', 10)   # 状态转移

        self.get_logger().info('风险状态机节点启动：订阅 /vision/analysis，'
                               '发布 /monitor/alert 与 /monitor/state')
        self.get_logger().info(
            '生效阈值(来自 config.yaml，-p 可覆盖)：'
            f'跌倒<{g("fall_sec"):.0f}s 久蹲<{g("squat_sec"):.0f}s '
            f'关注<{g("watch_sec"):.0f}s 高危<{g("alert_sec"):.0f}s '
            f'无人<{g("absent_sec"):.0f}s 恢复<{g("recover_sec"):.0f}s '
            f'冷却<{g("cooldown_sec"):.0f}s')

    def on_analysis(self, msg: String):
        try:
            status = PersonStatus.from_json(msg.data)
        except Exception as e:
            self.get_logger().warn(f'解析分析结果失败: {e}')
            return

        event = self.sm.update(status)
        if event is None:
            return

        out = String()
        out.data = event.to_json()

        if event.alert_type == 'state_change':
            self.state_pub.publish(out)
            self.get_logger().info(f'[状态转移] -> {event.state}: {event.description}')
        else:
            self.alert_pub.publish(out)
            self.get_logger().warn(f'[告警] {event.alert_type} risk={event.risk_level}: {event.description}')


def main(args=None):
    rclpy.init(args=args)
    node = RiskManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
