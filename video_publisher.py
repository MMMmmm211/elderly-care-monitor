"""
视频发布节点：采集图像 -> 发布 sensor_msgs/Image 到 /camera/image

source 参数：
  0                          -> 默认摄像头
  "xxx.jpg/png/bmp"          -> 循环发布这张图片（模拟静态监控，方便测试）
  "xxx.mp4/avi"              -> 循环播放视频文件
"""
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import cv2

from image_utils import numpy_to_imgmsg
from elderly_monitor import database


class VideoPublisher(Node):
    def __init__(self):
        super().__init__('video_publisher')

        # source: '0'=默认摄像头；图片路径(.jpg/.png等)=图片循环；视频路径=视频循环
        self.declare_parameter('source', '0')
        self.declare_parameter('fps', 5.0)
        self.declare_parameter('max_side', 640)   # 发布前降采样，减小消息体积

        source = self.get_parameter('source').value
        fps = float(self.get_parameter('fps').value)
        self.max_side = int(self.get_parameter('max_side').value)

        self.publisher = self.create_publisher(Image, 'camera/image', 10)

        # 判断 source 类型
        if source.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
            self._static_img = cv2.imread(source)
            if self._static_img is None:
                raise RuntimeError(f'无法读取图片: {source}')
            self._mode = 'image'
            self.get_logger().info(f'图片循环模式: {source}')
        else:
            cap_source = 0 if source == '0' else source
            self.cap = cv2.VideoCapture(cap_source)
            if not self.cap.isOpened():
                raise RuntimeError(f'无法打开视频源: {source}')
            self._mode = 'video'
            self.get_logger().info(f'视频流模式: source={source}')

        self.get_logger().info(f'视频发布节点启动，发布到 /camera/image，fps={fps}')
        self.timer = self.create_timer(1.0 / fps, self.publish_frame)

        # 摄像头开关：由 Web/手机端写入数据库，这里以 1s TTL 缓存读取
        self._cam_flag_checked = 0.0
        self._cam_enabled = True
        self._cam_last_state = True

    def _camera_enabled(self) -> bool:
        import time
        now = time.time()
        if now - self._cam_flag_checked > 1.0:
            self._cam_flag_checked = now
            try:
                self._cam_enabled = database.get_camera_enabled()
            except Exception:
                self._cam_enabled = True
        return self._cam_enabled

    def publish_frame(self):
        # 摄像头被关闭：暂停发布（监控暂停），切换时打日志
        if not self._camera_enabled():
            if self._cam_last_state:
                self.get_logger().info('摄像头已关闭（暂停监控）')
                self._cam_last_state = False
            return
        if not self._cam_last_state:
            self.get_logger().info('摄像头已开启（恢复监控）')
            self._cam_last_state = True

        if self._mode == 'image':
            frame = self._static_img.copy()
        else:
            ret, frame = self.cap.read()
            if not ret:
                # 视频播完循环播放
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self.cap.read()
                if not ret:
                    return

        # 降采样到 max_side，减小 DDS 消息体积（大图会分片导致丢包）
        h, w = frame.shape[:2]
        scale = self.max_side / max(h, w)
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_AREA)

        msg = numpy_to_imgmsg(frame, encoding='bgr8')
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'camera'
        self.publisher.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = VideoPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node._mode == 'video':
            node.cap.release()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
