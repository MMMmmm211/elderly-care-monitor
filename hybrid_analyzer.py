"""
混合分析器（阶段 E · 误报率调优第 2 条路的落地）

职责划分（感知与决策分离，与既有架构一致）：
- 姿态 / 是否有人  -> PoseDetector（确定性几何，召回高、可解释）
- 位置 / 表情 / 肢体 -> llava（慢，只做场景与面部子任务，不再让它判断姿态）
- 跌落时间特征     -> FallTemporalDetector（短窗内 直立→躺下 + 髋部下坠）
- 风险等级         -> 由上述确定性规则推导（本模块），供 risk_manager 状态机去抖

为什么这样拆：
llava:7b 对「躺下/站立」空间几何召回上限约 59%（实验已证），而 MediaPipe 姿态
关键点几何是确定性的，躺下召回可达 ~95%。但 llava 对「表情痛苦/挣扎」这类语义仍有用，
且能兜底姿态漏检（如蜷缩、盖被导致关键点可见度低时仍能看见人）。
"""
import threading
import time
from collections import deque
from typing import Optional

from pose_detector import PoseDetector, PoseResult
from ollama_analyzer import OllamaAnalyzer, _norm_loc, _norm_expr, _norm_body


# llava 子任务提示词：只问位置/表情/肢体 + 是否有人（姿态交给 MediaPipe）
CONTEXT_PROMPT = (
    "你是养老监护视觉分析助手，观察监控画面中老人的位置与状态。"
    "只输出一个JSON对象，字段："
    '{"person_present":true/false,'
    ' "location":"老人接触的表面：地面/床/沙发/椅子/其他/未知",'
    ' "expression":"面部表情：平静/痛苦/看不清",'
    ' "body_state":"肢体状态：放松/挣扎/蜷缩/静止/未知",'
    ' "confidence":0到1小数,'
    ' "explanation":"简短判断依据"}'
    "person_present：画面中能看到老人填true，看不到任何人填false。"
    "重点：老人躺下时，判断接触面是地面（可能跌倒）还是床/沙发（可能休息）。"
    "若面部太小或模糊，expression填看不清。"
)


class FallTemporalDetector:
    """时间序列跌落特征：检测「短时间窗内 直立→躺下 且髋部显著下坠」。

    跌倒的物理特征：重力使身体在 <1s 内从直立塌到地面，髋部(hip_y)从 ~0.5 坠到 ~0.8；
    而「上床躺下」髋部只小幅下降（床面抬高），且常经历「坐着」中间态。
    因此用「髋部下坠幅度」区分「倒地」与「上床休息」，比单帧判断地面/床更可靠。
    检测到后 latch 一段时间，让 risk_manager 的 5s 去抖窗口能持续看到「躺下+高」。
    """

    def __init__(self, window: float = 2.5, hip_drop: float = 0.18, latch: float = 30.0):
        self.window = window
        self.hip_drop = hip_drop
        self.latch = latch
        self._hist: deque = deque()          # (t, activity, hip_y, is_lying, is_upright)
        self._latch_until = 0.0

    def update(self, t: float, pose: PoseResult) -> bool:
        """输入一帧姿态结果，返回该帧是否命中「跌落时间特征」（已 latch）。"""
        if pose.person_present:
            lying = pose.activity == "躺下"
            # 只有「站立/行走 -> 躺下」才算跌落；「坐着 -> 躺下」多为上床休息，排除以减少误报
            upright = pose.activity in ("站立", "行走")
            self._hist.append((t, pose.activity, pose.hip_y, lying, upright))
            while self._hist and t - self._hist[0][0] > self.window:
                self._hist.popleft()
            if lying:
                hip_now = pose.hip_y
                for (_, _, hip_then, _, is_up) in self._hist:
                    if is_up and (hip_now - hip_then) >= self.hip_drop:
                        self._latch_until = t + self.latch
                        break
        return pose.activity == "躺下" and t < self._latch_until

    def reset(self) -> None:
        self._hist.clear()
        self._latch_until = 0.0


class HybridAnalyzer:
    """姿态(确定) + llava上下文(慢) + 跌落时间特征 -> PersonStatus 字段 dict。"""

    def __init__(
        self,
        model: str = "llava:7b",
        host: str = "http://localhost:11434",
        use_llava: bool = True,
        use_temporal_fall: bool = False,   # 时间跌落特征默认关闭：需真实跌倒数据调阈值后再开启
        pose_model_path: Optional[str] = None,
        fall_window: float = 2.5,
        fall_hip_drop: float = 0.18,
        fall_latch: float = 30.0,
    ):
        self.pose = PoseDetector(model_path=pose_model_path)
        self.llava = (
            OllamaAnalyzer(model=model, host=host, system_prompt=CONTEXT_PROMPT)
            if use_llava else None
        )
        self.fall = (
            FallTemporalDetector(window=fall_window, hip_drop=fall_hip_drop, latch=fall_latch)
            if use_temporal_fall else None
        )
        self._ctx_lock = threading.Lock()
        self._ctx: dict = {}

    # ---------- 慢速 llava 上下文 ----------
    def analyze_context(self, img_bgr) -> Optional[dict]:
        """调用 llava 更新位置/表情/肢体缓存（在独立线程低频率调用）。"""
        if self.llava is None:
            return None
        ctx = self.llava.analyze(img_bgr)  # 返回完整感知 dict，这里只取位置/表情/肢体/是否有人
        with self._ctx_lock:
            self._ctx = ctx
        return ctx

    def read_context(self) -> dict:
        with self._ctx_lock:
            return dict(self._ctx)

    # ---------- 快速姿态 + 决策 ----------
    def detect(self, img_bgr) -> dict:
        """每帧调用：姿态 -> 跌落特征(可选) -> 合并缓存上下文 -> 决策 -> PersonStatus dict。"""
        pose = self.pose.detect(img_bgr)
        t = time.time()
        fall = self.fall.update(t, pose) if self.fall else False
        ctx = self.read_context()
        return self._build(pose, fall, ctx)

    @staticmethod
    def _build(pose: PoseResult, fall: bool, ctx: dict) -> dict:
        # 是否有人：以姿态为准（快、确定性、无时延）。
        # 不用 llava 兜底 person_present：llava 慢（~90s/帧），其「有人」结论可能已严重过期，
        # 老人离开后仍缓存「有人」，会造成误报；宁可姿态漏检走「无人」分支（absent 告警兜底）。
        person_present = pose.person_present
        activity = pose.activity  # 无人时即 "无人"，有人时为 躺下/站立/坐着/其他

        # llava 上下文仅用于位置/表情/肢体（姿态能判的语义之外的部分）
        loc = _norm_loc(str(ctx.get("location", ""))) if ctx else "未知"
        expr = _norm_expr(str(ctx.get("expression", ""))) if ctx else "看不清"
        body = _norm_body(str(ctx.get("body_state", ""))) if ctx else "未知"
        distress = (expr == "痛苦") or (body in ("挣扎", "蜷缩"))

        risk, followup = "低", False
        reasons = []

        if not person_present:
            risk, followup = "无", False
        elif activity == "躺下":
            if fall:  # 快速倒地 = 跌倒，告警
                risk, followup, reasons = "高", True, ["快速倒地疑似跌倒"]
            else:  # 静态平躺 = 休息，不告警
                risk, followup, reasons = "低", False, ["平躺休息"]
        elif activity == "蹲着":
            risk, followup, reasons = "低", False, ["蹲着"]  # 久蹲超时由状态机处理
        elif activity in ("站立", "坐着", "行走"):
            if distress:
                risk, followup, reasons = "中", True, ["直立伴痛苦/蜷缩"]
            else:
                risk, followup, reasons = "低", False, []
        else:  # 其他（姿态不明）
            risk, followup, reasons = "中", True, ["姿态不明需复核"]

        explanation = "；".join(
            p for p in [pose.explanation, *reasons, (ctx.get("explanation", "") if ctx else "")]
            if p
        )

        return {
            "person_present": person_present,
            "activity": activity,
            "possible_risk": risk,
            "need_followup": followup,
            "confidence": round(float(pose.confidence), 3),
            "explanation": explanation,
            "location": loc,
            "expression": expr,
            "body_state": body,
            # 附加几何特征（调试/后续时间序列用，PersonStatus.from_json 会忽略多余字段）
            "vertical_fraction": round(pose.vertical_fraction, 3),
            "aspect_ratio": round(pose.aspect_ratio, 3),
            "temporal_fall": bool(fall),
        }
