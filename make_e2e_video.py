# -*- coding: utf-8 -*-
"""合成端到端验证用的「模拟场景视频」。

场景时序（模拟「老人出现 -> 跌倒 -> 持续倒地」，前后接无人段）：
  1. 无人 ~20s    (01.mp4 尾部无人段循环)      -> 触发 ABSENT
  2. 站立 ~8s     (05.mp4 头部站立段循环)      -> 恢复 NORMAL
  3. 跌倒 ~10s    (05.mp4 站立->躺下段循环)    -> 触发 fall 告警
  4. 持续躺下 ~10s (07.mp4 全段循环)           -> 保持 ALERT

为什么复用现有真实视频片段：MediaPipe 需要真实人体关键点，程序画的人形/火柴人
检测不到；因此拼接已标注的真实片段来驱动端到端链路（不调用摄像头）。
"""
import os
import sys
import cv2

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = r'E:\ros_vision'
OUT = os.path.join(ROOT, 'e2e_scenario.mp4')
FPS = 30
W, H = 1280, 720


def read_range(path, start, end):
    """读取 [start, end) 帧，统一到 WxH，返回 list[np.ndarray BGR]。"""
    cap = cv2.VideoCapture(path)
    frames = []
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i >= end:
            break
        if i >= start:
            if f.shape[1] != W or f.shape[0] != H:
                f = cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA)
            frames.append(f)
        i += 1
    cap.release()
    return frames


def tile(frames, target_n):
    """把帧序列循环到 target_n 帧。"""
    if not frames:
        return []
    return [frames[k % len(frames)] for k in range(target_n)]


def main():
    # 1) 无人段：01.mp4 帧 129-203（探测确认 129 帧起无人）
    absent = read_range(os.path.join(ROOT, 'test_videos', '01.mp4'), 129, 203)
    # 2) 站立段：05.mp4 帧 0-36（hip≈0.44，稳定站立，未下蹲）
    stand = read_range(os.path.join(ROOT, 'test_videos', '05.mp4'), 0, 36)
    # 3) 跌倒过渡段：05.mp4 帧 36-90（站立 hip0.44 -> 坐着 -> 躺下 hip0.77，drop≈0.33，稳定触发 latch）
    fall_trans = read_range(os.path.join(ROOT, 'test_videos', '05.mp4'), 36, 90)
    # 4) 持续躺下段：05.mp4 帧 90-172（纯躺下 hip≈0.77，循环边界也是躺下，保证「躺下+高」连续）
    lying = read_range(os.path.join(ROOT, 'test_videos', '05.mp4'), 90, 173)

    print(f'源片段帧数：无人={len(absent)} 站立={len(stand)} 过渡={len(fall_trans)} 躺下={len(lying)}')
    assert absent and stand and fall_trans and lying, '有源片段读取失败'

    segs = []
    segs += tile(absent, int(35 * FPS))
    segs += tile(stand, int(6 * FPS))
    segs += tile(fall_trans, int(6 * FPS))
    segs += tile(lying, int(12 * FPS))

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out_path = OUT
    vw = cv2.VideoWriter(out_path, fourcc, FPS, (W, H))
    if not vw.isOpened():
        # 兜底：mp4v 不可用则退回 MJPG + .avi
        out_path = OUT.replace('.mp4', '.avi')
        vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*'MJPG'), FPS, (W, H))
    for f in segs:
        vw.write(f)
    vw.release()

    total = len(segs) / FPS
    print(f'已合成 {out_path}：{len(segs)} 帧 {total:.1f}s ({W}x{H} @ {FPS}fps)')
    print(f'  无人 35s / 站立 6s / 跌倒过渡 6s / 持续躺下 12s')


if __name__ == '__main__':
    main()
