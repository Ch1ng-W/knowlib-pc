"""配置存取：优先放在 exe 同目录（便携），写不进去就落到用户目录。"""
import json
import os
import sys

APP_NAME = "XiaoTuanSouTi"

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
