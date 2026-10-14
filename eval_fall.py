# -*- coding: utf-8 -*-
"""跌倒/状态分类评估脚本：把 16 段真实视频跑一遍 llava，对照人工标注算指标。

用法（在 ROS2 环境下）：
  python eval_fall.py            # 全量 16 段 x 2 帧 = 32 帧
  python eval_fall.py --limit 4  # 只跑前 4 段（快速冒烟）
  python eval_fall.py --out result.json
"""
import argparse
import glob
import json
import os
import sys
import time

import cv2

sys.path.insert(0, r'E:\ros_vision')
from ollama_analyzer import OllamaAnalyzer

VIDEO_DIR = r'E:\ros_vision\test_videos'
LABELS_LATE = json.load(open(r'E:\ros_frames\labels.json', encoding='utf-8'))
LABELS_EARLY = json.load(open(r'E:\ros_frames\labels_early.json', encoding='utf-8'))

# 用于指标归类的“活动”标签
ACTIVITIES = ('坐着', '站立', '行走', '躺下', '其他', '无人')


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
    ap.add_argument('--out', type=str, default=r'E:\ros_vision\eval_result.json')
    ap.add_argument('--warmup', type=int, default=1)
    args = ap.parse_args()

    analyzer = OllamaAnalyzer(model='llava:7b')

    # 预热：首次调用会加载模型（~10s），不计入指标
    if args.warmup:
        warm = cv2.imread(r'E:\ros_vision\test_room.jpg')
        if warm is None:
            warm = load_frame(os.path.join(VIDEO_DIR, '01.mp4'), 0.5)
        if warm is not None:
            t0 = time.time()
            analyzer.analyze(warm)
            print(f'[预热] 模型加载 {time.time() - t0:.1f}s', flush=True)

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
                print(f'[跳过] {name}_{tag} 无法读取帧')
                continue
            t0 = time.time()
            r = analyzer.analyze(frame)
            cost = time.time() - t0
            rows.append({
                'video': name, 'frame': tag, 'gt': gt,
                'pred_present': r.get('person_present'),
                'pred_activity': r.get('activity'),
                'pred_location': r.get('location'),
                'pred_expression': r.get('expression'),
                'pred_body': r.get('body_state'),
                'pred_risk': r.get('possible_risk'),
                'followup': r.get('need_followup'),
                'confidence': r.get('confidence'),
                'cost': round(cost, 1),
                'explanation': r.get('explanation', ''),
            })
            print(f'[{name}_{tag}] gt={gt} -> 活动={r.get("activity")} 位置={r.get("location")} '
                  f'风险={r.get("possible_risk")} 置信={r.get("confidence")} ({cost:.0f}s)',
                  flush=True)

    # ===== 指标计算 =====
    def stats(title, cond, show_rows=True):
        sel = [r for r in rows if cond(r)]
        if show_rows:
            print(f'\n===== {title}（{len(sel)} 帧）=====')
            for r in sel:
                print(f'  {r["video"]}_{r["frame"]}: gt={r["gt"]} -> '
                      f'活动={r["pred_activity"]} 位置={r["pred_location"]} 风险={r["pred_risk"]} | {r["explanation"]}')
        return sel

    total = len(rows)
    # 1) 活动分类准确率（不含“无人”因为 activity 与 person_present 分离）
    act_correct = sum(1 for r in rows if r['pred_activity'] == r['gt'])
    print(f'\n========== 评估汇总（{total} 帧）==========')
    print(f'活动标签一致率: {act_correct}/{total} = {act_correct / total:.0%}')

    # 2) 躺下召回（跌倒检测最关键）
    lying = [r for r in rows if r['gt'] == '躺下']
    lying_hit = [r for r in lying if r['pred_activity'] == '躺下']
    print(f'躺下召回: {len(lying_hit)}/{len(lying)} = {len(lying_hit) / max(len(lying), 1):.0%}')
    print(f'  躺下帧的位置分布: ' + json.dumps({l: sum(1 for r in lying if r['pred_location'] == l) for l in ('地面', '床', '沙发', '椅子', '未知')}, ensure_ascii=False))
    print(f'  躺下帧被判高风险: {sum(1 for r in lying if r["pred_risk"] == "高")}/{len(lying)}')

    # 3) 直立（坐着/行走/站立）误报控制：不应被判成“躺下”
    upright = [r for r in rows if r['gt'] in ('坐着', '站立', '行走')]
    upright_fp = [r for r in upright if r['pred_activity'] == '躺下']
    print(f'直立误报(被判躺下): {len(upright_fp)}/{len(upright)} = {len(upright_fp) / max(len(upright), 1):.0%}')

    # 4) 无人召回
    absent = [r for r in rows if r['gt'] == '无人']
    absent_hit = [r for r in absent if r['pred_present'] is False]
    print(f'无人召回(person_present=false): {len(absent_hit)}/{len(absent)} = {len(absent_hit) / max(len(absent), 1):.0%}')

    # 5) 详细逐帧（按类型）
    stats('躺下帧明细', lambda r: r['gt'] == '躺下')
    stats('直立帧明细', lambda r: r['gt'] in ('坐着', '站立', '行走'))
    stats('无人帧明细', lambda r: r['gt'] == '无人')

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump({'rows': rows, 'metrics': {
            'activity_acc': act_correct / max(total, 1),
            'lying_recall': len(lying_hit) / max(len(lying), 1),
            'upright_fp': len(upright_fp) / max(len(upright), 1),
            'absent_recall': len(absent_hit) / max(len(absent), 1),
        }}, f, ensure_ascii=False, indent=2)
    print(f'\n[结果已保存] {args.out}')


if __name__ == '__main__':
    main()
