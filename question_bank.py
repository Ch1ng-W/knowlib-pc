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


init_db()
