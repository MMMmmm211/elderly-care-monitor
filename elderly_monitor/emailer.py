"""
SMTP 邮件发送封装（阶段 D）：把告警 + 实时截图发给联系人。

支持两种加密方式（常见邮箱均可）：
  - SSL：端口 465（QQ 邮箱 / 163 邮箱默认）
  - STARTTLS：端口 587（Gmail 等）
发件需「授权码 / 应用专用密码」，不是邮箱登录密码。
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr


def build_alert_message(sender: str, sender_name: str, recipients, subject: str,
                        body_text: str, image_path: str) -> EmailMessage:
    msg = EmailMessage()
    msg['From'] = formataddr((sender_name, sender))
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject
    msg.set_content(body_text)

    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            img_data = f.read()
        msg.add_attachment(
            img_data, maintype='image', subtype='png',
            filename=os.path.basename(image_path))
    return msg


def send_email(smtp_host: str, smtp_port: int, smtp_user: str, smtp_auth_code: str,
               smtp_ssl: bool, sender_name: str, recipients, subject: str,
               body_text: str, image_path: str) -> None:
    """发送一封邮件；失败时抛异常，由调用方决定如何记录。"""
    if not recipients:
        raise ValueError('没有可用的收件人邮箱')

    msg = build_alert_message(smtp_user, sender_name, recipients, subject, body_text, image_path)

    if smtp_ssl:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as server:
            server.login(smtp_user, smtp_auth_code)
            server.send_message(msg)
    else:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(smtp_user, smtp_auth_code)
            server.send_message(msg)


def send_alert_with_screenshot(settings: dict, recipients, subject: str,
                               body_text: str, image_path: str) -> None:
    """从 settings dict 读取 SMTP 配置并发送告警邮件。"""
    send_email(
        smtp_host=settings['smtp_host'],
        smtp_port=int(settings.get('smtp_port', '465')),
        smtp_user=settings['smtp_user'],
        smtp_auth_code=settings['smtp_auth_code'],
        smtp_ssl=settings.get('smtp_ssl', 'true') in ('true', '1', 'True', 'yes', 'on'),
        sender_name=settings.get('sender_name', '养老监护预警系统'),
        recipients=recipients,
        subject=subject,
        body_text=body_text,
        image_path=image_path,
    )
