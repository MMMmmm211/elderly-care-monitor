# -*- coding: utf-8 -*-
"""阶段 C 单元测试：验证「多维判定」的感知归一化 + 决策规则 + 失败兜底。"""
import sys
sys.path.insert(0, r'E:\ros_vision')

import numpy as np

from ollama_analyzer import OllamaAnalyzer, _FAILSAFE_RESULT


def P(act='坐着', loc='', expr='', body=''):
    return {'person_present': True, 'activity': act, 'location': loc,
            'expression': expr, 'body_state': body, 'confidence': 0.8, 'explanation': ''}


def test_normalize():
    from ollama_analyzer import _norm_loc, _norm_expr, _norm_body
    assert _norm_loc('地板上') == '地面'
    assert _norm_loc('在床上') == '床'
    assert _norm_loc('沙发') == '沙发'
    assert _norm_loc('') == '未知'
    assert _norm_expr('皱眉，表情痛苦') == '痛苦'
    assert _norm_expr('表情平静') == '平静'
    assert _norm_expr('') == '看不清'
    assert _norm_body('身体蜷缩') == '蜷缩'
    assert _norm_body('放松躺着') == '放松'
    print('[OK] 归一化：自由文本 -> 短枚举')


def test_decide():
    a = OllamaAnalyzer(model='llava:7b')

    # 跌倒：躺下+地面 -> 高
    r = a._decide(P('躺下', loc='地面', expr='痛苦'))
    assert r['possible_risk'] == '高' and r['need_followup'] is True, r
    print('[OK] 躺下+地面 -> 高(跌倒)')

    # 正常休息：躺下+床+平静 -> 低（关键：不再误报）
    r = a._decide(P('躺下', loc='床', expr='平静', body='放松'))
    assert r['possible_risk'] == '低' and r['need_followup'] is False, r
    print('[OK] 躺下+床+平静 -> 低(正常休息，不误报)')

    # 突发病症：躺下+床+痛苦 -> 高
    r = a._decide(P('躺下', loc='床', expr='痛苦'))
    assert r['possible_risk'] == '高' and r['need_followup'] is True, r
    print('[OK] 躺下+床+痛苦 -> 高(突发病症)')

    # 位置未知：躺下+未知 -> 中(关注复核)
    r = a._decide(P('躺下', loc='未知', expr='看不清'))
    assert r['possible_risk'] == '中' and r['need_followup'] is True, r
    print('[OK] 躺下+位置未知 -> 中(关注复核)')

    # 心梗前兆：坐着+痛苦/蜷缩 -> 中
    r = a._decide(P('坐着', expr='痛苦', body='蜷缩'))
    assert r['possible_risk'] == '中' and r['need_followup'] is True, r
    print('[OK] 坐着+痛苦/蜷缩 -> 中(前兆关注)')

    # 正常：坐着/行走+平静 -> 低
    for act in ['坐着', '站立', '行走']:
        r = a._decide(P(act, expr='平静', body='放松'))
        assert r['possible_risk'] == '低', r
    print('[OK] 直立姿态+平静 -> 低')

    # 其他 -> 中
    r = a._decide(P('其他'))
    assert r['possible_risk'] == '中', r
    print('[OK] 其他 -> 中')


def test_failsafe():
    assert _FAILSAFE_RESULT['possible_risk'] == '高'
    assert _FAILSAFE_RESULT['need_followup'] is True
    print('[OK] 兜底结果字段正确')


def test_analyze_failsafe():
    """真实走一遍：不存在的模型 -> 重试失败 -> 返回保守兜底（不抛异常）。"""
    a = OllamaAnalyzer(model='definitely-not-a-model-xyz', timeout=5)
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    r = a.analyze(img)
    assert r['possible_risk'] == '高', r
    assert '视觉分析失败' in r['explanation'], r
    print('[OK] 模型故障 -> 兜底结果:', r['possible_risk'], '|', r['explanation'])


if __name__ == '__main__':
    test_normalize()
    test_decide()
    test_failsafe()
    test_analyze_failsafe()
    print('\n[PASS] 阶段C 多维判定 + 归一化 + 兜底 测试通过')
