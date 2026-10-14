# -*- coding: utf-8 -*-
"""
二次审核智能体节点（阶段 F · ③）：告警先出轻提醒，llava 复核确认后再发正式邮件。

流程：
  risk_manager /monitor/alert
    -> alert_delivery（轻提醒：截图 + 落库 + 立即发「疑似告警」邮件，含照片+总结）
    -> alert_delivery 发布 /monitor/review_request
    -> review_agent（本节点）用 llava 对截图做二次审核：
        确认（跌倒/急症/位置不明） -> 发「确认告警」正式邮件（含照片+总结）
        排除（床/沙发正常休息）    -> 不发正式邮件，落库标记「已排除」

为什么用 llava 做二次审核而不是姿态：
  MediaPipe 对「躺下」召回高，但分不清「躺地上(跌倒)」与「躺床上(休息)」；
  llava 擅长位置/表情/肢体等语义。llava 只做事件驱动复核（告警后才触发），
  不占用每帧热路径，规避 llava 慢(生产配置 13~33s/帧)的问题。

并发模型：单工作线程 + 队列，llava 串行调用。实测本机内存是并发瓶颈，
  二次审核走串行 1 路最稳，避免与告警洪峰叠加触发 swap。
"""
import os
import sys
import queue
import threading
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from elderly_monitor import database
from elderly_monitor.emailer import send_alert_with_screenshot
from ollama_analyzer import OllamaAnalyzer


# 复核提示词：重点判断「姿势」与「接触面（地面 vs 床/沙发）」
REVIEW_PROMPT = (
    "你是养老监护二次审核助手，正在复核一张疑似跌倒的监控截图。"
    "请只输出一个JSON对象，字段："
    '{"person_present":true/false,'
    ' "activity":"坐着/站立/行走/躺下/其他",'
    ' "location":"老人接触的表面：地面/床/沙发/椅子/其他/未知",'
    ' "expression":"面部表情：平静/痛苦/看不清",'
    ' "body_state":"肢体状态：放松/挣扎/蜷缩/静止/未知",'
    ' "confidence":0到1小数,'
    ' "explanation":"简短判断依据"}'
    "重点判断：老人现在是什么姿势？若躺下，接触面是地面（可能跌倒）还是床/沙发（可能休息）。"
    "地面=跌倒需紧急处理；床/沙发+平静放松=正常休息，无需告警。"
)


class ReviewAgent(Node):
    def __init__(self):
        super().__init__('review_agent')

        self.declare_parameter('model', 'qwen2.5vl:7b')

        self._q = queue.Queue()
        model = self.get_parameter('model').value
        self._llava = OllamaAnalyzer(model=model, system_prompt=REVIEW_PROMPT)

        self.create_subscription(String, 'monitor/review_request', self.on_request, 10)

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

        self.get_logger().info(f'二次审核智能体启动：模型={model}，订阅 /monitor/review_request')

    # ---------- 订阅回调 ----------
    def on_request(self, msg: String):
        import json
        try:
            req = json.loads(msg.data)
        except Exception:
            return
        self._q.put(req)
        self.get_logger().info(
            f'[审核] 收到复核请求 告警#{req.get("alert_id")}，队列长度={self._q.qsize()}')

    # ---------- 串行工作线程 ----------
    def _run(self):
        while True:
            req = self._q.get()
            try:
                self._review(req)
            except Exception as e:
                self.get_logger().error(f'[审核] 复核异常: {e}')
            finally:
                self._q.task_done()

    def _review(self, req):
        alert_id = req.get('alert_id')
        path = req.get('screenshot_path', '')
        if not path or not os.path.exists(path):
            self.get_logger().warn(f'[审核] 告警#{alert_id} 无截图，跳过复核')
            return

        # 用 imdecode 读图：兼容含中文的真实路径（OpenCV imread 打不开中文路径）
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            self.get_logger().warn(f'[审核] 告警#{alert_id} 截图读取失败')
            return

        # llava 复核（生产配置约 13~33s，事件驱动可接受）
        self.get_logger().info(f'[审核] 开始 llava 复核 告警#{alert_id} ...')
        obs = self._llava.analyze(img)

        risk = obs.get('possible_risk', '中')
        act = obs.get('activity', '其他')
        loc = obs.get('location', '未知')
        expr = obs.get('expression', '看不清')
        body = obs.get('body_state', '未知')
        conf = float(obs.get('confidence', 0.0))
        summary = (f'活动={act} 位置={loc} 表情={expr} 肢体={body} '
                   f'风险={risk} 置信度={conf:.2f}')

        if risk == '低':
            # 床/沙发正常休息，或已恢复直立 -> 排除误报，不发正式邮件
            verdict = '已排除'
            self.get_logger().info(f'[审核] 告警#{alert_id} -> 已排除（{summary}）')
        else:
            # 高（地面/痛苦）或 中（位置/姿态不明）-> 保守确认，发正式邮件
            verdict = '已确认'
            self._send_formal_email(req, obs)
            self.get_logger().info(f'[审核] 告警#{alert_id} -> 已确认（{summary}），已发正式邮件')

        database.mark_review(alert_id, verdict, summary)

    # ---------- 正式邮件 ----------
    def _send_formal_email(self, req, obs):
        settings = database.get_all_settings()
        if not database.smtp_configured():
            self.get_logger().warn('[审核] SMTP 未配置，跳过正式邮件')
            return
        recipients = database.get_recipient_emails()
        if not recipients:
            self.get_logger().warn('[审核] 无收件人，跳过正式邮件')
            return

        label = {'fall': '跌倒', 'high_risk': '高风险'}.get(req.get('alert_type'), '异常')
        subject = (f'【养老监护·确认{label}】二次复核通过：'
                   f'{obs.get("activity", "异常")}·{obs.get("location", "未知")} '
                   f'（{req.get("risk_level", "高")}风险）')
        body = (
            f"发生时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"初步检测：{req.get('description', '检测到老人异常')}\n"
            f"二次复核：活动={obs.get('activity', '')}  位置={obs.get('location', '')}  "
            f"表情={obs.get('expression', '')}  肢体={obs.get('body_state', '')}  "
            f"风险={obs.get('possible_risk', '')}  置信度={obs.get('confidence', 0.0):.2f}\n"
            f"复核结论：{obs.get('explanation', '')}\n\n"
            "请查看附件照片并尽快确认老人状况。"
        )
        try:
            send_alert_with_screenshot(settings, recipients, subject, body,
                                       req.get('screenshot_path'))
            self.get_logger().info(f'[审核] 正式邮件已发送 -> {", ".join(recipients)}')
        except Exception as e:
            self.get_logger().error(f'[审核] 正式邮件发送失败: {e}')


def main(args=None):
    rclpy.init(args=args)
    node = ReviewAgent()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
