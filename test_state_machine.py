# -*- coding: utf-8 -*-
"""状态机单元测试（临时，验证完可删）"""
import sys
sys.path.insert(0, r'E:\ros_vision')

from elderly_monitor.state_machine import RiskStateMachine
from elderly_monitor.messages import PersonStatus


def P(present=True, act='坐着', risk='低'):
    return PersonStatus(person_present=present, activity=act, possible_risk=risk,
                        need_followup=False, confidence=0.8, explanation='')


def main():
    sm = RiskStateMachine(
        watch_sec=10, alert_sec=20, fall_sec=3,
        absent_sec=15, absent_alert_sec=60, recover_sec=30, cooldown_sec=300,
    )
    t = 1000.0

    # 1) 正常坐着 10s，不应有任何事件
    for _ in range(20):
        e = sm.update(P(), now=t)
        assert e is None, f'正常态不应有事件，实际 {e}'
        t += 0.5
    print('[OK]正常坐着 10s 无事件，状态 =', sm.state.value)

    # 2) 老人躺下（跌倒），持续 3s 应触发 fall 告警
    events = []
    for _ in range(8):
        e = sm.update(P(act='躺下', risk='高'), now=t)
        if e:
            events.append(e)
        t += 0.5
    print('[OK]躺下触发的告警:')
    for e in events:
        print('   ', e.state, '|', e.alert_type, '|', e.risk_level, '|', e.description)
    assert any(e.alert_type == 'fall' for e in events), '应触发 fall 告警'
    assert sm.state.value == 'alert', f'应处于 alert，实际 {sm.state.value}'

    # 3) 风险解除（坐起、低风险），持续 recover_sec 后恢复 NORMAL
    for _ in range(70):  # 35s > recover_sec=30
        e = sm.update(P(), now=t)
        t += 0.5
    print('[OK]风险解除后状态 =', sm.state.value)
    assert sm.state.value == 'normal', f'应恢复 normal，实际 {sm.state.value}'

    # 4) 无人：持续 absent_sec 后进入 absent，再持续到 absent_alert_sec 触发告警
    for _ in range(40):  # 20s
        e = sm.update(P(present=False), now=t)
        t += 0.5
    print('[OK]持续无人 20s，状态 =', sm.state.value)
    assert sm.state.value == 'absent', f'应进入 absent，实际 {sm.state.value}'

    absent_alert = None
    for _ in range(120):  # 60s，累计无人 80s > absent_alert_sec=60
        e = sm.update(P(present=False), now=t)
        if e and e.alert_type == 'absent':
            absent_alert = e
        t += 0.5
    print('[OK]无人超时告警 =', absent_alert)
    assert absent_alert is not None, '应触发 absent 告警'

    print('\n[PASS] 全部断言通过')


if __name__ == '__main__':
    main()
