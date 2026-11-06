"""配置存取：优先放在 exe 同目录（便携），写不进去就落到用户目录。"""
import json
import os
import sys

APP_NAME = "XiaoTuanSouTi"
REG_NAME = "Knowlib"        # 开机自启的注册表项名
APP_VERSION = "1.0.5"

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
    "region": {"left": 100, "top": 100, "width": 900, "height": 420},
    "prompt": (
        "你是解题助手。下面是一道题的截图文字识别结果。请：\n"
        "1) 先还原题目（题干+选项/空），修正 OCR 明显错字；\n"
        "2) 给出最终答案（选择题只给字母，多选用逗号分隔；填空/简答给内容）；\n"
        "3) 再用 3~6 句说明为什么，关键步骤写清楚。\n"
        "输出格式：\n答案：xxx\n解析：xxx"
    ),
}


def _dirs():
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
    user_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    return exe_dir, user_dir


def config_path():
    exe_dir, user_dir = _dirs()
    p1 = os.path.join(exe_dir, "config.json")
    try:
        if os.path.exists(p1):
            return p1
        open(p1, "a").close()
        os.remove(p1)
        return p1
    except Exception:
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, "config.json")


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
