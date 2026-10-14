# -*- coding: utf-8 -*-
"""llava:7b 并发基准：实测 1/2/3 路并发下单帧耗时，摸清本机并发上限。

结论决定：二次审核智能体能不能用 llava、以及高帧率下资源够不够。
"""
import os
import sys
import time
import threading
import cv2

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import ollama

HOST = 'http://localhost:11434'
MODEL = 'llava:7b'
PROMPT = '这个监控画面里老人是什么姿势？只回答一个词：站立/坐着/行走/躺下/无人'
OUT_DIR = r'E:\ros_vision\frames'
NUM_PREDICT = 32


def prepare_frames():
    os.makedirs(OUT_DIR, exist_ok=True)
    cap = cv2.VideoCapture(r'E:\ros_vision\test_videos\05.mp4')
    paths = []
    for idx in (30, 60, 120):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, f = cap.read()
        if ok:
            p = os.path.join(OUT_DIR, f'bench_{idx}.jpg')
            cv2.imwrite(p, f)
            paths.append(p)
    cap.release()
    return paths


def mem_available_gb():
    try:
        import psutil
        return psutil.virtual_memory().available / 1024**3
    except Exception:
        return -1


def call_once(image_path, timeout=360):
    client = ollama.Client(host=HOST, timeout=timeout)
    t0 = time.time()
    resp = client.chat(
        model=MODEL,
        messages=[{'role': 'user', 'content': PROMPT, 'images': [image_path]}],
        stream=False,
        options={'temperature': 0.0, 'num_predict': NUM_PREDICT},
    )
    dt = time.time() - t0
    return dt, resp['message']['content'].strip()


def run_parallel(paths, n):
    results = {}
    lock = threading.Lock()

    def worker(i):
        dt, ans = call_once(paths[i])
        with lock:
            results[i] = (dt, ans)

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    total = time.time() - t0
    return total, results


def main():
    paths = prepare_frames()
    print(f'测试图片: {[os.path.basename(p) for p in paths]}')
    print(f'初始可用内存: {mem_available_gb():.2f} GB\n')

    # 1 路基线（含模型加载/预热）
    print('=== 1 路（基线/预热）===')
    dt, ans = call_once(paths[0])
    print(f'  耗时 {dt:.1f}s -> {ans}')
    print(f'  可用内存: {mem_available_gb():.2f} GB\n')

    # 2 路并发
    print('=== 2 路并发 ===')
    total, res = run_parallel(paths, 2)
    for i in sorted(res):
        print(f'  路{i+1}: {res[i][0]:.1f}s -> {res[i][1]}')
    print(f'  总耗时 {total:.1f}s（若≈1路耗时则并发有效，若≈2倍则CPU瓶颈）')
    print(f'  可用内存: {mem_available_gb():.2f} GB\n')

    time.sleep(5)

    # 3 路并发
    print('=== 3 路并发 ===')
    total, res = run_parallel(paths, 3)
    for i in sorted(res):
        print(f'  路{i+1}: {res[i][0]:.1f}s -> {res[i][1]}')
    print(f'  总耗时 {total:.1f}s')
    print(f'  可用内存: {mem_available_gb():.2f} GB\n')


if __name__ == '__main__':
    main()
