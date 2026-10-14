"""
类型化消息 schema（阶段 B 的「自定义消息」实现）

说明：
  标准 ROS2 自定义消息需要 .msg 文件 + colcon build（依赖 CMake + rosidl_default_generators）。
  当前 pixi 版 ROS2 缺少 colcon/CMake，无法编译 .msg，因此这里用 Python dataclass
  实现等价的「类型安全消息」：线上仍走 std_msgs/String + JSON 传输，
  下游通过 from_json() 得到强类型对象，杜绝手写 json.loads 导致的 KeyError。

  等环境补齐 colcon + CMake 后，可把下面两个类 1:1 翻译成：
    PersonStatus.msg  /  AlertEvent.msg
"""
from dataclasses import dataclass, asdict
import json


@dataclass
class PersonStatus:
    """单帧分析结果（对应 vision_analysis_node 的输出）"""
    person_present: bool
    activity: str          # 坐着/站立/行走/躺下/其他
    possible_risk: str     # 无/低/中/高
    need_followup: bool
    confidence: float
    explanation: str = ""
    location: str = "未知"      # 地面/床/沙发/椅子/其他/未知
    expression: str = "看不清"  # 平静/痛苦/看不清
    body_state: str = "未知"    # 放松/挣扎/蜷缩/静止/未知

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "PersonStatus":
        """宽容解析：字段缺失/类型异常时回退默认值，不让单帧坏数据拖垮状态机"""
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        try:
            confidence = float(d.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            person_present=bool(d.get("person_present", False)),
            activity=str(d.get("activity", "其他")),
            possible_risk=str(d.get("possible_risk", "无")),
            need_followup=bool(d.get("need_followup", False)),
            confidence=confidence,
            explanation=str(d.get("explanation", "")),
            location=str(d.get("location", "未知")),
            expression=str(d.get("expression", "看不清")),
            body_state=str(d.get("body_state", "未知")),
        )


@dataclass
class AlertEvent:
    """状态机输出的状态转移 / 告警事件"""
    state: str            # normal/watch/alert/absent
    alert_type: str       # state_change / fall / high_risk / absent
    risk_level: str       # 无/低/中/高
    description: str
    duration_sec: float   # 该状态已持续秒数

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
