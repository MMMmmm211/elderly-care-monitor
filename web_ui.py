# -*- coding: utf-8 -*-
"""
养老监护 Web 界面（阶段 D）：登录 / 注册（含联系人邮箱） / 告警看板 / 邮件配置。

运行（普通 Python 即可，无需 ROS2）：
  python web_ui.py --port 5000
浏览器打开 http://127.0.0.1:5000

注册时填写的邮箱即「告警联系人」；告警投递节点会读取这些邮箱作为收件人。
SMTP 发件配置在「邮件设置」页填写（发件邮箱 + 授权码），保存后立即生效。
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_from_directory, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

from elderly_monitor import database
from elderly_monitor.emailer import send_alert_with_screenshot

ALERT_DIR = os.path.join(_here, 'alerts')
TEMPLATE_DIR = os.path.join(_here, 'elderly_monitor', 'templates')

app = Flask(__name__, template_folder=TEMPLATE_DIR)

# 持久化会话密钥：首次运行生成，之后复用（重启不清登录态）
_secret_file = os.path.join(database.DB_DIR, 'secret_key')


def _load_secret() -> str:
    os.makedirs(database.DB_DIR, exist_ok=True)
    if os.path.exists(_secret_file):
        with open(_secret_file, 'r', encoding='utf-8') as f:
            s = f.read().strip()
            if s:
                return s
    s = os.urandom(24).hex()
    with open(_secret_file, 'w', encoding='utf-8') as f:
        f.write(s)
    return s


app.secret_key = _load_secret()


# ============ 鉴权 ============
def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'uid' not in session:
            flash('请先登录', 'warn')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapper


# ============ 页面 ============
@app.route('/')
def index():
    if 'uid' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        gender = request.form.get('gender', '').strip()

        if not username or not password or not email:
            flash('用户名、密码、邮箱均为必填', 'error')
            return render_template('register.html')

        if '@' not in email or '.' not in email:
            flash('邮箱格式不正确', 'error')
            return render_template('register.html')

        if database.get_user_by_username(username):
            flash('用户名已存在', 'error')
            return render_template('register.html')

        database.add_user(username, generate_password_hash(password), email, phone, gender)
        flash('注册成功，请登录', 'ok')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = database.get_user_by_username(username)
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['uid'] = user['id']
            session['username'] = user['username']
            flash(f'欢迎回来，{user["username"]}', 'ok')
            return redirect(url_for('dashboard'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('已退出登录', 'ok')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    alerts = database.list_alerts(limit=100)
    users = database.list_users()
    smtp_ok = database.smtp_configured()
    return render_template('dashboard.html',
                           alerts=alerts, users=users, smtp_ok=smtp_ok)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        keys = ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_auth_code',
                'sender_name', 'recipient_emails']
        for k in keys:
            database.set_setting(k, request.form.get(k, '').strip())
        database.set_setting('smtp_ssl', 'true' if request.form.get('smtp_ssl') else 'false')
        flash('邮件设置已保存', 'ok')
        return redirect(url_for('settings'))

    cfg = database.get_all_settings()
    return render_template('settings.html', cfg=cfg)


@app.route('/settings/test', methods=['POST'])
@login_required
def settings_test():
    cfg = database.get_all_settings()
    if not database.smtp_configured():
        flash('请先填写 SMTP 服务器、发件邮箱与授权码', 'error')
        return redirect(url_for('settings'))
    recipients = database.get_recipient_emails()
    if not recipients:
        flash('没有联系人邮箱，请先注册或手动填写收件人', 'error')
        return redirect(url_for('settings'))
    try:
        send_alert_with_screenshot(
            cfg, recipients,
            subject='【养老监护系统】邮件通道测试',
            body_text='这是一封测试邮件。收到说明养老监护告警邮件通道已打通。\n\n养老监护预警系统',
            image_path='',
        )
        flash(f'测试邮件已发送 -> {", ".join(recipients)}', 'ok')
    except Exception as e:
        flash(f'测试邮件发送失败：{e}', 'error')
    return redirect(url_for('settings'))


@app.route('/screenshots/<path:filename>')
@login_required
def screenshots(filename):
    return send_from_directory(ALERT_DIR, filename)


# ============ 手机端 / JSON API ============
@app.route('/mobile')
@login_required
def mobile():
    return render_template('mobile.html')


@app.route('/api/camera', methods=['GET', 'POST'])
@login_required
def api_camera():
    """读取/切换摄像头开关。POST 时写入数据库，视频发布节点实时读取生效。"""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        enabled = data.get('enabled')
        if enabled is None:
            enabled = request.form.get('enabled') == 'true'
        database.set_camera_enabled(bool(enabled))
    return jsonify({'enabled': database.get_camera_enabled()})


@app.route('/api/alerts')
@login_required
def api_alerts():
    """手机端轮询的告警列表 + 摄像头状态。"""
    alerts = database.list_alerts(limit=50)
    out = []
    for a in alerts:
        fname = ''
        if a['screenshot_path']:
            fname = a['screenshot_path'].replace('\\', '/').split('/')[-1]
        out.append({
            'id': a['id'],
            'ts': a['ts'],
            'type': a['alert_type'],
            'risk': a['risk_level'],
            'desc': a['description'],
            'activity': a['activity'],
            'location': a['location'],
            'expression': a['expression'],
            'body_state': a['body_state'],
            'email_status': a['email_status'],
            'review_status': a['review_status'],
            'review_result': a['review_result'],
            'screenshot': fname,
        })
    return jsonify({'alerts': out, 'camera_enabled': database.get_camera_enabled()})


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--host', default='0.0.0.0',
                        help='默认监听所有网卡，方便手机通过局域网访问')
    args = parser.parse_args()

    database.init_db()
    print(f'* 养老监护 Web 界面: http://{args.host}:{args.port}')
    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8)
    except ImportError:
        # 未安装 waitress 时回退到开发服务器
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
