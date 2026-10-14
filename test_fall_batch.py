# -*- coding: utf-8 -*-
"""批量验证时间跌落特征：对 16 段测试视频 + fall_test.mp4 逐一跑，
输出每段「是否触发 / 首次触发时刻 / 是否有人做站立→躺下过渡」。

用于确认：该触发(跌倒)的都触发，不该触发(无人/已躺下无过渡)的不触发。
"""
import glob
import os
import sys

import cv2

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, r'E:\ros_vision')
from pose_detector import PoseDetector
from hybrid_analyzer import FallTemporalDetector


def run(video, drop, every=2):
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    det = PoseDetector()
    fall = FallTemporalDetector(window=2.5, hip_drop=drop, latch=30.0)
    idx = 0
    fired = False
    fire_t = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % every != 0:
            idx += 1
            continue
        r = det.detect(frame)
        t = idx / fps
        if fall.update(t, r):
            if not fired:
                fired = True
                fire_t = t
        idx += 1
    cap.release()
    return fired, fire_t


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--drop', type=float, default=0.18)
    args = ap.parse_args()

    videos = sorted(glob.glob(r'E:\ros_vision\test_videos\*.mp4')) + [r'E:\ros_vision\fall_test.mp4']
    print(f'{"视频":<16}{"触发":<6}{"首次触发时刻"}')
    print('-' * 40)
    for v in videos:
        name = os.path.basename(v)
        fired, t = run(v, args.drop)
        print(f'{name:<16}{"是" if fired else "否":<8}{t if t is not None else "-"}')


if __name__ == '__main__':
    main()
