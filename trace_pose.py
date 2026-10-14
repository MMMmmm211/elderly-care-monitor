# -*- coding: utf-8 -*-
"""姿态时间序列追踪：逐帧跑 PoseDetector，输出 (t, 活动, 髋y, 身体底, 宽高比, 竖直占比)。

用于「时间跌落特征」调参：观察跌倒(快速直立→躺下、髋部骤降) vs 上床休息(慢/髋部小幅降)的差异。

用法：
  python trace_pose.py E:\ros_vision\fall_test.mp4 [--every N] [--max M]
"""
import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--every', type=int, default=5, help='每 N 帧采样一次')
    ap.add_argument('--max', type=int, default=0, help='最多采样 M 帧(0=不限)')
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    det = PoseDetector()

    idx = 0
    n = 0
    print(f'# {os.path.basename(args.video)} total={total} fps={fps:.1f}')
    print('# idx\tt(s)\t活动\thip_y\tbottom\taspect\tvf\tpresent')
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % args.every != 0:
            idx += 1
            continue
        r = det.detect(frame)
        t = idx / fps
        print(f'{idx}\t{t:.2f}\t{r.activity}\t{r.hip_y:.3f}\t{r.body_bottom:.3f}\t'
              f'{r.aspect_ratio:.2f}\t{r.vertical_fraction:.3f}\t{int(r.person_present)}',
              flush=True)
        idx += 1
        n += 1
        if args.max and n >= args.max:
            break
    cap.release()


if __name__ == '__main__':
    main()
