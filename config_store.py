"""配置存取：数据统一放 %APPDATA%\\Knowlib（升级/卸载都不丢），便携模式可放 exe 同目录。

迁移规则（首次运行新版本时自动执行，只复制不删除）：
  exe 同目录 / %APPDATA%\\XiaoTuanSouTi  →  %APPDATA%\\Knowlib
涉及文件：config.json（含 API Key）、bank.db（本地题库）、history.jsonl（历史）
"""
import json
import os
import shutil
import sys

APP_NAME = "XiaoTuanSouTi"      # 旧目录名，仅用于迁移
BRAND = "Knowlib"               # 新数据目录名
REG_NAME = "Knowlib"            # 开机自启的注册表项名
APP_VERSION = "1.3.1"

DATA_FILES = ("config.json", "bank.db", "history.jsonl")

PROVIDERS = {
    "DeepSeek 官方": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_hint": "sk- 开头，platform.deepseek.com 获取",
    },
    "OpenAI 兼容（中转/其他）": {
        "base_url": "",
        "model": "",
        "key_hint": "填中转站的 Key，端点一般以 /v1 结尾",
    },
    "Ollama 本地（免费）": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5vl:7b",
        "key_hint": "本地模型不需要真 Key，随便填 ollama",
    },
}

DEFAULTS = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "provider": "DeepSeek 官方",
    "temperature": 0.2,
    "max_tokens": 900,
    "scan_interval": 1.2,
    "dedupe": True,
    "bank_first": True,
    "check_update": True,
    "close_action": "tray",
    "animations": True,
    "hotkeys": {"region": "F1", "pause": "F2", "shot": "F3",
                "again": "F4", "overlay": "F5"},
    "region": {"left": 100, "top": 100, "width": 900, "height": 420},
    "prompt": (
        "你是解题助手。下面是一道题的截图文字识别结果。请：\n"
        "1) 先还原题目（题干+选项/空），修正 OCR 明显错字；\n"
        "2) 给出最终答案（选择题只给字母，多选用逗号分隔；填空/简答给内容）；\n"
        "3) 再用 3~6 句说明为什么，关键步骤写清楚。\n"
        "输出格式：\n答案：xxx\n解析：xxx"
    ),
}


def exe_dir():
    base = sys.argv[0] if getattr(sys, "frozen", False) else __file__
    return os.path.dirname(os.path.abspath(base))


def appdata_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, BRAND)


def is_portable():
    """exe 同目录放一个 portable.flag 就切成便携模式（数据跟程序走）。"""
    try:
        return os.path.exists(os.path.join(exe_dir(), "portable.flag"))
    except Exception:
        return False


def data_dir():
    if is_portable():
        return exe_dir()
    d = appdata_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _migrate_once():
    """把旧位置的数据复制到新目录（目标已存在就不动，绝不删除源文件）。"""
    if is_portable():
        return
    dst = appdata_dir()
    candidates = [exe_dir(),
                  os.path.join(os.environ.get("APPDATA", ""), APP_NAME)]
    done = getattr(_migrate_once, "_done", False)
    if done:
        return
    _migrate_once._done = True
    for src in candidates:
        try:
            if not src or not os.path.isdir(src):
                continue
            if os.path.abspath(src) == os.path.abspath(dst):
                continue
            for name in DATA_FILES:
                s = os.path.join(src, name)
                d = os.path.join(dst, name)
                if os.path.exists(s) and not os.path.exists(d):
                    shutil.copy2(s, d)
        except Exception:
            continue


def config_path():
    _migrate_once()
    return os.path.join(data_dir(), "config.json")


def load():
    cfg = dict(DEFAULTS)
    p = config_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    return cfg


def save(cfg):
    p = config_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True, p
    except Exception as e:
        return False, str(e)


def history_path():
    return os.path.join(os.path.dirname(config_path()), "history.jsonl")


def load_history(limit=200):
    """读历史，最新的在前。"""
    p = history_path()
    if not os.path.exists(p):
        return []
    items = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    items.reverse()
    return items[:limit]


def clear_history():
    try:
        p = history_path()
        if os.path.exists(p):
            open(p, "w", encoding="utf-8").close()
        return True
    except Exception:
        return False


def write_history(items):
    """整份重写 history.jsonl（items 按时间从旧到新）。会先备份一份。"""
    try:
        p = history_path()
        if os.path.exists(p):
            shutil.copy2(p, p + ".bak")
        with open(p, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def dedupe_history(items, keep_valid=True, threshold=0.82):
    """items 按“最新在前”。返回 (保留列表, 被丢掉的数量)。

    同题识别多次会因 OCR 噪声（提交成功/输入答案/候选字乱码）变成不同 norm，
    所以除了精确相同，还要用相似度合并（同一题的截图变体）。
    """
    from difflib import SequenceMatcher
    kept, dropped = [], 0
    norms = []
    for it in items:
        q = (it.get("q") or "").strip()
        a = (it.get("a") or "").strip()
        if keep_valid:
            try:
                import paper_export
                if not paper_export.is_valid(q, a):
                    dropped += 1
                    continue
            except Exception:
                pass
        try:
            import paper_export
            n = paper_export.norm_key(q)
        except Exception:
            n = q
        dup = False
        for old in norms:
            if abs(len(old) - len(n)) > max(20, len(n) * 0.5):
                continue
            short, long_ = (n, old) if len(n) <= len(old) else (old, n)
            r1 = SequenceMatcher(None, n, old).ratio()
            # OCR 截断（只括到一半）也算重复
            r2 = SequenceMatcher(None, short, long_[:len(short) + 2]).ratio()
            if max(r1, r2) >= threshold:
                dup = True
                break
        if dup:
            dropped += 1
            continue
        kept.append(it)
        norms.append(n)
    return kept, dropped


def clean_history(keep_valid=True, dedupe=True):
    """清理历史：去重（同题只留最新）+ 丢掉“无法作答/界面文字”这类无效记录。"""
    p = history_path()
    if not os.path.exists(p):
        return {"before": 0, "after": 0, "removed": 0, "dups": 0}
    raw = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return {"before": 0, "after": 0, "removed": 0, "dups": 0}

    before = len(raw)
    newest_first = list(reversed(raw))
    if dedupe:
        kept, dropped = dedupe_history(newest_first, keep_valid=keep_valid)
    else:
        kept, dropped = newest_first, 0
    kept.reverse()                     # 回到从旧到新
    write_history(kept)
    return {"before": before, "after": len(kept), "removed": dropped, "dups": dropped}


# ---------------------------------------------------------------- 开机自启
def launch_command():
    """返回开机自启要执行的命令行；源码运行则用 pythonw 跑 app.py。"""
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.argv[0])}" --tray'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "app.py"))
    return f'"{exe}" "{script}" --tray'


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            try:
                winreg.QueryValueEx(k, REG_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception:
        return False


def set_autostart(on):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, REG_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, REG_NAME)
                except FileNotFoundError:
                    pass
        return True, "ok"
    except Exception as e:
        return False, str(e)





