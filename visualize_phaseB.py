# -*- coding: utf-8 -*-
"""
阶段 B 端到端结果可视化：
  1) 读取 recorder_node.py 生成的 e2e_log.jsonl
  2) 输出静态时间线 PNG（matplotlib）
  3) 输出自包含交互式 HTML（纯 SVG，无需联网/CDN，可 hover 查看依据）

用法：python visualize_phaseB.py
"""
import json
import os
import sys
from collections import Counter

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

LOG = r'E:\ros_vision\e2e_log.jsonl'
PNG = r'E:\ros_vision\phaseB_timeline.png'
HTML = r'E:\ros_vision\phaseB_report.html'

ACT = {'站立': 0, '坐着': 1, '行走': 2, '躺下': 3, '其他': 4, '无人': 5}
ACT_LABEL = {v: k for k, v in ACT.items()}
RISK = {'无': 0, '低': 1, '中': 2, '高': 3}
RISK_LABEL = {v: k for k, v in RISK.items()}
STATE = {'normal': 0, 'watch': 1, 'alert': 2, 'absent': 3}
STATE_LABEL = {v: k for k, v in STATE.items()}
STATE_CN = {'normal': '正常', 'watch': '关注', 'alert': '告警', 'absent': '无人'}


def load():
    rows = []
    if not os.path.exists(LOG):
        print('找不到日志:', LOG)
        sys.exit(1)
    with open(LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def pick_font():
    for name in ['Microsoft YaHei', 'SimHei', 'SimSun', 'Noto Sans CJK SC', 'PingFang SC']:
        for p in font_manager.findSystemFonts():
            if name.lower() in os.path.basename(p).lower():
                return p
    return None


def build_timeline(rows):
    """从原始事件重建时间线。返回 (times, act_y, risk_y, state_y, alerts, infos)"""
    analysis = [r for r in rows if r['kind'] == 'analysis']
    alerts = [r for r in rows if r['kind'] == 'alert']
    states = [r for r in rows if r['kind'] == 'state']

    times, act_y, risk_y = [], [], []
    for r in analysis:
        d = r['data']
        times.append(r['t'])
        act_y.append(ACT.get(d.get('activity'), 4))
        risk_y.append(RISK.get(d.get('possible_risk'), 0))

    # 状态线：从 NORMAL 出发，按 state/alert 事件推进（fall 告警的 state 字段即目标状态）
    state_y = [0] * len(times)
    if times:
        # 先按时间排序事件，映射到最近的 analysis 采样点
        evs = states + alerts
        evs = sorted(evs, key=lambda x: x['t'])
        # 简单实现：每个 analysis 点取该时刻已发生的最后一个事件状态
        for i, t in enumerate(times):
            cur = 0
            for e in evs:
                if e['t'] <= t + 1e-9:
                    cur = STATE.get(e['data'].get('state', 'normal'), 0)
                else:
                    break
            state_y[i] = cur

    return times, act_y, risk_y, state_y, alerts, analysis


def gen_png(times, act_y, risk_y, state_y, alerts):
    fp = pick_font()
    if fp:
        font_manager.fontManager.addfont(fp)
        prop = font_manager.FontProperties(fname=fp)
        plt.rcParams['font.family'] = prop.get_name()
    plt.rcParams['axes.unicode_minus'] = False

    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True,
                             gridspec_kw={'height_ratios': [1.2, 1, 1, 1]})
    fig.suptitle('阶段B 端到端监控时间线（真实 ROS2 话题数据）', fontsize=14, fontweight='bold')

    if not times:
        for ax in axes:
            ax.text(0.5, 0.5, '无数据', ha='center', va='center')
        fig.savefig(PNG, dpi=110, bbox_inches='tight')
        print('done ->', PNG)
        return

    # 1) 活动
    ax = axes[0]
    ax.step(times, act_y, where='post', color='#2c7fb8', lw=2, label='activity')
    ax.scatter(times, act_y, color='#2c7fb8', s=40, zorder=3)
    ax.set_yticks(list(ACT_LABEL.keys()))
    ax.set_yticklabels([ACT_LABEL[i] for i in sorted(ACT_LABEL)])
    ax.set_ylabel('活动姿势')
    ax.grid(alpha=0.3)
    ax.legend(loc='upper right')

    # 2) 风险等级
    ax = axes[1]
    ax.step(times, risk_y, where='post', color='#f0a202', lw=2, label='risk')
    ax.scatter(times, risk_y, color='#f0a202', s=40, zorder=3)
    ax.set_yticks(list(RISK_LABEL.keys()))
    ax.set_yticklabels([RISK_LABEL[i] for i in sorted(RISK_LABEL)])
    ax.set_ylabel('风险等级')
    ax.grid(alpha=0.3)
    ax.legend(loc='upper right')

    # 3) 状态机状态
    ax = axes[2]
    ax.step(times, state_y, where='post', color='#d9534f', lw=2, label='state')
    ax.scatter(times, state_y, color='#d9534f', s=40, zorder=3)
    ax.set_yticks(list(STATE_LABEL.keys()))
    ax.set_yticklabels([STATE_CN[STATE_LABEL[i]] for i in sorted(STATE_LABEL)])
    ax.set_ylabel('状态机')
    ax.grid(alpha=0.3)
    ax.legend(loc='upper right')

    # 4) 告警标记
    ax = axes[3]
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_ylabel('告警事件')
    for a in alerts:
        ax.axvline(a['t'], color='#d9534f', ls='--', alpha=0.7, lw=1.5)
        ax.plot(a['t'], 0.5, 'r*', markersize=16)
        ax.text(a['t'], 0.8, a['data'].get('alert_type', ''), ha='center',
                fontsize=9, color='#a00', fontweight='bold')
    ax.set_xlabel('时间 (秒，相对记录器启动)')
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(PNG, dpi=110, bbox_inches='tight')
    plt.close(fig)
    print('done ->', PNG)


def _svg_polyline(pts, w, h, color, width=2):
    if not pts:
        return ''
    d = 'M ' + ' L '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
    return f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{color}" stroke-width="{width}"/>'


def gen_html(times, act_y, risk_y, state_y, alerts, analysis):
    # 布局常量
    W, H = 1000, 560
    left, right = 70, 980
    pad_t, pad_b = 30, 40
    plot_h = H - pad_t - pad_b
    band = plot_h / 3.0

    def tx(t):
        if not times:
            return left
        tmax = max(times) or 1.0
        return left + (t / tmax) * (right - left)

    def ty(level, nlevels, offset, band_h):
        # level: 0..nlevels-1, 顶部在上
        if nlevels <= 1:
            return pad_t + offset + band_h / 2
        return pad_t + offset + (level / (nlevels - 1)) * (band_h - 20)

    act_pts = [(tx(t), ty(v, 6, 0, band)) for t, v in zip(times, act_y)]
    risk_pts = [(tx(t), ty(v, 4, band, band)) for t, v in zip(times, risk_y)]
    state_pts = [(tx(t), ty(v, 4, 2 * band, band)) for t, v in zip(times, state_y)]

    # 网格线 + 轴标签
    rows_html = []
    for i in range(6):
        y = pad_t + i * (band - 20) / 5
        rows_html.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#eee"/>')
    for i in range(4):
        y = pad_t + band + i * (band - 20) / 3
        rows_html.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#eee"/>')
    for i in range(4):
        y = pad_t + 2 * band + i * (band - 20) / 3
        rows_html.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#eee"/>')

    alert_marks = ''
    for a in alerts:
        x = tx(a['t'])
        alert_marks += (f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{H-pad_b}" '
                        f'stroke="#d9534f" stroke-dasharray="4,3" stroke-width="1.5"/>')
        alert_marks += (f'<text x="{x:.1f}" y="{pad_t-8}" text-anchor="middle" fill="#c00" '
                        f'font-size="11" font-weight="bold">{a["data"].get("alert_type","")}</text>')

    # hover 点
    dots = ''
    for t, v in zip(times, act_y):
        dots += f'<circle cx="{tx(t):.1f}" cy="{ty(v,6,0,band):.1f}" r="4" fill="#2c7fb8"><title>活动: {ACT_LABEL.get(v)} @ {t}s</title></circle>'
    for t, v in zip(times, risk_y):
        dots += f'<circle cx="{tx(t):.1f}" cy="{ty(v,4,band,band):.1f}" r="4" fill="#f0a202"><title>风险: {RISK_LABEL.get(v)} @ {t}s</title></circle>'
    for t, v in zip(times, state_y):
        dots += f'<circle cx="{tx(t):.1f}" cy="{ty(v,4,2*band,band):.1f}" r="4" fill="#d9534f"><title>状态: {STATE_CN.get(STATE_LABEL.get(v))} @ {t}s</title></circle>'

    # 数据表 + 统计
    act_counter = Counter(a.get('data', {}).get('activity', '?') for a in analysis)
    risk_counter = Counter(a.get('data', {}).get('possible_risk', '?') for a in analysis)
    total = len(analysis)
    lying = act_counter.get('躺下', 0)

    table_rows = ''
    for a in analysis:
        d = a['data']
        table_rows += (f'<tr><td>{a["t"]}s</td><td>{d.get("activity","")}</td>'
                       f'<td>{d.get("location","")}</td><td>{d.get("expression","")}</td>'
                       f'<td>{d.get("body_state","")}</td><td>{d.get("possible_risk","")}</td>'
                       f'<td>{d.get("confidence","")}</td><td>{d.get("need_followup","")}</td>'
                       f'<td>{d.get("explanation","")}</td></tr>')

    stat_html = (
        f'<p>分析次数 <b>{total}</b> · 躺下 <b>{lying}</b> 次'
        f'（{100*lying/total if total else 0:.0f}%） · 活动分布 {dict(act_counter)} · '
        f'风险分布 {dict(risk_counter)}</p>'
    )

    html = f'''<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>阶段B 端到端监控时间线</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#222;background:#fafafa}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px}}
.card{{background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:16px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #e3e3e3;padding:6px 8px;text-align:left}}
th{{background:#f4f4f4}}
.muted{{color:#888}} .alert{{color:#c00;font-weight:bold}}
</style></head><body>
<h1>阶段B 端到端监控时间线（真实 ROS2 话题数据）</h1>
{stat_html}
<div class="card">
<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>
<text x="{left}" y="{pad_t-14}" font-size="12" fill="#2c7fb8" font-weight="bold">活动姿势</text>
<text x="{left}" y="{pad_t+band-14}" font-size="12" fill="#f0a202" font-weight="bold">风险等级</text>
<text x="{left}" y="{pad_t+2*band-14}" font-size="12" fill="#d9534f" font-weight="bold">状态机</text>
{''.join(rows_html)}
{_svg_polyline(act_pts, W, H, '#2c7fb8')}
{_svg_polyline(risk_pts, W, H, '#f0a202')}
{_svg_polyline(state_pts, W, H, '#d9534f')}
{alert_marks}
{dots}
<line x1="{left}" y1="{H-pad_b}" x2="{right}" y2="{H-pad_b}" stroke="#999"/>
<text x="{right}" y="{H-pad_b+18}" text-anchor="end" font-size="11" fill="#888">时间(秒)</text>
</svg>
</div>
<h2>逐帧分析明细</h2>
<div class="card"><table>
<tr><th>时间</th><th>活动</th><th>位置</th><th>表情</th><th>肢体</th><th>风险</th><th>置信度</th><th>需跟进</th><th>判断依据</th></tr>
{table_rows}
</table></div>
</body></html>'''

    with open(HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print('done ->', HTML)


def main():
    rows = load()
    times, act_y, risk_y, state_y, alerts, analysis = build_timeline(rows)
    summary = next((r['data'] for r in rows if r['kind'] == 'summary'), {})
    print('=' * 60)
    print('采集统计:', summary)
    print('分析次数:', len(analysis), '告警次数:', len(alerts))
    for a in alerts:
        print('  告警:', a['data'])
    print('=' * 60)
    gen_png(times, act_y, risk_y, state_y, alerts)
    gen_html(times, act_y, risk_y, state_y, alerts, analysis)


if __name__ == '__main__':
    main()
