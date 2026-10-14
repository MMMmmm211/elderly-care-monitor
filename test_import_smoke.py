# -*- coding: utf-8 -*-
"""冒烟：验证 vision_analysis_node 在 ROS 环境下可导入，hybrid 依赖链完整。"""
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import vision_analysis_node
print("vision_analysis_node 导入 OK")
print("VisionAnalysisNode:", vision_analysis_node.VisionAnalysisNode.__name__)
