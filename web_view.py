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
    from PyQt5.QtCore import QUrl
    HAVE_WEBENGINE = True
except Exception:            # 没装 / 装坏了
    HAVE_WEBENGINE = False


def web_available():
    return HAVE_WEBENGINE and os.path.exists(os.path.join(WEB_DIR, "q.html"))


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
    view.load(QUrl.fromLocalFile(os.path.join(WEB_DIR, "q.html")))


def current_index(view, cb):
    """取网页里当前题号（0 起）。"""
    view.page().runJavaScript(
        "try{ (typeof i==='number') ? i : -1 }catch(e){ -1 }", cb)


_SAVE_JS = r"""
(function(){
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
