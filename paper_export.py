"""题库导出：题目模板 Markdown（Typora）+ PDF（QTextDocument 直出）。

Markdown 版沿用哥哥的试题排版规范：
  · 章节固定顺序：选择题 → 判断题 → 填空题 → 简答题 → 计算题（没有的题型跳过）
  · 选择题按「四步法」宽度决定一行四列 / 两行两列 / 一行一个
  · 填空题答案区用下划线空白
  · 公式保持 LaTeX `$...$`
PDF 版：QTextDocument 不支持 flex，改用 table 排版；空白用全角下划线（渲染最稳）。
"""
import re
import time

from PyQt5.QtGui import QFont, QPagedPaintDevice, QPdfWriter, QTextDocument
from PyQt5.QtCore import QSizeF

# 章节顺序（按哥哥的模板；判断题没有就不出这一节）
TYPE_ORDER = ["选择题", "判断题", "填空题", "简答题", "计算题"]

_NOISE_LINES = re.compile(
    r"^(提交成功|已提交|提交|开始|停止|识别|设置|确定|取消|上一题|下一题|返回|"
    r"【?[单多]?选?择?题】?|判断题|填空题|简答题|计算题|计算分析题|[（(]?\s*\d+\s*分\s*[)）]?|"
    r"\d+\s*/\s*\d+|试题|答题卡|倒计时.*|鼎|田|器.*)$")
_CUT_MARKERS = ("本题得分", "正确答案", "查看解析", "可乱序作答", "区分大小写",
                "输入答案", "我的答案", "参考答案")
_NUM_HEAD = re.compile(r"^\s*[（(]?\s*\d+\s*[.、．)）]\s*")
_OPT_HEAD = re.compile(r"^\s*([A-D])\s*[.、．:：)）]?\s*(.*)$")
_JUNK_LIST = re.compile(r"^[I|丨l]?\s*\d{1,2}\s*[、.．]?\s*[^\s]{0,6}$")

_FAIL_RE = re.compile(
    r"无法(确定|判断|作答|识别|回答)|题目(内容)?(缺失|不完整|不清晰)|缺少题目|没有题目|"
    r"不是一道(题|试题)|识别到的不(是|像)|不能仅凭|题干残缺|无法还原|缺失/|题干不完整")

_UI_WORDS = ("提交成功", "按住题目", "开始识别", "停止识别", "答题卡", "倒计时",
             "github", "release", "http", "框选区域", "监控区域", "没读到文字",
             "显示答案窗", "热键：f1")


# ---------------------------------------------------------------- 题型识别
def detect_type(q_text, answer=""):
    t = (q_text or "")
    a = (answer or "").strip()
    if "判断" in t[:40]:
        return "判断题"
    if a in ("正确", "错误", "对", "错", "√", "×", "T", "F"):
        return "判断题"
    if re.search(r"[（(]\s*[)）]", t) and not re.search(r"^\s*A\s*[.、．]", t, re.M):
        pass
    if "选择题" in t[:40] or "单选题" in t[:40] or "多选题" in t[:40]:
        return "选择题"
    if len(parse_options(t)[1]) >= 2:
        return "选择题"
    if "填空" in t[:40] or "____" in t or "＿＿" in t:
        return "填空题"
    if "计算" in t[:40] or re.search(r"[（(]\s*\d+\s*分\s*[)）]", t[:60]):
        if "计算" in t[:40]:
            return "计算题"
    if "简答" in t[:40] or "论述" in t[:40] or "名词解释" in t[:40]:
        return "简答题"
    return "简答题"


# ---------------------------------------------------------------- 文本清洗
def _prepare_lines(q_text):
    """清洗行：去界面文字 / 分值 / 题号 / 候选字乱码（保留 A/B/C/D 字母行）。"""
    out = []
    for raw in (q_text or "").replace("\r", "").split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        ln = ln.lstrip("■●◆*·口囗〇 ").strip()
        ln = re.sub(r"[（(]\s*\d+(\.\d+)?\s*分\s*[)）]\s*$", "", ln).strip()
        cut = None
        for mk in _CUT_MARKERS:
            if mk in ln:
                cut = mk
                break
        if cut:
            head = ln.split(cut)[0].strip()
            if head:
                out.append(head)
            break
        ln = _NUM_HEAD.sub("", ln).strip()
        ln = ln.lstrip("■●◆*·口囗〇 ").strip()
        if not ln:
            continue
        if _NOISE_LINES.match(ln):
            continue
        if _JUNK_LIST.match(ln) and not re.search(r"[\u4e00-\u9fa5]{4,}", ln):
            continue
        out.append(ln)
    return out


def clean_stem(q_text):
    """只要题干（去掉选项字母行）。"""
    lines = [l for l in _prepare_lines(q_text)
             if not (len(l) <= 2 and l.upper() in ("A", "B", "C", "D"))]
    return "\n".join(lines).strip()


def _ans_sol(it):
    """答案 / 解析（历史直接导出的条目可能没拆过）。"""
    ans = (it.get("answer") or "").strip()
    sol = (it.get("solution") or "").strip()
    if not ans and sol:
        try:
            import question_bank
            a, s = question_bank.split_answer(sol)
            return a, s
        except Exception:
            return "", sol
    return ans, sol


def parse_options(q_text):
    """返回 (题干, [(字母, 内容), ...])。识别不到选项就返回空列表。"""
    text = (q_text or "").replace("\r", "")
    lines = _prepare_lines(text)

    def is_letter(ln):
        m = _OPT_HEAD.match(ln)
        if not m:
            return None
        letter, body = m.group(1), m.group(2).strip()
        if body:
            return letter, body
        if len(ln) <= 3:          # 单独一个 "A"
            return letter, ""
        return None

    start = None
    for i, ln in enumerate(lines):
        got = is_letter(ln)
        if got and got[0] == "A":
            for j in range(i + 1, min(i + 6, len(lines))):
                gb = is_letter(lines[j])
                if gb and gb[0] == "B":
                    start = i
                    break
        if start is not None:
            break

    if start is None:
        return _fallback_options(lines)

    stem = clean_stem("\n".join(lines[:start]))
    opts = []          # [[letter, [lines]], ...]
    for ln in lines[start:]:
        got = is_letter(ln)
        if got and (not opts or got[0] != opts[-1][0]):
            opts.append([got[0], [got[1]] if got[1] else []])
        elif opts:
            opts[-1][1].append(ln)

    # OCR 丢了 C/D 字母时：B 后面接了好几行短句，当 C/D 拆
    if len(opts) in (2, 3) and len(opts[-1][1]) >= 2:
        tail = [l for l in opts[-1][1] if len(l) <= 30]
        if len(tail) == len(opts[-1][1]) and len(opts) + len(tail) - 1 <= 4:
            base = opts[-1][0]
            first = opts[-1][1][0]
            opts[-1][1] = [first]
            for k, extra in enumerate(tail[1:], start=1):
                nxt = chr(ord(base) + k)
                opts.append([nxt, [extra]])

    out = [(letter, " ".join([x for x in body if x]).strip()) for letter, body in opts]
    out = [(a, b) for a, b in out if b]
    return stem, out


def _fallback_options(lines):
    """没有 A/B 字母的题（纯 4 行选项）：把末尾几行短句当选项。"""
    if len(lines) < 3:
        return clean_stem("\n".join(lines)), []
    tail = []
    for ln in reversed(lines):
        ok = (len(ln) <= 34 and not ln.endswith(("。", "？", "?", "；", ";"))) or len(ln) <= 16
        if not ok:
            break
        tail.append(ln)
        if len(tail) == 4:
            break
    if len(tail) == 4 and len(lines) > 4:
        head = lines[:-4]
        # 末四行看起来真的是选项：长度相近且不重复
        if len(set(tail)) == 4:
            stem = clean_stem("\n".join(head))
            return stem, [(chr(65 + i), t) for i, t in enumerate(reversed(tail))]
    return clean_stem("\n".join(lines)), []


def is_valid(q_text, answer):
    """历史记录里哪些算「有效」。"""
    q = (q_text or "").strip()
    a = (answer or "").strip()
    if len(re.sub(r"\s", "", q)) < 8:
        return False
    if not a:
        return False
    if _FAIL_RE.search(a[:80]):
        return False
    low = q.lower()
    ui_hits = sum(1 for w in _UI_WORDS if w in low)
    if ui_hits >= 2:
        return False
    if re.search(r"^[\s\W]*$", q):
        return False
    return True


def _norm_key_raw(q_text):
    try:
        from question_bank import normalize
    except Exception:
        def normalize(t):
            return "".join((t or "").split())
    return normalize(clean_stem(q_text))


try:
    from functools import lru_cache as _lru_cache

    # clean_stem 的正则比较费时（每题 ~3ms），历史记录一多，去重会明显卡。
    # 按题干文本缓存归一结果：同题重复出现时直接命中；新增一条也只需算新的那条。
    norm_key = _lru_cache(maxsize=6000)(_norm_key_raw)
except Exception:                       # 理论上不会发生
    def norm_key(q_text):
        return _norm_key_raw(q_text)


# ---------------------------------------------------------------- Markdown
def _px(text):
    """按哥哥四步法估宽度：中文 16px，英数 9px。"""
    w = 0
    for ch in text or "":
        w += 16 if ord(ch) > 127 else 9
    return w


def _md_choice_block(opts):
    """选择题选项：≤110 一行四列，≤250 两行两列，否则一行一个。"""
    if not opts:
        return []
    longest = max(_px(f"{a}. {b}") for a, b in opts)
    if longest <= 110:
        cells = "".join(
            f'<span style="display: inline-block; width: 110px;">{a}. {b}</span>'
            for a, b in opts)
        return [f'<div style="display: flex; gap: 30px;">{cells}</div>', ""]
    if longest <= 250:
        rows = [opts[i:i + 2] for i in range(0, len(opts), 2)]
        out = []
        for row in rows:
            cells = "".join(
                f'<span style="display: inline-block; width: 250px;">{a}. {b}</span>'
                for a, b in row)
            out.append(f'<div style="display: flex; gap: 30px;">{cells}</div>')
        out.append("")
        return out
    return [f"{a}. {b}\n" for a, b in opts] + [""]


def build_md(items, title="Knowlib 题库导出", with_answer=True, subject=""):
    buckets = {k: [] for k in TYPE_ORDER}
    for it in items:
        q = it.get("q_text", "")
        a = it.get("answer", "")
        t = it.get("qtype") or detect_type(q, a)
        if t not in buckets:
            t = "简答题"
        buckets[t].append(it)

    out = [f"# {title}", ""]
    meta = f"> 导出于 {time.strftime('%Y-%m-%d %H:%M')} · 共 {len(items)} 题 · Knowlib 题库"
    out += [meta, ""]

    idx = 0
    cn = ["一", "二", "三", "四", "五"]
    sec = 0
    for t in TYPE_ORDER:
        group = buckets[t]
        if not group:
            continue
        out += [f"## {cn[sec]}、{t}", ""]
        sec += 1
        for it in group:
            idx += 1
            stem, opts = parse_options(it.get("q_text", ""))
            stem = re.sub(r"\s*\n\s*", "\n", stem).strip()
            if opts:
                out.append(f"{idx}. {stem}")
                out.append("")
                out += _md_choice_block(opts)
            else:
                out.append(f"{idx}. {stem}")
                out.append("")
            if with_answer:
                ans, sol = _ans_sol(it)
                if ans:
                    out.append(f"> **答案：** {ans}")
                if sol:
                    sol = sol.replace("\n", " ").strip()
                    out.append(f"> ")
                    out.append(f"> **解析：** {sol}")
                out.append("")
    if not items:
        out += ["（题库为空）", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- PDF
def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_choice_table(opts):
    if not opts:
        return ""
    longest = max(_px(f"{a}. {b}") for a, b in opts)
    cols = 4 if longest <= 110 else (2 if longest <= 250 else 1)
    rows = [opts[i:i + cols] for i in range(0, len(opts), cols)]
    html = ['<table width="100%" cellspacing="0" cellpadding="2">']
    for row in rows:
        html.append("<tr>")
        for a, b in row:
            html.append(f'<td width="{100 // cols}%">{a}. {_esc(b)}</td>')
        for _ in range(cols - len(row)):
            html.append(f'<td width="{100 // cols}%"></td>')
        html.append("</tr>")
    html.append("</table>")
    return "".join(html)


def build_html(items, title="Knowlib 题库导出", with_answer=True):
    buckets = {k: [] for k in TYPE_ORDER}
    for it in items:
        q = it.get("q_text", "")
        a = it.get("answer", "")
        t = it.get("qtype") or detect_type(q, a)
        buckets.setdefault(t if t in buckets else "简答题", []).append(it)

    css = """
    body { font-family: "Microsoft YaHei", "SimSun", sans-serif; font-size: 10.5pt;
           color: #1a1a1a; line-height: 160%; }
    h1 { font-size: 17pt; text-align: center; margin: 0 0 4px 0; }
    .meta { text-align: center; color: #666666; font-size: 9pt; margin-bottom: 14px; }
    h2 { font-size: 12.5pt; margin: 16px 0 6px 0; padding-bottom: 3px;
         border-bottom: 1px solid #cccccc; }
    .q { margin: 8px 0 6px 0; }
    .stem { font-weight: normal; }
    .ans { color: #1f6feb; margin: 3px 0 10px 0; }
    .sol { color: #444444; margin: 0 0 10px 0; font-size: 10pt; }
    td { font-size: 10.5pt; }
    """
    html = [f"<html><head><style>{css}</style></head><body>"]
    html.append(f"<h1>{_esc(title)}</h1>")
    html.append(f'<div class="meta">导出于 {time.strftime("%Y-%m-%d %H:%M")} · '
                f"共 {len(items)} 题 · Knowlib 题库</div>")

    idx = 0
    cn = ["一", "二", "三", "四", "五"]
    sec = 0
    for t in TYPE_ORDER:
        group = buckets.get(t) or []
        if not group:
            continue
        html.append(f"<h2>{cn[sec]}、{t}</h2>")
        sec += 1
        for it in group:
            idx += 1
            stem, opts = parse_options(it.get("q_text", ""))
            stem_html = _esc(stem).replace("\n", "<br/>")
            html.append(f'<div class="q"><b>{idx}.</b> <span class="stem">{stem_html}</span></div>')
            if opts:
                html.append(_pdf_choice_table(opts))
            if with_answer:
                a_, s_ = _ans_sol(it)
                ans = _esc(a_)
                sol = _esc(s_.replace("\n", " "))
                if ans:
                    html.append(f'<div class="ans"><b>答案：</b>{ans}</div>')
                if sol:
                    html.append(f'<div class="sol"><b>解析：</b>{sol}</div>')
    html.append("</body></html>")
    return "".join(html)


def export_pdf(items, path, title="Knowlib 题库导出", with_answer=True):
    """用 QTextDocument 直出 PDF（无需 reportlab / 联网）。"""
    doc = QTextDocument()
    f = QFont("Microsoft YaHei", 10)
    doc.setDefaultFont(f)
    doc.setDocumentMargin(48)
    doc.setHtml(build_html(items, title, with_answer))

    writer = QPdfWriter(path)
    writer.setPageSize(QPagedPaintDevice.A4)
    writer.setResolution(150)
    writer.setTitle(title)
    writer.setCreator("Knowlib")

    doc.setPageSize(QSizeF(writer.width(), writer.height()))
    doc.print_(writer)
    return True
