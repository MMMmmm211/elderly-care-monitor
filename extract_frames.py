# -*- coding: utf-8 -*-
"""从每段视频抽取帧，用于人工判断跌倒/正常（临时脚本）"""
import glob
import os
import cv2

d = r'E:\ros_vision\test_videos'
out = r'E:\ros_frames'
os.makedirs(out, exist_ok=True)

for f in sorted(glob.glob(os.path.join(d, '*.mp4'))):
    name = os.path.basename(f).replace('.mp4', '')
    cap = cv2.VideoCapture(f)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    for frac, tag in [(0.35, 'early'), (0.75, 'late')]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
        ret, frame = cap.read()
        if ret:
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (360, int(360 * h / w)))
            cv2.imwrite(os.path.join(out, f'{name}_{tag}.jpg'), frame)
    cap.release()
print('抽帧完成:', len(glob.glob(os.path.join(out, '*.jpg'))), '张')
