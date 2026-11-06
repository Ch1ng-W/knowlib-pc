"""本地题库：搜过的题存 SQLite，下次先查库（精确 → 模糊），命中就不调 AI。"""
import os
import re
import sqlite3
import time
import unicodedata
from difflib import SequenceMatcher

import config_store

DB_NAME = "bank.db"
FUZZY_THRESHOLD = 0.86


# ---------------------------------------------------------------- 路径 / 连接
def db_path():
    return os.path.join(os.path.dirname(config_store.config_path()), DB_NAME)


def _conn():
    c = sqlite3.connect(db_path(), timeout=5)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    try:
        with _conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS questions("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "norm TEXT NOT NULL UNIQUE,"
                "q_text TEXT NOT NULL,"
                "answer TEXT,"
                "solution TEXT,"
                "source TEXT DEFAULT 'ai',"
                "hits INTEGER DEFAULT 0,"
                "created TEXT,"
                "used TEXT,"
                "qtype TEXT DEFAULT '')"
            )
            cols = [r[1] for r in c.execute("PRAGMA table_info(questions)").fetchall()]
            if "qtype" not in cols:
                c.execute("ALTER TABLE questions ADD COLUMN qtype TEXT DEFAULT ''")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 文本规范化
_PUNCT = re.compile(r"[，。、；：？！“”‘’（）《》〈〉【】\[\]{}<>·,.;:?!\"'()\-—_~`|/\\]")
_ANSWER_RE = re.compile(r"答\s*案\s*[:：]\s*([\s\S]*)")


def normalize(text):
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"\s+", "", t)
    t = _PUNCT.sub("", t)
    return t.lower()


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def split_answer(full):
    """从完整回答里拆出 (答案, 解析)。"""
    full = (full or "").strip()
    idx = -1
    for marker in ("解析：", "解析:"):
        i = full.find(marker)
        if i >= 0:
            idx = i
            break
    head = full if idx < 0 else full[:idx]
    tail = "" if idx < 0 else full[idx:]
    m = _ANSWER_RE.search(head)
    ans = (m.group(1) if m else head).strip().strip("：:").strip()
    tail = tail.replace("解析：", "").replace("解析:", "").strip()
    return ans, tail


# ---------------------------------------------------------------- 读写
def save(q_text, full_answer, source="ai"):
    """存/更新一条。同一题再次作答就覆盖答案。"""
    text = (q_text or "").strip()
    if len(normalize(text)) < 4:
        return False
    norm = normalize(text)
    ans, sol = split_answer(full_answer)
    if not (ans or sol):
        return False
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    qtype = detect_type(text, ans)
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO questions(norm,q_text,answer,solution,source,hits,created,used,qtype)"
                " VALUES(?,?,?,?,?,0,?,?,?)"
                " ON CONFLICT(norm) DO UPDATE SET"
                " q_text=excluded.q_text, answer=excluded.answer,"
                " solution=excluded.solution, source=excluded.source,"
                " used=excluded.used,"
                " qtype=CASE WHEN questions.qtype IS NULL OR questions.qtype=''"
                "  THEN excluded.qtype ELSE questions.qtype END",
                (norm, text, ans, sol, source, now, now, qtype))
        return True
    except Exception:
        return False


def detect_type(q_text, answer=""):
    """题型粗判（paper_export.detect_type 的轻量版，避免循环导入）。"""
    try:
        import paper_export
        return paper_export.detect_type(q_text, answer)
    except Exception:
        return ""


def set_type(qid, qtype):
    try:
        with _conn() as c:
            c.execute("UPDATE questions SET qtype=? WHERE id=?", (qtype, qid))
        return True
    except Exception:
        return False


def update(qid, q_text, answer, solution, qtype=None):
    """手动编辑一条题目（题目/答案/解析/题型）。返回 (ok, msg)。"""
    text = (q_text or "").strip()
    if len(normalize(text)) < 2:
        return False, "题目不能为空"
    norm = normalize(text)
    try:
        with _conn() as c:
            dup = c.execute("SELECT id FROM questions WHERE norm=? AND id<>?",
                            (norm, qid)).fetchone()
            if dup:
                return False, "题库里已有同样的题目"
            if qtype is None:
                qtype = detect_type(text, answer or "")
            c.execute("UPDATE questions SET norm=?, q_text=?, answer=?, solution=?,"
                      " qtype=? WHERE id=?",
                      (norm, text, (answer or "").strip(),
                       (solution or "").strip(), qtype, qid))
        return True, "已保存"
    except Exception as e:
        return False, str(e)


def add_manual(q_text, answer, solution, qtype="", source="manual"):
    """用户手动新建一条题目。返回 (ok, msg, qid)。"""
    text = (q_text or "").strip()
    if len(normalize(text)) < 2:
        return False, "题目不能为空", None
    norm = normalize(text)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO questions(norm,q_text,answer,solution,source,hits,created,used,qtype)"
                " VALUES(?,?,?,?,?,0,?,?,?)",
                (norm, text, (answer or "").strip(), (solution or "").strip(),
                 source, now, now, qtype or detect_type(text, answer or "")))
            return True, "已添加", cur.lastrowid
    except Exception as e:
        return False, ("题库里已有同样的题目" if "UNIQUE" in str(e) else str(e)), None


def _row_to_dict(row, score=1.0):
    keys = row.keys()
    return {"id": row["id"], "q_text": row["q_text"], "answer": row["answer"] or "",
            "solution": row["solution"] or "", "hits": row["hits"] or 0,
            "created": row["created"] or "", "used": row["used"] or "",
            "source": row["source"] or "ai",
            "qtype": (row["qtype"] if "qtype" in keys else "") or "",
            "score": score}


def lookup(q_text, threshold=FUZZY_THRESHOLD):
    """先精确、后模糊。命中返回 dict（含 score），否则 None。"""
    norm = normalize(q_text)
    if len(norm) < 4:
        return None
    try:
        with _conn() as c:
            row = c.execute("SELECT * FROM questions WHERE norm=? LIMIT 1", (norm,)).fetchone()
            if row:
                c.execute("UPDATE questions SET hits=hits+1, used=? WHERE id=?",
                          (time.strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
                return _row_to_dict(row, 1.0)

            # 模糊：用长度 + 字块重合度粗筛，再算相似度
            lo, hi = int(len(norm) * 0.7), int(len(norm) * 1.4) + 1
            rows = c.execute(
                "SELECT * FROM questions WHERE length(norm) BETWEEN ? AND ?"
                " ORDER BY id DESC LIMIT 4000", (lo, hi)).fetchall()
            if not rows:
                return None
            target = _bigrams(norm)
            best, best_score = None, 0.0
            for r in rows:
                cand = _bigrams(r["norm"])
                if not cand:
                    continue
                overlap = len(target & cand) / max(1, min(len(target), len(cand)))
                if overlap < 0.55:
                    continue
                score = SequenceMatcher(None, norm, r["norm"]).ratio()
                if score > best_score:
                    best, best_score = r, score
            if best is not None and best_score >= threshold:
                c.execute("UPDATE questions SET hits=hits+1, used=? WHERE id=?",
                          (time.strftime("%Y-%m-%d %H:%M:%S"), best["id"]))
                return _row_to_dict(best, round(best_score, 3))
    except Exception:
        return None
    return None


def items(keyword="", limit=500):
    try:
        with _conn() as c:
            if keyword:
                kw = f"%{keyword}%"
                rows = c.execute(
                    "SELECT * FROM questions WHERE q_text LIKE ? OR answer LIKE ?"
                    " ORDER BY id DESC LIMIT ?", (kw, kw, limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM questions ORDER BY id DESC LIMIT ?",
                                 (limit,)).fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def delete(qid):
    try:
        with _conn() as c:
            c.execute("DELETE FROM questions WHERE id=?", (qid,))
        return True
    except Exception:
        return False


def clear():
    try:
        with _conn() as c:
            c.execute("DELETE FROM questions")
        return True
    except Exception:
        return False


def stats():
    try:
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) n FROM questions").fetchone()["n"]
            h = c.execute("SELECT COALESCE(SUM(hits),0) h FROM questions").fetchone()["h"]
            return {"count": n, "hits": h}
    except Exception:
        return {"count": 0, "hits": 0}


def export_csv(path):
    import csv
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM questions ORDER BY id DESC").fetchall()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["题目", "答案", "解析", "来源", "命中次数", "创建时间"])
            for r in rows:
                w.writerow([r["q_text"], r["answer"], r["solution"], r["source"],
                            r["hits"], r["created"]])
        return True, len(rows)
    except Exception as e:
        return False, str(e)


def import_csv(path):
    """从 CSV 导入（需有「题目」「答案」「解析」列）。"""
    import csv
    ok = 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                q = row.get("题目") or row.get("question") or ""
                a = row.get("答案") or row.get("answer") or ""
                s = row.get("解析") or row.get("solution") or ""
                if q and (a or s):
                    if save(q, f"答案：{a}\n解析：{s}", source="import"):
                        ok += 1
        return True, ok
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- 多格式导入
_Q_START = re.compile(
    r"^(#{1,6}\s|第\s*\d+\s*题|\*\*\s*\d+\s*[.、)）]|\d+\s*[.、)）]\s|【\s*题)")


def _parse_block(block):
    """一块文本 → (题目, 答案, 解析)，没有「答案：」就返回 None。"""
    b = (block or "").strip()
    if not b:
        return None
    m = re.search(r"答\s*案\s*[:：]", b)
    if not m:
        return None
    q = b[:m.start()].strip()
    rest = b[m.end():].strip()
    q = re.sub(r"^#{1,6}\s*", "", q, flags=re.M)
    q = re.sub(r"^\*\*(.+?)\*\*\s*$", r"\1", q, flags=re.M).strip()
    mm = re.search(r"解\s*析\s*[:：]", rest)
    if mm:
        ans = rest[:mm.start()].strip()
        sol = rest[mm.end():].strip()
    else:
        ans, sol = rest, ""
    if len(normalize(q)) < 4 or not (ans or sol):
        return None
    return q, ans, sol


def import_md_txt(path):
    """Markdown / 纯文本导入：按题号切块，靠「答案：」定位答案。"""
    ok = 0
    try:
        txt = open(path, "r", encoding="utf-8-sig", errors="replace").read()
    except Exception as e:
        return False, f"读不了文件：{e}"
    blocks, cur = [], []
    for ln in txt.replace("\r", "").split("\n"):
        st = ln.strip()
        if _Q_START.match(st) and cur and any("答案" in x for x in cur):
            blocks.append("\n".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append("\n".join(cur))

    for b in blocks:
        r = _parse_block(b)
        if not r:
            continue
        q, ans, sol = r
        body = f"答案：{ans}"
        if sol:
            body += f"\n解析：{sol}"
        if save(q, body, source="import"):
            ok += 1
    return True, ok


def import_json(path):
    ok = 0
    try:
        import json
        data = json.load(open(path, "r", encoding="utf-8-sig"))
    except Exception as e:
        return False, f"JSON 解析失败：{e}"
    if isinstance(data, dict):
        data = data.get("items") or data.get("questions") or []
    if not isinstance(data, list):
        return False, "JSON 里没有题目列表"
    for row in data:
        if not isinstance(row, dict):
            continue
        q = row.get("q") or row.get("question") or row.get("q_text") or ""
        a = row.get("a") or row.get("answer") or ""
        sol = row.get("solution") or row.get("explain") or ""
        if q and (a or sol):
            body = f"答案：{a}"
            if sol:
                body += f"\n解析：{sol}"
            if save(q, body, source="import"):
                ok += 1
    return True, ok


def import_any(path):
    """按后缀自动选择导入方式。返回 (ok, n_or_msg)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return import_csv(path)
    if ext == ".json":
        return import_json(path)
    if ext in (".md", ".markdown", ".txt", ".text"):
        return import_md_txt(path)
    # 兜底：先当文本试
    return import_md_txt(path)


init_db()
