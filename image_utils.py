"""
轻量级 sensor_msgs/Image <-> numpy 转换
（零依赖替代 cv_bridge，避免 Windows 上 cv_bridge 的编译安装坑）
"""
import numpy as np
from array import array
from sensor_msgs.msg import Image


def imgmsg_to_numpy(msg: Image) -> np.ndarray:
    """
    sensor_msgs/Image -> numpy 图像（统一转成 BGR，供 OpenCV 使用）
    支持: mono8 / rgb8 / bgr8
    """
    data = np.frombuffer(msg.data, dtype=np.uint8)

    if msg.encoding == 'mono8':
        img = data.reshape(msg.height, msg.width)
    elif msg.encoding in ('rgb8', 'bgr8'):
        img = data.reshape(msg.height, msg.width, 3)
        if msg.encoding == 'rgb8':
            img = img[:, :, ::-1].copy()  # RGB -> BGR
    else:
        raise ValueError(f'不支持的图像编码: {msg.encoding}')
    return img


def numpy_to_imgmsg(img: np.ndarray, encoding: str = 'bgr8') -> Image:
    """
    numpy 图像 -> sensor_msgs/Image
    灰度图用 mono8，彩色图默认 bgr8
    """
    msg = Image()
    msg.encoding = encoding

    if img.ndim == 2:
        msg.height, msg.width = img.shape
        msg.step = msg.width
    else:
        msg.height, msg.width = img.shape[:2]
        msg.step = msg.width * img.shape[2]

    # rclpy 的 data 字段是 array('B') 类型
    msg.data = array('B', img.tobytes())
    return msg
