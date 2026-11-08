# -*- coding: utf-8 -*-
"""web_view.py —— 用 QtWebEngine 跑网页版试卷页（q.html + 本地 KaTeX）。

好处：
- 公式直接用 KaTeX 渲染，不再把 \\frac 画成 PNG
- 答题页的样式/交互和 knowlib.fun 上完全一致
- 离线可用（katex 字体已打包到 web/katex/fonts）

没有装 PyQtWebEngine 时 web_available() 返回 False，调用方退回原来的 QTextBrowser。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_web():
    """源码运行 / PyInstaller onefolder(_internal) / onedir 都能找到 web/。"""
    cands = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "web"))
    cands.append(os.path.join(_HERE, "web"))
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands.append(os.path.join(exe_dir, "web"))
        cands.append(os.path.join(exe_dir, "_internal", "web"))
    except Exception:
        pass
    for c in cands:
        if os.path.exists(os.path.join(c, "q.html")):
            return c
    return cands[0] if cands else os.path.join(_HERE, "web")


WEB_DIR = _find_web()

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings, QWebEngineScript
    from PyQt5.QtCore import QUrl, Qt, QTimer
    HAVE_WEBENGINE = True
except Exception:            # 没装 / 装坏了
    HAVE_WEBENGINE = False


def _page_path():
    """桌面版优先（pc_quiz.html），没有就退回网页原版。"""
    for name in ("pc_quiz.html", "q.html"):
        p = os.path.join(WEB_DIR, name)
        if os.path.exists(p):
            return p
    return os.path.join(WEB_DIR, "q.html")


def web_available():
    return HAVE_WEBENGINE and os.path.exists(_page_path())


class QuizView(object):
    """延迟创建：只有真要用的时候才 new，避免拖慢启动。"""

    def __new__(cls, parent=None):
        if not HAVE_WEBENGINE:
            raise RuntimeError("PyQtWebEngine not available")

        view = QWebEngineView(parent)
        st = view.settings()
        st.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        st.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        st.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        st.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
        view._kl_script = None
        return view


def load_paper(view, doc):
    """把试卷数据注入并加载本地 q.html。"""
    data = dict(doc or {})
    if not data.get("logo"):
        logo = os.path.join(WEB_DIR, "logo-line.png")
        if os.path.exists(logo):
            data["logo"] = QUrl.fromLocalFile(logo).toString()
    js = "window.__KLDATA__ = %s;" % json.dumps(data, ensure_ascii=False)

    scr = QWebEngineScript()
    scr.setName("kl_data")
    scr.setInjectionPoint(QWebEngineScript.DocumentCreation)
    scr.setWorldId(QWebEngineScript.MainWorld)
    scr.setRunsOnSubFrames(False)
    scr.setSourceCode(js)
    try:
        view.page().scripts().remove(view._kl_script)
    except Exception:
        pass
    view.page().scripts().insert(scr)
    view._kl_script = scr
    view.load(QUrl.fromLocalFile(_page_path()))


def current_index(view, cb):
    """取网页里当前题号（0 起）。"""
    view.page().runJavaScript(
        "try{ (typeof i==='number') ? i : -1 }catch(e){ -1 }", cb)


_SAVE_JS = r"""
(function(){
  if(window.__KLPC__) return 'pc-native';
  if(window.__klSaveHooked) return 'already';
  window.__klSaveHooked=true;
  function addBtn(){
    var q=document.getElementById('q'); if(!q) return;
    var card=q.querySelector('.card'); if(!card) return;
    if(card.querySelector('.klsavebtn')) return;
    var b=document.createElement('button');
    b.type='button';
    b.className='qbtn klsavebtn';
    b.textContent='＋ 加入题库';
    b.style.marginLeft='.5rem';
    b.onclick=function(ev){
      if(ev){ ev.preventDefault(); }
      var idx=(typeof i==='number')?i:0;
      b.textContent='已加入 ✓';
      b.disabled=true;
      b.style.opacity='.55';
      location.hash='klsave:'+idx;
      return false;
    };
    card.appendChild(b);
  }
  if(document.readyState!=='loading'){ addBtn(); }
  document.addEventListener('DOMContentLoaded', addBtn);
  try{ new MutationObserver(addBtn).observe(document.body,{childList:true,subtree:true}); }catch(e){}
  setInterval(addBtn, 1200);
  return 'ok';
})()
"""


def install_save_button(view):
    """每题卡片上加一个「加入题库」按钮，点完通过 #klsave:N 通知 Python。"""
    view.page().runJavaScript(_SAVE_JS)


# ================================================================ 公式视图
# 答案窗 / 历史预览共用：把 Markdown + LaTeX 交给本地 KaTeX 渲染，
# 比 QTextBrowser（分式画成 PNG）整齐得多。

def _math_path():
    return os.path.join(WEB_DIR, "math.html")


def math_available():
    return HAVE_WEBENGINE and os.path.exists(_math_path())


def make_math_view(parent=None, transparent=False, menu=False):
    """建一个跑 math.html 的 QWebEngineView。

    transparent=True 用于答案窗（无边框 + 半透明卡片），
    历史预览传 False，用页面自己给的底色。
    """
    if not HAVE_WEBENGINE:
        raise RuntimeError("PyQtWebEngine not available")

    view = QWebEngineView(parent)
    st = view.settings()
    st.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
    st.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    st.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
    st.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
    st.setAttribute(QWebEngineSettings.ShowScrollBars, True)

    if not menu:
        view.setContextMenuPolicy(Qt.NoContextMenu)
    if transparent:
        try:
            view.setAttribute(Qt.WA_TranslucentBackground, True)
            view.setStyleSheet("background:transparent;border:none;")
            view.page().setBackgroundColor(Qt.transparent)
        except Exception:
            pass

    view._kl_ready = False
    view._kl_payload = None
    view._kl_error = ""
    view.loadFinished.connect(lambda ok: _math_loaded(view, ok))
    view.load(QUrl.fromLocalFile(_math_path()))
    return view


def _math_loaded(view, ok):
    view._kl_ready = bool(ok)
    if not ok:
        view._kl_error = "math.html 加载失败"
        return
    payload = getattr(view, "_kl_payload", None)
    if payload is not None:
        _math_push(view, payload)


def _math_push(view, payload):
    try:
        js = "window.klRender(%s);" % json.dumps(payload, ensure_ascii=False)
        view.page().runJavaScript(js)
    except Exception:
        pass


def math_render(view, payload):
    """渲染一组 block；页面还没加载好就先记住，加载完自动补渲染。"""
    view._kl_payload = payload
    if getattr(view, "_kl_ready", False):
        _math_push(view, payload)


def math_clear(view):
    view._kl_payload = None
    if getattr(view, "_kl_ready", False):
        try:
            view.page().runJavaScript("window.klClear && window.klClear();")
        except Exception:
            pass


_WARM = {"win": None, "tries": 0}


def warm_up():
    """提前把 WebEngine 渲染进程 + KaTeX 字体跑热。

    第一次切到「历史记录 / 题库」时，QWebEngineView 首次真正绘制要建合成层、
    加载 KaTeX 字体，会卡一下。这里在屏幕外开个 8x8 的透明小窗先画一次，
    把进程/字体都预热好，之后切页就不卡了。
    """
    if not math_available():
        return False
    if _WARM.get("win") is not None:
        return True
    try:
        from PyQt5.QtWidgets import QWidget, QVBoxLayout

        win = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
        win.setAttribute(Qt.WA_TranslucentBackground)
        win.setAttribute(Qt.WA_ShowWithoutActivating)
        win.setFixedSize(8, 8)
        win.move(-4000, -4000)          # 屏幕外，看不见
        lay = QVBoxLayout(win)
        lay.setContentsMargins(0, 0, 0, 0)
        view = make_math_view(win, transparent=True)
        lay.addWidget(view)
        win.show()                      # 处于“可见”状态，Chromium 才会真正画
        # 一段带分式/根号/求和的样例，目的是把 KaTeX 字体全部触发一遍
        math_render(view, {
            "blocks": [{"k": "md", "v": (
                "$x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}$ "
                "$$\\sum_{i=1}^{n} i^2=\\frac{n(n+1)(2n+1)}{6}$$")}],
            "size": 12, "bg": "transparent",
        })
        _WARM["win"] = win
        return True
    except Exception:
        return False


def math_height(view, cb, _tries=0):
    """读内容实际高度（px），用来让网页视图贴合内容。"""
    if not getattr(view, "_kl_ready", False):
        if _tries < 8:
            QTimer.singleShot(60, lambda: math_height(view, cb, _tries + 1))
        else:
            cb(0)
        return

    def got(v):
        try:
            cb(int(v or 0))
        except Exception:
            cb(0)

    try:
        view.page().runJavaScript("window.klHeight ? window.klHeight() : 0", got)
    except Exception:
        got(0)
