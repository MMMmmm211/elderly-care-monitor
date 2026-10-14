"""
【已废弃】本 launch 文件为开发阶段产物，未同步 review_agent 与生产参数。
生产环境请使用 start_all.bat / supervisor.py（一键启动 5 个 ROS2 节点 + Web 界面）。
如需 ros2 launch，请先更新节点列表与参数后再使用。

用法（示例）：
  ros2 launch E:/ros_vision/elderly_monitor/launch/monitor.launch.py
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration

# launch/ 的上两级 = 项目根目录 E:\ros_vision
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODES_DIR = os.path.join(PROJECT_ROOT, 'elderly_monitor', 'nodes')


def generate_launch_description():
    source_arg = LaunchConfiguration('source')
    model_arg = LaunchConfiguration('model')
    fall_arg = LaunchConfiguration('fall_sec')
    watch_arg = LaunchConfiguration('watch_sec')
    recover_arg = LaunchConfiguration('recover_sec')
    cooldown_arg = LaunchConfiguration('cooldown_sec')
    return LaunchDescription([
        DeclareLaunchArgument('source', default_value='test_room.jpg',
                              description='视频源：图片路径 / 视频路径 / 0(摄像头)'),
        DeclareLaunchArgument('model', default_value='llava:7b'),
        DeclareLaunchArgument('fall_sec', default_value='5.0',
                              description='连续躺下多少秒触发跌倒告警（短循环视频建议 1.0）'),
        DeclareLaunchArgument('watch_sec', default_value='10.0'),
        DeclareLaunchArgument('recover_sec', default_value='30.0'),
        DeclareLaunchArgument('cooldown_sec', default_value='300.0',
                              description='同类告警冷却秒数（测试建议 30.0）'),
        # 1. 视频发布节点（source 可传 test_videos/05.mp4 等）
        ExecuteProcess(
            cmd=['python', os.path.join(PROJECT_ROOT, 'video_publisher.py'),
                 '--ros-args', '-p', ['source:=', "'", source_arg, "'"]],
            cwd=PROJECT_ROOT,
            output='screen',
        ),
        # 2. 视觉分析节点（llava:7b）
        ExecuteProcess(
            cmd=['python', os.path.join(PROJECT_ROOT, 'vision_analysis_node.py'),
                 '--ros-args', '-p', ['model:=', model_arg]],
            cwd=PROJECT_ROOT,
            output='screen',
        ),
        # 3. 风险状态机节点
        ExecuteProcess(
            cmd=['python', os.path.join(NODES_DIR, 'risk_manager.py'),
                 '--ros-args',
                 '-p', ['fall_sec:=', fall_arg],
                 '-p', ['watch_sec:=', watch_arg],
                 '-p', ['recover_sec:=', recover_arg],
                 '-p', ['cooldown_sec:=', cooldown_arg]],
            cwd=PROJECT_ROOT,
            output='screen',
        ),
        # 4. 告警投递节点（截图 + 邮件推送）
        ExecuteProcess(
            cmd=['python', os.path.join(PROJECT_ROOT, 'alert_delivery_node.py')],
            cwd=PROJECT_ROOT,
            output='screen',
        ),
    ])
