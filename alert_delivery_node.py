# -*- coding: utf-8 -*-
"""
告警投递节点（阶段 D）：真实告警 -> 截图 -> 邮件推送

职责：
  1. 订阅 /camera/image，缓存最新一帧（线程安全）
  2. 订阅 /vision/analysis，缓存最近一次多维观察（作为邮件上下文）
  3. 订阅 /monitor/alert，收到真实告警时：
     a) 取最新帧，叠加中文时间/风险/活动/位置/表情/肢体水印
     b) 存为 PNG 到 alerts/ 目录
     c) 从数据库读取 SMTP 配置 + 联系人邮箱，发送带截图的邮件
     d) 把告警 + 截图路径 + 邮件状态写入数据库（供 Web 界面展示）

设计：SMTP 未配置时不崩溃——照常截图 + 落库，仅标记「未发送」，
      用户到 Web 设置页填好邮箱后，后续告警即自动推送。
"""
import os
import sys
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# 保证可导入同目录 image_utils 与 elderly_monitor 包
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
from sensor_msgs.msg import Image
from std_msgs.msg import String

from image_utils import imgmsg_to_numpy
from elderly_monitor import database
from elderly_monitor.emailer import send_alert_with_screenshot

ALERT_DIR = os.path.join(_here, 'alerts')
FONT_PATH = r'C:\Windows\Fonts\msyh.ttc'   # 微软雅黑，用于中文水印


def draw_alert_overlay(img_bgr, risk_level: str, description: str,
                       activity: str, location: str, expression: str, body_state: str):
    """在截图上叠加半透明顶栏 + 中文告警信息水印（OpenCV 不支持中文，用 Pillow 画）。"""
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
    except Exception:
        return img_bgr  # Pillow 不可用就退回原图

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil = PILImage.fromarray(rgb).convert('RGB')
    draw = ImageDraw.Draw(pil)

    h = pil.height
    band_h = 128
    overlay = PILImage.new('RGBA', (pil.width, band_h), (0, 0, 0, 160))
    pil.paste(overlay, (0, 0), overlay)

    try:
        f_big = ImageFont.truetype(FONT_PATH, 30)
        f_small = ImageFont.truetype(FONT_PATH, 20)
    except Exception:
        f_big = ImageFont.load_default()
        f_small = ImageFont.load_default()

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    draw.text((16, 10), f'[{risk_level}] {description}', font=f_big, fill=(255, 80, 80))
    draw.text((16, 52), f'{ts}', font=f_small, fill=(255, 255, 255))
    draw.text((16, 82),
              f'活动={activity}  位置={location}  表情={expression}  肢体={body_state}',
              font=f_small, fill=(255, 255, 255))

    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


class AlertDeliveryNode(Node):
    def __init__(self):
        super().__init__('alert_delivery_node')
        self.declare_parameter('min_email_interval', 60.0)  # 同类型告警邮件最小间隔秒数

        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_analysis = {}

        self.create_subscription(Image, 'camera/image', self.cam_cb, 10)
        self.create_subscription(String, 'vision/analysis', self.analysis_cb, 10)
        self.create_subscription(String, 'monitor/alert', self.alert_cb, 10)

        # 二次审核请求：轻提醒发出后，交给 review_agent 用 llava 复核
        self.review_pub = self.create_publisher(String, 'monitor/review_request', 10)

        database.init_db()
        os.makedirs(ALERT_DIR, exist_ok=True)

        self.min_email_interval = float(self.get_parameter('min_email_interval').value)
        self._last_email_at = {}   # alert_type -> 上次发信时间

        # 邮件后台发送线程池：SMTP 是阻塞 IO，放后台避免卡住订阅回调
        self._email_pool = ThreadPoolExecutor(max_workers=2)

        # 邮件失败重试（自愈）：每 60s 重试一次未发送成功的告警
        self.create_timer(60.0, self._retry_pending_emails)

        self.get_logger().info(f'告警投递节点启动，截图目录={ALERT_DIR}')

    # ---------- 订阅回调 ----------
    def cam_cb(self, msg: Image):
        img = imgmsg_to_numpy(msg)
        with self._lock:
            self._latest_frame = img

    def analysis_cb(self, msg: String):
        import json
        try:
            self._latest_analysis = json.loads(msg.data)
        except Exception:
            self._latest_analysis = {}

    def alert_cb(self, msg: String):
        import json
        try:
            evt = json.loads(msg.data)
        except Exception:
            evt = {'raw': msg.data}

        alert_type = evt.get('alert_type', 'unknown')
        # 只有真正的告警（非 state_change）才走投递
        if alert_type == 'state_change':
            return

        risk = evt.get('risk_level', '中')
        desc = evt.get('description', '检测到老人异常')

        # 邮件发送节流：同类型告警间隔太短只截图落库、不重复发信
        now = time.time()
        last = self._last_email_at.get(alert_type)
        throttle_email = (last is not None and now - last < self.min_email_interval)

        # 1) 截图
        with self._lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()

        screenshot_path = ''
        if frame is not None:
            a = self._latest_analysis
            annotated = draw_alert_overlay(
                frame, risk, desc,
                a.get('activity', ''), a.get('location', ''),
                a.get('expression', ''), a.get('body_state', ''))
            fname = f'alert_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            screenshot_path = os.path.join(ALERT_DIR, fname)
            # 用 imencode 写图：兼容含中文的真实路径（OpenCV imwrite 打不开中文路径）
            cv2.imencode('.png', annotated)[1].tofile(screenshot_path)
            self.get_logger().info(f'[投递] 已截图 {screenshot_path}')
        else:
            self.get_logger().warn('[投递] 尚无相机帧，跳过截图')

        # 2) 落库（邮件状态先占位，后台发送完成后回填）
        a = self._latest_analysis
        if throttle_email:
            email_status = '节流未发送'
        elif not screenshot_path:
            email_status = '无截图未发送'
        else:
            email_status = '发送中'
        alert_id = database.add_alert(
            alert_type=alert_type,
            risk_level=risk,
            description=desc,
            activity=a.get('activity', ''),
            location=a.get('location', ''),
            expression=a.get('expression', ''),
            body_state=a.get('body_state', ''),
            screenshot_path=screenshot_path,
            email_status=email_status,
        )

        # 3) 邮件后台发送（SMTP 阻塞 IO 不卡回调）
        if not throttle_email and screenshot_path:
            self._last_email_at[alert_type] = now
            self._email_pool.submit(self._send_email_bg, alert_id, evt, screenshot_path, a)

        # 4) 发起二次审核（仅跌倒/高风险需要 llava 复核；无人告警由轻提醒直接兜底）
        if alert_type in ('fall', 'high_risk') and screenshot_path:
            self._publish_review_request(alert_id, screenshot_path, evt, a, risk)

    # 告警类型 -> 中文标签（用于邮件主题）
    _TYPE_LABEL = {'fall': '跌倒', 'high_risk': '高风险', 'absent': '无人',
                   'squat': '久蹲', 'night_absent': '夜间无人'}

    @classmethod
    def _subject(cls, evt: dict) -> str:
        """轻提醒主题：标明「疑似」+ 结论，收件箱/通知栏一眼看到。"""
        label = cls._TYPE_LABEL.get(evt.get('alert_type', ''), '异常')
        risk = evt.get('risk_level', '高')
        desc = evt.get('description', '检测到老人异常')
        return f'【养老监护·疑似{label}】{desc}（{risk}风险，请尽快查看）'

    @staticmethod
    def _build_body(evt: dict, a: dict = None) -> str:
        """轻提醒正文：附照片 + 智能分析总结，供家属自行判断是否立即处理。"""
        a = a or {}
        summary = (f"活动={a.get('activity', '')}  位置={a.get('location', '')}  "
                   f"表情={a.get('expression', '')}  肢体={a.get('body_state', '')}")
        return (
            f"发生时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"初步检测：{evt.get('description', '检测到老人异常')}\n"
            f"智能分析：{summary}\n\n"
            "请查看附件照片，自行判断是否需要立即拨打急救电话。\n"
            "（系统正在进行二次智能复核，确认后将会再次提醒。）"
        )

    def _publish_review_request(self, alert_id, screenshot_path, evt, a, risk):
        """把告警打包成二次审核请求，交给 review_agent 节点。"""
        import json
        req = {
            'alert_id': alert_id,
            'screenshot_path': screenshot_path,
            'alert_type': evt.get('alert_type', ''),
            'risk_level': risk,
            'description': evt.get('description', ''),
            'activity': a.get('activity', ''),
            'location': a.get('location', ''),
            'expression': a.get('expression', ''),
            'body_state': a.get('body_state', ''),
        }
        msg = String()
        msg.data = json.dumps(req, ensure_ascii=False)
        self.review_pub.publish(msg)
        self.get_logger().info(f'[投递] 已发起二次审核 告警#{alert_id}')

    def _send_email_bg(self, alert_id, evt, screenshot_path, a):
        """后台发送邮件并回填数据库；失败则交给定时重试队列兜底。"""
        try:
            status = self._send_email(evt, screenshot_path, a)
        except Exception as e:
            status = f'发送失败: {e}'
        attempts = 1 if status.startswith('发送失败') else 0
        database.mark_email_status(alert_id, status, attempts)
        self.get_logger().info(f'[投递] 告警#{alert_id} 邮件后台发送完成: {status}')

    def _send_email(self, evt: dict, screenshot_path: str, a: dict = None) -> str:
        settings = database.get_all_settings()
        if not database.smtp_configured():
            self.get_logger().warn('[投递] SMTP 未配置（请在 Web 设置页填写发件邮箱/授权码），仅截图落库')
            return '未配置SMTP未发送'

        recipients = database.get_recipient_emails()
        if not recipients:
            self.get_logger().warn('[投递] 没有联系人邮箱（请注册或手动配置收件人）')
            return '无收件人未发送'

        subject = self._subject(evt)
        body = self._build_body(evt, a if a is not None else self._latest_analysis)
        try:
            send_alert_with_screenshot(settings, recipients, subject, body, screenshot_path)
            self.get_logger().info(f'[投递] 邮件已发送 -> {recipients}')
            return f'已发送 -> {", ".join(recipients)}'
        except Exception as e:
            self.get_logger().error(f'[投递] 邮件发送失败: {e}')
            return f'发送失败: {e}'

    # ---------- 邮件失败重试（自愈） ----------
    def _retry_pending_emails(self):
        try:
            pending = database.list_pending_emails(max_attempts=3)
        except Exception:
            return
        for a in pending:
            self._retry_one(a)

    def _retry_one(self, a: dict):
        settings = database.get_all_settings()
        if not database.smtp_configured() or not database.get_recipient_emails():
            return
        evt = {'alert_type': a['alert_type'], 'risk_level': a['risk_level'],
               'description': a['description']}
        subject = self._subject(evt)
        body = self._build_body(evt, a)
        try:
            send_alert_with_screenshot(settings, database.get_recipient_emails(),
                                       subject, body, a['screenshot_path'])
            database.mark_email_status(a['id'],
                                       f'已发送(重试) -> {", ".join(database.get_recipient_emails())}',
                                       a['email_attempts'] + 1)
            self.get_logger().info(f'[投递] 重试成功 -> 告警#{a["id"]}')
        except Exception as e:
            database.mark_email_status(a['id'], f'发送失败: {e}', a['email_attempts'] + 1)
            self.get_logger().warn(f'[投递] 重试失败 告警#{a["id"]}: {e}')


def main(args=None):
    rclpy.init(args=args)
    node = AlertDeliveryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
