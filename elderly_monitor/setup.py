"""
forward-looking 构建脚本：等环境装好 colcon + CMake 后，执行
    cd E:/ros_vision && colcon build
即可把 elderly_monitor 安装进 ROS2，之后可 ros2 run elderly_monitor risk_manager。
当前（无 colcon）直接按路径 python 运行即可。
"""
from setuptools import find_packages, setup

package_name = 'elderly_monitor'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@example.com',
    description='养老监控预警系统',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'risk_manager = elderly_monitor.nodes.risk_manager:main',
        ],
    },
)
