"""OCR + AI：RapidOCR 本地识别，OpenAI 兼容接口流式作答。"""
import time
from typing import Callable, Optional

import numpy as np

# ⚠️ 必须在 PyQt5 之前 import（onnxruntime 与 Qt 的 DLL 加载顺序敏感）
from rapidocr_onnxruntime import RapidOCR


class OcrEngine:
    def __init__(self):
        self.ocr = RapidOCR()
        # 预热，避免第一次卡 6 秒
        warm = np.full((60, 200, 3), 255, dtype=np.uint8)
        try:
            self.ocr(warm)
        except Exception:
            pass

    def recognize(self, pil_image):
        import numpy as np
        arr = np.array(pil_image.convert("RGB"))
        t0 = time.time()
        result, _ = self.ocr(arr)
        cost = time.time() - t0
        if not result:
            return "", 0.0, cost
        texts, confs = [], []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                try:
                    score = float(item[2])
                except Exception:
                    continue
                if score > 0.25:
                    texts.append(str(item[1]))
                    confs.append(score)
        text = "\n".join(texts).strip()
        avg = sum(confs) / len(confs) if confs else 0.0
        return text, avg, cost


class AiClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_usage = {}

    def _client(self):
        from openai import OpenAI
        return OpenAI(
            api_key=self.cfg.get("api_key") or "ollama",
            base_url=self.cfg.get("base_url"),
            timeout=90.0,
            max_retries=1,
        )

    def test_connection(self):
        """真实调一次，返回 (ok, 说明, 秒)。"""
        t0 = time.time()
        try:
            c = self._client()
            r = c.chat.completions.create(
                model=self.cfg.get("model"),
                messages=[{"role": "user", "content": "只回复两个字：可用"}],
                max_tokens=8,
                temperature=0,
            )
            cost = time.time() - t0
            txt = (r.choices[0].message.content or "").strip()
            return True, f"连接成功 · 模型回：{txt}", cost
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", time.time() - t0


    def solve_image(self, pil_image, on_delta=None, on_state=None):
        """直接把截图喂给视觉模型（公式/图表 OCR 乱了时用）。"""
        import base64
        import io as _io
        cfg = self.cfg
        img = pil_image.convert("RGB")
        w, h = img.size
        scale = min(1.0, 1600.0 / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        data_url = "data:image/jpeg;base64," + b64

        prompt = (cfg.get("prompt") or "").strip()
        t0 = time.time()
        try:
            c = self._client()
            kw = dict(
                model=cfg.get("vision_model") or cfg.get("model"),
                messages=[
                    {"role": "system", "content": "你是严谨的解题助手。直接看图片里的题目作答。"},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]},
                ],
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 900)),
                stream=True,
            )
            try:
                stream = c.chat.completions.create(
                    stream_options={"include_usage": True}, **kw)
            except Exception:
                stream = c.chat.completions.create(**kw)
            self.last_usage = {}
            buf2 = []
            for chunk in stream:
                try:
                    u = getattr(chunk, "usage", None)
                    if u is not None:
                        self.last_usage = {
                            "in": int(getattr(u, "prompt_tokens", 0) or 0),
                            "out": int(getattr(u, "completion_tokens", 0) or 0),
                            "total": int(getattr(u, "total_tokens", 0) or 0),
                        }
                except Exception:
                    pass
                try:
                    delta = chunk.choices[0].delta.content or ""
                except Exception:
                    delta = ""
                if delta:
                    buf2.append(delta)
                    if on_delta:
                        on_delta(delta)
            return "".join(buf2).strip(), time.time() - t0
        except Exception as e:
            if on_state:
                on_state(f"图片作答失败：{type(e).__name__}: {e}")
            raise

    def solve(self, question_text: str,
              on_delta: Optional[Callable[[str], None]] = None,
              on_state: Optional[Callable[[str], None]] = None):
        """流式求解。返回 (完整文本, 秒)。"""
        cfg = self.cfg
        prompt = (cfg.get("prompt") or "").strip() + "\n\n—— 识别到的题目文字 ——\n" + question_text
        t0 = time.time()
        try:
            c = self._client()
            kw = dict(
                model=cfg.get("model"),
                messages=[
                    {"role": "system", "content": "你是严谨的解题助手。严格按格式输出：第一行「答案：」，第二行起「解析：」；不要复述题干，不要罗列全部选项（选择题只给正确选项的字母）。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 900)),
                stream=True,
            )
            try:
                stream = c.chat.completions.create(
                    stream_options={"include_usage": True}, **kw)
            except Exception:
                stream = c.chat.completions.create(**kw)
            self.last_usage = {}
            buf = []
            for chunk in stream:
                try:
                    u = getattr(chunk, "usage", None)
                    if u is not None:
                        self.last_usage = {
                            "in": int(getattr(u, "prompt_tokens", 0) or 0),
                            "out": int(getattr(u, "completion_tokens", 0) or 0),
                            "total": int(getattr(u, "total_tokens", 0) or 0),
                        }
                except Exception:
                    pass
                try:
                    delta = chunk.choices[0].delta.content or ""
                except Exception:
                    delta = ""
                if delta:
                    buf.append(delta)
                    if on_delta:
                        on_delta(delta)
            return "".join(buf).strip(), time.time() - t0
        except Exception as e:
            if on_state:
                on_state(f"出错：{type(e).__name__}: {e}")
            raise

    def grade_answer(self, question, reference, mine):
        """给填空/简答判分。返回 {score(0-100), verdict, comment} 或 None。"""
        import json as _json
        import re as _re
        cfg = self.cfg
        sys_p = (
            "你是严格的阅卷老师。对比学生答案和参考答案，给 0-100 的得分。\n"
            "规则：允许等价表述（近义词、单位换算、分数与小数、顺序不同、多写不扣分）；\n"
            "完全正确=100；核心对但有漏/多余=60-90；方向对但不完整=30-59；错误或空白=0。\n"
            '只输出一行 JSON，不要任何其他文字：{"score":0-100,"verdict":"对|基本对|不对","comment":"不超过40字"}'
        )
        user_p = "题目：%s\n\n参考答案：%s\n\n学生答案：%s" % (
            (question or "").strip()[:800],
            (reference or "").strip()[:800],
            (mine or "").strip()[:400],
        )
        t0 = time.time()
        c = self._client()
        r = c.chat.completions.create(
            model=cfg.get("model"),
            messages=[{"role": "system", "content": sys_p},
                      {"role": "user", "content": user_p}],
            temperature=0,
            max_tokens=200,
        )
        txt = (r.choices[0].message.content or "").strip()
        m = _re.search(r"\{[\s\S]*\}", txt)
        if not m:
            return None
        try:
            j = _json.loads(m.group(0))
        except Exception:
            return None
        try:
            score = max(0, min(100, int(float(j.get("score")) or 0)))
        except Exception:
            score = 0
        return {
            "score": score,
            "verdict": str(j.get("verdict") or "").strip(),
            "comment": str(j.get("comment") or "").strip(),
            "cost": time.time() - t0,
        }
