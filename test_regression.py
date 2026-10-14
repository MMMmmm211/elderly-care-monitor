# -*- coding: utf-8 -*-
"""回归测试：验证状态机「条件切换去抖」修正。

场景复现线上真实 bug：
  行走阶段结束 → 第一次检测到躺下，就立刻触发 fall 告警（duration_sec=0）。
根因：_cond_since 只在『正常』分支重置，导致躺下继承了行走阶段的旧时间戳。
"""
import sys
sys.path.insert(0, r'E:\ros_vision')

from elderly_monitor.state_machine import RiskStateMachine
from elderly_monitor.messages import PersonStatus


def P(present=True, act='坐着', risk='低'):
    return PersonStatus(person_present=present, activity=act, possible_risk=risk,
                        need_followup=False, confidence=0.8, explanation='')


def main():
    sm = RiskStateMachine(
        watch_sec=10, alert_sec=20, fall_sec=2.0,
        absent_sec=15, absent_alert_sec=60, recover_sec=30, cooldown_sec=300,
    )
    t = 0.0

    # 行走 30s（正常阶段）
    for _ in range(6):
        sm.update(P(act='行走', risk='低'), now=t)
        t += 5.0

    # 第一次躺下：不应立即告警（去抖要求连续躺下 fall_sec 秒）
    e1 = sm.update(P(act='躺下', risk='高'), now=t)
    t += 5.0
    assert e1 is None, f'第一次躺下不应告警，实际 {e1}'
    print('[OK] 第一次躺下未告警（去抖生效），状态 =', sm.state.value)

    # 第二次躺下（5s 后，已超过 fall_sec=2.0）：应触发 fall
    e2 = sm.update(P(act='躺下', risk='高'), now=t)
    assert e2 is not None and e2.alert_type == 'fall', f'第二次躺下应触发 fall，实际 {e2}'
    assert sm.state.value == 'alert', f'应进入 alert，实际 {sm.state.value}'
    print('[OK] 第二次躺下触发 fall 告警:', e2.alert_type, '|', e2.description)

    print('\n[PASS] 回归测试通过：条件切换去抖正确')


if __name__ == '__main__':
    main()
