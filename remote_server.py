# -*- coding: utf-8 -*-
"""remote_server.py —— 手机遥控 PC 搜题（局域网）。

PC 静默跑一个 HTTP 服务，手机浏览器打开就能：
  · 选要截的窗口（或全屏）
  · 看窗口预览
  · 点「截图搜题」→ PC 截图+OCR+AI → 答案回手机
  · 收藏进 PC 题库

安全：配对码（6 位，PC 上显示）换一次性 token；token 存手机 localStorage。
也接受「同步码的同步密钥」直接配对（老用户不用再输码）。
"""
import base64
import ctypes
import hashlib
import ctypes.wintypes as wt
import io
import json
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

_CTX = {
    "cfg": {},
    "status": None,
    "log": None,
    "token": "",
    "pair_code": "",
    "target": None,   # None = 全屏；否则 hwnd
    "port": 18765,
    "engine": None,
    "lock": threading.Lock(),
}

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

_HTML = r"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Knowlib 遥控搜题</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--fg:#1b1b1b;--fg2:#6b7280;--line:#e5e7eb;--accent:#0067C0}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1f1f1f;--fg:#f2f2f2;--fg2:#a1a1a6;--line:#333;--accent:#4CC2FF}}
*{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 'Segoe UI','Microsoft YaHei UI',system-ui;padding:16px;word-break:break-word}
select{max-width:100%;text-overflow:ellipsis}img,table{max-width:100%}
.wrap{max-width:680px;margin:0 auto}h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--fg2);font-size:12.5px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
input,select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg);font-size:15px}
select{appearance:none;-webkit-appearance:none;padding-right:34px;background-repeat:no-repeat;background-position:right 12px center;background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' fill='none' stroke='%236b7280' stroke-width='1.8' stroke-linecap='round'/%3E%3C/svg%3E")}
button{padding:11px 16px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:15px;font-weight:600}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg);font-weight:400}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.row>*{flex:1 1 auto;min-width:0}
.row>.tag{flex:0 0 auto;align-self:center}
img.prev{width:100%;border-radius:10px;border:1px solid var(--line);display:block;margin-top:8px}
.ans{white-space:pre-wrap;font-size:15px;line-height:1.75}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;background:var(--bg);border:1px solid var(--line);color:var(--fg2)}
.err{color:#d9534f;font-size:13px}
.hide{display:none}
</style></head><body><div class="wrap">
<h1>Knowlib 遥控搜题</h1>
<div class="sub">手机上操作，PC 后台截图 + 识别 + 作答，屏幕上不会弹任何东西。</div>

<div id="pairBox" class="card hide">
  <div style="margin-bottom:8px">第一次用：输入 PC 上显示的 <b>配对码</b></div>
  <input id="code" inputmode="numeric" placeholder="6 位配对码" autocomplete="off">
  <div class="row" style="margin-top:10px"><button onclick="pair()">配对</button></div>
  <div id="pairMsg" class="err" style="margin-top:8px"></div>
</div>

<div id="main" class="hide">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <b>截哪块屏幕</b><span id="state" class="tag">未连接</span>
    </div>
    <select id="win" onchange="pick()"></select>
    <div class="row" style="margin-top:10px">
      <button class="ghost" onclick="refresh()">刷新窗口</button>
      <span id="pvState" class="tag">未预览</span>
    </div>
    <img id="prev" class="prev hide">
  </div>

  <div class="card">
    <div class="row"><button id="go" onclick="ask()">📸 截图搜题</button></div>
    <div id="msg" class="err" style="margin-top:8px"></div>
  </div>

  <div id="ansCard" class="card hide">
    <div style="display:flex;justify-content:space-between;margin-bottom:8px">
      <b>答案</b><span id="cost" class="tag"></span>
    </div>
    <div id="q" class="sub" style="margin-bottom:8px"></div>
    <div id="ans" class="ans"></div>
    <div class="row" style="margin-top:12px">
      <button class="ghost" onclick="copyAns()">复制</button>
      <button class="ghost" onclick="saveBank()">收藏到题库</button>
    </div>
  </div>
</div>
<script>
var T='', LAST=null;
function $(id){return document.getElementById(id);}
function token(){try{return localStorage.getItem('kl.remote.token')||'';}catch(e){return '';}}
function setToken(t){try{localStorage.setItem('kl.remote.token',t);}catch(e){}}
function api(path,opt){
  opt=opt||{};opt.headers=Object.assign({'Content-Type':'application/json'},opt.headers||{});
  var u=path+(path.indexOf('?')>=0?'&':'?')+'t='+encodeURIComponent(T);
  return fetch(u,opt).then(function(r){return r.json().then(function(j){return {s:r.status,j:j};});});
}
function pair(){
  var c=$('code').value.trim(); if(!c){$('pairMsg').textContent='输入配对码';return;}
  fetch('/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c})})
   .then(function(r){return r.json();}).then(function(j){
     if(!j.ok){$('pairMsg').textContent=j.message||'配对失败';return;}
     T=j.token;setToken(T);boot();
   }).catch(function(e){$('pairMsg').textContent='网络错误：'+e.message;});
}
function boot(){
  T=token();
  if(!T){$('pairBox').classList.remove('hide');$('main').classList.add('hide');return;}
  $('pairBox').classList.add('hide');$('main').classList.remove('hide');refresh();
}
function refresh(){
  api('/api/windows').then(function(r){
    if(r.s===403){localStorage.removeItem('kl.remote.token');boot();return;}
    var sel=$('win');sel.innerHTML='';
    var o=document.createElement('option');o.value='';o.textContent='全屏（主屏）';sel.appendChild(o);
    (r.j.windows||[]).forEach(function(w){
      var t=w.title.length>34?w.title.slice(0,34)+'…':w.title;
      var x=document.createElement('option');x.value=w.id;x.textContent=t+'  ('+w.w+'×'+w.h+')'+(w.min?' · 已最小化':'');
      sel.appendChild(x);
    });
    if(r.j.target){sel.value=String(r.j.target);}
    $('state').textContent='已连接';
    if(PV_AUTO){ preview(); }
  }).catch(function(e){$('state').textContent='连接失败';});
}
function pick(){
  api('/api/target',{method:'POST',body:JSON.stringify({win:$('win').value})}).then(function(){PV_HASH='';preview();});
}
var PV_TIMER=null, PV_HASH='', PV_AUTO=true;
function toggleAuto(){
  PV_AUTO=true;
  var b=$('autoBtn'); if(b){ b.style.display='none'; }
  preview();
}
function stopPv(){ if(PV_TIMER){clearInterval(PV_TIMER);PV_TIMER=null;} }
function preview(){
  var img=$('prev');
  img.classList.remove('hide');
  img.onerror=function(){ $('pvState').textContent='预览失败'; };
  stopPv();
  drawPv(true);
  if(PV_AUTO){
    PV_TIMER=setInterval(function(){
      if(document.hidden) return;      // 切到后台就暂停，省电
      drawPv(false);
    }, 1500);
  }
}
function drawPv(force){
  var img=$('prev');
  var win=$('win').value;
  var u='/api/preview?json=1&t='+encodeURIComponent(T)+'&win='+encodeURIComponent(win);
  if(!force && PV_HASH){ u+='&since='+encodeURIComponent(PV_HASH); }
  fetch(u).then(function(r){return r.json();}).then(function(j){
    if(!j.ok){ $('pvState').textContent='预览失败'; return; }
    if(j.same){ $('pvState').textContent='实时'; return; }
    PV_HASH=j.hash||'';
    img.src=j.img;
    $('pvState').textContent='实时 · '+new Date().toLocaleTimeString().slice(0,8);
  }).catch(function(){ $('pvState').textContent='连接断开'; });
}
function ask(){
  $('go').disabled=true;$('msg').textContent='正在截图识别…';$('ansCard').classList.add('hide');
  var t0=Date.now();
  api('/api/ask',{method:'POST',body:JSON.stringify({win:$('win').value})}).then(function(r){
    $('go').disabled=false;
    if(!r.j.ok){$('msg').textContent=r.j.message||'失败';return;}
    $('msg').textContent='';
    LAST=r.j;
    $('q').textContent=r.j.q?(r.j.q.slice(0,160)+(r.j.q.length>160?'…':'')):'';
    $('ans').textContent=r.j.answer||'';
    $('cost').textContent=((Date.now()-t0)/1000).toFixed(1)+' 秒';
    $('ansCard').classList.remove('hide');
  }).catch(function(e){$('go').disabled=false;$('msg').textContent='网络错误：'+e.message;});
}
function copyText(t){
  if(navigator.clipboard && window.isSecureContext){ return navigator.clipboard.writeText(t); }
  return new Promise(function(res,rej){
    var ta=document.createElement('textarea'); ta.value=t; ta.style.position='fixed'; ta.style.top='0'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    var ok=false; try{ ok=document.execCommand('copy'); }catch(e){}
    document.body.removeChild(ta);
    ok?res():rej(new Error('copy failed'));
  });
}
function copyAns(){ if(!LAST)return; copyText(LAST.answer||'').then(function(){$('msg').textContent='已复制';}).catch(function(){$('msg').textContent='复制失败，长按答案手动复制';}); }
function saveBank(){ if(!LAST)return; api('/api/save',{method:'POST',body:JSON.stringify({q:LAST.q,a:LAST.answer})}).then(function(r){$('msg').textContent=r.j.ok?'已收藏到 PC 题库':'收藏失败';}); }
boot();
</script></body></html>"""


def _ua_is_mobile(ua):
    ua = ua or ""
    return any(k in ua for k in ("Android", "iPhone", "iPad", "Mobile", "MicroMessenger"))


def list_windows():
    """枚举可见的顶层窗口。"""
    out = []
    try:
        def cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                _min = bool(user32.IsIconic(hwnd))
                _pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
                if _pid.value == os.getpid():
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = (buf.value or "").strip()
                if not title:
                    return True
                ex = user32.GetWindowLongW(hwnd, -20)
                if ex & 0x00000080:      # WS_EX_TOOLWINDOW
                    return True
                rect = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                if w < 100 or h < 80 or rect.left <= -30000:
                    return True
                out.append({"id": int(hwnd), "title": title[:80], "w": w, "h": h,
                            "min": _min})
            except Exception:
                pass
            return True

        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        user32.EnumWindows(CB(cb), 0)
    except Exception:
        pass
    return out


def _capture_hwnd(hwnd):
    """PrintWindow 抓窗口（即使被遮住也行）。失败返回 None。"""
    try:
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        hdc = user32.GetWindowDC(hwnd)
        if not hdc:
            return None
        mfc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mfc, bmp)
        ok = user32.PrintWindow(hwnd, mfc, 2)   # PW_RENDERFULLCONTENT
        gdi32.SelectObject(mfc, old)

        class BMIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        bmi = BMIH()
        bmi.biSize = ctypes.sizeof(BMIH)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mfc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mfc)
        user32.ReleaseDC(hwnd, hdc)
        if not ok or got == 0:
            return None
        from PIL import Image
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        return img
    except Exception:
        return None


def _capture_rect(rect):
    try:
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=(rect[0], rect[1], rect[2], rect[3]), all_screens=True)
    except Exception:
        return None


def capture(target):
    """target=None → 全屏；否则窗口句柄。"""
    if target:
        try:
            if user32.IsIconic(int(target)):
                return None
        except Exception:
            pass
        img = _capture_hwnd(int(target))
        if img is not None:
            return img
        rect = wt.RECT()
        user32.GetWindowRect(int(target), ctypes.byref(rect))
        return _capture_rect((rect.left, rect.top, rect.right, rect.bottom))
    rect = wt.RECT()
    user32.GetSystemMetrics.restype = ctypes.c_int
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return _capture_rect((0, 0, sw, sh))


def _jpeg_b64(img, quality=80, max_w=900):
    try:
        from PIL import Image
        w, h = img.size
        if w > max_w:
            img = img.resize((max_w, int(h * max_w / w)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _authed(self, qs):
        t = (qs.get("t", [""])[0] or "")
        return bool(t) and t == _CTX["token"]

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, _HTML, "text/html; charset=utf-8")
            return
        if u.path == "/api/windows":
            if not self._authed(qs):
                self._json(403, {"ok": False, "message": "no token"})
                return
            wins = list_windows()
            self._json(200, {"ok": True, "windows": wins, "target": _CTX["target"],
                             "pc": socket.gethostname()})
            return
        if u.path == "/api/preview":
            if not self._authed(qs):
                self._json(403, {"ok": False})
                return
            wid = qs.get("win", [""])[0]
            tgt = None if wid in ("", "0") else int(wid)
            img = capture(tgt)
            if img is None:
                self._json(200, {"ok": False, "message": "截图失败"})
                return
            b64 = _jpeg_b64(img, quality=70, max_w=760)
            try:
                raw = base64.b64decode(b64)
            except Exception:
                raw = b""
            # json=1：带哈希，页面可以「只在画面变了才刷新」
            if qs.get("json", [""])[0] in ("1", "true"):
                h = hashlib.md5(raw).hexdigest()[:12]
                if qs.get("since", [""])[0] == h:
                    self._json(200, {"ok": True, "same": True, "hash": h})
                    return
                self._json(200, {"ok": True, "same": False, "hash": h,
                                 "img": "data:image/jpeg;base64," + b64})
                return
            self._send(200, raw, "image/jpeg")
            return
        self._json(404, {"ok": False})

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            body = {}

        if u.path == "/api/pair":
            code = str(body.get("code") or "").strip()
            ok = False
            if code and code == _CTX["pair_code"]:
                ok = True
            if not ok:
                key = (_CTX["cfg"].get("sync_key") or "").strip()
                if key and code == key:
                    ok = True
            self._json(200, {"ok": ok, "token": _CTX["token"] if ok else "",
                             "message": "" if ok else "配对码不对"})
            return

        if not self._authed(qs):
            self._json(403, {"ok": False, "message": "no token"})
            return

        if u.path == "/api/target":
            wid = str(body.get("win") or "").strip()
            _CTX["target"] = None if wid in ("", "0") else int(wid)
            self._json(200, {"ok": True, "target": _CTX["target"]})
            return

        if u.path == "/api/save":
            q = str(body.get("q") or "").strip()
            a = str(body.get("a") or "").strip()
            if not q or not a:
                self._json(200, {"ok": False, "message": "空内容"})
                return
            try:
                import question_bank as qbank
                ok = qbank.save(q, a)
                self._json(200, {"ok": bool(ok)})
            except Exception as e:
                self._json(200, {"ok": False, "message": str(e)})
            return

        if u.path == "/api/ask":
            wid = str(body.get("win") or "").strip()
            tgt = None if wid in ("", "0") else int(wid)
            self._json(200, self._do_ask(tgt))
            return

        self._json(404, {"ok": False})

    # ---------- 真活儿
    def _do_ask(self, target):
        import ocr_ai
        t0 = time.time()
        try:
            img = capture(target)
        except Exception as e:
            return {"ok": False, "message": "截图失败：%s" % e}
        if img is None:
            return {"ok": False, "message": "截图失败：这个窗口已最小化或已关闭，先在电脑上点开它"}
        try:
            if _CTX["engine"] is None:
                _CTX["engine"] = ocr_ai.OcrEngine()
            text, conf, ocr_cost = _CTX["engine"].recognize(img)
        except Exception as e:
            return {"ok": False, "message": "识别失败：%s" % e}
        if not text or len(text) < 4:
            return {"ok": False, "message": "没识别到文字（换个窗口/区域试试）"}
        try:
            client = ocr_ai.AiClient(_CTX["cfg"])
            full, ai_cost = client.solve(text)
        except Exception as e:
            return {"ok": False, "message": "AI 出错：%s" % e}
        return {"ok": True, "q": text, "answer": full,
                "ocr": round(ocr_cost, 2), "ai": round(ai_cost, 2),
                "total": round(time.time() - t0, 2)}


def start(cfg, port=18765, log=None):
    """启动遥控服务，返回 (ok, 说明, url_hint)。"""
    global _CTX
    if _CTX.get("server") is not None:
        return True, "已经在运行", "http://%s:%d/" % (_local_ip(), _CTX["port"])
    _CTX["cfg"] = dict(cfg or {})
    _CTX["log"] = log
    _CTX["token"] = secrets.token_urlsafe(18)
    _CTX["pair_code"] = "%06d" % secrets.randbelow(1000000)
    _CTX["target"] = None
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", int(port)), _Handler)
    except Exception as e:
        return False, "端口被占用：%s" % e, ""
    _CTX["server"] = srv
    _CTX["port"] = int(port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    ip = _local_ip()
    return True, "遥控已开启", "http://%s:%d/" % (ip, _CTX["port"])


def stop():
    srv = _CTX.pop("server", None)
    if srv:
        try:
            srv.shutdown()
        except Exception:
            pass
        return True
    return False


def info():
    return {
        "running": _CTX.get("server") is not None,
        "port": _CTX.get("port"),
        "pair_code": _CTX.get("pair_code") or "",
        "ip": _local_ip(),
        "url": "http://%s:%s/" % (_local_ip(), _CTX.get("port") or ""),
    }


def set_cfg(cfg):
    _CTX["cfg"] = dict(cfg or {})
