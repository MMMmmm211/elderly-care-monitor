# -*- coding: utf-8 -*-
"""验证下载的视频文件是否有效（临时脚本）"""
import glob
import os
import cv2

d = r'E:\ros_vision\test_videos'
for f in sorted(glob.glob(os.path.join(d, '*.mp4'))):
    name = os.path.basename(f)
    cap = cv2.VideoCapture(f)
    if not cap.isOpened():
        print(f'[FAIL] {name} 无法打开')
        continue
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = n / fps if fps else 0
    print(f'[OK] {name}: {n}帧 {fps:.1f}fps {w}x{h} {dur:.1f}s')
    cap.release()
