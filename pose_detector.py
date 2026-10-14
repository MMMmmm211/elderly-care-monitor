"""
姿态检测器（阶段 E · 误报率调优第 2 条路）

背景：
llava:7b 对「躺下/站立」这类姿态判断的召回上限约 59%（分辨率实验与 prompt 实验已证明
不是 prompt/阈值能突破的）。根本原因是 7B 视觉模型对空间几何的感知不可靠。

本模块用 MediaPipe PoseLandmarker 做【确定性几何判断】，取代 llava 对「姿态/是否有人」的
分类。关键点几何是确定性的，且可解释、可调阈值。

核心改进：用【3D 世界坐标关键点】判断姿态，而非 2D 图像关键点。
原因：2D 图像里「人朝向/背离镜头躺下」时，躯干在画面上仍是竖直的，2D 夹角会误判成直立；
3D 世界坐标直接给出真实空间位置，竖直占比 = |肩y-髋y|/躯干3D长 与拍摄角度无关。

设计原则（与既有架构一致）：
- 感知与决策分离：这里只输出「看到什么」（是否有人、姿态、关键特征），
  风险等级仍由 risk_manager 的确定性规则推导。
- 无状态：每帧独立判断；时间序列（跌落特征）放到上层节点做，保持单一职责。
- 懒加载 mediapipe：本模块可被任意进程 import，只在真正创建 PoseDetector 时才加载模型。
"""
import math
import os
from dataclasses import dataclass

import cv2
import numpy as np


# MediaPipe Pose 关键点索引
K = {
    "nose": 0,
    "L_ear": 7, "R_ear": 8,
    "L_shoulder": 11, "R_shoulder": 12,
    "L_elbow": 13, "R_elbow": 14,
    "L_wrist": 15, "R_wrist": 16,
    "L_hip": 23, "R_hip": 24,
    "L_knee": 25, "R_knee": 26,
    "L_ankle": 27, "R_ankle": 28,
}

# 关键点（用于判定是否有人）：肩/髋/膝/踝，只要这些点可见度足够就认为有人
_KEY_IDS = [11, 12, 23, 24, 25, 26, 27, 28]


@dataclass
class PoseResult:
    person_present: bool
    activity: str                 # 躺下/站立/坐着/其他/无人
    confidence: float
    vertical_fraction: float      # 3D 躯干竖直占比：1=竖直(站/坐)，0=水平(躺)
    torso_angle: float            # 2D 躯干与竖直方向夹角(度)，0=竖直，90=水平（仅兜底/展示）
    shoulder_y: float             # 2D 肩部归一化 y（越大越靠画面下方）
    hip_y: float                  # 2D 髋部归一化 y
    ankle_y: float                # 2D 踝部归一化 y
    body_bottom: float            # 2D 身体最低点归一化 y
    aspect_ratio: float           # 2D 人体框 高/宽
    visible_ratio: float          # 关键点可见度均值
    explanation: str = ""


def _mid2d(lm, a, b):
    la, lb = lm[a], lm[b]
    return ((la.x + lb.x) / 2.0, (la.y + lb.y) / 2.0)


def _mid3d(wl, a, b):
    la, lb = wl[a], wl[b]
    return ((la.x + lb.x) / 2.0, (la.y + lb.y) / 2.0, (la.z + lb.z) / 2.0)


class PoseDetector:
    def __init__(
        self,
        model_path: str = None,
        min_visibility: float = 0.15,
        min_detection_conf: float = 0.5,
        # 姿态判定阈值（3D 世界坐标为主，可调）
        lie_vfrac: float = 0.45,     # 3D 竖直占比 < 此值判「躺下」（阈值扫描最优：95%召回/0误报）
        lean_vfrac: float = 0.65,    # 3D 竖直占比在此与 lie_vfrac 之间判「其他(斜靠)」
        stand_leg_ratio: float = 1.0,  # 直立时 髋->踝 3D 竖直跨度 / 躯干3D长 > 此值判「站立」
        squat_leg_ratio: float = 0.35,  # 髋->踝 3D 竖直跨度 / 躯干3D长 < 此值判「蹲着」（髋贴地折叠）
        # 2D 兜底阈值（无 3D 关键点时使用）
        lie_angle: float = 55.0,
        lean_angle: float = 30.0,
    ):
        # 注意：必须用 ASCII 路径 E:\ros_vision（junction），
        # 因为 mediapipe 的 C++ 层无法打开含中文的路径（E:\ai项目\... 会 GBK 失败）。
        if model_path:
            self.model_path = model_path
        else:
            candidates = [
                r"E:\ros_vision\models\pose_landmarker_lite.task",
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "pose_landmarker_lite.task"),
            ]
            self.model_path = next((p for p in candidates if os.path.exists(p)), candidates[0])
        self.min_visibility = min_visibility
        self.lie_vfrac = lie_vfrac
        self.lean_vfrac = lean_vfrac
        self.stand_leg_ratio = stand_leg_ratio
        self.squat_leg_ratio = squat_leg_ratio
        self.lie_angle = lie_angle
        self.lean_angle = lean_angle

        # 懒加载：创建对象时才 import mediapipe
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"姿态模型不存在: {self.model_path}")

        options = vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=min_detection_conf,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_segmentation_masks=False,
        )
        self._mp = mp
        self._landmarker = vision.PoseLandmarker.create_from_options(options)

    def _empty(self, explanation="未检测到人体关键点") -> PoseResult:
        return PoseResult(
            person_present=False, activity="无人", confidence=0.0,
            vertical_fraction=0.0, torso_angle=0.0, shoulder_y=0.0,
            hip_y=0.0, ankle_y=0.0, body_bottom=0.0, aspect_ratio=0.0,
            visible_ratio=0.0, explanation=explanation,
        )

    def detect(self, img_bgr) -> PoseResult:
        """输入 BGR 图，输出结构化姿态结果。"""
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        res = self._landmarker.detect(mp_img)

        if not res.pose_landmarks:
            return self._empty()

        lm = res.pose_landmarks[0]

        # 关键点可见度
        vis = [min(1.0, lm[i].visibility) for i in _KEY_IDS]
        visible_ratio = float(np.mean(vis)) if vis else 0.0

        # 2D 特征
        sx, sy = _mid2d(lm, K["L_shoulder"], K["R_shoulder"])
        hx, hy = _mid2d(lm, K["L_hip"], K["R_hip"])
        ax, ay = _mid2d(lm, K["L_ankle"], K["R_ankle"])

        dx, dy = hx - sx, hy - sy
        torso_len_2d = math.hypot(dx, dy)
        torso_angle = math.degrees(math.atan2(abs(dx), abs(dy))) if torso_len_2d > 1e-3 else 0.0

        xs = [lm[i].x for i in _KEY_IDS]
        ys = [lm[i].y for i in _KEY_IDS]
        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)
        aspect_ratio = bbox_h / bbox_w if bbox_w > 1e-3 else 0.0
        body_bottom = max(ys)

        person_present = visible_ratio >= self.min_visibility

        # ===== 3D 世界坐标（主信号）=====
        vertical_fraction = None
        if res.pose_world_landmarks:
            wl = res.pose_world_landmarks[0]
            wsx, wsy, wsz = _mid3d(wl, K["L_shoulder"], K["R_shoulder"])
            whx, why, whz = _mid3d(wl, K["L_hip"], K["R_hip"])
            wax, way, waz = _mid3d(wl, K["L_ankle"], K["R_ankle"])
            wkx, wky, wkz = _mid3d(wl, K["L_knee"], K["R_knee"])

            torso_len_3d = math.dist((wsx, wsy, wsz), (whx, why, whz))
            if torso_len_3d > 1e-3:
                vertical_fraction = abs(wsy - why) / torso_len_3d
                leg_span_3d = abs(way - why)  # 髋->踝竖直跨度（世界坐标 y 向下为正）
                leg_ratio_3d = leg_span_3d / torso_len_3d

        # 姿态判定
        if not person_present:
            activity, expl = "无人", "关键点可见度过低"
        elif vertical_fraction is not None:
            if vertical_fraction < self.lie_vfrac:
                activity = "躺下"
                expl = f"躯干3D接近水平(竖直占比{vertical_fraction:.2f})，躺卧"
            elif vertical_fraction < self.lean_vfrac:
                activity = "其他"
                expl = f"躯干3D倾斜(竖直占比{vertical_fraction:.2f})，斜靠/过渡"
            else:
                if leg_ratio_3d > self.stand_leg_ratio:
                    activity = "站立"
                    expl = f"躯干3D竖直(占比{vertical_fraction:.2f})，腿伸直"
                elif leg_ratio_3d < self.squat_leg_ratio:
                    activity = "蹲着"
                    expl = f"躯干3D竖直但髋贴地(髋踝比{leg_ratio_3d:.2f})，蹲姿"
                else:
                    activity = "坐着"
                    expl = f"躯干3D竖直(占比{vertical_fraction:.2f})，腿弯曲"
        else:
            # 2D 兜底
            if torso_angle > self.lie_angle:
                activity = "躺下"
                expl = f"躯干2D接近水平({torso_angle:.0f}°)"
            elif torso_angle > self.lean_angle:
                activity = "其他"
                expl = f"躯干2D倾斜({torso_angle:.0f}°)"
            else:
                leg_span = abs(ay - hy)
                leg_ratio = leg_span / torso_len_2d if torso_len_2d > 1e-3 else 0.0
                activity = "站立" if leg_ratio > 0.85 else "坐着"
                expl = f"躯干2D竖直({torso_angle:.0f}°)"

        conf = float(np.clip(visible_ratio, 0.0, 1.0))
        return PoseResult(
            person_present=person_present, activity=activity, confidence=conf,
            vertical_fraction=(vertical_fraction if vertical_fraction is not None else 0.0),
            torso_angle=torso_angle, shoulder_y=sy, hip_y=hy, ankle_y=ay,
            body_bottom=body_bottom, aspect_ratio=aspect_ratio,
            visible_ratio=visible_ratio, explanation=expl,
        )
