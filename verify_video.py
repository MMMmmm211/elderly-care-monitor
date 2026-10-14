# -*- coding: utf-8 -*-
"""验证跌倒视频并抽取关键帧（输出到 ASCII 路径，避免 cv2 中文路径问题）"""
import os
import cv2

path = r'E:\ros_vision\fall_test.mp4'
outdir = r'C:\Users\DELL\AppData\Local\Temp\ros_frames'
os.makedirs(outdir, exist_ok=True)

cap = cv2.VideoCapture(path)
print('opened:', cap.isOpened())
fps = cap.get(cv2.CAP_PROP_FPS)
n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print('fps:', fps)
print('frames:', n)
print('duration_s:', round(n / fps, 1) if fps else 'N/A')
print('size:', w, 'x', h)

for name, idx in [('start', 0), ('mid', n // 2), ('end', max(0, n - 3))]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    if ok:
        out = os.path.join(outdir, f'frame_{name}.jpg')
        saved = cv2.imwrite(out, frame)
        print('saved:', name, '->', saved, out)
    else:
        print('read fail:', name)

cap.release()
