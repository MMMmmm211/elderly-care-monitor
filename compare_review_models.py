# -*- coding: utf-8 -*-
"""
复核模型 A/B 对比：同一张跌倒截图，分别用 llava:7b 与 qwen2.5vl:7b 做二次审核，
对比「姿势/位置/表情/肢体/风险/置信度」结论是否一致，找出更适合做独立第二意见的模型。
"""
import os
import sys
import time
import glob

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import cv2
import numpy as np
from ollama_analyzer import OllamaAnalyzer


def imread_any(path):
    """兼容含中文路径的读图（OpenCV imread 打不开中文路径）。"""
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)

REVIEW_PROMPT = (
    "你是养老监护二次审核助手，正在复核一张疑似跌倒的监控截图。"
    "请只输出一个JSON对象，字段："
    '{"person_present":true/false,'
    ' "activity":"坐着/站立/行走/躺下/其他",'
    ' "location":"老人接触的表面：地面/床/沙发/椅子/其他/未知",'
    ' "expression":"面部表情：平静/痛苦/看不清",'
    ' "body_state":"肢体状态：放松/挣扎/蜷缩/静止/未知",'
    ' "confidence":0到1小数,'
    ' "explanation":"简短判断依据"}'
    "重点判断：老人现在是什么姿势？若躺下，接触面是地面（可能跌倒）还是床/沙发（可能休息）。"
    "地面=跌倒需紧急处理；床/沙发+平静放松=正常休息，无需告警。"
)


def main():
    # 默认取最新一张告警截图
    alerts = sorted(glob.glob(os.path.join(_here, 'alerts', 'alert_*.png')),
                    key=os.path.getmtime, reverse=True)
    if not alerts:
        print('未找到告警截图')
        return
    path = alerts[0]
    print(f'对比截图: {os.path.basename(path)}\n')
    img = imread_any(path)
    if img is None:
        print(f'截图读取失败: {path}')
        return

    for model in ['llava:7b', 'qwen2.5vl:7b']:
        print(f'========== {model} ==========')
        t0 = time.time()
        analyzer = OllamaAnalyzer(model=model, system_prompt=REVIEW_PROMPT)
        obs = analyzer.analyze(img)
        dt = time.time() - t0
        print(f'  耗时: {dt:.1f}s')
        print(f'  有人: {obs.get("person_present")}')
        print(f'  活动: {obs.get("activity")}')
        print(f'  位置: {obs.get("location")}')
        print(f'  表情: {obs.get("expression")}')
        print(f'  肢体: {obs.get("body_state")}')
        print(f'  风险: {obs.get("possible_risk")}')
        print(f'  置信度: {obs.get("confidence")}')
        print(f'  判断: {obs.get("explanation")}')
        print()


if __name__ == '__main__':
    main()
