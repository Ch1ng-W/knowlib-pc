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

    def solve(self, question_text: str,
              on_delta: Optional[Callable[[str], None]] = None,
              on_state: Optional[Callable[[str], None]] = None):
        """流式求解。返回 (完整文本, 秒)。"""
        cfg = self.cfg
        prompt = (cfg.get("prompt") or "").strip() + "\n\n—— 识别到的题目文字 ——\n" + question_text
        t0 = time.time()
        try:
            c = self._client()
            stream = c.chat.completions.create(
                model=cfg.get("model"),
                messages=[
                    {"role": "system", "content": "你是严谨的解题助手，输出简洁准确。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 900)),
                stream=True,
            )
            buf = []
            for chunk in stream:
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
