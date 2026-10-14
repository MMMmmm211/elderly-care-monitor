# -*- coding: utf-8 -*-
"""混合分析器评估：16 段真实视频 early/late 帧，对照人工标注算指标。

与 eval_pose.py 同源，但走 HybridAnalyzer（姿态 + 可选 llava 上下文 + 时间跌落特征）。

用法：
  python eval_hybrid.py               # 仅姿态（快，~秒级）
  python eval_hybrid.py --llava       # 加 llava 上下文（慢，每帧 ~9s）
  python eval_hybrid.py --limit 4     # 快速冒烟
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
from hybrid_analyzer import HybridAnalyzer

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
    ap.add_argument('--llava', action='store_true')
    ap.add_argument('--fall', action='store_true', help='启用时间跌落特征(默认关)')
    ap.add_argument('--out', type=str, default=r'E:\ros_vision\eval_hybrid_result.json')
    args = ap.parse_args()

    analyzer = HybridAnalyzer(use_llava=args.llava, use_temporal_fall=args.fall)

    videos = sorted(glob.glob(os.path.join(VIDEO_DIR, '*.mp4')))
    if args.limit:
        videos = videos[:args.limit]

    rows = []
    for v in videos:
        name = os.path.basename(v).replace('.mp4', '')
        if analyzer.fall:
            analyzer.fall.reset()  # 每段视频独立，避免时间跌落特征跨视频串扰
        for frac, tag, gt in [(0.35, 'early', LABELS_EARLY.get(name)),
                              (0.75, 'late', LABELS_LATE.get(name))]:
            frame = load_frame(v, frac)
            if frame is None:
                continue
            t0 = time.time()
            r = analyzer.detect(frame)   # 姿态 + 决策（llava 上下文若启用则合并缓存）
            if args.llava:
                analyzer.analyze_context(frame)  # 刷新 llava 上下文，供后续帧复用
            cost = time.time() - t0
            rows.append({
                'video': name, 'frame': tag, 'gt': gt,
                'pred_present': r['person_present'],
                'pred_activity': r['activity'],
                'risk': r['possible_risk'],
                'followup': r['need_followup'],
                'location': r['location'],
                'expression': r['expression'],
                'body_state': r['body_state'],
                'temporal_fall': r.get('temporal_fall', False),
                'vertical_fraction': r.get('vertical_fraction', 0.0),
                'aspect_ratio': r.get('aspect_ratio', 0.0),
                'cost_ms': round(cost * 1000, 0),
            })
            print(f'[{name}_{tag}] gt={gt} -> 活动={r["activity"]} 风险={r["possible_risk"]} '
                  f'位置={r["location"]} 表情={r["expression"]} 肢体={r["body_state"]} '
                  f'fall={r.get("temporal_fall")} ({cost*1000:.0f}ms)', flush=True)

    total = len(rows)
    print(f'\n========== 混合分析器评估汇总（{total} 帧）==========')

    act_correct = sum(1 for r in rows if r['pred_activity'] == r['gt'])
    print(f'活动标签一致率: {act_correct}/{total} = {act_correct / max(total, 1):.0%}')

    lying = [r for r in rows if r['gt'] == '躺下']
    lying_hit = [r for r in lying if r['pred_activity'] == '躺下']
    print(f'躺下召回: {len(lying_hit)}/{len(lying)} = {len(lying_hit) / max(len(lying), 1):.0%}')
    for r in lying:
        if r['pred_activity'] != '躺下':
            print(f'  漏报: {r["video"]}_{r["frame"]} -> {r["pred_activity"]}')

    upright = [r for r in rows if r['gt'] in ('坐着', '站立', '行走')]
    upright_fp = [r for r in upright if r['pred_activity'] == '躺下']
    print(f'直立误报(被判躺下): {len(upright_fp)}/{len(upright)} = {len(upright_fp) / max(len(upright), 1):.0%}')

    absent = [r for r in rows if r['gt'] == '无人']
    absent_hit = [r for r in absent if not r['pred_present']]
    print(f'无人召回: {len(absent_hit)}/{len(absent)} = {len(absent_hit) / max(len(absent), 1):.0%}')

    # 风险分布（躺下帧里 高/中/低 各多少，反映位置/时间特征的作用）
    lying_risk = {k: sum(1 for r in lying if r['risk'] == k) for k in ('高', '中', '低')}
    print(f'躺下帧风险分布: {lying_risk}')

    metrics = {
        'activity_acc': act_correct / max(total, 1),
        'lying_recall': len(lying_hit) / max(len(lying), 1),
        'upright_fp': len(upright_fp) / max(len(upright), 1),
        'absent_recall': len(absent_hit) / max(len(absent), 1),
        'lying_risk_dist': lying_risk,
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump({'rows': rows, 'metrics': metrics}, f, ensure_ascii=False, indent=2)
    print(f'\n[结果已保存] {args.out}')


if __name__ == '__main__':
    main()
