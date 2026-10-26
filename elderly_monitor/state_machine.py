"""
养老监护风险状态机

为什么需要状态机：
  单帧 LLM 分析有噪声——activity 在「坐着/站立」间抖动、risk 在「低/中」间跳变。
  若每帧直接告警会刷屏误报。状态机维护一个「随时间演化的状态」，
  只有某种风险条件「持续超过阈值」才发生状态转移，从而去抖、降误报，
  并管理告警生命周期（触发 / 恢复 / 冷却）。

状态：
  NORMAL  正常：有人且风险低
  WATCH   关注：持续中/高风险，需要观察
  ALERT   告警：跌倒/躺下，或持续高风险
  ABSENT  无人：画面持续无人
"""
import time
from enum import Enum
from typing import Optional

from .messages import PersonStatus, AlertEvent


class RiskState(str, Enum):
    NORMAL = "normal"
    WATCH = "watch"
    ALERT = "alert"
    ABSENT = "absent"


# 中文风险等级 -> 严重度数值
_RISK_SEVERITY = {"无": 0, "低": 1, "中": 2, "高": 3}


class RiskStateMachine:
    def __init__(
        self,
        watch_sec: float = 10.0,          # 连续 N 秒 风险>=中 -> WATCH
        alert_sec: float = 20.0,          # 连续 M 秒 风险=高 -> ALERT
        fall_sec: float = 5.0,            # 连续 F 秒 躺下/跌倒 -> ALERT（更快）
        squat_sec: float = 15.0,          # 连续 S 秒 蹲着 -> 告警（老人久蹲起不来）
        absent_sec: float = 15.0,         # 连续 T 秒 无人 -> ABSENT
        absent_alert_sec: float = 60.0,   # （保留：无人告警已由夜间提示取代）
        recover_sec: float = 30.0,        # 连续 K 秒 风险回落 -> 恢复 NORMAL
        cooldown_sec: float = 300.0,      # 同类告警冷却，避免刷屏
        night_check_hour: float = 20.0,   # 晚上几点后无人做提示（每天一次）
        cond_hold_sec: float = 3.0,       # 候选条件短暂中断的容忍时长（迟滞，防抖动重置计时）
    ):
        self.watch_sec = watch_sec
        self.alert_sec = alert_sec
        self.fall_sec = fall_sec
        self.squat_sec = squat_sec
        self.absent_sec = absent_sec
        self.absent_alert_sec = absent_alert_sec
        self.recover_sec = recover_sec
        self.cooldown_sec = cooldown_sec
        self.night_check_hour = night_check_hour
        self.cond_hold_sec = cond_hold_sec

        self.state = RiskState.NORMAL
        self._state_since = time.time()              # 进入当前状态的时间
        self._cond_since = time.time()               # 当前「候选风险条件」持续多久
        self._cond = None                            # 当前候选条件标签（用于条件切换时重置计时）
        self._cond_last_seen = time.time()           # 当前候选条件最后一次被确认的时间
        self._absent_since: Optional[float] = None   # 开始无人的时间
        self._last_alert_at: dict = {}  # alert_type -> 上次告警时间（按类型冷却）
        self._night_notified = None     # 上次夜间无人提示的「年内第几天」，用于每天一次

    # ---------- 内部工具 ----------
    def _switch(self, new_state: RiskState, now: float) -> None:
        self.state = new_state
        self._state_since = now
        self._cond_since = now
        self._cond = None
        self._cond_last_seen = now

    def _held(self, now: float, threshold: float) -> bool:
        """当前候选条件是否已持续超过 threshold 秒"""
        return now - self._cond_since >= threshold

    def _set_cond(self, cond: str, now: float) -> None:
        """切换候选条件，并对「短暂中断」做迟滞处理。

        背景：单帧姿态检测会抖（蹲/坐/站之间来回跳），条件一变化就清零计时，
        会让 `squat_sec=15s` 这种长阈值永远凑不满（实测最长连续蹲只有 8.4s）。

        规则：条件短暂离开、并在 cond_hold_sec 秒内回到同一个条件，视为抖动，
        计时不清零，继续累加；离开超过 cond_hold_sec 才真正切换并从
        「新条件实际开始的那一刻」重新计时。

        关键修正（保留）：去抖时长必须是「当前条件」的真实持续时长。
        旧实现只在『正常』分支重置 _cond_since，导致「行走→躺下」这种
        条件切换时，第一次躺下就继承了行走阶段遗留的旧时间戳，从而
        立刻触发告警（表现为 duration_sec=0 的假阳性）。"""
        if cond == self._cond:
            self._cond_last_seen = now
            return

        # 条件变了：若上一个条件还没「断太久」，判为抖动，保持原计时不动
        if self._cond is not None and (now - self._cond_last_seen) <= self.cond_hold_sec:
            return

        # 真正切换：计时起点取「上一次见到旧条件的时刻」，把容忍窗口补回来
        self._cond = cond
        self._cond_since = self._cond_last_seen if self._cond_last_seen else now
        self._cond_last_seen = now

    def _risk_for_state(self) -> str:
        return {
            RiskState.NORMAL: "无",
            RiskState.WATCH: "中",
            RiskState.ALERT: "高",
            RiskState.ABSENT: "中",
        }[self.state]

    def _event(self, now: float, alert_type: str, risk_level: str, description: str) -> AlertEvent:
        return AlertEvent(
            state=self.state.value,
            alert_type=alert_type,
            risk_level=risk_level,
            description=description,
            duration_sec=now - self._state_since,
        )

    def _maybe_alert(self, now: float, alert_type: str, risk_level: str, description: str) -> Optional[AlertEvent]:
        """带冷却的告警：同类型告警在冷却期内不再重复触发（不同类型互不影响）"""
        last = self._last_alert_at.get(alert_type)
        if last is not None and now - last < self.cooldown_sec:
            return None
        self._last_alert_at[alert_type] = now
        return self._event(now, alert_type, risk_level, description)

    def _today(self, now: float) -> int:
        """返回「年内第几天」，用于跨天判断（每天一次的夜间提示）。"""
        return time.localtime(now).tm_yday

    def _is_night(self, now: float) -> bool:
        """是否已到夜间检查时段（小时 >= night_check_hour）。"""
        return time.localtime(now).tm_hour >= self.night_check_hour

    # ---------- 主入口 ----------
    def update(self, s: PersonStatus, now: Optional[float] = None) -> Optional[AlertEvent]:
        """输入一帧分析结果，返回 None（无事件）或一个状态转移/告警事件"""
        now = now if now is not None else time.time()

        # ===== 无人分支 =====
        if not s.person_present:
            if self._absent_since is None:
                self._absent_since = now
            absent_dur = now - self._absent_since

            # 无人持续超过 absent_sec 才切 ABSENT（去抖：llava 偶尔漏检）
            if absent_dur >= self.absent_sec:
                if self.state != RiskState.ABSENT:
                    self._switch(RiskState.ABSENT, now)
                    return self._event(now, "state_change", self._risk_for_state(),
                                       f"画面持续无人 {absent_dur:.0f}s，进入无人状态")
                # 白天无人静默；夜间（如晚上 8 点后）仍无人，每天提醒一次
                if self._is_night(now) and self._night_notified != self._today(now):
                    self._night_notified = self._today(now)
                    return self._maybe_alert(now, "night_absent", "中",
                                             "夜间时段画面仍无人，请确认老人是否归家")
            return None

        # ===== 有人分支 =====
        self._absent_since = None
        severity = _RISK_SEVERITY.get(s.possible_risk, 0)
        is_lying = s.activity == "躺下"
        # 真正的跌倒/急症信号：躺下 且 高风险（区分于「床上正常休息」的低风险躺下）
        suspected_fall = is_lying and s.possible_risk == "高"

        # 人回来了，直接恢复 NORMAL
        if self.state == RiskState.ABSENT:
            self._switch(RiskState.NORMAL, now)
            return self._event(now, "state_change", "无", "检测到人员，恢复正常监控")

        # NORMAL：观察是否进入 WATCH 或直接 ALERT（躺下更快告警）
        if self.state == RiskState.NORMAL:
            if suspected_fall:
                self._set_cond('lying', now)
                if self._held(now, self.fall_sec):
                    self._switch(RiskState.ALERT, now)
                    return self._maybe_alert(now, "fall", "高", "检测到老人异常躺下，疑似跌倒或突发急症")
            elif s.activity == "蹲着":  # 久蹲：超过阈值提醒
                self._set_cond('squat', now)
                if self._held(now, self.squat_sec):
                    return self._maybe_alert(now, "squat", "中",
                                             f"老人持续蹲着超过 {self.squat_sec:.0f}s，请关注")
            elif severity >= 2:  # 中/高
                self._set_cond('risk', now)
                if self._held(now, self.watch_sec):
                    self._switch(RiskState.WATCH, now)
                    return self._event(now, "state_change", self._risk_for_state(),
                                       "风险持续偏高，进入关注状态")
            else:
                self._set_cond('normal', now)  # 正常，重置计时
            return None

        # WATCH：升级告警 或 回落恢复
        if self.state == RiskState.WATCH:
            if severity >= 3:  # 高
                self._set_cond('high', now)
                if self._held(now, self.alert_sec):
                    self._switch(RiskState.ALERT, now)
                    return self._maybe_alert(now, "high_risk", "高", "持续高风险，触发告警")
            elif severity <= 1:  # 低/无
                self._set_cond('low', now)
                if self._held(now, self.recover_sec):
                    self._switch(RiskState.NORMAL, now)
                    return self._event(now, "state_change", "无", "风险回落，恢复正常")
            else:  # 中：既没升级也没回落，维持 WATCH
                self._set_cond('mid', now)
            return None

        # ALERT：只有持续低风险才解除
        if self.state == RiskState.ALERT:
            if severity <= 1 and not suspected_fall:
                self._set_cond('recover', now)
                if self._held(now, self.recover_sec):
                    self._switch(RiskState.NORMAL, now)
                    return self._event(now, "state_change", "无", "风险解除，恢复正常")
            else:
                self._set_cond('alert_hold', now)
            return None

        return None
