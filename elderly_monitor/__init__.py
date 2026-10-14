"""
elderly_monitor —— 养老监控预警系统

生产链路（由 supervisor.py 守护，start_all.bat 一键启动）：
  video_publisher -> /camera/image -> vision_analysis -> /vision/analysis
      -> risk_manager -> /monitor/alert -> alert_delivery（轻提醒）
      -> /monitor/review_request -> review_agent（二次审核 / 正式邮件）
  另有 web_ui 提供告警看板（桌面版 + 手机端）。
"""
