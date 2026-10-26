# -*- coding: utf-8 -*-
"""统一配置读取。

优先级：ROS 参数（--ros-args -p 名字:=值） > config.yaml > 代码内默认值。

节点里的标准用法：

    from app_config import section
    cfg = section('risk', {'watch_sec': 10.0, 'alert_sec': 20.0})
    self.declare_parameter('watch_sec', float(cfg['watch_sec']))

把配置文件的值当作 declare_parameter 的默认值，命令行显式传参依然能覆盖它，
所以「配置化」不会削弱原来的调参能力。

配置文件在进程启动时读一次并缓存，改完需要重启节点生效。
"""
import os

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(ROOT, 'config.yaml')

_cache = {}


def load(path=None):
    """读取整个 config.yaml，返回 dict；文件不存在或格式错误时返回空 dict。"""
    p = path or os.environ.get('MONITOR_CONFIG') or DEFAULT_PATH
    p = os.path.abspath(p)
    if p in _cache:
        return _cache[p]

    data = {}
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f'[config] 读取 {p} 失败，全部使用代码默认值：{e}')
            data = {}
    if not isinstance(data, dict):
        data = {}

    _cache[p] = data
    return data


def section(name, defaults=None):
    """取某个配置段并与代码默认值合并（配置文件里的值优先，None 视为未配置）。"""
    out = dict(defaults or {})
    cfg = load().get(name) or {}
    if isinstance(cfg, dict):
        out.update({k: v for k, v in cfg.items() if v is not None})
    return out


def get(name, key, default=None):
    """取单个值，带默认值兜底：get('risk', 'cooldown_sec', 300.0)。"""
    cfg = load().get(name) or {}
    if isinstance(cfg, dict) and cfg.get(key) is not None:
        return cfg[key]
    return default


def config_path():
    """返回当前实际使用的配置文件路径（用于启动日志）。"""
    return os.path.abspath(os.environ.get('MONITOR_CONFIG') or DEFAULT_PATH)
