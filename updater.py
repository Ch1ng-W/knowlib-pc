"""自动更新：查版本 → 下载安装包 → 静默安装。

优先用官网（国内直连），查不到再退回 GitHub Releases。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

import config_store

REPO = "Ch1ng-W/knowlib-pc"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
SITE_JSON = "https://knowlib.fun/download/version.json"
SITE_EXE = "https://knowlib.fun/download/Knowlib-PC-latest.exe"
UA = {"User-Agent": "Knowlib-PC-Updater"}


def parse_version(v):
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts[:3]) or (0,)


def current_version():
    return config_store.APP_VERSION


def is_newer(latest, current=None):
    return parse_version(latest) > parse_version(current or current_version())


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _check_site(timeout=8):
    data = _get_json(SITE_JSON, timeout)
    latest = (data.get("version") or "").lstrip("vV")
    url = data.get("url") or SITE_EXE
    return {"ok": True, "latest": latest, "url": url,
            "notes": data.get("notes") or "",
            "has_update": bool(latest) and is_newer(latest),
            "size": int(data.get("size") or 0),
            "source": "knowlib.fun"}


def _check_github(timeout=10):
    data = _get_json(API_LATEST, timeout)
    latest = (data.get("tag_name") or "").lstrip("vV")
    url = ""
    size = 0
    for a in data.get("assets") or []:
        if a.get("name", "").lower().endswith(".exe"):
            url = a.get("browser_download_url") or ""
            size = a.get("size") or 0
            break
    return {"ok": True, "latest": latest, "url": url,
            "notes": data.get("body") or "",
            "has_update": bool(latest) and bool(url) and is_newer(latest),
            "size": size, "source": "github"}


def check(timeout=10):
    """返回 dict：ok / latest / url / notes / has_update / source / error。"""
    err = ""
    try:
        r = _check_site(timeout)
        if r["latest"]:
            return r
        err = "site: empty version"
    except Exception as e:
        err = "site: %s: %s" % (type(e).__name__, e)
    try:
        return _check_github(timeout)
    except Exception as e:
        return {"ok": False, "has_update": False,
                "error": "%s | github: %s: %s" % (err, type(e).__name__, e)}


def download(url, on_progress=None, timeout=60):
    """下载到临时目录，返回文件路径。on_progress(已下载字节, 总字节)。"""
    name = url.split("/")[-1] or "Knowlib-update.exe"
    dest = os.path.join(tempfile.gettempdir(), name)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if on_progress:
                on_progress(got, total)
    return dest


def run_installer(path):
    """静默装（Inno Setup）。调用前请先退出本程序。"""
    args = [path, "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART"]
    if sys.platform.startswith("win"):
        # DETACHED_PROCESS，避免安装器成为本进程子进程被杀
        subprocess.Popen(args, close_fds=True,
                         creationflags=0x00000008 | 0x00000200)
    else:
        subprocess.Popen(args)
