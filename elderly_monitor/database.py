"""
SQLite 数据层（阶段 D）：用户/联系人、SMTP 配置、告警历史。

同时被两个进程访问：
  - web_ui.py（Flask 界面，读写）
  - alert_delivery_node.py（ROS2 节点，读配置 + 写告警记录）

因此这里做了并发友好设计：
  - 每次操作独立建立连接（不跨线程共享连接）
  - WAL 模式，允许「一写多读」
  - 写操作带短事务，写完即提交
"""
import os
import sqlite3
import threading

# 数据库文件：E:\ros_vision\data\monitor.db
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(_ROOT, 'data')
DB_PATH = os.path.join(DB_DIR, 'monitor.db')

# 默认 SMTP 配置（无密钥，需用户到设置页填写）
DEFAULT_SETTINGS = {
    'smtp_host': 'smtp.qq.com',
    'smtp_port': '465',
    'smtp_user': '',           # 发件邮箱（如 xxx@qq.com）
    'smtp_auth_code': '',      # 授权码 / 应用专用密码（非登录密码）
    'smtp_ssl': 'true',
    'sender_name': '养老监护预警系统',
    'recipient_emails': '',    # 手动指定收件人（逗号分隔）；留空则发给所有已注册用户邮箱
    'camera_enabled': 'true',  # 摄像头开关（手机端可切换，视频发布节点实时读取）
}

_write_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def init_db() -> None:
    with _write_lock:
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT DEFAULT '',
                    gender TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT (datetime('now','localtime')),
                    alert_type TEXT,
                    risk_level TEXT,
                    description TEXT,
                    activity TEXT,
                    location TEXT,
                    expression TEXT,
                    body_state TEXT,
                    screenshot_path TEXT,
                    email_status TEXT,
                    email_attempts INTEGER DEFAULT 0,
                    review_status TEXT DEFAULT '待复核',
                    review_result TEXT DEFAULT ''
                );
                """
            )
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
            # 老库迁移：users 表若无 gender 列则补齐
            try:
                conn.execute("ALTER TABLE users ADD COLUMN gender TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            # 老库迁移：alerts 表若无 email_attempts 列则补齐
            try:
                conn.execute("ALTER TABLE alerts ADD COLUMN email_attempts INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # 老库迁移：alerts 表若无二次审核字段则补齐
            try:
                conn.execute("ALTER TABLE alerts ADD COLUMN review_status TEXT DEFAULT '待复核'")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE alerts ADD COLUMN review_result TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()


# ============ settings ============
def get_setting(key: str, default: str = '') -> str:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default
    finally:
        conn.close()


def set_setting(key: str, value) -> None:
    with _write_lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
            conn.commit()
        finally:
            conn.close()


def get_all_settings() -> dict:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        conn.close()


def smtp_configured() -> bool:
    s = get_all_settings()
    return bool(s.get('smtp_host') and s.get('smtp_user') and s.get('smtp_auth_code'))


def get_recipient_emails() -> list:
    """告警收件人：优先用手动配置，否则用所有已注册用户的邮箱。"""
    manual = get_setting('recipient_emails', '').strip()
    if manual:
        return [e.strip() for e in manual.split(',') if e.strip()]
    conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT email FROM users WHERE email != ''").fetchall()
        return [r['email'] for r in rows]
    finally:
        conn.close()


def get_camera_enabled() -> bool:
    """摄像头开关（true=开启监控，false=暂停监控）"""
    return get_setting('camera_enabled', 'true') in ('true', '1', 'on', 'True', 'yes')


def set_camera_enabled(enabled: bool) -> None:
    set_setting('camera_enabled', 'true' if enabled else 'false')


# ============ users ============
def add_user(username: str, password_hash: str, email: str, phone: str = '',
             gender: str = '') -> bool:
    with _write_lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO users(username, password_hash, email, phone, gender) VALUES(?,?,?,?,?)",
                (username, password_hash, email, phone, gender))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()


def get_user_by_username(username: str):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    finally:
        conn.close()


def list_users() -> list:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============ alerts ============
def add_alert(alert_type: str, risk_level: str, description: str,
              activity: str = '', location: str = '', expression: str = '',
              body_state: str = '', screenshot_path: str = '', email_status: str = '') -> int:
    with _write_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO alerts(alert_type, risk_level, description, activity, location, "
                "expression, body_state, screenshot_path, email_status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (alert_type, risk_level, description, activity, location,
                 expression, body_state, screenshot_path, email_status))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def list_alerts(limit: int = 50) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_alert(alert_id: int):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    finally:
        conn.close()


def mark_email_status(alert_id: int, email_status: str, attempts: int) -> None:
    with _write_lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE alerts SET email_status=?, email_attempts=? WHERE id=?",
                (email_status, attempts, alert_id))
            conn.commit()
        finally:
            conn.close()


def mark_review(alert_id: int, review_status: str, review_result: str) -> None:
    """记录二次审核结果（已确认 / 已排除 / 待复核）。"""
    with _write_lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE alerts SET review_status=?, review_result=? WHERE id=?",
                (review_status, review_result, alert_id))
            conn.commit()
        finally:
            conn.close()


def list_pending_emails(max_attempts: int = 3) -> list:
    """待重试的告警：邮件发送失败且未超过最大重试次数，且有截图。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE email_status LIKE '发送失败%' "
            "AND email_attempts < ? AND screenshot_path != '' ORDER BY id",
            (max_attempts,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
