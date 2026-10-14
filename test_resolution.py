# -*- coding: utf-8 -*-
"""分辨率对照实验：同一批“难分帧”在 max_side=640/1024/1344 下的识别结果。
    用于判断「躺下→坐着」「无人臆造」是否是小图分辨率导致的。"""
import sys
import time

import cv2

sys.path.insert(0, r'E:\ros_vision')
from ollama_analyzer import OllamaAnalyzer

VIDEO_DIR = r'E:\ros_vision\test_videos'

# (视频, 帧位置, 期望标签)
CASES = [
    ('02', 0.35, '躺下'),
    ('07', 0.35, '躺下'),
    ('08', 0.35, '躺下'),
    ('13', 0.75, '躺下'),
    ('01', 0.75, '无人'),
    ('02', 0.75, '无人'),
]


def load_frame(video, frac):
    cap = cv2.VideoCapture(f'{VIDEO_DIR}\\{video}.mp4')
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
    ok, frame = cap.read()
    cap.release()
    return frame


for max_side in (640, 1024, 1344):
    print(f'\n========== max_side={max_side} ==========')
    a = OllamaAnalyzer(model='llava:7b', max_side=max_side)
    for video, frac, gt in CASES:
        frame = load_frame(video, frac)
        t0 = time.time()
        r = a.analyze(frame)
        print(f'  [{video}@{frac:.2f}] gt={gt} -> 有人={r.get("person_present")} '
              f'活动={r.get("activity")} 位置={r.get("location")} 风险={r.get("possible_risk")} '
              f'({time.time()-t0:.0f}s) | {r.get("explanation")}', flush=True)
