# -*- coding: utf-8 -*-
"""online_quiz.py —— 拉取 knowlib.fun 上分享的在线试卷（PC 端用）。

只用标准库，不增加打包体积。
"""
import json
import re
import urllib.request

BASE = "https://knowlib.fun"
UA = "KnowlibPC/1.0 (+https://knowlib.fun)"

_TYPE_CN = {"choice": "选择题", "judge": "判断题", "blank": "填空题", "short": "简答题"}

_CODE_IN_URL = re.compile(r"(?:/s/|[?&]code=)([a-z0-9]{5,12})", re.I)
_PURE_CODE = re.compile(r"^[a-z0-9]{5,12}$", re.I)


def code_from(text):
    """从链接 / knowlib:// 深链 / 纯 code 里取出试卷 code。"""
    t = (text or "").strip()
    if not t:
        return ""
    m = _CODE_IN_URL.search(t)
    if m:
        return m.group(1).lower()
    if t.lower().startswith("knowlib://"):
        rest = t.split("://", 1)[1].strip("/")
        rest = rest.split("?")[0].split("#")[0]
        if rest.startswith("s/"):
            rest = rest[2:]
        if _PURE_CODE.match(rest):
            return rest.lower()
    if _PURE_CODE.match(t):
        return t.lower()
    return ""


def fetch(code, timeout=15):
    """取回试卷，返回 (doc_dict, meta)。"""
    url = "%s/api/doc?code=%s" % (BASE, code)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        j = json.loads(raw)
    except Exception:
        raise ValueError("服务器返回的不是 JSON（可能被网关拦了）")
    if not j.get("ok"):
        raise ValueError("链接不存在或已过期")
    doc = j.get("doc")
    if isinstance(doc, str):
        doc = json.loads(doc)
    if not isinstance(doc, dict):
        raise ValueError("试卷数据格式不对")
    return doc, j


def _judge_key(ans):
    a = (ans or "").strip()
    if a in ("对", "正确", "√", "T", "t", "true", "True", "是", "1"):
        return "对"
    if a in ("错", "错误", "×", "x", "X", "F", "f", "false", "False", "否", "0"):
        return "错"
    return ""


def normalize(doc):
    """统一成 [{type,type_name,stem,options[(k,txt)],answer,analysis,score}]"""
    out = []
    for q in (doc.get("questions") or []):
        if not isinstance(q, dict):
            continue
        t = str(q.get("type") or "").lower()
        opts = []
        for o in (q.get("options") or []):
            if isinstance(o, dict):
                k = str(o.get("label") or o.get("key") or "").strip().upper() or chr(65 + len(opts))
                txt = str(o.get("text") or o.get("t") or "").strip()
                opts.append((k, txt))
            else:
                opts.append((chr(65 + len(opts)), str(o).strip()))
        stem = str(q.get("stem") or "").strip()
        ans = str(q.get("answerMd") or q.get("answer") or "").strip()
        if t == "judge" and not ans:
            ans = "正确" if ("（对）" in stem or "(对)" in stem) else (
                "错误" if ("（错）" in stem or "(错)" in stem) else "")
        out.append({
            "type": t,
            "type_name": str(q.get("typeName") or _TYPE_CN.get(t, "")),
            "stem": stem,
            "options": opts,
            "answer": ans,
            "analysis": str(q.get("analysis") or "").strip(),
            "score": float(q.get("score") or 0),
        })
    return out


def answer_text(q):
    """拼成 qbank 需要的「答案：… 解析：…」格式。"""
    a = (q.get("answer") or "").strip()
    s = (q.get("analysis") or "").strip()
    if not a and s:
        return "解析：" + s
    return "答案：%s\n解析：%s" % (a, s)


def correct_key(q):
    """选择题/判断题的正确答案 key（A/B/C/D 或 对/错）。"""
    if q.get("type") == "judge":
        return _judge_key(q.get("answer"))
    m = re.match(r"^\s*([A-Ha-h])", q.get("answer") or "")
    return m.group(1).upper() if m else ""


# ---------------------------------------------------------------- 判分 / 分享
_PUNCT = re.compile(r"[\s\u3000，。、；：？！“”‘’（）()\[\]【】《》,.!?;:'\"`]+")


def norm_answer(s):
    t = (s or "").strip().lower()
    t = t.replace("×", "x").replace("÷", "/").replace("＝", "=")
    t = _PUNCT.sub("", t)
    # 判断题：正确/√/对 → 对；错误/×/错 → 错
    if t in ("正确", "对", "√"):
        return "对"
    if t in ("错误", "错", "×", "x"):
        return "错"
    return t


def same_answer(ref, mine):
    """本地快速判定（完全一致 / 数值等价）。"""
    a0, b0 = (ref or "").strip(), (mine or "").strip()
    if not a0 or not b0:
        return False
    # 先试数值（避免 0.5 被标点清理成 05）
    try:
        from fractions import Fraction

        def _num(x):
            s = x.replace(" ", "").replace("\u3000", "")
            s = s.replace("×", "*").replace("÷", "/").replace("＝", "=")
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
            try:
                return float(Fraction(s))
            except Exception:
                try:
                    return float(s)
                except Exception:
                    return None

        na, nb = _num(a0), _num(b0)
        if na is not None and nb is not None and abs(na - nb) < 1e-9:
            return True
    except Exception:
        pass
    a, b = norm_answer(a0), norm_answer(b0)
    return bool(a and b and a == b)


def parse_options_inline(text):
    """选择题写在同一行时的兜底解析："题干 A．甲 B．乙 C．丙"。"""
    marks = list(re.finditer(r"(?:^|\s)([A-H])\s*[．.、)）:：]\s*", text or ""))
    if len(marks) < 2:
        return "", []
    stem = (text or "")[:marks[0].start()].strip()
    opts = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        opts.append((m.group(1), text[m.end():end].strip()))
    return stem, opts


def share(doc, author="", code="", timeout=25):
    payload = json.dumps({
        "doc": json.dumps(doc, ensure_ascii=False),
        "author": author or "",
        "code": code or "",
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/doc", data=payload,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    if not j.get("ok"):
        raise ValueError(j.get("message") or "上传失败")
    return j.get("short") or (BASE + "/s/" + str(j.get("code") or ""))


def claim_owner(code, key, timeout=15):
    """给试卷认领作者密钥（第一次调用生效，后面只校验）。"""
    payload = json.dumps({"code": code, "key": key}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/owner", data=payload,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    return bool(j.get("ok"))
