# -*- coding: utf-8 -*-
"""姿态检测器评估：16 段真实视频，early/late 帧，对照 llava 人工标注算指标。

与 eval_fall.py（llava 评估）完全同源：
  - early 帧(35%) -> labels_early.json
  - late 帧(75%)  -> labels.json
  - 全分辨率帧（load_frame 不缩放）

用法：
  python eval_pose.py            # 全量 32 帧
  python eval_pose.py --limit 4  # 快速冒烟
"""
import argparse
import glob
import json
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

VIDEO_DIR = r'E:\ros_vision\test_videos'
LABELS_LATE = json.load(open(r'E:\ros_frames\labels.json', encoding='utf-8'))
LABELS_EARLY = json.load(open(r'E:\ros_frames\labels_early.json', encoding='utf-8'))


def load_frame(video, frac):
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--out', type=str, default=r'E:\ros_vision\eval_pose_result.json')
    args = ap.parse_args()

    detector = PoseDetector()

    videos = sorted(glob.glob(os.path.join(VIDEO_DIR, '*.mp4')))
    if args.limit:
        videos = videos[:args.limit]

    rows = []
    for v in videos:
        name = os.path.basename(v).replace('.mp4', '')
        for frac, tag, gt in [(0.35, 'early', LABELS_EARLY.get(name)),
                              (0.75, 'late', LABELS_LATE.get(name))]:
            frame = load_frame(v, frac)
            if frame is None:
                continue
            t0 = time.time()
            r = detector.detect(frame)
            cost = time.time() - t0
            rows.append({
                'video': name, 'frame': tag, 'gt': gt,
                'pred_present': r.person_present,
                'pred_activity': r.activity,
                'vertical_fraction': round(r.vertical_fraction, 2),
                'torso_angle': round(r.torso_angle, 1),
                'aspect_ratio': round(r.aspect_ratio, 2),
                'visible_ratio': round(r.visible_ratio, 2),
                'cost_ms': round(cost * 1000, 0),
                'explanation': r.explanation,
            })
            print(f'[{name}_{tag}] gt={gt} -> 活动={r.activity} 竖直占比={r.vertical_fraction:.2f} '
                  f'2D角={r.torso_angle:.0f}° 可见度={r.visible_ratio:.2f} ({cost*1000:.0f}ms)',
                  flush=True)

    total = len(rows)
    print(f'\n========== 姿态检测器评估汇总（{total} 帧，全分辨率）==========')

    # 1) 活动一致率
    act_correct = sum(1 for r in rows if r['pred_activity'] == r['gt'])
    print(f'活动标签一致率: {act_correct}/{total} = {act_correct / total:.0%}')

    # 2) 躺下召回（跌倒最关键）
    lying = [r for r in rows if r['gt'] == '躺下']
    lying_hit = [r for r in lying if r['pred_activity'] == '躺下']
    print(f'躺下召回: {len(lying_hit)}/{len(lying)} = {len(lying_hit) / max(len(lying), 1):.0%}')
    miss = [r for r in lying if r['pred_activity'] != '躺下']
    for r in miss:
        print(f'  漏报: {r["video"]}_{r["frame"]} gt=躺下 -> {r["pred_activity"]} (躯干角{r["torso_angle"]}°)')

    # 3) 直立误报（坐着/站立/行走 被判成躺下）
    upright = [r for r in rows if r['gt'] in ('坐着', '站立', '行走')]
    upright_fp = [r for r in upright if r['pred_activity'] == '躺下']
    print(f'直立误报(被判躺下): {len(upright_fp)}/{len(upright)} = {len(upright_fp) / max(len(upright), 1):.0%}')

    # 4) 无人召回
    absent = [r for r in rows if r['gt'] == '无人']
    absent_hit = [r for r in absent if r['pred_present'] is False]
    print(f'无人召回(person_present=false): {len(absent_hit)}/{len(absent)} = {len(absent_hit) / max(len(absent), 1):.0%}')
    for r in absent:
        print(f'  无人帧: {r["video"]}_{r["frame"]} -> present={r["pred_present"]} 活动={r["pred_activity"]} 可见度={r["visible_ratio"]}')

    # 5) 混淆矩阵（活动）
    from collections import Counter
    cm = Counter((r['gt'], r['pred_activity']) for r in rows)
    acts = ['坐着', '站立', '行走', '躺下', '其他', '无人']
    print('\n混淆矩阵 (行=真值, 列=预测):')
    print('        ' + ''.join(f'{a:>6}' for a in acts))
    for g in acts:
        line = f'{g:<8}' + ''.join(f'{cm[(g, p)]:>6}' for p in acts)
        print(line)

    metrics = {
        'activity_acc': act_correct / max(total, 1),
        'lying_recall': len(lying_hit) / max(len(lying), 1),
        'upright_fp': len(upright_fp) / max(len(upright), 1),
        'absent_recall': len(absent_hit) / max(len(absent), 1),
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump({'rows': rows, 'metrics': metrics}, f, ensure_ascii=False, indent=2)
    print(f'\n[结果已保存] {args.out}')


if __name__ == '__main__':
    main()
