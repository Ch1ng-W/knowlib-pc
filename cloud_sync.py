# -*- coding: utf-8 -*-
"""cloud_sync.py —— Knowlib 跨端同步（同步码 + 写入密钥）。

数据放 knowlib.fun：
  q  题库（key = 题目 norm，天然去重）
  h  搜题历史（id = hash(时间+题目+答案)）
  p  我发布过的在线试卷（code/title/key）
冲突策略：同一条取 t（更新时间）更大的那份。
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

import config_store
import question_bank as qbank

BASE = "https://knowlib.fun"
UA = "KnowlibPC/1.0 (+https://knowlib.fun)"


def _req(method, path, obj=None, timeout=20):
    data = None if obj is None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


def new_sync_code():
    """新建一个同步码，返回 {ok, code, key, hub}。"""
    return _req("POST", "/api/sync/new")[1]


# ---------------------------------------------------------------- 本地历史
def history_path():
    return os.path.join(os.path.dirname(config_store.config_path()), "history.jsonl")


def local_history(limit=500):
    out = []
    p = history_path()
    if not os.path.exists(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            it = json.loads(line)
        except Exception:
            continue
        out.append({
            "id": _hist_id(it),
            "t": _ts(it.get("t")),
            "q": (it.get("q") or "")[:3000],
            "a": (it.get("a") or "")[:4000],
        })
    return out


def _hist_id(it):
    raw = "%s|%s|%s" % (it.get("t") or "", it.get("q") or "", it.get("a") or "")
    return hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _ts(s):
    try:
        return int(time.mktime(time.strptime(str(s), "%Y-%m-%d %H:%M:%S")) * 1000)
    except Exception:
        return int(time.time() * 1000)


def merge_history(remote_items):
    """把云端历史补进本地 jsonl（按 id 去重）。返回新增条数。"""
    p = history_path()
    have = set(it["id"] for it in local_history(limit=2000))
    add = []
    for it in remote_items or []:
        if it.get("id") in have:
            continue
        q = it.get("q") or ""
        a = it.get("a") or ""
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime((it.get("t") or 0) / 1000.0))
        add.append(json.dumps({"t": ts, "q": q, "a": a, "cost": 0, "sync": True},
                              ensure_ascii=False))
        have.add(it.get("id"))
    if add:
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n".join(add) + "\n")
        except Exception:
            return 0
    return len(add)


# ---------------------------------------------------------------- 同步
def sync_now(cfg=None):
    """双向同步一次。返回 dict(ok, added, updated, hist_added, sent, msg)。"""
    cfg = cfg or config_store.load()
    code = (cfg.get("sync_code") or "").strip()
    key = (cfg.get("sync_key") or "").strip()
    if not code or not key:
        return {"ok": False, "msg": "还没设置同步码（先去设置 → 常规 → 云同步）"}

    qloc = qbank.export_sync()
    hloc = local_history()
    papers = []
    for c, info in (cfg.get("papers") or {}).items():
        papers.append({"code": c, "title": info.get("title") or "", "t": int(info.get("t") or 0),
                       "key": info.get("key") or ""})

    st, remote = _req("GET", "/api/sync?code=%s&key=%s" % (code, key))
    if st == 403:
        return {"ok": False, "msg": "同步密钥不对"}
    if st != 200 or not remote.get("ok"):
        return {"ok": False, "msg": remote.get("message") or "拉取失败（%s）" % st}

    added, updated = qbank.apply_remote(remote.get("q") or [])

    st2, res = _req("POST", "/api/sync", {"code": code, "key": key,
                                          "q": qloc, "h": hloc, "p": papers,
                                          "device": _device_info()})
    if st2 == 403:
        return {"ok": False, "msg": "同步密钥不对"}
    if st2 != 200 or not res.get("ok"):
        return {"ok": False, "msg": res.get("message") or "上传失败（%s）" % st2}

    added2, updated2 = qbank.apply_remote(res.get("q") or [])
    hist_added = merge_history(res.get("h") or [])

    # 把云端「我发布过的试卷」合并回本地，这样换设备也能在组卷菜单里看到后台
    papers_got = 0
    try:
        cfg2 = config_store.load()
        local = dict(cfg2.get("papers") or {})
        for it in (res.get("p") or []):
            c = str(it.get("code") or "")
            if not c:
                continue
            t = int(it.get("t") or 0)
            if c not in local or t > int((local[c] or {}).get("t") or 0):
                local[c] = {"key": it.get("key") or "", "title": it.get("title") or "", "t": t}
                papers_got += 1
        cfg2["papers"] = local
        config_store.save(cfg2)
    except Exception:
        pass

    return {
        "ok": True,
        "added": added + added2,
        "updated": updated + updated2,
        "hist_added": hist_added,
        "papers_got": papers_got,
        "devices": list((res.get("dev") or {}).values()),
        "sent": len(qloc),
        "total": len(res.get("q") or []),
        "msg": "同步完成：新增 %d、更新 %d、历史 +%d" % (added + added2, updated + updated2, hist_added),
    }
