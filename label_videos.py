# -*- coding: utf-8 -*-
"""用 llava 给每段视频的 late 帧打标签，结果写入 UTF-8 文件"""
import glob
import json
import os
import ollama

frames = sorted(glob.glob(r'E:\ros_frames\*_late.jpg'))
client = ollama.Client(host='http://localhost:11434', timeout=120)

prompt = ('这个监控画面里的人是什么姿势？'
          '只从下面选一个词回答，不要解释：站立、坐着、行走、躺下、无人')

results = {}
for f in frames:
    name = os.path.basename(f).replace('_late.jpg', '')
    try:
        resp = client.chat(
            model='llava:7b',
            messages=[{'role': 'user', 'content': prompt, 'images': [f]}],
            stream=False,
            options={'temperature': 0.0, 'num_predict': 16},
        )
        results[name] = resp['message']['content'].strip()
    except Exception as e:
        results[name] = f'ERROR: {e}'

out = r'E:\ros_frames\labels.json'
with open(out, 'w', encoding='utf-8') as fp:
    json.dump(results, fp, ensure_ascii=False, indent=2)
print('done ->', out)
