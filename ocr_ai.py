"""OCR + AI：RapidOCR 本地识别，OpenAI 兼容接口流式作答。"""
import time
from typing import Callable, Optional

import numpy as np

# ⚠️ 必须在 PyQt5 之前 import（onnxruntime 与 Qt 的 DLL 加载顺序敏感）
from rapidocr_onnxruntime import RapidOCR


# 系统级提示词（与鸿蒙端保持一致）
SYSTEM_PROMPT = (
    "【系统规则】你是一个专业的解题助手，回答用户问题时请遵守：\n"
    "1. 只针对题目作答，不寒暄不废话；\n"
    "2. 优先从用户导入的“学习资料库”中寻找答案和解题方法并作答；若资料库中没有相关答案，再结合你自己的知识解答；\n"
    "3. 回答顺序固定为：① 第一行必须是答案本身，格式固定为“答案：…”——选择题写成“答案：X 选项原文”（如“答案：B 下降的直线”，选项原文尽量短，方便核对）；填空题按顺序写出每个空的答案、用“、”分隔；判断题写“对”或“错”；简答题/计算题写结论或最终结果；② 接着给简要解析（1~2 句，讲清为什么）；③ 最后给分步骤解析。**严禁复述、重写或引用题干**（不要出现“题目：…”“根据题意…”或把原题抄一遍）；\n"
    "4. 用简洁中文回答。"
)


class OcrEngine:
    def __init__(self):
        self.ocr = RapidOCR()
        self.last_info = {}
        # 检测放宽一点：屏幕截图里细笔画 / 浅色字 / 贴边的选项容易被漏掉；
        # 识别结果的置信度在 _read() 里自己再筛一遍。
        try:
            self.ocr.text_score = 0.45
            self.ocr.text_det.postprocess_op.box_thresh = 0.3
            self.ocr.text_det.postprocess_op.unclip_ratio = 1.8
        except Exception:
            pass
        # 预热，避免第一次卡 6 秒
        warm = np.full((60, 200, 3), 255, dtype=np.uint8)
        try:
            self.ocr(warm)
        except Exception:
            pass

    # ---------------- 预处理
    @staticmethod
    def _resize(img):
        """小图放大再识别（选项/公式字号小时效果明显）。"""
        from PIL import Image
        try:
            w, h = img.size
            m = max(w, h)
            if m < 700:
                k = 2.2
            elif m < 1100:
                k = 1.7
            else:
                return img
            return img.resize((max(1, int(w * k)), max(1, int(h * k))),
                              Image.LANCZOS)
        except Exception:
            return img

    @staticmethod
    def _mean_lum(img):
        try:
            g = img.convert("L").resize((32, 32))
            d = list(g.getdata())
            return sum(d) / max(1, len(d))
        except Exception:
            return 255

    @staticmethod
    def _gray_variant(base):
        from PIL import ImageOps
        g = ImageOps.autocontrast(ImageOps.grayscale(base), cutoff=1).convert("RGB")
        return ImageOps.expand(g, border=10, fill=(255, 255, 255))

    @staticmethod
    def _invert_variant(base):
        from PIL import ImageOps
        g = ImageOps.autocontrast(ImageOps.grayscale(base), cutoff=1).convert("RGB")
        return ImageOps.expand(ImageOps.invert(g), border=10, fill=(255, 255, 255))

    def _variants(self, img):
        """深色底（深色模式截图）先反色，浅色底先原图；后面再补一版灰度增强。"""
        base = self._resize(img)
        try:
            dark = self._mean_lum(base) < 120
        except Exception:
            dark = False
        try:
            if dark:
                yield "invert", self._invert_variant(base)
                yield "orig", base
            else:
                yield "orig", base
            yield "gray", self._gray_variant(base)
        except Exception:
            return

    def _read(self, arr):
        """跑一次 OCR，返回 (文本, 平均置信度, 行数)。"""
        result, _ = self.ocr(arr)
        if not result:
            return "", 0.0, 0
        texts, confs, seen = [], [], None
        for item in result:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            try:
                score = float(item[2])
            except Exception:
                continue
            if score < 0.4:
                continue
            t = str(item[1]).strip()
            if not t:
                continue
            if t == seen:
                continue
            seen = t
            texts.append(t)
            confs.append(score)
        text = "\n".join(texts).strip()
        avg = sum(confs) / len(confs) if confs else 0.0
        return text, avg, len(texts)

    def recognize(self, pil_image):
        img = pil_image.convert("RGB")
        t0 = time.time()
        best = None
        tried = []
        attempts = 0
        for name, im in self._variants(img):
            attempts += 1
            try:
                text, avg, n = self._read(np.array(im))
            except Exception as e:
                tried.append([name, 0, 0.0, type(e).__name__])
                if attempts >= 2:
                    break
                continue
            # 打分：字多优先，置信度做加权（避免"认出一堆垃圾"赢过少而准的结果）
            quality = len(text) * (0.5 + avg)
            tried.append([name, len(text), round(avg, 3)])
            if best is None or quality > best[0]:
                best = (quality, text, avg, name)
            # 够好 / 或已经试过两版 → 收工，别拖慢每一帧
            if (len(text) >= 12 and avg >= 0.80) or attempts >= 2:
                break

        cost = time.time() - t0
        if not best:
            self.last_info = {"passes": tried, "picked": ""}
            return "", 0.0, cost
        _q, text, avg, picked = best
        self.last_info = {"passes": tried, "picked": picked,
                          "chars": len(text), "conf": round(avg, 3),
                          "cost": round(cost, 2)}
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
                    {"role": "system", "content": SYSTEM_PROMPT},
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
