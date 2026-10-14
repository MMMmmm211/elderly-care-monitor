# -*- coding: utf-8 -*-
"""时间跌落特征验证：在真实视频上逐帧跑 PoseDetector + FallTemporalDetector，
报告是否触发、首次触发时刻、latch 时长。用于调参后回归验证。

用法：
  python test_fall_detector.py E:\ros_vision\fall_test.mp4 [--window 2.5] [--drop 0.18] [--every 1]
"""
import argparse
import os
import sys
import time

import cv2

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, r'E:\ros_vision')
from pose_detector import PoseDetector
from hybrid_analyzer import FallTemporalDetector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--window', type=float, default=2.5)
    ap.add_argument('--drop', type=float, default=0.18)
    ap.add_argument('--latch', type=float, default=30.0)
    ap.add_argument('--every', type=int, default=2)
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    det = PoseDetector()
    fall = FallTemporalDetector(window=args.window, hip_drop=args.drop, latch=args.latch)

    idx = 0
    fired = False
    fire_t = None
    last_true = None
    total_true = 0
    events = []  # (t, 活动, hip_y, fall)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % args.every != 0:
            idx += 1
            continue
        r = det.detect(frame)
        t = idx / fps
        is_fall = fall.update(t, r)
        if is_fall:
            if not fired:
                fired = True
                fire_t = t
                print(f'[触发] t={t:.2f}s 活动={r.activity} hip_y={r.hip_y:.3f} '
                      f'bottom={r.body_bottom:.3f} vf={r.vertical_fraction:.3f}')
            total_true += 1
            last_true = t
        idx += 1

    cap.release()
    print(f'\n===== {os.path.basename(args.video)} window={args.window}s drop={args.drop} =====')
    print(f'是否触发跌落: {"是" if fired else "否"}')
    if fired:
        print(f'首次触发时刻: {fire_t:.2f}s')
        print(f'最后命中时刻: {last_true:.2f}s')
        print(f'命中帧数(采样): {total_true}')
    else:
        print('全程未触发（无 站立/行走→躺下 的髋部骤降）')


if __name__ == '__main__':
    main()
