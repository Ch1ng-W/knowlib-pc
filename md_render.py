"""
md_render.py —— 轻量 Markdown + LaTeX → HTML（给 QTextBrowser 用）

不依赖任何第三方库（打包体积不变）。
支持：标题 / 粗体 / 斜体 / 行内代码 / 代码块 / 列表 / 引用 / 分隔线 / 简单表格
公式：$...$ 、$$...$$ 、\\( \\) 、\\[ \\]  → 转成 Qt 富文本能显示的 HTML
"""

import html
import re

# ---------------------------------------------------------------- LaTeX 符号
_SYM = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "div": "÷", "pm": "±", "mp": "∓", "cdot": "·", "ast": "*",
    "star": "⋆", "circ": "∘", "bullet": "•", "oplus": "⊕", "otimes": "⊗",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "propto": "∝",
    "ll": "≪", "gg": "≫",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏",
    "int": "∫", "iint": "∬", "oint": "∮",
    "rightarrow": "→", "to": "→", "leftarrow": "←", "gets": "←",
    "leftrightarrow": "↔", "Rightarrow": "⇒", "implies": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "iff": "⇔", "uparrow": "↑", "downarrow": "↓",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "triangle": "△",
    "degree": "°", "deg": "°", "prime": "′", "ldots": "…", "cdots": "⋯",
    "dots": "…", "vdots": "⋮", "ddots": "⋱",
    "quad": "\u2003", "qquad": "\u2003\u2003",
    " ": " ", ",": "\u2009", ";": "\u2009", ":": "\u2009", "!": "",
    "{": "{", "}": "}", "%": "%", "$": "$", "&": "&", "#": "#", "_": "_",
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec",
    "csc": "csc", "log": "log", "ln": "ln", "lg": "lg", "exp": "exp",
    "lim": "lim", "max": "max", "min": "min", "sup": "sup", "inf": "inf",
    "det": "det", "arcsin": "arcsin", "arccos": "arccos", "arctan": "arctan",
    "left": "", "right": "", "big": "", "Big": "", "bigg": "", "Bigg": "",
    "displaystyle": "", "textstyle": "", "limits": "", "nolimits": "",
}

_WRAP = ("text", "mathrm", "textrm", "operatorname", "mbox")
_BOLD = ("mathbf", "textbf", "bm")
_ITAL = ("mathit", "textit", "boldsymbol")


def _read_group(s, i):
    """从 s[i] 开始读一个 {...} 组，返回 (内容, 新位置)。"""
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] != "{":
        # 单字符/单命令
        if s[i] == "\\":
            m = re.match(r"\\([a-zA-Z]+|.)", s[i:])
            if m:
                return m.group(0), i + m.end()
            return "", i + 1
        return s[i], i + 1
    depth = 0
    j = i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return s[i + 1:], len(s)


def latex_to_html(src, display=False):
    """把一段 LaTeX 转成 Qt 富文本可显示的 HTML。"""
    s = (src or "").strip()
    s = s.replace("\\\\", "<br/>")

    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            m = re.match(r"\\([a-zA-Z]+)", s[i:])
            if not m:
                nxt = s[i + 1] if i + 1 < n else ""
                out.append(_SYM.get(nxt, html.escape(nxt)))
                i += 2
                continue
            name = m.group(1)
            i += m.end()
            if name in ("frac", "dfrac", "tfrac"):
                a, i = _read_group(s, i)
                b, i = _read_group(s, i)
                ha = latex_to_html(a)
                hb = latex_to_html(b)
                out.append(
                    f'<span style="white-space:nowrap"><sup style="font-size:82%">{ha}'
                    f'</sup>⁄<sub style="font-size:82%">{hb}</sub></span>')
                continue
            if name == "sqrt":
                idx = ""
                k = i
                while k < n and s[k] in " \t":
                    k += 1
                if k < n and s[k] == "[":
                    e = s.find("]", k)
                    if e > 0:
                        idx = latex_to_html(s[k + 1:e])
                        i = e + 1
                a, i = _read_group(s, i)
                ha = latex_to_html(a)
                pre = f'<sup style="font-size:75%">{idx}</sup>' if idx else ""
                out.append(f"{pre}√<span style=\"text-decoration:overline\">{ha}</span>")
                continue
            if name in _WRAP:
                a, i = _read_group(s, i)
                out.append(html.escape(a))
                continue
            if name in _BOLD:
                a, i = _read_group(s, i)
                out.append("<b>" + latex_to_html(a) + "</b>")
                continue
            if name in _ITAL:
                a, i = _read_group(s, i)
                out.append("<i>" + latex_to_html(a) + "</i>")
                continue
            if name == "overline":
                a, i = _read_group(s, i)
                out.append('<span style="text-decoration:overline">'
                           + latex_to_html(a) + "</span>")
                continue
            if name == "vec":
                a, i = _read_group(s, i)
                out.append(latex_to_html(a) + "&#8407;")
                continue
            if name == "begin" or name == "end":
                a, i = _read_group(s, i)
                if name == "begin" and "matrix" in a:
                    out.append("")
                continue
            if name in ("hspace", "vspace", "phantom", "hphantom", "label", "tag"):
                _a, i = _read_group(s, i)
                continue
            if name == "item":
                out.append("• ")
                continue
            out.append(_SYM.get(name, name))
            continue
        if ch in "^_":
            a, i2 = _read_group(s, i + 1)
            tag = "sup" if ch == "^" else "sub"
            out.append(f"<{tag}>{latex_to_html(a)}</{tag}>")
            i = i2
            continue
        if ch == "&":
            out.append(" ")
            i += 1
            continue
        if ch == "~":
            out.append("\u00a0")
            i += 1
            continue
        out.append(html.escape(ch))
        i += 1

    body = "".join(out)
    if display:
        return (f'<div style="margin:8px 0;padding:8px 12px;text-align:center;'
                f'background:#161b22;border-radius:6px">{body}</div>')
    return f'<span style="font-family:Cambria Math,DejaVu Sans,sans-serif">{body}</span>'


# ---------------------------------------------------------------- Markdown
_MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.S)
_MATH_INLINE = re.compile(r"(?<!\\)\$(?!\$)([^$\n]{1,300}?)(?<!\\)\$")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _inline(text):
    """行内元素：公式 / 粗体 / 斜体 / 代码 / 链接。"""
    parts = []
    pos = 0
    for m in _MATH_INLINE.finditer(text):
        body = m.group(1)
        if _CJK.search(body):
            continue          # “价格为 $5 元” 这种不是公式，原样保留
        parts.append(("t", text[pos:m.start()]))
        parts.append(("m", body))
        pos = m.end()
    parts.append(("t", text[pos:]))

    out = []
    for kind, val in parts:
        if kind == "m":
            out.append(latex_to_html(val))
            continue
        t = html.escape(val)
        t = re.sub(r"`([^`]+)`",
                   r'<span style="font-family:Consolas,monospace;'
                   r'background:#161b22;padding:1px 4px;border-radius:3px">\1</span>', t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<i>\1</i>", t)
        t = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: _img_html(m.group(2)), t)
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<span style="color:#58a6ff">\1</span>', t)
        out.append(t)
    return "".join(out)


def _img_html(src):
    """Local image (screenshots inserted into bank) -> img tag QTextBrowser can show."""
    s = (src or "").strip().strip('"').strip("'")
    if s.startswith("file://") or s.startswith("http://") or s.startswith("https://"):
        url = s
    else:
        url = "file:///" + s.replace("\\", "/")
    return ('<br/><img src="%s" style="max-width:100%%;'
            'border-radius:8px;margin:6px 0" />' % url)


def to_html(md, color="#e6e9ef", accent="#8fb6ff", font_size=13):
    """Markdown → HTML 片段（深色主题）。"""
    if not md:
        return f'<div style="color:#7f8896;padding:12px">（空）</div>'

    src = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    # 先把块级公式抽出来，避免被按行切碎
    blocks = []

    def _stash(m):
        blocks.append(latex_to_html(m.group(1).strip(), display=True))
        return f"\x00B{len(blocks) - 1}\x00"

    def _stash_inline(m):
        blocks.append(latex_to_html(m.group(1).strip(), display=False))
        return f"\x00B{len(blocks) - 1}\x00"

    src = _MATH_BLOCK.sub(_stash, src)
    src = re.sub(r"\\\[(.+?)\\\]", _stash, src, flags=re.S)
    src = re.sub(r"\\\((.+?)\\\)", _stash_inline, src, flags=re.S)

    lines = src.split("\n")
    out = []
    in_code = False
    list_mode = None
    table_rows = []

    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        rows = [r for r in table_rows if not re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", r)]
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        if cells:
            h = cells[0]
            td = (f'<table cellspacing="0" cellpadding="5" '
                  f'style="border-collapse:collapse;margin:8px 0">'
                  f'<tr>' + "".join(
                      f'<td style="border:1px solid #30363d;background:#161b22">'
                      f'<b>{_inline(c)}</b></td>' for c in h) + "</tr>")
            for row in cells[1:]:
                td += "<tr>" + "".join(
                    f'<td style="border:1px solid #30363d">{_inline(c)}</td>'
                    for c in row) + "</tr>"
            td += "</table>"
            out.append(td)
        table_rows = []

    for raw in lines:
        line = raw.rstrip()

        if line.strip().startswith("```"):
            flush_table()
            if in_code:
                out.append("</pre>")
                in_code = False
            else:
                out.append('<pre style="background:#161b22;padding:8px;'
                           'border-radius:6px;font-family:Consolas,monospace;'
                           'font-size:12px;white-space:pre-wrap">')
                in_code = True
            continue
        if in_code:
            out.append(html.escape(raw))
            continue

        if "|" in line and line.strip().startswith("|"):
            table_rows.append(line)
            continue
        flush_table()

        if not line.strip():
            if list_mode:
                out.append(f"</{list_mode}>")
                list_mode = None
            out.append("")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if list_mode:
                out.append(f"</{list_mode}>")
                list_mode = None
            lv = len(m.group(1))
            size = {1: 21, 2: 18, 3: 16, 4: 15, 5: 14, 6: 13}[lv]
            out.append(f'<div style="font-size:{size}px;font-weight:600;'
                       f'color:{accent};margin:14px 0 6px">'
                       f'{_inline(m.group(2))}</div>')
            continue

        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            if list_mode:
                out.append(f"</{list_mode}>")
                list_mode = None
            out.append('<hr style="border:none;border-top:1px solid #30363d;margin:12px 0"/>')
            continue

        mq = re.match(r"^\s*>\s?(.*)$", line)
        if mq:
            if list_mode:
                out.append(f"</{list_mode}>")
                list_mode = None
            out.append(f'<div style="border-left:3px solid #30363d;padding:2px 10px;'
                       f'color:#8b949e;margin:6px 0">{_inline(mq.group(1))}</div>')
            continue

        mo = re.match(r"^\s*(\d+)[.、)]\s+(.*)$", line)
        mu = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if mo or mu:
            want = "ol" if mo else "ul"
            if list_mode != want:
                if list_mode:
                    out.append(f"</{list_mode}>")
                out.append(f'<{want} style="margin:4px 0 4px 22px">')
                list_mode = want
            txt = mo.group(2) if mo else mu.group(1)
            out.append(f"<li>{_inline(txt)}</li>")
            continue

        if list_mode:
            out.append(f"</{list_mode}>")
            list_mode = None
        out.append(f'<div style="margin:4px 0">{_inline(line)}</div>')

    if list_mode:
        out.append(f"</{list_mode}>")
    if in_code:
        out.append("</pre>")
    flush_table()

    body = "\n".join(out)
    for idx, b in enumerate(blocks):
        body = body.replace(f"\x00B{idx}\x00", b)
    body = body.replace("\x00", "")
    return (f'<div style="color:{color};font-size:{font_size}px;line-height:1.65;'
            f'font-family:Microsoft YaHei UI,Segoe UI,sans-serif">{body}</div>')


# ---------------------------------------------------------------- 文本清洗
_NOISE = re.compile(
    r"^[\s\d:：.．%()（）\-—·•]*$|"
    r"^(剩余|电量|WiFi|WIFI|wifi|4G|5G|信号|时间|提交|确定|取消|返回|上一题|下一题|"
    r"答题卡|倒计时|智能|拍照搜题|OCR|识别|搜索|分享|收藏|笔记|纠错|解析|答案|"
    r"第\s*\d+\s*题|单选|多选|判断|填空|共\s*\d+\s*题).{0,6}$"
)


def clean_text(s, max_lines=None):
    """去掉 OCR 顺手抓进来的界面噪声（时间/电量/按钮文字）。"""
    if not s:
        return ""
    out = []
    for line in (s or "").replace("\r", "").split("\n"):
        t = line.strip()
        if not t:
            if out and out[-1] != "":
                out.append("")
            continue
        if len(t) <= 12 and _NOISE.match(t):
            continue
        out.append(line.rstrip())
    txt = "\n".join(out).strip()
    if max_lines:
        ls = txt.split("\n")
        txt = "\n".join(ls[:max_lines])
    return txt
