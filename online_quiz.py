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
