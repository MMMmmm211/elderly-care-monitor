"""
Ollama 视觉分析封装（阶段 C 增强版 · 多维判定）
设计原则：
- 感知与决策分离：llava 只负责「看到什么」（姿态/位置/表情/肢体），
  风险等级由确定性规则从这些结构化观察里推导，避免 llava 对「风险」这种
  抽象判断的不可靠校准。
- 控制 token：新增字段都用 1~2 字短枚举值，图像仍降采样到 max_side，num_predict 不变。
"""
import json
import os
import re
import time
import tempfile
from functools import wraps
from typing import Literal, Optional

import cv2
import ollama
from pydantic import BaseModel, Field


# ========== 1. 强约束输出结构（感知层） ==========
class SceneAnalysisResult(BaseModel):
    person_present: bool
    activity: Literal["坐着", "站立", "行走", "躺下", "其他"]
    location: str = ""      # 老人所在/接触表面（地面/床/沙发/椅子/其他/未知）
    expression: str = ""    # 面部表情（平静/痛苦/看不清）
    body_state: str = ""    # 肢体状态（放松/挣扎/蜷缩/静止/未知）
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = ""


# 分析彻底失败时的保守兜底（宁可升级关注，不可漏报）
_FAILSAFE_RESULT = {
    "person_present": True,
    "activity": "其他",
    "location": "未知",
    "expression": "看不清",
    "body_state": "未知",
    "possible_risk": "高",
    "need_followup": True,
    "confidence": 0.0,
    "explanation": "视觉分析失败，保守判定为高风险",
}


SYSTEM_PROMPT = (
    "你是养老监护视觉分析助手，观察监控画面中老人的身体姿态与状态。"
    "只输出一个JSON对象，字段："
    '{"person_present":true/false,'
    ' "activity":"坐着/站立/行走/躺下/其他",'
    ' "location":"老人接触的表面：地面/床/沙发/椅子/其他/未知",'
    ' "expression":"面部表情：平静/痛苦/看不清",'
    ' "body_state":"肢体状态：放松/挣扎/蜷缩/静止/未知",'
    ' "confidence":0到1小数,'
    ' "explanation":"简短判断依据"}'
    "person_present：画面中能看到老人填true，看不到任何人填false。"
    "注意区分躺下与坐着：躺下时身体接近水平，坐着时躯干竖直。"
    "特别注意：老人躺下时，务必判断是在床/沙发上（可能只是休息）还是在地面上（可能是跌倒）。"
)


# ========== 2. 重试装饰器 ==========
def retry(max_retries=3, delay=1.5):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last = None
            for i in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last = e
                    if i < max_retries - 1:
                        time.sleep(delay)
            raise last
        return wrapper
    return decorator


# ========== 3. 归一化：把 llava 的自由文本映射到短枚举 ==========
def _norm_loc(s: str) -> str:
    if "沙发" in s:
        return "沙发"
    if "床" in s:
        return "床"
    if "地面" in s or "地板" in s or "地上" in s:
        return "地面"
    if "椅" in s or "凳" in s:
        return "椅子"
    return "未知"


def _norm_expr(s: str) -> str:
    if any(k in s for k in ("痛苦", "难受", "皱眉", "紧闭", "狰狞")):
        return "痛苦"
    if any(k in s for k in ("平静", "放松", "正常", "安详")):
        return "平静"
    return "看不清"


def _norm_body(s: str) -> str:
    if any(k in s for k in ("挣扎", "抽搐", "颤抖")):
        return "挣扎"
    if any(k in s for k in ("蜷缩", "蜷", "抱", "捂")):
        return "蜷缩"
    if any(k in s for k in ("静止", "不动", "无动作")):
        return "静止"
    if any(k in s for k in ("放松", "自然", "舒展")):
        return "放松"
    return "未知"


# ========== 4. 分析器 ==========
class OllamaAnalyzer:
    def __init__(
        self,
        model: str = "llava:7b",
        host: str = "http://localhost:11434",
        max_side: int = 640,          # 图像最长边，越小越快越省 token
        jpeg_quality: int = 80,        # 压缩质量
        num_predict: int = 256,        # 最大输出 token（JSON 输出很短，够用）
        timeout: int = 300,
        fallback_model: Optional[str] = None,   # 主模型失败时的备用模型（暂留扩展）
        system_prompt: Optional[str] = None,    # 覆盖默认 SYSTEM_PROMPT（供混合分析器聚焦子任务）
    ):
        self.model = model
        self.fallback_model = fallback_model
        self.max_side = max_side
        self.jpeg_quality = jpeg_quality
        self.num_predict = num_predict
        self.system_prompt = system_prompt
        self.client = ollama.Client(host=host, timeout=timeout)
        self._tmp = os.path.join(tempfile.gettempdir(), "ros_vision_frame.jpg")

    def _preprocess(self, img_bgr):
        """降采样：减少视觉 token、加快编码、省显存"""
        h, w = img_bgr.shape[:2]
        scale = self.max_side / max(h, w)
        if scale < 1.0:
            img_bgr = cv2.resize(
                img_bgr, (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )
        return img_bgr

    def _parse(self, raw: dict) -> dict:
        """宽容解析：单字段坏值不拖垮整帧，回退默认值。"""
        try:
            return SceneAnalysisResult(**raw).model_dump()
        except Exception:
            act = raw.get("activity", "其他")
            if act not in ("坐着", "站立", "行走", "躺下", "其他"):
                act = "其他"
            try:
                conf = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            conf = max(0.0, min(1.0, conf))
            return {
                "person_present": bool(raw.get("person_present", True)),
                "activity": act,
                "location": str(raw.get("location", "")),
                "expression": str(raw.get("expression", "")),
                "body_state": str(raw.get("body_state", "")),
                "confidence": conf,
                "explanation": str(raw.get("explanation", "")),
            }

    def _decide(self, obs: dict) -> dict:
        """决策层：由结构化观察推导风险 + 跟进，区分「正常休息」与「跌倒/急症」。

        核心规则：
          躺下+地面              -> 高（跌倒）
          躺下+床/沙发+平静/放松  -> 低（正常休息，不告警）
          躺下+床/沙发+痛苦/挣扎  -> 高（突发病症）
          躺下+位置未知          -> 中（关注复核）
          直立姿态+痛苦/蜷缩     -> 中（心梗等前兆）
        """
        act = obs.get("activity", "其他")
        loc = _norm_loc(str(obs.get("location", "")))
        expr = _norm_expr(str(obs.get("expression", "")))
        body = _norm_body(str(obs.get("body_state", "")))
        distress = (expr == "痛苦") or (body in ("挣扎", "蜷缩"))

        risk, followup = "低", False
        if act == "躺下":
            if loc == "地面":
                risk, followup = "高", True
            elif loc in ("床", "沙发"):
                risk, followup = ("高", True) if distress else ("低", False)
            else:  # 未知/其他/椅子
                risk, followup = "中", True
        elif act in ("坐着", "站立", "行走"):
            risk, followup = ("中", True) if distress else ("低", False)
        else:  # 其他
            risk, followup = "中", True

        obs["location"] = loc
        obs["expression"] = expr
        obs["body_state"] = body
        obs["possible_risk"] = risk
        obs["need_followup"] = followup
        return obs

    @retry(max_retries=3, delay=1.5)
    def _analyze_raw(self, img_bgr, model: str) -> dict:
        img = self._preprocess(img_bgr)
        cv2.imwrite(self._tmp, img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])

        response = self.client.chat(
            model=model,
            messages=[
                {"role": "system", "content": self.system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": "分析这张监控画面", "images": [self._tmp]},
            ],
            stream=False,
            format="json",   # 若某些 llava 版本不支持，可去掉这行，靠 prompt+Pydantic 兜底
            options={"temperature": 0.0, "num_predict": self.num_predict},
        )

        raw = self._extract_json(response["message"]["content"])
        return self._parse(raw)

    @staticmethod
    def _extract_json(content: str) -> dict:
        """宽容提取 JSON：llava 偶尔会在 JSON 外多输出前言/后缀，
        或生成被截断的响应。先剥 markdown 围栏，再截取首个 { 到最后一个 }。"""
        content = content.strip()
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.IGNORECASE).strip()
        try:
            return json.loads(content)
        except Exception:
            s = content.find('{')
            e = content.rfind('}')
            if s != -1 and e != -1 and e > s:
                return json.loads(content[s:e + 1])
            raise

    def analyze(self, img_bgr) -> dict:
        """输入 BGR numpy 图，返回「感知+决策」后的结构化 dict。

        鲁棒性：主模型失败 -> 尝试备用模型 -> 仍失败则返回保守兜底。
        """
        try:
            obs = self._analyze_raw(img_bgr, self.model)
        except Exception:
            if self.fallback_model and self.fallback_model != self.model:
                try:
                    obs = self._analyze_raw(img_bgr, self.fallback_model)
                except Exception:
                    return dict(_FAILSAFE_RESULT)
            else:
                return dict(_FAILSAFE_RESULT)

        return self._decide(obs)
