"""小团搜题 — 截图 + OCR + AI 解题，带状态反馈的桌面小工具。"""
import io
import os
import re
import sys
import time
import shutil
import ctypes
import ctypes.wintypes  # noqa: F401
import hashlib
import html as _html
import traceback

# ⚠️ 先导入 OCR（onnxruntime），再导入 PyQt5，否则会 0xC0000005 崩溃
import ocr_ai  # noqa: F401  (顺序敏感)

from PyQt5.QtCore import (Qt, QThread, pyqtSignal, QObject, QTimer, QPoint, QRect, QRectF, QSize,
                          QEvent, QPropertyAnimation, QEasingCurve, QVariantAnimation,
                          QAbstractAnimation)
from PyQt5.QtGui import (QColor, QFont, QPainter, QPen, QBrush, QIcon, QPixmap,
                         QTextCursor, QGuiApplication, QDesktopServices)
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QDialog, QLineEdit, QComboBox, QFormLayout, QMessageBox,
    QTextBrowser, QCheckBox, QSpinBox, QSizePolicy, QFrame, QMenu, QAction, QGridLayout,
    QListWidget, QListWidgetItem, QSystemTrayIcon, QStyle, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QStackedWidget, QScrollArea, QSlider, QInputDialog,
)

import config_store
import md_render
import online_quiz
import question_bank as qbank
import remote_server
import updater

try:
    import web_view
except Exception:          # 没装 PyQtWebEngine / 资源缺失
    web_view = None

APP_TITLE = "Knowlib"

# 窗口四周边距（现由系统 DWM 画阴影/圆角，不再自绘，所以为 0）
SHADOW_MARGIN = 0

# Win32：给无边框窗口加回原生 WS_THICKFRAME，
# 这样系统自带的拖拽缩放 / Aero Snap / 最小化还原动画都能用
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084


def enable_native_frame(widget):
    """加上原生缩放边框样式（只留能力，不画系统标题栏），并让 DWM 画圆角。"""
    try:
        hwnd = int(widget.winId())
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        u.SetWindowLongW(hwnd, GWL_STYLE,
                         style | WS_THICKFRAME | WS_MINIMIZEBOX
                         | WS_MAXIMIZEBOX | WS_SYSMENU)
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        try:
            val = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass
    except Exception:
        pass

COLOR_OK = "#3ddc84"
COLOR_WARN = "#ffb020"
COLOR_ERR = "#ff5c5c"
COLOR_IDLE = "#8a8f98"
COLOR_ACCENT = "#5b9dff"


def resource_path(name):
    """找打包进 exe 的资源：PyInstaller 6 放在 _internal 里。"""
    root = os.path.dirname(os.path.abspath(
        sys.argv[0] if getattr(sys, "frozen", False) else __file__))
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, name))
    cands += [os.path.join(root, name), os.path.join(root, "_internal", name)]
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[-1]



# ---------------------------------------------------------------- 统一深色消息框
# 注意：主窗口的样式表会串到 QMessageBox 上（浅色字 + 默认白底 = 看不清），
# 所以所有消息框都显式套一层样式。
_BOX_QSS = (
    "QMessageBox{background:#1b1d22;}"
    "QMessageBox QLabel{color:#e6e9ef;font-size:13px;background:transparent;}"
    "QMessageBox QPushButton{background:#2b303a;color:#e6e9ef;border:none;"
    "border-radius:7px;padding:7px 18px;min-width:78px;font-size:13px;}"
    "QMessageBox QPushButton:hover{background:#39404d;}"
    "QMessageBox QPushButton:default{background:#3b6ef0;color:#ffffff;}"
)


def _box(parent, icon, title, text):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.setStyleSheet(_BOX_QSS)
    return box


class _DarkBox(QDialog):
    """现代化深色弹窗（替代老旧的 QMessageBox 样式）。"""

    KINDS = {
        "question": ("#f0b429", "？"),
        "warning": ("#f0b429", "！"),
        "error": ("#e5484d", "×"),
        "info": ("#3b82f6", "i"),
    }

    def __init__(self, parent, title, text, kind="info",
                 ok_text="确定", cancel_text=None, danger=False):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self._ok = False

        col, glyph = self.KINDS.get(kind, self.KINDS["info"])

        card = QFrame(self)
        card.setObjectName("boxCard")
        card.setStyleSheet(
            "#boxCard{background:#1b1f27;border:1px solid #2c3340;border-radius:14px;}"
            "QLabel{color:#e6e9ef;background:transparent;}"
            "QPushButton{background:#252b36;color:#dfe3ea;border:none;"
            "border-radius:9px;padding:8px 18px;font-size:13px;}"
            "QPushButton:hover{background:#2f3746;}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.addWidget(card)
        sh = QGraphicsDropShadowEffect(card)
        sh.setBlurRadius(30)
        sh.setOffset(0, 10)
        sh.setColor(QColor(0, 0, 0, 180))
        card.setGraphicsEffect(sh)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel(glyph)
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"color:{col};background:{col}22;border-radius:11px;"
            f"font-size:13px;font-weight:700;")
        head.addWidget(badge)
        t = QLabel(title)
        t.setStyleSheet("font-size:14px;font-weight:600;color:#eef1f6;")
        head.addWidget(t, 1)
        lay.addLayout(head)

        msg = QLabel(text or "")
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#a6afbd;font-size:12.5px;line-height:1.6;")
        lay.addWidget(msg)

        row = QHBoxLayout()
        row.addStretch(1)
        if cancel_text:
            b = QPushButton(cancel_text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(self.reject)
            row.addWidget(b)
        ok = QPushButton(ok_text)
        ok.setCursor(Qt.PointingHandCursor)
        ok.setDefault(True)
        ok_color = "#e5484d" if danger else "#3b6ef0"
        ok_hover = "#ef5f64" if danger else "#4c7df5"
        ok.setStyleSheet(
            f"QPushButton{{background:{ok_color};color:#fff;border:none;"
            f"border-radius:9px;padding:8px 20px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{background:{ok_hover};}}")
        ok.clicked.connect(self._do_ok)
        row.addWidget(ok)
        lay.addLayout(row)

        self.setFixedWidth(400)
        self.adjustSize()
        if parent is not None:
            try:
                g = parent.window().geometry()
                self.move(g.center().x() - self.width() // 2,
                          g.center().y() - self.height() // 2)
            except Exception:
                pass

    def _do_ok(self):
        self._ok = True
        self.accept()

    @property
    def ok(self):
        return self._ok


def ask_yes(parent, title, text, ok_text="确定", danger=True):
    d = _DarkBox(parent, title, text, "question", ok_text=ok_text,
                 cancel_text="取消", danger=danger)
    d.exec_()
    return d.ok


def warn_box(parent, title, text):
    _DarkBox(parent, title, text, "warning", ok_text="知道了").exec_()


def error_box(parent, title, text):
    _DarkBox(parent, title, text, "error", ok_text="知道了").exec_()



# ---------------------------------------------------------------- 区域选择
class RegionSelector(QWidget):
    selected = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowState(Qt.WindowFullScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.origin = None
        self.cur = None
        self.hint = "拖拽框选题目区域 · Esc 取消"

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self.origin and self.cur:
            r = QRect(self.origin, self.cur).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(COLOR_ACCENT), 2))
            p.drawRect(r)
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Microsoft YaHei", 11))
            p.drawText(r.left() + 8, max(20, r.top() - 8), f"{r.width()} × {r.height()}")
        p.setPen(QColor(255, 255, 255, 220))
        p.setFont(QFont("Microsoft YaHei", 14))
        p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignHCenter | Qt.AlignTop, self.hint)

    def mousePressEvent(self, e):
        self.origin = e.pos()
        self.cur = e.pos()
        self.update()

    def mouseMoveEvent(self, e):
        if self.origin:
            self.cur = e.pos()
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.origin:
            return
        r = QRect(self.origin, e.pos()).normalized()
        self.close()
        if r.width() > 30 and r.height() > 20:
            self.selected.emit({"left": r.left(), "top": r.top(),
                                "width": r.width(), "height": r.height()})

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()


# ---------------------------------------------------------------- 答案浮窗
def _err_log_path():
    try:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                            "Knowlib", "error.log")
    except Exception:
        return None


def safe_slot(fn):
    """包住 Qt 槽函数：PyQt5 里槽里抛未捕获异常会直接 abort（0xc0000409），
    包一层就能把 traceback 写进 error.log，并且不让软件闪退。

    另外兼容 `clicked(bool)` 这类会多传一个参数的信号：按函数签名裁掉多余实参。
    """
    import functools
    import inspect

    try:
        _sig = inspect.signature(fn)
        _npos = 0
        for _p in _sig.parameters.values():
            if _p.kind in (_p.POSITIONAL_ONLY, _p.POSITIONAL_OR_KEYWORD):
                _npos += 1
            elif _p.kind is _p.VAR_POSITIONAL:
                _npos = 99
                break
    except Exception:
        _npos = 99

    @functools.wraps(fn)
    def _wrapped(*a, **k):
        try:
            if _npos != 99 and len(a) > _npos:
                a = a[:_npos]
            return fn(*a, **k)
        except Exception:
            p = _err_log_path()
            try:
                with open(p, "a", encoding="utf-8") as f:
                    f.write("\n--- %s %s ---\n" % (time.strftime("%H:%M:%S"), fn.__qualname__))
                    f.write(traceback.format_exc())
            except Exception:
                pass
            return None

    return _wrapped


# ---------------------------------------------------------------- 答案解析拆分
_LBL_JX = re.compile(
    r"[*_#>\s]*(?:[\[【]\s*解\s*析\s*[\]】][*_#>\s]*[:：]?"
    r"|解\s*析[*_#>\s]*[:：])[*_#>\s]*")
_LBL_ANS = re.compile(
    r"[*_#>\s]*(?:[\[【]\s*答\s*案\s*[\]】][*_#>\s]*[:：]?"
    r"|答\s*案[*_#>\s]*[:：])[*_#>\s]*")
_MARKS = "*_#> \t\r\n"


def _trim_marks(s):
    """去掉标签切割后残留的 markdown 记号（**、#、> 等）。

    注意：正文里的成对 ** 要保留，否则会变成半截粗体。
    """
    t = (s or "").strip()
    t = re.sub(r"^[*_#>\s]+", "", t)                     # 开头的残留肯定要去
    if t.count("*") % 2 == 1:                             # 结尾的只在不成对时去
        t = re.sub(r"[*\s]+$", "", t)
    return t.strip()


def _answer_text(s):
    """答案只要纯文本：两边残留的 ** 一律削掉（答案本来就不该加粗）。"""
    return re.sub(r"^[*_#>\s]+|[*_#\s]+$", "", (s or "")).strip()


def split_qa(full):
    """把模型输出拆成 (答案, 解析)。

    兼容 **答案：** / **解析**: / 【解析】: / ### 解析： 这些写法：
    先把标签两边的 markdown 记号一起吃掉，避免 ** 漏进正文被当成字面量显示。
    """
    s = (full or "").replace("\r\n", "\n").replace("\r", "\n")
    mj = _LBL_JX.search(s)
    head, tail = (s[:mj.start()], s[mj.end():]) if mj else (s, "")
    ma = _LBL_ANS.search(head)
    ans = head[ma.end():] if ma else head
    return _answer_text(ans), _trim_marks(tail)


class Stepper(QWidget):
    """现代步进器：− [数值] +，替掉 QSpinBox 那两个老式上下小箭头。"""

    def __init__(self, minimum=0, maximum=100, step=1, suffix="", parent=None):
        super().__init__(parent)
        self._min, self._max, self._step, self._suffix = minimum, maximum, step, suffix
        self._value = minimum
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        for b in (self.minus, self.plus):
            b.setObjectName("stepBtn")
            b.setFixedSize(32, 30)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        self.edit = QLineEdit()
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.setFixedWidth(104)
        h.addWidget(self.minus)
        h.addWidget(self.edit)
        h.addWidget(self.plus)
        h.addStretch(1)
        self.minus.clicked.connect(lambda: self.setValue(self._value - self._step))
        self.plus.clicked.connect(lambda: self.setValue(self._value + self._step))
        self.edit.editingFinished.connect(self._on_edit)
        self.setValue(minimum)

    def value(self):
        return self._value

    def setValue(self, v):
        try:
            v = int(v)
        except Exception:
            v = self._value
        self._value = max(self._min, min(self._max, v))
        self.edit.setText("%d%s" % (self._value, self._suffix))

    def _on_edit(self):
        txt = self.edit.text().replace(self._suffix.strip(), "")
        digits = re.sub(r"[^\d]", "", txt)
        self.setValue(int(digits) if digits else self._value)


class AnswerOverlay(QWidget):
    def __init__(self, host):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.host = host
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag = None
        self._full = ""
        cfg = getattr(host, "cfg", {}) or {}
        self._width = int(cfg.get("overlay_width", 560))
        self._font = int(cfg.get("overlay_font", 14))
        self.setFixedWidth(self._width)
        self._apply_opacity()

        root = QFrame(self)
        root.setObjectName("card")
        root.setStyleSheet(
            "#card{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(24,27,34,244),stop:1 rgba(16,18,23,246));"
            "border-radius:16px;border:1px solid rgba(255,255,255,30);}"
            "QLabel{color:#e8eaed;background:transparent;}"
            "QPushButton{color:#dfe3ea;background:rgba(255,255,255,18);border:none;"
            "border-radius:8px;padding:5px 10px;}"
            "QPushButton:hover{background:rgba(255,255,255,34);}"
            "QPlainTextEdit{background:rgba(0,0,0,90);color:#e8eaed;"
            "border:1px solid rgba(255,255,255,30);border-radius:8px;padding:8px;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(root)

        inner = QVBoxLayout(root)
        inner.setContentsMargins(16, 12, 16, 12)

        head = QHBoxLayout()
        self.title = QLabel("答案")
        self.title.setStyleSheet("font-size:13px;color:#9aa4b2;")
        self.btn_fix = QPushButton("纠错")
        self.btn_copy = QPushButton("复制")
        self.btn_retry = QPushButton("重问")
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedWidth(30)
        for b in (self.btn_fix, self.btn_copy, self.btn_retry, self.btn_close):
            b.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.btn_fix)
        head.addWidget(self.btn_copy)
        head.addWidget(self.btn_retry)
        head.addWidget(self.btn_close)
        inner.addLayout(head)

        # 大号答案
        self.answer = QLabel("")
        self.answer.setWordWrap(True)
        self.answer.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.answer.setMinimumHeight(56)
        inner.addWidget(self.answer)

        # 解析
        self.part_label = QLabel("解析")
        self.part_label.setStyleSheet("color:#7f8896;font-size:11px;margin-top:6px;")
        inner.addWidget(self.part_label)

        self.body = QTextBrowser()
        self.body.setStyleSheet("background:transparent;border:none;")
        self.body.setMinimumHeight(90)
        self.body.setMaximumHeight(380)
        inner.addWidget(self.body)

        self.foot = QLabel("")
        self.foot.setStyleSheet("color:#7f8896;font-size:11px;")
        inner.addWidget(self.foot)

        self.btn_fix.clicked.connect(self.do_fix)
        self.btn_copy.clicked.connect(self.copy_text)
        self.btn_retry.clicked.connect(host.ask_again)
        self.btn_close.clicked.connect(self.hide)
        self.apply_settings()

    # ---------------- 外观设置
    def _cfg(self):
        base = dict(getattr(self.host, "cfg", {}) or {})
        base.update(getattr(self, "_override", None) or {})
        return base

    def _apply_opacity(self):
        cfg = self._cfg()
        try:
            op = int(cfg.get("overlay_opacity", 96)) / 100.0
        except Exception:
            op = 0.96
        self.setWindowOpacity(max(0.25, min(1.0, op)))

    def preview(self, opts, text=None):
        """设置页拖动时实时预览外观。
        答案窗本来就开着时 → 直接改真窗口，所见即所得；
        没开着 → 在原来的位置弹一张示例卡。
        """
        was_visible = self.isVisible()
        if not getattr(self, "_pv_active", False):
            self._was_visible = was_visible
            self._pv_text_backup = None
        self._pv_active = True
        self._override = dict(opts or {})
        self.apply_settings()
        if was_visible:
            # 真窗口开着：只改外观，不动内容
            self.raise_()
        else:
            if text is not None:
                self.title.setText("答案 · 预览")
                self.set_text(text)
            self.adjustSize()
            pos = (getattr(self.host, "cfg", {}) or {}).get("overlay_pos") or []
            if len(pos) == 2:
                self.move(int(pos[0]), int(pos[1]))
            else:
                scr = QApplication.primaryScreen().availableGeometry()
                self.move(scr.center().x() - self.width() // 2, scr.top() + 90)
            self.show()
            self.raise_()
        t = getattr(self, "_pv_hide", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._end_preview)
            self._pv_hide = t
        t.start(8000)

    def _end_preview(self):
        self._override = {}
        self._pv_active = False
        try:
            self.title.setText("答案")
        except Exception:
            pass
        if not getattr(self, "_was_visible", False):
            self.hide()
            self.set_text("")
        else:
            # 真窗口还开着：恢复成真实配置的外观
            self.apply_settings()

    @safe_slot
    def apply_settings(self):
        cfg = self._cfg()
        self._width = int(cfg.get("overlay_width", 560))
        self._font = int(cfg.get("overlay_font", 14))
        self._afont = int(cfg.get("overlay_answer_font", self._font + 20))
        try:
            self._height = int(cfg.get("overlay_height", 0) or 0)
        except Exception:
            self._height = 0
        self.setFixedWidth(self._width)
        self._apply_opacity()
        self._acolor = (cfg.get("overlay_answer_color") or "#7ee787").strip()
        self._tcolor = (cfg.get("overlay_text_color") or "#d5dae3").strip()
        self.answer.setStyleSheet(
            f"font-size:{self._afont}px;font-weight:800;color:{self._acolor};"
            "padding:10px 14px;border-radius:12px;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 rgba(126,231,135,26),stop:1 rgba(126,231,135,10));")
        self.body.setStyleSheet(
            f"background:transparent;border:none;color:{self._tcolor};"
            f"font-size:{self._font}px;")
        if self._height >= 180:
            self.body.setMaximumHeight(max(90, self._height - 170))
            self.setFixedHeight(self._height)
        else:
            self.body.setMaximumHeight(380)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.adjustSize()
        self._render()

    # ---------------- 内容
    @safe_slot
    def set_text(self, text):
        self._full = text or ""
        self._render()

    @safe_slot
    def append_text(self, delta):
        self._full = getattr(self, "_full", "") + (delta or "")
        self._render()

    @safe_slot
    def _render(self):
        import re as _re
        import html as _html
        full = getattr(self, "_full", "") or ""

        ans, tail = split_qa(full)

        # 从题干里解析选项（A．xxx B．xxx）
        opts = []
        try:
            import paper_export as _pe
            _, opts = _pe.parse_options(getattr(self.host, "last_question", "") or "")
        except Exception:
            opts = []

        letter = ""
        mm = _re.match(r"^\s*([A-Ha-h])\b", ans)
        if mm:
            letter = mm.group(1).upper()

        if opts and letter:
            pick = None
            for k, txt in opts:
                if k.upper() == letter:
                    pick = txt
                    break
            big = f"{letter}．{pick}" if pick else letter
        else:
            big = ans

        big = (big or "").replace("**", "").replace("__", "").strip()
        self.answer.setText(big or "…")
        self.answer.show()

        parts = []
        if opts:
            rows = []
            for k, txt in opts:
                on = bool(letter) and k.upper() == letter
                color = self._acolor if on else self._tcolor
                weight = "700" if on else "400"
                mark = "  ✓" if on else ""
                rows.append(
                    f"<div style='color:{color};font-weight:{weight};"
                    f"font-size:{self._font}px;padding:2px 0'>"
                    f"{k}．{_html.escape(txt)}{mark}</div>")
            parts.append("<div style='margin-bottom:10px'>" + "".join(rows) + "</div>")
        if tail:
            parts.append(md_render.to_html(tail, color=self._tcolor,
                                           font_size=self._font))
        self.body.setHtml("".join(parts) if parts else "")
        self.part_label.setVisible(bool(tail))
        self.body.verticalScrollBar().setValue(0)

    def copy_text(self):
        text = (getattr(self, "_full", "") or "").strip()
        if not text:
            text = (self.answer.text() + "\n" + self.body.toPlainText()).strip()
        QApplication.clipboard().setText(text)
        self.foot.setText("已复制到剪贴板")

    # ---------------- 纠错
    def do_fix(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("纠错")
        dlg.resize(600, 400)
        v = QVBoxLayout(dlg)
        tip = QLabel("改好答案/解析后点保存：会覆盖本地题库里这道题，下次同一题直接用新答案。")
        tip.setWordWrap(True)
        v.addWidget(tip)
        ed = QPlainTextEdit(self._full)
        v.addWidget(ed, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        ok = QPushButton("保存")
        ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        if dlg.exec_() == QDialog.Accepted:
            txt = ed.toPlainText().strip()
            if not txt:
                return
            self.set_text(txt)
            try:
                self.host.apply_correction(txt)
            except Exception:
                pass

    # ---------------- 拖动 / 定位
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            try:
                self.host.on_overlay_moved(self.pos())
            except Exception:
                pass

    def place_default(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.top() + 90)

    def place_avoiding(self, region):
        """尽量把答案窗放到监控区域之外，避免拍到自己造成闪。"""
        scr = QApplication.primaryScreen().availableGeometry()
        w, h = self.width(), max(self.sizeHint().height(), 220)
        m = 10
        rx, ry = int(region.get("left", 0)), int(region.get("top", 0))
        rw, rh = int(region.get("width", 0)), int(region.get("height", 0))

        saved = (self.host.cfg.get("overlay_pos") or []) if hasattr(self, "host") else []
        if len(saved) == 2:
            x, y = int(saved[0]), int(saved[1])
            inside = (x >= scr.left() and y >= scr.top()
                      and x + w <= scr.right() and y + h <= scr.bottom())
            overlap = rw and rh and not (x + w < rx or x > rx + rw or y + h < ry or y > ry + rh)
            if inside and not overlap:
                self.move(x, y)
                return

        cands = []
        if rw and rh:
            cands += [
                (rx + rw + m, ry),
                (rx - w - m, ry),
                (rx, ry + rh + m),
                (rx, ry - h - m),
            ]
        cands += [(scr.right() - w - 20, scr.top() + 80)]

        best = None
        for x, y in cands:
            if x < scr.left() + 8 or y < scr.top() + 8:
                continue
            if x + w > scr.right() - 8 or y + h > scr.bottom() - 8:
                continue
            if rw and rh and not (x + w < rx or x > rx + rw or y + h < ry or y > ry + rh):
                continue
            best = (x, y)
            break

        if best is None:
            best = (scr.right() - w - 20, scr.top() + 80)
        self.move(*best)


class WinCombo(QComboBox):
    """Win11 风格下拉框：自绘 chevron，不用系统老式箭头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        col = QColor(self.palette().color(self.foregroundRole()))
        pen = QPen(col)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx = self.width() - 15
        cy = self.height() / 2.0 - 1
        p.drawLine(QPoint(int(cx - 4), int(cy - 2)), QPoint(int(cx), int(cy + 2)))
        p.drawLine(QPoint(int(cx), int(cy + 2)), QPoint(int(cx + 4), int(cy - 2)))
        p.end()


class KeyCapture(QDialog):
    """按一下要绑定的键。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key_name = None
        self.setWindowTitle("按下新的快捷键")
        self.setFixedSize(320, 120)
        lay = QVBoxLayout(self)
        tip = QLabel("请按 F1 ~ F12 中的一个键\n（Esc 取消）")
        tip.setAlignment(Qt.AlignCenter)
        lay.addWidget(tip)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.reject()
            return
        if Qt.Key_F1 <= k <= Qt.Key_F12:
            self.key_name = "F%d" % (k - Qt.Key_F1 + 1)
            self.accept()
            return
        super().keyPressEvent(e)


HOTKEY_ITEMS = (
    ("region", "框选区域"),
    ("pause", "暂停 / 继续"),
    ("shot", "立即识别"),
    ("again", "重问一次"),
    ("overlay", "显示 / 隐藏答案窗"),
)


class SettingsPage(QWidget):
    """设置：二级导航（常规 / 模型与密钥 / 快捷键）。"""

    def __init__(self, cfg, parent=None, on_saved=None, on_back=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self._on_saved = on_saved
        self._on_back = on_back
        self._theme = getattr(parent, "theme", LIGHT)
        self.setObjectName("settingsPage")
        self.setStyleSheet("")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        _title = QLabel("设置")
        _title.setObjectName("pageTitle")
        outer.addWidget(_title)

        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)

        # 二级导航
        self.subnav = QListWidget()
        self.subnav.setObjectName("subNav")
        self.subnav.setFixedWidth(168)
        self.subnav.setFocusPolicy(Qt.NoFocus)
        self.subnav.addItems(["常规", "设备", "模型与密钥", "快捷键", "答案窗"])
        self.subnav.setCurrentRow(0)
        lay.addWidget(self.subnav)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        self.stack = QStackedWidget()
        for _w in (self._build_general(), self._build_devices(), self._build_model(),
                   self._build_hotkeys(), self._build_overlay()):
            self.stack.addWidget(self._wrap_scroll(_w))
        right.addWidget(self.stack, 1)
        lay.addLayout(right, 1)
        outer.addLayout(lay, 1)

        self.subnav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.provider.currentTextChanged.connect(self._on_provider)

    def _wrap_scroll(self, w):
        """每个二级页自己滚，左侧导航固定不动。"""
        sa = QScrollArea()
        sa.setObjectName("pageScroll")
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        return sa

    # ---------------- 设备（云同步 / 配对 / 设备管理）
    def _build_devices(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # ---------------- 云同步（跨端：PC / 手机 / 网页）
        card2 = QFrame()
        card2.setObjectName("card")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 14, 16, 14)
        c2.setSpacing(10)
        t2 = QLabel("云同步（题库 / 搜题记录 / 试卷）")
        t2.setObjectName("sectionTitle")
        c2.addWidget(t2)

        tip2 = QLabel("两端（或多端）填同一个同步码，就能拿到同一份题库和搜题记录。"
                      "新设备直接输码；没装 App 的手机可以用网页版。\n"
                      "同步码和密钥保存在本机，填一次就行，之后自动同步；换设备时把这两个值填过去即可。")
        tip2.setObjectName("sub")
        tip2.setWordWrap(True)
        c2.addWidget(tip2)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        lb1 = QLabel("同步码")
        lb1.setMinimumWidth(64)
        self.sync_code = QLineEdit(self.cfg.get("sync_code", "") or "")
        self.sync_code.setPlaceholderText("8 位同步码，例：4j5fsmcg")
        r1.addWidget(lb1)
        r1.addWidget(self.sync_code, 1)
        c2.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        lb2 = QLabel("同步密钥")
        lb2.setMinimumWidth(64)
        self.sync_key = QLineEdit(self.cfg.get("sync_key", "") or "")
        self.sync_key.setPlaceholderText("新建同步码时自动生成，写入/读取都要它")
        r2.addWidget(lb2)
        r2.addWidget(self.sync_key, 1)
        c2.addLayout(r2)

        r3 = QHBoxLayout()
        r3.setSpacing(8)
        self.btn_sync_new = QPushButton("新建同步码")
        self.btn_sync_new.setCursor(Qt.PointingHandCursor)
        self.btn_sync_now = QPushButton("立即同步")
        self.btn_sync_now.setObjectName("primary")
        self.btn_sync_now.setCursor(Qt.PointingHandCursor)
        self.btn_sync_web = QPushButton("网页版")
        self.btn_sync_web.setCursor(Qt.PointingHandCursor)
        self.btn_sync_qr = QPushButton("二维码配对")
        self.btn_sync_qr.setCursor(Qt.PointingHandCursor)
        self.btn_sync_new.clicked.connect(self.do_sync_new)
        self.btn_sync_now.clicked.connect(self.do_sync_now)
        self.btn_sync_web.clicked.connect(self.open_sync_web)
        self.btn_sync_qr.clicked.connect(self.do_sync_qr)
        r3.addWidget(self.btn_sync_new)
        r3.addWidget(self.btn_sync_now)
        r3.addWidget(self.btn_sync_web)
        r3.addWidget(self.btn_sync_qr)
        self.btn_sync_dev = QPushButton("设备")
        self.btn_sync_dev.setCursor(Qt.PointingHandCursor)
        self.btn_sync_dev.clicked.connect(self.do_sync_devices)
        r3.addWidget(self.btn_sync_dev)
        r3.addStretch(1)
        self.sync_state = QLabel(self.cfg.get("sync_last") or "尚未同步")
        self.sync_state.setObjectName("sub")
        r3.addWidget(self.sync_state)
        c2.addLayout(r3)

        self.sync_auto = QCheckBox("启动时自动同步（每 10 分钟再同步一次）")
        self.sync_auto.setChecked(bool(self.cfg.get("sync_auto", True)))
        c2.addWidget(self.sync_auto)

        v.addWidget(card2)

        # ---------------- 设备列表 + 设备详情（两栏各自独立滚动）
        cardD = QFrame()
        cardD.setObjectName("card")
        cD = QVBoxLayout(cardD)
        cD.setContentsMargins(16, 14, 16, 14)
        cD.setSpacing(8)
        tD = QLabel("设备")
        tD.setObjectName("sectionTitle")
        cD.addWidget(tD)
        tipD = QLabel("同一个同步码下的设备都会出现在左边；点一台看它的详情和同步内容。"
                      "左右两栏各自滚动，拖右边不会带着左边跑。")
        tipD.setObjectName("sub")
        tipD.setWordWrap(True)
        cD.addWidget(tipD)

        bodyD = QHBoxLayout()
        bodyD.setSpacing(12)
        self.dev_list = QListWidget()
        self.dev_list.setFixedWidth(232)
        self.dev_list.setMinimumHeight(180)
        self.dev_list.currentRowChanged.connect(self.on_dev_pick)
        bodyD.addWidget(self.dev_list, 0)

        rD = QVBoxLayout()
        rD.setSpacing(6)
        self.dev_title = QLabel("还没同步过")
        self.dev_title.setStyleSheet("font-size:14px;font-weight:600;")
        rD.addWidget(self.dev_title)
        self.dev_meta = QLabel("点一次「立即同步」，这里会列出这台设备的系统、标识、最近同步时间。")
        self.dev_meta.setObjectName("sub")
        self.dev_meta.setWordWrap(True)
        rD.addWidget(self.dev_meta)

        lb_items = QLabel("同步内容")
        lb_items.setObjectName("sub")
        rD.addWidget(lb_items)
        self.dev_items = QListWidget()
        self.dev_items.setMinimumHeight(120)
        rD.addWidget(self.dev_items, 1)   # 自己带滚动条，不跟外层一起滚

        rowD = QHBoxLayout()
        rowD.setSpacing(8)
        self.btn_dev_refresh = QPushButton("刷新")
        self.btn_dev_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_dev_refresh.clicked.connect(self.refresh_devices)
        self.btn_dev_mine = QPushButton("设为本机设备")
        self.btn_dev_mine.setCursor(Qt.PointingHandCursor)
        self.btn_dev_mine.clicked.connect(self.mark_dev_mine)
        rowD.addWidget(self.btn_dev_refresh)
        rowD.addWidget(self.btn_dev_mine)
        rowD.addStretch(1)
        rD.addLayout(rowD)
        bodyD.addLayout(rD, 1)
        cD.addLayout(bodyD, 1)

        v.addWidget(cardD, 1)
        return w

    @safe_slot
    def refresh_devices(self):
        """把配置里登记的设备铺到左边列表，默认选中第一台。"""
        cur = self.dev_list.currentRow()
        self.dev_list.clear()
        devs = sorted((self.cfg.get("sync_devices") or []),
                      key=lambda x: x.get("ts") or 0, reverse=True)
        mine = set(self.cfg.get("sync_mine") or [])
        if not devs:
            it = QListWidgetItem("（还没有设备）先点上面的「立即同步」")
            it.setFlags(Qt.NoItemFlags)
            self.dev_list.addItem(it)
            self.dev_title.setText("还没同步过")
            self.dev_meta.setText("同步一次后，云端登记的设备会出现在这里。")
            self.dev_items.clear()
            return
        for d in devs:
            did = d.get("id") or ""
            name = d.get("name") or did or "未命名设备"
            tag = " · 我的" if did in mine else ""
            it = QListWidgetItem(name + tag)
            it.setData(Qt.UserRole, d)
            self.dev_list.addItem(it)
        self.dev_list.setCurrentRow(min(max(cur, 0), self.dev_list.count() - 1))

    @safe_slot
    def on_dev_pick(self, row):
        if row < 0:
            return
        it = self.dev_list.item(row)
        d = it.data(Qt.UserRole) if it else None
        if not d:
            return
        did = d.get("id") or ""
        mine = set(self.cfg.get("sync_mine") or [])
        ts = d.get("ts") or 0
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts / 1000.0)) if ts else "未知"
        self.dev_title.setText(d.get("name") or did or "未命名设备")
        self.dev_meta.setText("系统：%s\n标识：%s\n最近同步：%s\n%s"
                              % (d.get("os") or "未知", did or "-", when,
                                 "已标记为「我的设备」" if did in mine else "不是「我的设备」"))

        # 同步内容（本机口径，能算的都算出来）
        self.dev_items.clear()
        rows = []
        try:
            import question_bank as _qb
            st = _qb.stats() or {}
            n = st.get("total") or st.get("count") or 0
            rows.append("题库：%s 题" % n)
        except Exception:
            rows.append("题库：-")
        try:
            import cloud_sync as _cs
            rows.append("搜题记录：%d 条" % len(_cs.local_history()))
        except Exception:
            rows.append("搜题记录：-")
        try:
            rows.append("组卷试卷：%d 份" % len(self.cfg.get("papers") or []))
        except Exception:
            rows.append("组卷试卷：-")
        for t in rows:
            self.dev_items.addItem(QListWidgetItem(t))

    @safe_slot
    def mark_dev_mine(self):
        row = self.dev_list.currentRow()
        if row < 0:
            return
        it = self.dev_list.item(row)
        d = it.data(Qt.UserRole) if it else None
        if not d:
            return
        did = d.get("id") or ""
        mine = list(self.cfg.get("sync_mine") or [])
        if did in mine:
            mine.remove(did)
        else:
            mine.append(did)
        self.cfg["sync_mine"] = mine
        config_store.save(self.cfg)
        self.refresh_devices()
        self.on_dev_pick(row)

    # ---------------- 常规
    def _build_general(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        c.setContentsMargins(16, 14, 16, 14)
        c.setSpacing(12)

        t = QLabel("启动与关闭")
        t.setObjectName("sectionTitle")
        c.addWidget(t)

        self.autostart = QCheckBox("开机自动启动（静默进托盘）")
        self.autostart.setChecked(config_store.autostart_enabled())
        c.addWidget(self.autostart)

        row = QHBoxLayout()
        row.setSpacing(10)
        lb = QLabel("关闭按钮")
        lb.setMinimumWidth(96)
        self.close_action = WinCombo()
        self.close_action.addItem("最小化到托盘", "tray")
        self.close_action.addItem("直接退出程序", "exit")
        self.close_action.addItem("每次询问我", "ask")
        i = self.close_action.findData(self.cfg.get("close_action", "tray"))
        self.close_action.setCurrentIndex(i if i >= 0 else 0)
        row.addWidget(lb)
        row.addWidget(self.close_action, 1)
        c.addLayout(row)

        self.animations = QCheckBox("界面动画")
        self.animations.setChecked(bool(self.cfg.get("animations", True)))
        c.addWidget(self.animations)

        self.check_update = QCheckBox("启动时检查更新")
        self.check_update.setChecked(bool(self.cfg.get("check_update", True)))
        c.addWidget(self.check_update)

        v.addWidget(card)

        # ---------------- 云同步已挪到「设备」页
        hint2 = QLabel("云同步 / 设备配对 / 设备管理 → 左侧「设备」")
        hint2.setObjectName("sub")
        v.addWidget(hint2)

        # ---------------- 手机遥控搜题
        card3 = QFrame()
        card3.setObjectName("card")
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(16, 14, 16, 14)
        c3.setSpacing(10)
        t3 = QLabel("手机遥控搜题（局域网）")
        t3.setObjectName("sectionTitle")
        c3.addWidget(t3)

        tip3 = QLabel("PC 后台静默截图 + 识别 + 作答，答案只发到手机，PC 不弹任何窗口。"
                      "手机需和电脑连同一个 WiFi。")
        tip3.setObjectName("sub")
        tip3.setWordWrap(True)
        c3.addWidget(tip3)

        self.remote_on = QCheckBox("开启手机遥控")
        self.remote_on.setChecked(bool(self.cfg.get("remote_on", False)))
        self.remote_on.toggled.connect(self.do_remote_toggle)
        c3.addWidget(self.remote_on)

        r3a = QHBoxLayout()
        r3a.setSpacing(8)
        r3a.addWidget(QLabel("端口"))
        self.remote_port = QLineEdit(str(self.cfg.get("remote_port", 18765)))
        self.remote_port.setFixedWidth(90)
        r3a.addWidget(self.remote_port)
        self.remote_state = QLabel("未开启")
        self.remote_state.setObjectName("sub")
        r3a.addWidget(self.remote_state, 1)
        c3.addLayout(r3a)

        r3b = QHBoxLayout()
        r3b.setSpacing(8)
        r3b.addWidget(QLabel("配对码"))
        self.remote_code = QLabel("------")
        self.remote_code.setStyleSheet("font-size:18px;font-weight:700;letter-spacing:3px;")
        r3b.addWidget(self.remote_code)
        r3b.addStretch(1)
        self.btn_remote_qr = QPushButton("二维码")
        self.btn_remote_web = QPushButton("打开控制页")
        self.btn_remote_qr.clicked.connect(self.do_remote_qr)
        self.btn_remote_web.clicked.connect(self.open_remote_page)
        r3b.addWidget(self.btn_remote_qr)
        r3b.addWidget(self.btn_remote_web)
        c3.addLayout(r3b)

        v.addWidget(card3)
        v.addStretch(1)
        return w

    # ---------------- 手机遥控
    @safe_slot
    def do_remote_toggle(self, on):
        try:
            port = int(self.remote_port.text().strip() or "18765")
        except Exception:
            port = 18765
        cfg = dict(self.cfg)
        cfg["remote_port"] = port
        if on:
            ok, msg, url = remote_server.start(cfg, port, log=None)
            self.remote_state.setText(msg if ok else ("启动失败：" + msg))
        else:
            remote_server.stop()
            self.remote_state.setText("未开启")
        info = remote_server.info()
        self.remote_code.setText(info.get("pair_code") or "------")

    @safe_slot
    def do_remote_qr(self):
        info = remote_server.info()
        if not info.get("running"):
            self.remote_state.setText("先开启遥控")
            return
        url = info.get("url") or ""
        try:
            import qrcode
            import io as _io
            qr = qrcode.QRCode(border=2, box_size=8)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            pm = QPixmap()
            pm.loadFromData(buf.getvalue(), "PNG")
        except Exception as e:
            self.remote_state.setText("生成二维码失败：%s" % e)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("手机遥控")
        dlg.resize(380, 470)
        v = QVBoxLayout(dlg)
        tip = QLabel("手机连同一个 WiFi，扫码打开（或直接输网址）")
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        v.addWidget(tip)
        lb = QLabel()
        lb.setAlignment(Qt.AlignCenter)
        lb.setPixmap(pm.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        v.addWidget(lb, 1)
        u = QLabel(url)
        u.setAlignment(Qt.AlignCenter)
        u.setObjectName("sub")
        v.addWidget(u)
        c = QLabel("配对码：%s" % (info.get("pair_code") or ""))
        c.setAlignment(Qt.AlignCenter)
        c.setStyleSheet("font-size:18px;font-weight:700;letter-spacing:3px;")
        v.addWidget(c)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("复制网址")
        b.clicked.connect(lambda: (QApplication.clipboard().setText(url), tip.setText("已复制 ✓")))
        row.addWidget(b)
        okb = QPushButton("关闭")
        okb.clicked.connect(dlg.accept)
        row.addWidget(okb)
        v.addLayout(row)
        dlg.exec_()

    def open_remote_page(self):
        info = remote_server.info()
        if not info.get("running"):
            self.remote_state.setText("先开启遥控")
            return
        QDesktopServices.openUrl(QUrl(info.get("url") or ""))

    # ---------------- 云同步按钮
    @safe_slot
    def do_sync_new(self):
        import cloud_sync
        if self.sync_code.text().strip() and not ask_yes(
                self, "新建同步码", "现在已经有同步码了，新建会换成一套全新的（旧数据不丢，但两端都要重填）。继续？"):
            return
        j = cloud_sync.new_sync_code()
        if not j.get("ok"):
            self.sync_state.setText("新建失败：%s" % (j.get("message") or "网络不通"))
            return
        self.sync_code.setText(j.get("code") or "")
        self.sync_key.setText(j.get("key") or "")
        self.sync_state.setText("已生成新同步码（记得保存设置）")

    @safe_slot
    def do_sync_now(self):
        import cloud_sync
        code = self.sync_code.text().strip()
        key = self.sync_key.text().strip()
        if not code or not key:
            self.sync_state.setText("先填同步码和密钥")
            return
        cfg = dict(self.cfg)
        cfg["sync_code"] = code
        cfg["sync_key"] = key
        self.sync_state.setText("同步中…")
        QApplication.processEvents()
        r = cloud_sync.sync_now(cfg)
        self.sync_state.setText(r.get("msg") or "")
        if r.get("ok"):
            self.cfg["sync_code"] = code
            self.cfg["sync_key"] = key
            self.cfg["sync_last"] = time.strftime("%m-%d %H:%M 已同步")
            if r.get("devices"):
                self.cfg["sync_devices"] = r.get("devices")
            config_store.save(self.cfg)

    @safe_slot
    def do_sync_devices(self):
        devs = self.cfg.get("sync_devices") or []
        if not devs:
            self.sync_state.setText("先点一次「立即同步」，再来看设备")
            return
        mine = set(self.cfg.get("sync_mine") or [])
        dlg = QDialog(self)
        dlg.setWindowTitle("同步设备")
        dlg.resize(520, 360)
        v = QVBoxLayout(dlg)
        tip = QLabel("勾选的是「我的设备」（换设备时用来确认）。\n"
                     "所有设备共用同一个同步码，内容更新后下次同步自动合并。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        lw = QListWidget()
        for d in sorted(devs, key=lambda x: x.get("ts") or 0, reverse=True):
            did = d.get("id") or ""
            name = d.get("name") or did
            ts = d.get("ts") or 0
            when = time.strftime("%m-%d %H:%M", time.localtime(ts / 1000.0)) if ts else ""
            it = QListWidgetItem("%s  ·  %s  ·  最近同步 %s" % (name, d.get("os") or "", when))
            it.setData(Qt.UserRole, did)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if did in mine else Qt.Unchecked)
            lw.addItem(it)
        v.addWidget(lw, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        bok = QPushButton("保存")
        bok.setObjectName("primary")
        bok.clicked.connect(dlg.accept)
        bcancel = QPushButton("取消")
        bcancel.clicked.connect(dlg.reject)
        row.addWidget(bcancel)
        row.addWidget(bok)
        v.addLayout(row)
        if dlg.exec_() != QDialog.Accepted:
            return
        picked = []
        for i in range(lw.count()):
            it = lw.item(i)
            if it.checkState() == Qt.Checked:
                picked.append(it.data(Qt.UserRole))
        self.cfg["sync_mine"] = picked
        config_store.save(self.cfg)
        self.sync_state.setText("已记住 %d 台我的设备" % len(picked))

    def open_sync_web(self):
        code = self.sync_code.text().strip()
        url = "https://knowlib.fun/sync.html" + (("#" + code) if code else "")
        QDesktopServices.openUrl(QUrl(url))

    @safe_slot
    def do_sync_qr(self):
        """生成配对二维码：手机扫一下就能打开网页版并自动带上同步码/密钥。"""
        code = self.sync_code.text().strip()
        key = self.sync_key.text().strip()
        if not code:
            self.sync_state.setText("先生成/填入同步码")
            return
        url = "https://knowlib.fun/sync.html?code=%s&key=%s" % (code, key)
        try:
            import qrcode
            from PIL import Image as _Img
            import io as _io
            qr = qrcode.QRCode(border=2, box_size=8)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            pm = QPixmap()
            pm.loadFromData(buf.getvalue(), "PNG")
        except Exception as e:
            self.sync_state.setText("生成二维码失败：%s" % e)
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("手机扫码配对")
        dlg.resize(380, 460)
        v = QVBoxLayout(dlg)
        tip = QLabel("用手机相机/微信扫一下：\n自动打开网页版题库，并带上你的同步码")
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        v.addWidget(tip)
        lb = QLabel()
        lb.setAlignment(Qt.AlignCenter)
        lb.setPixmap(pm.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        v.addWidget(lb, 1)
        code_lb = QLabel("同步码：%s" % code)
        code_lb.setAlignment(Qt.AlignCenter)
        code_lb.setObjectName("sub")
        v.addWidget(code_lb)
        warn = QLabel("⚠️ 二维码里带着写入密钥：只给自己手机扫，别发给别人")
        warn.setAlignment(Qt.AlignCenter)
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#d9534f;font-size:12px;")
        v.addWidget(warn)
        row = QHBoxLayout()
        row.addStretch(1)
        bcopy = QPushButton("复制链接")
        bok = QPushButton("关闭")
        row.addWidget(bcopy)
        row.addWidget(bok)
        v.addLayout(row)
        bcopy.clicked.connect(lambda: (QApplication.clipboard().setText(url), tip.setText("链接已复制 ✓")))
        bok.clicked.connect(dlg.accept)
        dlg.exec_()

    # ---------------- 模型与密钥
    def _build_model(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        c.setContentsMargins(16, 14, 16, 14)
        c.setSpacing(10)

        t = QLabel("模型服务")
        t.setObjectName("sectionTitle")
        c.addWidget(t)

        self.provider = WinCombo()
        self.provider.addItems(list(config_store.PROVIDERS.keys()))
        if self.cfg.get("provider") in config_store.PROVIDERS:
            self.provider.setCurrentText(self.cfg["provider"])
        self.provider.activated[str].connect(self._apply_provider)
        c.addLayout(self._labeled("服务商", self.provider))

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#7f8896;font-size:11px;")
        self.hint.setText(
            (config_store.PROVIDERS.get(self.provider.currentText()) or {}).get("key_hint", ""))
        c.addWidget(self.hint)

        self.base_url = QLineEdit(self.cfg.get("base_url", ""))
        self.base_url.setPlaceholderText("https://api.deepseek.com/v1")
        c.addLayout(self._labeled("API 端点", self.base_url))

        self.api_key = QLineEdit(self.cfg.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("sk-...")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.api_key, 1)
        self.show_key = QPushButton("显示")
        self.show_key.setCheckable(True)
        self.show_key.setFixedWidth(60)
        self.show_key.toggled.connect(
            lambda on: self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        row.addWidget(self.show_key)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        c.addLayout(self._labeled("API Key", holder))

        self.model = QLineEdit(self.cfg.get("model", ""))
        self.model.setPlaceholderText("deepseek-chat")
        c.addLayout(self._labeled("模型", self.model))

        self.vision_model = WinCombo()
        self.vision_model.addItem("不使用（只走文字）", "")
        for _vm in config_store.VISION_PRESETS:
            self.vision_model.addItem(_vm, _vm)
        self.vision_model.addItem("自定义…", "__custom__")
        _cur = (self.cfg.get("vision_model") or "").strip()
        if _cur and _cur not in config_store.VISION_PRESETS:
            self.vision_model.insertItem(self.vision_model.count() - 1, _cur, _cur)
        _vi = self.vision_model.findData(_cur) if _cur else 0
        self.vision_model.setCurrentIndex(_vi if _vi >= 0 else 0)
        self.vision_model.setToolTip("从列表选；表里没有就选「自定义…」手填")
        self.vision_model.activated[int].connect(self._on_vision_pick)
        c.addLayout(self._labeled("视觉模型", self.vision_model))

        self.vision_mode = WinCombo()
        self.vision_mode.addItem("自动（OCR 乱了才用图片）", "auto")
        self.vision_mode.addItem("总是用图片作答", "always")
        self.vision_mode.addItem("关闭", "off")
        vi = self.vision_mode.findData(self.cfg.get("vision_mode", "auto"))
        self.vision_mode.setCurrentIndex(vi if vi >= 0 else 0)
        c.addLayout(self._labeled("图片作答", self.vision_mode))

        self.interval = Stepper(500, 5000, 100, " ms")
        self.interval.setValue(int(float(self.cfg.get("scan_interval", 1.2)) * 1000))
        c.addLayout(self._labeled("扫描间隔", self.interval))

        self.bank_first = QCheckBox("优先查本地题库（命中不花 token）")
        self.bank_first.setChecked(bool(self.cfg.get("bank_first", True)))
        c.addWidget(self.bank_first)

        self.auto_bank = QCheckBox("自动把 AI 答案存进题库（默认关：只在手动收藏时入库）")
        self.auto_bank.setChecked(bool(self.cfg.get("auto_save_bank", False)))
        c.addWidget(self.auto_bank)

        v.addWidget(card)

        card2 = QFrame()
        card2.setObjectName("card")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 14, 16, 14)
        c2.setSpacing(10)
        t2 = QLabel("解题提示词")
        t2.setObjectName("sectionTitle")
        c2.addWidget(t2)
        self.prompt = QPlainTextEdit(self.cfg.get("prompt", ""))
        self.prompt.setMinimumHeight(120)
        c2.addWidget(self.prompt)
        v.addWidget(card2, 1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self.do_test)
        self.test_result = QLabel("尚未测试")
        self.test_result.setObjectName("sub")
        row2.addWidget(self.btn_test)
        row2.addWidget(self.test_result, 1)
        v.addLayout(row2)
        return w

    def _labeled(self, text, widget):
        row = QHBoxLayout()
        row.setSpacing(10)
        lb = QLabel(text)
        lb.setMinimumWidth(96)
        row.addWidget(lb)
        row.addWidget(widget, 1)
        return row

    def _apply_provider(self, name):
        """选服务商时自动填端点 / 模型 / 视觉模型，并给 Key 提示。"""
        p = config_store.PROVIDERS.get(name)
        if not p:
            return
        if p.get("base_url"):
            self.base_url.setText(p["base_url"])
        if p.get("model"):
            self.model.setText(p["model"])
        v = (p.get("vision") or "").strip()
        self.vision_model.clear()
        self.vision_model.addItem("不使用（只走文字）", "")
        if v:
            self.vision_model.addItem(v, v)
        for x in config_store.VISION_PRESETS:
            if x and x != v:
                self.vision_model.addItem(x, x)
        self.vision_model.addItem("自定义…", "__custom__")
        pos = self.vision_model.findData(v)
        self.vision_model.setCurrentIndex(pos if pos >= 0 else 0)
        self.hint.setText(p.get("key_hint", ""))

    def _on_vision_pick(self, idx):
        """选「自定义…」时弹框手填模型名，并加进列表。"""
        if self.vision_model.itemData(idx) != "__custom__":
            return
        txt, ok = QInputDialog.getText(
            self, "自定义视觉模型", "模型名称（例：gpt-4o / qwen-vl-max）：")
        txt = (txt or "").strip()
        if ok and txt:
            pos = self.vision_model.findData(txt)
            if pos < 0:
                self.vision_model.insertItem(self.vision_model.count() - 1, txt, txt)
                pos = self.vision_model.findData(txt)
            self.vision_model.setCurrentIndex(pos)
        else:
            self.vision_model.setCurrentIndex(0)

    def _vision_value(self):
        d = self.vision_model.currentData()
        return "" if d in (None, "__custom__") else str(d)

    # ---------------- 答案窗
    def _build_overlay(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        c.setContentsMargins(16, 14, 16, 16)
        c.setSpacing(14)

        t = QLabel("答案窗外观")
        t.setObjectName("sectionTitle")
        c.addWidget(t)

        self.sl_opacity = self._slider(c, "透明度",
                                       int(self.cfg.get("overlay_opacity", 96)), 30, 100, "%")
        self.sl_width = self._slider(c, "宽度",
                                     int(self.cfg.get("overlay_width", 560)), 420, 1000, " px")
        self.sl_font = self._slider(c, "解析字号",
                                    int(self.cfg.get("overlay_font", 14)), 12, 22, " px")
        self.sl_afont = self._slider(c, "答案字号",
                                     int(self.cfg.get("overlay_answer_font",
                                                      int(self.cfg.get("overlay_font", 14)) + 20)), 24, 64, " px")
        self.sl_height = self._slider(c, "高度",
                                      int(self.cfg.get("overlay_height", 420) or 420), 180, 1000, " px")
        hrow = QHBoxLayout()
        self.chk_auto_h = QCheckBox("高度自适应（跟内容自动变，需要固定高度就取消勾选）")
        self.chk_auto_h.setChecked(int(self.cfg.get("overlay_height", 0) or 0) < 180)
        self.sl_height.setEnabled(not self.chk_auto_h.isChecked())
        self.chk_auto_h.toggled.connect(lambda on: (self.sl_height.setEnabled(not on), self._fire_preview()))
        hrow.addWidget(self.chk_auto_h)
        hrow.addStretch(1)
        c.addLayout(hrow)
        for sl in (self.sl_opacity, self.sl_width, self.sl_font, self.sl_afont, self.sl_height):
            sl.valueChanged.connect(self._fire_preview)
        self._acolor_holder = self._color_row(
            c, "答案颜色", "overlay_answer_color", "#7ee787")
        self._tcolor_holder = self._color_row(
            c, "解析颜色", "overlay_text_color", "#d5dae3")
        prow = QHBoxLayout()
        self.btn_preview = QPushButton("👁 预览答案窗")
        self.btn_preview.setCursor(Qt.PointingHandCursor)
        self.btn_preview.clicked.connect(self._fire_preview)
        prow.addWidget(self.btn_preview)
        self.preview_hint = QLabel("拖一下就实时生效，并自动保存")
        self.preview_hint.setStyleSheet("color:#7f8896;font-size:11px;")
        prow.addWidget(self.preview_hint, 1)
        c.addLayout(prow)
        v.addWidget(card)
        v.addStretch(1)
        return w

    def _color_row(self, parent_layout, label, key, default):
        """一行颜色选择（点色块弹调色板，带几个常用色）。"""
        row = QHBoxLayout()
        row.setSpacing(10)
        lb = QLabel(label)
        lb.setMinimumWidth(72)
        row.addWidget(lb)
        holder = [self.cfg.get(key, default) or default]
        btn = QPushButton()
        btn.setFixedSize(72, 26)
        btn.setCursor(Qt.PointingHandCursor)

        def paint():
            c0 = holder[0]
            btn.setText(c0)
            btn.setStyleSheet(
                "QPushButton{background:%s;color:#101418;border:none;"
                "border-radius:7px;font-size:11px;font-weight:700;}" % c0)
        paint()

        def pick():
            from PyQt5.QtWidgets import QColorDialog
            c0 = QColorDialog.getColor(QColor(holder[0]), self, "选一个颜色")
            if c0.isValid():
                holder[0] = c0.name()
                paint()
                self._fire_preview()

        btn.clicked.connect(pick)
        row.addWidget(btn)
        for preset in ("#7ee787", "#e6e9ef", "#ffd166", "#ff8fa3", "#7ec8ff"):
            pb = QPushButton()
            pb.setFixedSize(18, 18)
            pb.setCursor(Qt.PointingHandCursor)
            pb.setStyleSheet(
                "QPushButton{background:%s;border-radius:9px;border:1px solid #2c3340;}" % preset)
            pb.clicked.connect(lambda _c=False, v=preset: (holder.__setitem__(0, v), paint(), self._fire_preview()))
            row.addWidget(pb)
        row.addStretch(1)
        parent_layout.addLayout(row)
        return holder

    def _fire_preview(self, *_a):
        """滑块一变就通知主窗预览（60ms 防抖）。"""
        cb = getattr(self, "on_preview", None)
        if cb is None:
            return
        if not hasattr(self, "_pv_timer"):
            self._pv_timer = QTimer(self)
            self._pv_timer.setSingleShot(True)
            self._pv_timer.setInterval(60)
            self._pv_timer.timeout.connect(self._do_preview)
        self._pv_timer.start()

    def _do_preview(self):
        cb = getattr(self, "on_preview", None)
        if cb is None:
            return
        cb({"overlay_opacity": self.sl_opacity.value(),
            "overlay_width": self.sl_width.value(),
            "overlay_font": self.sl_font.value(),
            "overlay_answer_font": self.sl_afont.value(),
            "overlay_answer_color": self._acolor_holder[0],
            "overlay_text_color": self._tcolor_holder[0],
            "overlay_height": 0 if self.chk_auto_h.isChecked() else self.sl_height.value()})

    def _slider(self, parent_layout, label, value, lo, hi, suffix):
        row = QHBoxLayout()
        row.setSpacing(12)
        lb = QLabel(label)
        lb.setMinimumWidth(72)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(value)
        val = QLabel(f"{value}{suffix}")
        val.setMinimumWidth(64)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s.valueChanged.connect(lambda v, l=val, suf=suffix: l.setText(f"{v}{suf}"))
        row.addWidget(lb)
        row.addWidget(s, 1)
        row.addWidget(val)
        parent_layout.addLayout(row)
        return s

    # ---------------- 快捷键
    def _build_hotkeys(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        hint = QLabel("点右边的按键可以改成 F1~F12；截图/答题时用得到。")
        hint.setObjectName("sub")
        v.addWidget(hint)

        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        c.setContentsMargins(16, 14, 16, 14)
        c.setSpacing(10)

        hks = dict(self.cfg.get("hotkeys") or {})
        self._hk_buttons = {}
        for action, label in HOTKEY_ITEMS:
            row = QHBoxLayout()
            row.setSpacing(10)
            lb = QLabel(label)
            row.addWidget(lb, 1)
            b = QPushButton(hks.get(action, ""))
            b.setFixedWidth(96)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, a=action: self._capture(a))
            row.addWidget(b)
            self._hk_buttons[action] = b
            c.addLayout(row)
        v.addWidget(card)
        v.addStretch(1)

        reset = QPushButton("恢复默认 F1~F5")
        reset.clicked.connect(self._reset_hotkeys)
        v.addWidget(reset, 0, Qt.AlignLeft)
        return w

    def _capture(self, action):
        dlg = KeyCapture(self)
        if dlg.exec_() == QDialog.Accepted and dlg.key_name:
            self._hk_buttons[action].setText(dlg.key_name)

    def _reset_hotkeys(self):
        for i, (action, _) in enumerate(HOTKEY_ITEMS):
            self._hk_buttons[action].setText("F%d" % (i + 1))

    # ---------------- 逻辑
    def _on_provider(self, name):
        p = config_store.PROVIDERS.get(name) or {}
        if p.get("base_url"):
            self.base_url.setText(p["base_url"])
        if p.get("model"):
            self.model.setText(p["model"])
        hint = p.get("key_hint")
        if hint:
            self.api_key.setPlaceholderText(hint)

    def _collect(self):
        c = dict(self.cfg)
        c["provider"] = self.provider.currentText()
        c["base_url"] = self.base_url.text().strip()
        c["api_key"] = self.api_key.text().strip()
        c["model"] = self.model.text().strip()
        c["scan_interval"] = self.interval.value() / 1000.0
        c["prompt"] = self.prompt.toPlainText().strip()
        c["bank_first"] = self.bank_first.isChecked()
        c["check_update"] = self.check_update.isChecked()
        c["close_action"] = self.close_action.currentData() or "tray"
        c["animations"] = self.animations.isChecked()
        c["sync_code"] = self.sync_code.text().strip()
        c["sync_key"] = self.sync_key.text().strip()
        c["sync_auto"] = self.sync_auto.isChecked()
        c["remote_on"] = self.remote_on.isChecked()
        try:
            c["remote_port"] = int(self.remote_port.text().strip() or "18765")
        except Exception:
            c["remote_port"] = 18765
        hk = dict(c.get("hotkeys") or {})
        for action, _ in HOTKEY_ITEMS:
            b = self._hk_buttons.get(action)
            if b and b.text().startswith("F"):
                hk[action] = b.text()
        c["hotkeys"] = hk
        c["overlay_opacity"] = self.sl_opacity.value()
        c["overlay_width"] = self.sl_width.value()
        c["overlay_font"] = self.sl_font.value()
        c["overlay_answer_font"] = self.sl_afont.value()
        c["overlay_answer_color"] = self._acolor_holder[0]
        c["overlay_text_color"] = self._tcolor_holder[0]
        c["overlay_height"] = 0 if self.chk_auto_h.isChecked() else self.sl_height.value()
        c["auto_save_bank"] = self.auto_bank.isChecked()
        c["vision_model"] = self._vision_value()
        c["vision_mode"] = self.vision_mode.currentData() or "auto"
        return c

    def do_test(self):
        c = self._collect()
        if not c["base_url"] or not c["model"]:
            self.test_result.setText("请先填 API 端点和模型")
            self.test_result.setForegroundRole(self.test_result.foregroundRole())
            return
        self.btn_test.setEnabled(False)
        self.test_result.setText("正在测试…（最多 30 秒）")
        QApplication.processEvents()
        ok, msg, cost = ocr_ai.AiClient(c).test_connection()
        self.btn_test.setEnabled(True)
        if ok:
            self.test_result.setText(f"✅ {msg} · {cost:.2f}s")
        else:
            self.test_result.setText(f"❌ {msg}")

    def do_save(self):
        c = self._collect()
        if not c["base_url"] or not c["model"]:
            warn_box(self, "还没填完", "API 端点和模型不能为空")
            return
        if not c["api_key"]:
            if not ask_yes(self, "没填 Key", "没填 API Key，多数服务会调用失败。仍然保存？"):
                return
        ok, info = config_store.save(c)
        if not ok:
            error_box(self, "保存失败", info)
            return
        want_as = self.autostart.isChecked()
        if want_as != config_store.autostart_enabled():
            ok2, info2 = config_store.set_autostart(want_as)
            if not ok2:
                warn_box(self, "开机自启设置失败", info2)
        self.cfg = c
        if self._on_saved:
            self._on_saved(c)
        if self._on_back:
            self._on_back()

    def _go_back(self):
        if self._on_back:
            self._on_back()


class HistoryPage(QWidget):
    """历史记录：左列表（可折叠 / 可勾选），右侧 Markdown + 公式渲染。"""

    LIST_W = 250
    FOLD_W = 54

    def __init__(self, parent=None, on_back=None):
        super().__init__(parent)
        self._on_back = on_back
        self.setObjectName("historyPage")
        self.setStyleSheet("")
        self._c = theme_colors(getattr(parent, "theme", LIGHT))

        self.items = []
        self.sel = set()          # 已勾选的 key
        self._collapsed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("历史记录")
        t.setObjectName("pageTitle")
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜题目或答案里的关键词…")
        self.search.textChanged.connect(self.apply_filter)
        self.count = QLabel("")
        self.count.setStyleSheet("color:#7f8896;font-size:12px;")
        self.chk_clean = QCheckBox("只看有效（自动去重）")
        self.chk_clean.setChecked(True)
        self.chk_clean.setStyleSheet("QCheckBox{color:#98a2b3;font-size:12px;}")
        self.chk_clean.toggled.connect(self.apply_filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.chk_clean)
        top.addWidget(self.count)
        lay.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.list = QListWidget()
        self.list.setFixedWidth(self.LIST_W)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.currentRowChanged.connect(self.show_item)
        self.list.itemChanged.connect(self._on_item_changed)
        body.addWidget(self.list)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        right.addWidget(self.view, 1)

        # 工具栏放进横向滚动区：窗口再窄也不会把按钮文字压扁
        bar = QScrollArea()
        bar.setWidgetResizable(True)
        bar.setFixedHeight(46)
        bar.setFrameShape(QFrame.NoFrame)
        bar.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        bar.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        bar.setStyleSheet("QScrollArea{background:transparent;}"
                          "QScrollBar:horizontal{height:6px;background:transparent;}"
                          "QScrollBar::handle:horizontal{background:#30363d;border-radius:3px;}"
                          "QScrollBar::add-line,QScrollBar::sub-line{width:0;}")
        holder = QWidget()
        tools = QHBoxLayout(holder)
        tools.setContentsMargins(0, 0, 0, 6)
        tools.setSpacing(8)

        self.btn_add = self._mkbtn("加入题库（选中 0）", self.add_selected)
        self.btn_add_all = self._mkbtn("有效项全部入库", self.add_all_to_bank)
        self.btn_clean = self._mkbtn("清理无效", self.clean_invalid)
        self.btn_del = self._mkbtn("清空全部", self.clear_all)
        self.btn_close = self._mkbtn("← 返回", self._go_back)
        self.btn_close.setVisible(False)
        for b in (self.btn_add, self.btn_add_all, self.btn_clean,
                  self.btn_del, self.btn_close):
            b.setFixedHeight(30)
            tools.addWidget(b)
        tools.addStretch(1)
        bar.setWidget(holder)
        right.addWidget(bar)

        body.addLayout(right, 1)
        lay.addLayout(body, 1)

        self.reload()

    # ------------- 小工具
    def _mkbtn(self, text, slot):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        b.setMinimumWidth(self.fontMetrics().horizontalAdvance(text) + 26)
        b.clicked.connect(slot)
        return b

    def toggle_fold(self):
        self._collapsed = not self._collapsed
        self.list.setFixedWidth(self.FOLD_W if self._collapsed else self.LIST_W)
        self.btn_fold.setText("▶ 展开" if self._collapsed else "◀ 收起")
        self._relabel()

    def _relabel(self):
        for i in range(self.list.count()):
            it = self.list.item(i)
            data = it.data(Qt.UserRole) or {}
            if self._collapsed:
                it.setText(str(i + 1))
                it.setToolTip((" ".join((data.get("q") or "").split()))[:120])
            else:
                q = (data.get("q") or "")
                it.setText(f"{data.get('t', '')[5:16]}  " +
                           (" ".join(q.split())[:26] or "（无题目）"))

    @staticmethod
    def _key(it):
        return (str(it.get("t", "")) + "|" + (it.get("q") or "")[:60])

    # ------------- 数据
    def reload(self):
        self.items = config_store.load_history(limit=500)
        self.apply_filter()

    def _visible_items(self):
        kw = self.search.text().strip().lower()
        pool = []
        for it in self.items:
            q = (it.get("q") or "")
            a = (it.get("a") or "")
            if kw and kw not in q.lower() and kw not in a.lower():
                continue
            pool.append(it)
        if not self.chk_clean.isChecked():
            return pool, 0
        kept, dropped = config_store.dedupe_history(pool, keep_valid=True)
        return kept, dropped

    def apply_filter(self):
        vis, dropped = self._visible_items()
        cur = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for it in vis:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, it)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if self._key(it) in self.sel else Qt.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._relabel()

        txt = f"共 {len(vis)} 条"
        if dropped:
            txt += f" · 已隐藏 {dropped} 条无效/重复"
        if self.search.text().strip():
            txt += "（已搜索）"
        self.count.setText(txt)
        self._update_selcount()
        if vis:
            self.list.setCurrentRow(min(max(cur, 0), len(vis) - 1))
        else:
            self.view.setHtml("<div style='color:#7f8896;padding:20px'>"
                              "没有可显示的记录。取消勾选「只看有效」可以看全部。</div>")

    def _on_item_changed(self, item):
        it = item.data(Qt.UserRole) or {}
        k = self._key(it)
        if item.checkState() == Qt.Checked:
            self.sel.add(k)
        else:
            self.sel.discard(k)
        self._update_selcount()

    def _update_selcount(self):
        n = len(self.sel)
        self.btn_add.setText(f"加入题库（选中 {n}）")
        self.btn_add.setMinimumWidth(
            self.fontMetrics().horizontalAdvance(self.btn_add.text()) + 26)
        self.btn_add.setEnabled(n > 0)

    def _checked_items(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole) or {})
        return out

    # ------------- 预览
    def show_item(self, row):
        if row < 0 or row >= self.list.count():
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        q = md_render.clean_text((it.get("q") or "").strip())
        a = md_render.clean_text((it.get("a") or "").strip())
        cost = it.get("cost")
        cost_s = f" · {cost}s" if isinstance(cost, (int, float)) else ""
        html = (
            f"<div style='color:{self._c['muted']};font-size:12px'>"
            f"{self._esc(it.get('t', ''))}{cost_s}</div>"
            f"<div style='color:{self._c['accent']};font-size:12px;margin:12px 0 4px'>题目</div>"
            + md_render.to_html(q, color=self._c["text"], font_size=14)
            + f"<div style='color:{self._c['accent']};font-size:12px;margin:14px 0 4px'>答案与解析</div>"
            + md_render.to_html(a, color=self._c["text"], font_size=14)
        )
        self.view.setHtml(html)
        sb = self.view.verticalScrollBar()
        sb.setValue(0)

    @staticmethod
    def _esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # ------------- 操作
    def clear_all(self):
        if not ask_yes(self, "清空历史", "确定删除所有历史记录？不可恢复。"):
            return
        config_store.clear_history()
        self.sel.clear()
        self.reload()

    def _go_back(self):
        if self._on_back:
            self._on_back()

    def add_selected(self):
        items = self._checked_items()
        if not items:
            self.count.setText("先勾选要入库的题目")
            return
        ok = 0
        for it in items:
            q = md_render.clean_text((it.get("q") or "").strip())
            a = md_render.clean_text((it.get("a") or "").strip())
            if q and a and qbank.save(q, a, source="history"):
                ok += 1
        self.count.setText(f"已入库 {ok} / {len(items)} 条 ✓")
        self.sel.clear()
        self.apply_filter() if False else self._refresh_checks()

    def _refresh_checks(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Unchecked)
        self.list.blockSignals(False)
        self._update_selcount()

    def add_all_to_bank(self):
        vis, _ = self._visible_items()
        if not vis:
            self.count.setText("没有可入库的记录")
            return
        if not ask_yes(self, "批量入库", f"把当前列表里的 {len(vis)} 条全部加入题库？"):
            return
        ok = 0
        for it in vis:
            q = md_render.clean_text((it.get("q") or "").strip())
            a = md_render.clean_text((it.get("a") or "").strip())
            if q and a and qbank.save(q, a, source="history"):
                ok += 1
        self.count.setText(f"已入库 {ok} 条 ✓")

    def clean_invalid(self):
        if not ask_yes(self, "清理历史",
                       "删除重复记录和无效记录（无法作答 / 界面文字）？\n"
                       "会自动备份一份 history.jsonl.bak。"):
            return
        r = config_store.clean_history(keep_valid=True, dedupe=True)
        self.sel.clear()
        self.reload()
        self.count.setText(f"清理完成：{r['before']} → {r['after']} 条")


# ---------------------------------------------------------------- 本地题库窗
# 从解析里猜答案（题库里有老数据 answer 为空）
_GUESS_PATTERNS = (
    r"故(?:选|应选|答案选)\s*([A-H]{1,4})",
    r"答案\s*[:：]?\s*([A-H]{1,4})",
    r"答案\s*[:：]?\s*(正确|错误|对|错|是|否|√|×)",
    r"^(正确|错误)",
)


def _guess_answer(text):
    s = (text or "").strip()
    if not s:
        return ""
    for p in _GUESS_PATTERNS:
        m = re.search(p, s)
        if m:
            return m.group(1)
    return ""


class EditQuestionDialog(QDialog):
    """编辑 / 新建一条题库题目，支持在解析里插入图片。"""

    QTYPES = ["", "选择题", "多选题", "判断题", "填空题", "简答题", "计算题"]

    def __init__(self, parent=None, item=None, on_saved=None):
        super().__init__(parent)
        self.item = item or {}
        self.on_saved = on_saved
        self.setWindowTitle("编辑题目" if item else "新建题目")
        self.resize(700, 640)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        def _lbl(txt):
            lb = QLabel(txt)
            lb.setStyleSheet("color:#8fb6ff;font-size:12px;")
            lay.addWidget(lb)

        _lbl("题目（支持 Markdown / LaTeX，例如 $x^2$）")
        self.q = QPlainTextEdit(self.item.get("q_text", ""))
        self.q.setMinimumHeight(120)
        lay.addWidget(self.q, 2)

        _lbl("答案")
        self.a = QPlainTextEdit(self.item.get("answer", ""))
        self.a.setMinimumHeight(60)
        lay.addWidget(self.a, 1)

        _lbl("解析（可在光标处插入图片）")
        self.s = QPlainTextEdit(self.item.get("solution", ""))
        self.s.setMinimumHeight(150)
        lay.addWidget(self.s, 3)

        brow = QHBoxLayout()
        brow.addWidget(QLabel("题型"))
        self.cbo = QComboBox()
        self.cbo.addItems(self.QTYPES)
        cur = self.item.get("qtype") or ""
        if cur in self.QTYPES:
            self.cbo.setCurrentText(cur)
        brow.addWidget(self.cbo)
        self.btn_img = QPushButton("🖼 插入图片")
        self.btn_img.setCursor(Qt.PointingHandCursor)
        self.btn_img.clicked.connect(self.insert_image)
        brow.addWidget(self.btn_img)
        brow.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("保存")
        self.btn_ok.setStyleSheet(
            "QPushButton{background:#2f6feb;color:#fff;border:none;border-radius:8px;"
            "padding:7px 18px;}QPushButton:hover{background:#3f7ffb;}")
        self.btn_ok.clicked.connect(self.do_save)
        brow.addWidget(self.btn_cancel)
        brow.addWidget(self.btn_ok)
        lay.addLayout(brow)

    def insert_image(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            self, "插入图片", "",
            "图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if not p:
            return
        try:
            d = os.path.join(os.path.dirname(config_store.config_path()), "bank_images")
            os.makedirs(d, exist_ok=True)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}_{os.path.basename(p)}"
            dst = os.path.join(d, name)
            shutil.copy2(p, dst)
            cur = self.s.textCursor()
            cur.insertText(f"\n![]({dst})\n")
            self.s.setTextCursor(cur)
            self.s.setFocus()
        except Exception as e:
            error_box(self, "插图失败", str(e))

    def do_save(self):
        q = self.q.toPlainText().strip()
        a = self.a.toPlainText().strip()
        s = self.s.toPlainText().strip()
        t = self.cbo.currentText().strip()
        if not q:
            warn_box(self, "还差题目", "题目不能为空")
            return
        if self.item.get("id"):
            ok, msg = qbank.update(self.item["id"], q, a, s, t)
        else:
            ok, msg, _qid = qbank.add_manual(q, a, s, t, source="manual")
        if not ok:
            error_box(self, "没保存成功", msg)
            return
        if self.on_saved:
            self.on_saved()
        self.accept()


class _DashFetch(QThread):
    """拉取试卷后台数据。"""

    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def __init__(self, code, key, parent=None):
        super().__init__(parent)
        self.code = code
        self.key = key

    def run(self):
        import json as _json
        import urllib.parse as _up
        import urllib.request as _ur
        try:
            u = "https://knowlib.fun/api/dash?code=%s&key=%s" % (
                _up.quote(self.code or ""), _up.quote(self.key or ""))
            with _ur.urlopen(u, timeout=20) as r:
                self.ok.emit(_json.loads(r.read().decode("utf-8", "replace")))
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 403:
                self.bad.emit("作者密钥不对")
            else:
                self.bad.emit("%s: %s" % (type(e).__name__, e))


class DashDialog(QDialog):
    """试卷后台（PC 内直接看，不用开浏览器）。"""

    def __init__(self, parent, code, key, title=""):
        super().__init__(parent)
        self.code = code
        self.key = key
        self.setWindowTitle("试卷后台" + ((" · " + title) if title else ""))
        self.resize(860, 640)
        self._th = None
        c = theme_colors(getattr(parent, "theme", LIGHT))
        self._c = c

        v = QVBoxLayout(self)
        head = QHBoxLayout()
        self.lbl = QLabel("加载中…")
        self.lbl.setObjectName("sub")
        head.addWidget(self.lbl, 1)
        self.chk = QCheckBox("自动刷新（20 秒）")
        self.chk.setChecked(True)
        self.chk.toggled.connect(self._toggle_timer)
        head.addWidget(self.chk)
        self.btn = QPushButton("刷新")
        self.btn.clicked.connect(self.reload)
        head.addWidget(self.btn)
        v.addLayout(head)

        self.view = QTextBrowser()
        v.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        bweb = QPushButton("在浏览器打开")
        bweb.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://knowlib.fun/dash.html?code=%s&key=%s" % (code, key))))
        bok = QPushButton("关闭")
        bok.clicked.connect(self.accept)
        row.addWidget(bweb)
        row.addWidget(bok)
        v.addLayout(row)

        self.timer = QTimer(self)
        self.timer.setInterval(20000)
        self.timer.timeout.connect(self.reload)
        self.timer.start()
        self.reload()

    def _toggle_timer(self, on):
        if on:
            self.timer.start()
        else:
            self.timer.stop()

    def reload(self):
        if self._th is not None and self._th.isRunning():
            return
        self.btn.setEnabled(False)
        th = _DashFetch(self.code, self.key, self)
        th.ok.connect(self._on_data)
        th.bad.connect(self._on_err)
        self._th = th
        th.start()

    @safe_slot
    def _on_err(self, msg):
        self.btn.setEnabled(True)
        self.lbl.setText("加载失败：" + msg)

    @safe_slot
    def _on_data(self, j):
        self.btn.setEnabled(True)
        if not j.get("ok"):
            self.lbl.setText("加载失败" if j.get("message") != "no doc" else "找不到这份试卷")
            return
        c = self._c
        s = j.get("summary") or {}
        self.lbl.setText("《%s》 共 %s 题 · 浏览 %s · 作答 %s 人 / %s 次" % (
            j.get("title") or "", j.get("questions") or 0, s.get("views") or 0,
            s.get("users") or 0, s.get("attempts") or 0))

        def fmt(ts):
            if not ts:
                return "-"
            return time.strftime("%m-%d %H:%M", time.localtime(ts / 1000.0))

        def dur(sec):
            sec = int(sec or 0)
            if sec <= 0:
                return "-"
            if sec < 60:
                return "%d 秒" % sec
            return "%d 分 %d 秒" % (sec // 60, sec % 60)

        cards = [("浏览量", s.get("views") or 0), ("访客", s.get("visitors") or 0),
                 ("作答人数", s.get("users") or 0), ("作答次数", s.get("attempts") or 0),
                 ("平均分", "%s / %s" % (s.get("avgScore") or 0, j.get("total") or "-")),
                 ("平均用时", dur(s.get("avgDur")))]
        html = ["<div style='font-family:Microsoft YaHei UI;color:%s'>" % c["text"]]
        html.append("<table cellspacing=6><tr>")
        for k, val in cards:
            html.append("<td style='padding:8px 14px;border:1px solid %s;border-radius:8px'>"
                        "<div style='font-size:11px;color:%s'>%s</div>"
                        "<div style='font-size:18px;font-weight:700'>%s</div></td>" % ("#d9d9d9", c["muted"], k, val))
        html.append("</tr></table>")

        html.append("<h3 style='font-size:14px;margin:14px 0 6px'>作答的人</h3>")
        html.append("<table width='100%' cellspacing=0 cellpadding=6 border=0>")
        html.append("<tr style='color:%s;font-size:11px'><td>设备</td><td>次数</td><td>首次</td>"
                    "<td>最近</td><td>平均分</td><td>最高</td><td>平均用时</td></tr>" % c["muted"])
        for u in (j.get("users") or []):
            tag = " <span style='color:#d9534f'>(已排除)</span>" if u.get("ex") else ""
            html.append("<tr style='border-top:1px solid #eee'><td>%s<br><span style='font-size:11px;color:%s'>%s</span>%s</td>"
                        "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                            u.get("uid") or "-", c["muted"], u.get("device") or "", tag,
                            u.get("count") or 0, fmt(u.get("first")), fmt(u.get("last")),
                            u.get("avg") or 0, u.get("best") or 0, dur(u.get("avgDur"))))
        if not (j.get("users") or []):
            html.append("<tr><td colspan=7 style='color:%s'>还没有人作答</td></tr>" % c["muted"])
        html.append("</table>")

        html.append("<h3 style='font-size:14px;margin:14px 0 6px'>最近作答</h3>")
        html.append("<table width='100%' cellspacing=0 cellpadding=6>")
        html.append("<tr style='color:%s;font-size:11px'><td>时间</td><td>设备</td><td>得分</td><td>用时</td></tr>" % c["muted"])
        for a in (j.get("recent") or [])[:30]:
            html.append("<tr style='border-top:1px solid #eee'><td>%s</td><td>%s</td><td>%s / %s</td><td>%s</td></tr>" % (
                fmt(a.get("t")), a.get("d") or "-", a.get("score") or 0,
                a.get("total") or j.get("total") or "-", dur(a.get("dur"))))
        html.append("</table>")
        html.append("<div style='margin-top:12px;font-size:11px;color:%s'>"
                    "想把自己测的设备排除？在浏览器后台里点该行的「排除」。</div></div>" % c["muted"])
        self.view.setHtml("".join(html))


class _OnlineFetch(QThread):
    """后台拉取在线试卷，避免卡 UI。"""

    ok = pyqtSignal(object, object)
    bad = pyqtSignal(str)

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self.code = code

    def run(self):
        try:
            doc, _meta = online_quiz.fetch(self.code)
            self.ok.emit(doc, online_quiz.normalize(doc))
        except Exception as e:
            self.bad.emit(str(e) or e.__class__.__name__)


class _GradeThread(QThread):
    """后台判分（填空/简答），不卡 UI。"""

    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def __init__(self, cfg, q, mine, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg or {})
        self.q = q
        self.mine = mine

    def run(self):
        try:
            client = ocr_ai.AiClient(self.cfg)
            r = client.grade_answer(self.q.get("stem"), self.q.get("answer"), self.mine)
            if not r:
                self.bad.emit("模型没按要求返回，再试一次")
            else:
                self.ok.emit(r)
        except Exception as e:
            self.bad.emit("%s: %s" % (type(e).__name__, e))


class OnlinePage(QWidget):
    """在线试卷：粘分享链接 → 在 PC 里做题 → 一键收藏进本地题库。"""

    def __init__(self, parent=None, on_back=None):
        super().__init__(parent)
        self._on_back = on_back
        self.setObjectName("onlinePage")
        self.setStyleSheet("")
        self._c = theme_colors(getattr(parent, "theme", LIGHT))
        self.doc = {}
        self.qs = []
        self.row = -1
        self.picked = {}          # 题号 -> 选过的 key
        self.saved = set()        # 已收藏的题号
        self._th = None
        self._gth = None
        self._answer_open = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # 有 QtWebEngine 就跑桌面版答题页（KaTeX 真公式），否则退回老富文本
        self.use_web = bool(web_view and web_view.web_available())

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.link = QLineEdit()
        self.link.setPlaceholderText(
            "粘贴分享链接：https://knowlib.fun/s/xxxxxx 或 knowlib://s/xxxxxx")
        self.link.returnPressed.connect(self.open_link)
        self.btn_open = QPushButton("打开")
        self.btn_open.setObjectName("primary")
        self.btn_open.setMinimumWidth(72)
        self.btn_open.clicked.connect(self.open_link)
        self.btn_save_all = QPushButton("收藏整卷")
        self.btn_save_all.setCursor(Qt.PointingHandCursor)
        self.btn_save_all.clicked.connect(self.save_all)
        row1.addWidget(self.link, 1)
        row1.addWidget(self.btn_open)
        if self.use_web:
            # 每题卡片上已经「加入题库」，这里只留一个整卷收藏
            self.btn_save_all.setObjectName("ghost")
            row1.addWidget(self.btn_save_all)
        lay.addLayout(row1)

        self.tip = QLabel("")
        self.tip.setObjectName("sub")
        self.tip.setWordWrap(True)
        self.tip.setVisible(False)
        lay.addWidget(self.tip)

        body = QHBoxLayout()
        body.setSpacing(12)
        right = QVBoxLayout()
        right.setSpacing(8)
        self.list = None
        self.ans = None
        if self.use_web:
            self.view = web_view.QuizView(self)
            self.view.loadFinished.connect(self._on_web_loaded)
            self.view.urlChanged.connect(self._on_web_url)
            body.addWidget(self.view, 1)
        else:
            self.list = QListWidget()
            self.list.setFixedWidth(240)
            self.list.setUniformItemSizes(True)
            self.list.currentRowChanged.connect(self.show_item)
            body.addWidget(self.list)
            self.view = QTextBrowser()
            self.view.setOpenLinks(False)
            self.view.anchorClicked.connect(self._on_anchor)
            right.addWidget(self.view, 3)
            self.ans = QTextBrowser()
            self.ans.setVisible(False)
            right.addWidget(self.ans, 2)

        # 填空/简答：自己写答案 → AI 判分
        self.grade_row = QHBoxLayout()
        self.grade_row.setSpacing(8)
        self.mine = QLineEdit()
        self.mine.setPlaceholderText("填空/简答题：在这里写你的答案，点「AI 判分」对比参考答案")
        self.mine.returnPressed.connect(self.do_grade)
        self.btn_grade = QPushButton("AI 判分")
        self.btn_grade.clicked.connect(self.do_grade)
        self.btn_grade.setMinimumWidth(88)
        self.grade_row.addWidget(self.mine, 1)
        self.grade_row.addWidget(self.btn_grade)
        self.grade_hint = QLabel("")
        self.grade_hint.setObjectName("sub")
        self.grade_row.addWidget(self.grade_hint)
        if not self.use_web:
            right.addLayout(self.grade_row)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.btn_show = QPushButton("显示答案")
        self.btn_show.clicked.connect(self.toggle_answer)
        self.btn_save = QPushButton("收藏本题")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self.save_current)
        # 网页模式：上面的行已经有「收藏整卷」，这里只放状态
        show_list = [] if self.use_web else [self.btn_show, self.btn_save]
        for b in show_list:
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 28)
            tools.addWidget(b)
        tools.addStretch(1)
        self.stat = QLabel("")
        self.stat.setObjectName("sub")
        tools.addWidget(self.stat)
        if self.use_web:
            # 网页模式：网页自己占满宽度，状态行放在下面
            lay.addLayout(body, 1)
            lay.addLayout(tools)
        else:
            right.addLayout(tools)
            body.addLayout(right, 1)
            lay.addLayout(body, 1)

        self._empty()

    # ---------------- 网页模式回调
    @safe_slot
    def _on_web_loaded(self, ok):
        if not ok:
            return
        try:
            web_view.install_save_button(self.view)
        except Exception:
            pass

    @safe_slot
    def _on_web_url(self, url):
        """网页里点了「加入题库」→ location.hash 变成 #klsave:N。"""
        try:
            s = url.toString()
        except Exception:
            return
        m = re.search(r"#klsave:(\d+)", s)
        if not m:
            return
        i = int(m.group(1))
        if i < 0 or i >= len(self.qs):
            return
        self.row = i
        if i in self.saved:
            self.stat.setText("第 %d 题已经在题库里了" % (i + 1))
            return
        if self._save_one(i):
            self._refresh_marks()
            self.stat.setText("已加入题库 ✓（第 %d 题，共 %d 题）" % (i + 1, len(self.saved)))
        else:
            self.stat.setText("加入失败（题库写入出错）")

    # ---------------- 状态
    def _empty(self):
        self.view.setHtml("<div style='color:#7f8896;padding:24px;font-family:sans-serif'>"
                           "还没有打开试卷。把同学发你的链接粘到上面，点「打开」。</div>")

    def load_code(self, code):
        code = (code or "").strip()
        if code:
            self.link.setText(code)
            self.open_link()

    def open_link(self):
        code = online_quiz.code_from(self.link.text())
        if not code:
            self.tip.setText("链接里没找到试卷 code（应该是 …/s/xxxxxx 这种）")
            self.tip.setVisible(True)
            return
        self.tip.setText("正在拉取试卷…")
        self.tip.setVisible(True)
        self.btn_open.setEnabled(False)
        th = _OnlineFetch(code, self)
        th.ok.connect(self._on_doc)
        th.bad.connect(self._on_err)
        self._th = th
        th.start()

    @safe_slot
    def _on_err(self, msg):
        self.btn_open.setEnabled(True)
        self.tip.setText("打开失败：" + msg)
        self.tip.setVisible(True)

    @safe_slot
    def _on_doc(self, doc, qs):
        self.btn_open.setEnabled(True)
        self.doc = doc or {}
        self.qs = qs or []
        self.row = -1
        self.picked = {}
        self.saved = set()
        self._answer_open = False
        if self.ans is not None:
            self.ans.setVisible(False)
        title = (self.doc.get("title") or "在线试卷").strip()
        if not self.qs:
            self.tip.setText("这份试卷是空的")
            self._empty()
            return
        total = sum(float(q.get("score") or 0) for q in self.qs)
        if self.use_web:
            # 网页顶部已经有大标题和「共 N 题 · M 分」，这里不再重复
            self.tip.setText("")
            self.tip.setVisible(False)
            try:
                web_view.load_paper(self.view, self.doc)
            except Exception as e:
                self.tip.setText("渲染失败：%s" % e)
                self.tip.setVisible(True)
            self.stat.setText("已加入 0 / %d" % len(self.qs))
            return
        self.tip.setText("《%s》· %d 题 · 共 %g 分" % (title, len(self.qs), total))
        self.tip.setVisible(True)
        self.list.clear()
        for i, q in enumerate(self.qs):
            it = QListWidgetItem("%d. %s" % (i + 1, q.get("type_name") or "题目"))
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        self._refresh_marks()
        self.list.setCurrentRow(0)

    def _refresh_marks(self):
        if self.list is None:
            self.stat.setText("已收藏 %d / %d" % (len(self.saved), len(self.qs)))
            return
        for i in range(self.list.count()):
            it = self.list.item(i)
            q = self.qs[it.data(Qt.UserRole)] if it.data(Qt.UserRole) is not None else None
            if q is None:
                continue
            star = " ★" if i in self.saved else ""
            it.setText("%d. %s%s" % (i + 1, q.get("type_name") or "题目", star))
        self.stat.setText("已收藏 %d / %d" % (len(self.saved), len(self.qs)))

    # ---------------- 答题
    def show_item(self, row):
        if row < 0 or row >= len(self.qs):
            return
        self.row = row
        self._answer_open = False
        self.ans.setVisible(False)
        self.mine.clear()
        self.grade_hint.setText("")
        show_grade = self.qs[row].get("type") in ("blank", "short")
        for i in range(self.grade_row.count()):
            w = self.grade_row.itemAt(i).widget()
            if w is not None:
                w.setVisible(show_grade)
        self._render()

    def _render(self):
        q = self.qs[self.row]
        parts = []
        parts.append("<div style='color:%s;font-size:12px;margin-bottom:8px'>第 %d 题 · %s · %g 分</div>"
                     % (self._c["accent"], self.row + 1, q.get("type_name") or "",
                        float(q.get("score") or 0)))
        parts.append(md_render.to_html(q.get("stem") or "", color=self._c["text"], font_size=15))
        opts = q.get("options") or []
        if q.get("type") == "judge" and not opts:
            opts = [("对", "正确"), ("错", "错误")]
        if opts:
            pick = self.picked.get(self.row)
            right = online_quiz.correct_key(q)
            rows = []
            for k, txt in opts:
                bg, bd = "transparent", "#30363d"
                if pick == k:
                    ok = (k == right)
                    bg = "rgba(46,160,67,0.18)" if ok else "rgba(217,83,79,0.18)"
                    bd = "#2ea043" if ok else "#d9534f"
                elif pick and right and k == right:
                    bd = "#2ea043"
                rows.append(
                    "<div style='margin:6px 0;padding:8px 10px;border:1px solid %s;"
                    "background:%s;border-radius:6px'>"
                    "<a href='pick:%s' style='color:%s;text-decoration:none'>%s．%s</a></div>"
                    % (bd, bg, k, self._c["text"], k, _html.escape(txt)))
            parts.append("".join(rows))
        if self.row in self.saved:
            parts.append("<div style='color:%s;font-size:12px;margin-top:10px'>★ 已收藏到本地题库</div>"
                         % self._c["answer"])
        self.view.setHtml("".join(parts))

    @safe_slot
    def _on_anchor(self, url):
        s = url.toString()
        m = re.search(r"pick:([A-Za-z\u4e00-\u9fff])", s)
        if not m or self.row < 0:
            return
        key = m.group(1).upper() if m.group(1).isascii() else m.group(1)
        self.picked[self.row] = key
        self._render()
        self._answer_open = True
        self.ans.setVisible(True)
        self._render_answer()

    def toggle_answer(self):
        self._answer_open = not self._answer_open
        self.ans.setVisible(self._answer_open)
        if self._answer_open:
            self._render_answer()

    def _render_answer(self, grade=None):
        if self.row < 0:
            return
        q = self.qs[self.row]
        ans = (q.get("answer") or "").strip() or "（这道题没给答案）"
        sol = (q.get("analysis") or "").strip()
        head = ""
        if grade:
            sc = int(grade.get("score") or 0)
            col = self._c["answer"] if sc >= 60 else "#d9534f"
            head = ("<div style='font-size:15px;font-weight:700;color:%s;margin-bottom:8px'>"
                    "AI 判分：%d 分 · %s</div>"
                    % (col, sc, _html.escape(str(grade.get("verdict") or ""))))
            if grade.get("comment"):
                head += ("<div style='color:%s;font-size:13px;margin-bottom:10px'>%s</div>"
                         % (self._c["muted"], _html.escape(str(grade["comment"]))))
        html = (head
                + "<div style='color:%s;font-size:12px;margin-bottom:4px'>答案</div>" % self._c["accent"]
                + md_render.to_html(ans, color=self._c["answer"], font_size=17))
        if sol:
            html += ("<div style='color:%s;font-size:12px;margin:12px 0 4px'>解析</div>" % self._c["accent"]
                     + md_render.to_html(sol, color=self._c["text"], font_size=14))
        self.ans.setHtml(html)
        self.ans.verticalScrollBar().setValue(0)

    # ---------------- 填空/简答判分
    def do_grade(self):
        if self.row < 0:
            return
        mine = self.mine.text().strip()
        if not mine:
            self.grade_hint.setText("先写你的答案")
            return
        q = self.qs[self.row]
        ref = (q.get("answer") or "").strip()
        if not (q.get("stem") or "").strip():
            return
        if ref and online_quiz.same_answer(ref, mine):
            self.grade_hint.setText("与参考答案一致")
            self._show_grade({"score": 100, "verdict": "对", "comment": "与参考答案一致", "cost": 0.0})
            return
        self.btn_grade.setEnabled(False)
        self.grade_hint.setText("判分中…")
        th = _GradeThread(config_store.load(), q, mine, self)
        th.ok.connect(self._show_grade)
        th.bad.connect(self._grade_err)
        self._gth = th
        th.start()

    @safe_slot
    def _show_grade(self, r):
        self.btn_grade.setEnabled(True)
        sc = int((r or {}).get("score") or 0)
        c = (r or {}).get("comment") or ""
        self.grade_hint.setText("%d 分%s" % (sc, (" · " + c) if c else ""))
        self._answer_open = True
        self.ans.setVisible(True)
        self._render_answer(grade=r)

    @safe_slot
    def _grade_err(self, msg):
        self.btn_grade.setEnabled(True)
        self.grade_hint.setText("判分失败：" + (msg or ""))

    # ---------------- 收藏
    def _save_one(self, i):
        q = self.qs[i]
        stem = (q.get("stem") or "").strip()
        if not stem:
            return False
        ok = qbank.save(stem, online_quiz.answer_text(q))
        if ok:
            self.saved.add(i)
        return bool(ok)

    def save_current(self):
        if self.use_web:
            web_view.current_index(self.view, self._save_at)
            return
        if self.row < 0:
            return
        if self.row in self.saved:
            self.stat.setText("这道题已经在题库里了")
            return
        if self._save_one(self.row):
            self._refresh_marks()
            self._render()
            self.stat.setText("已收藏 ✓（题库里可编辑）")
        else:
            self.stat.setText("收藏失败（题库写入出错）")

    @safe_slot
    def _save_at(self, i):
        """网页模式下：拿到当前题号再收藏。"""
        try:
            i = int(i)
        except Exception:
            i = -1
        if i < 0 or i >= len(self.qs):
            self.stat.setText("先在右边选一道题再收藏")
            return
        self.row = i
        if i in self.saved:
            self.stat.setText("这道题已经在题库里了")
            return
        if self._save_one(i):
            self._refresh_marks()
            self.stat.setText("已收藏第 %d 题 ✓（题库里可编辑）" % (i + 1))
        else:
            self.stat.setText("收藏失败（题库写入出错）")

    def save_all(self):
        if not self.qs:
            return
        n = 0
        for i in range(len(self.qs)):
            if i not in self.saved and self._save_one(i):
                n += 1
        self._refresh_marks()
        if not self.use_web and self.row >= 0:
            self._render()
        self.stat.setText("已收藏 %d 题 ✓" % n if n else "都已收藏过了")

    def _go_back(self):
        if self._on_back:
            self._on_back()



class _DashPanel(QWidget):
    """试卷后台（内嵌面板，不开新窗口）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.code = ""
        self.key = ""
        self._th = None
        self._c = theme_colors(getattr(parent, "theme", LIGHT))
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        row = QHBoxLayout()
        self.pick = WinCombo()
        self.pick.setMinimumWidth(240)
        self.pick.activated[int].connect(self._on_pick)
        row.addWidget(self.pick, 1)
        self.btn = QPushButton("刷新")
        self.btn.clicked.connect(self.reload)
        row.addWidget(self.btn)
        self.chk = QCheckBox("自动刷新（20 秒）")
        self.chk.setChecked(True)
        self.chk.toggled.connect(self._toggle)
        row.addWidget(self.chk)
        v.addLayout(row)

        self.lbl = QLabel("选一份试卷看数据")
        self.lbl.setObjectName("sub")
        v.addWidget(self.lbl)

        self.view = QTextBrowser()
        v.addWidget(self.view, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(20000)
        self.timer.timeout.connect(self.reload)
        self._reload_papers()

    def _reload_papers(self):
        self.pick.blockSignals(True)
        self.pick.clear()
        papers = dict(config_store.load().get("papers") or {})
        if not papers:
            self.pick.addItem("（还没有发布过在线试卷）", "")
        else:
            rows = sorted(papers.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)
            for code, info in rows:
                self.pick.addItem("《%s》 %s" % (info.get("title") or "未命名", code),
                                  (code, info.get("key") or ""))
        self.pick.blockSignals(False)
        if self.pick.count():
            self._on_pick(0)

    def _on_pick(self, idx):
        data = self.pick.itemData(idx)
        if not data:
            return
        self.code, self.key = data[0], data[1]
        self.reload()

    def _toggle(self, on):
        if on:
            self.timer.start()
        else:
            self.timer.stop()

    def reload(self):
        if not self.code:
            return
        if self._th is not None and self._th.isRunning():
            return
        self.btn.setEnabled(False)
        th = _DashFetch(self.code, self.key, self)
        th.ok.connect(self._on_data)
        th.bad.connect(self._on_err)
        self._th = th
        th.start()

    @safe_slot
    def _on_err(self, msg):
        self.btn.setEnabled(True)
        self.lbl.setText("加载失败：" + msg)

    @safe_slot
    def _on_data(self, j):
        self.btn.setEnabled(True)
        if not j.get("ok"):
            self.lbl.setText("加载失败" if j.get("message") != "no doc" else "找不到这份试卷")
            return
        c = self._c
        s = j.get("summary") or {}
        self.lbl.setText("《%s》 共 %s 题 · 浏览 %s · 作答 %s 人 / %s 次" % (
            j.get("title") or "", j.get("questions") or 0, s.get("views") or 0,
            s.get("users") or 0, s.get("attempts") or 0))

        def fmt(ts):
            return time.strftime("%m-%d %H:%M", time.localtime(ts / 1000.0)) if ts else "-"

        def dur(sec):
            sec = int(sec or 0)
            if sec <= 0:
                return "-"
            return ("%d 秒" % sec) if sec < 60 else ("%d 分 %d 秒" % (sec // 60, sec % 60))

        cards = [("浏览量", s.get("views") or 0), ("访客", s.get("visitors") or 0),
                 ("作答人数", s.get("users") or 0), ("作答次数", s.get("attempts") or 0),
                 ("平均分", "%s / %s" % (s.get("avgScore") or 0, j.get("total") or "-")),
                 ("平均用时", dur(s.get("avgDur")))]
        h = ["<div style='font-family:Microsoft YaHei UI'>"]
        h.append("<table cellspacing=6><tr>")
        for k, val in cards:
            h.append("<td style='padding:8px 14px;border:1px solid #ddd;border-radius:8px'>"
                     "<div style='font-size:11px;color:%s'>%s</div>"
                     "<div style='font-size:18px;font-weight:700'>%s</div></td>" % (c["muted"], k, val))
        h.append("</tr></table>")

        h.append("<div style='font-size:13px;font-weight:600;margin:12px 0 4px'>作答的人</div>")
        h.append("<table width='100%' cellspacing=0 cellpadding=5>")
        h.append("<tr style='color:%s;font-size:11px'><td>设备</td><td>次数</td><td>首次</td>"
                 "<td>最近</td><td>均分</td><td>最高</td><td>用时</td></tr>" % c["muted"])
        for u in (j.get("users") or []):
            tag = " <span style='color:#d9534f'>(已排除)</span>" if u.get("ex") else ""
            h.append("<tr style='border-top:1px solid #eee'><td>%s<br><span style='font-size:11px;color:%s'>%s</span>%s</td>"
                     "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                         u.get("uid") or "-", c["muted"], u.get("device") or "", tag,
                         u.get("count") or 0, fmt(u.get("first")), fmt(u.get("last")),
                         u.get("avg") or 0, u.get("best") or 0, dur(u.get("avgDur"))))
        if not (j.get("users") or []):
            h.append("<tr><td colspan=7 style='color:%s'>还没有人作答</td></tr>" % c["muted"])
        h.append("</table>")
        self.view.setHtml("".join(h))


class PaperHubPage(QWidget):
    """试卷主页面：在线试卷 / 历史试卷 / 试卷后台 / 组卷。"""

    TABS = (("online", "在线试卷"), ("hist", "历史试卷"),
            ("dash", "试卷后台"), ("compose", "组卷"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("paperPage")
        self.setStyleSheet("")
        self._c = theme_colors(getattr(parent, "theme", LIGHT))
        self._parent = parent

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_tabs = {}
        self.on_tab = None
        # 四个分区现在在左侧上级菜单（navSub），页面顶部不再占一行
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        # 在线试卷
        self.online = OnlinePage(self)
        self.stack.addWidget(self.online)

        # 历史试卷
        self.pane_hist = QWidget()
        vh = QVBoxLayout(self.pane_hist)
        vh.setContentsMargins(0, 0, 0, 0)
        vh.setSpacing(8)
        self.hist = QListWidget()
        self.hist.itemDoubleClicked.connect(lambda _i: self.open_hist())
        vh.addWidget(self.hist, 1)
        rh = QHBoxLayout()
        rh.addStretch(1)
        b1 = QPushButton("打开试卷")
        b2 = QPushButton("看后台")
        b3 = QPushButton("复制链接")
        b1.clicked.connect(self.open_hist)
        b2.clicked.connect(self.hist_dash)
        b3.clicked.connect(self.copy_hist)
        for b in (b1, b2, b3):
            rh.addWidget(b)
        vh.addLayout(rh)
        self.stack.addWidget(self.pane_hist)

        # 试卷后台
        self.dash = _DashPanel(self)
        self.stack.addWidget(self.dash)

        # 组卷
        self.pane_compose = QWidget()
        vc = QVBoxLayout(self.pane_compose)
        vc.setContentsMargins(0, 0, 0, 0)
        vc.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(QLabel("试卷标题"))
        self.paper_title = QLineEdit()
        self.paper_title.setPlaceholderText("例：液压泵章节练习")
        top.addWidget(self.paper_title, 1)
        vc.addLayout(top)
        self.qlist = QListWidget()
        vc.addWidget(self.qlist, 1)
        rc = QHBoxLayout()
        self.chk_all = QCheckBox("全选")
        self.chk_all.toggled.connect(self._check_all)
        rc.addWidget(self.chk_all)
        rc.addStretch(1)
        bmd = QPushButton("导出 Markdown")
        bpdf = QPushButton("导出 PDF")
        blin = QPushButton("在线分享链接")
        blin.setObjectName("primary")
        bmd.clicked.connect(lambda: self.make_paper("md"))
        bpdf.clicked.connect(lambda: self.make_paper("pdf"))
        blin.clicked.connect(self.share_paper)
        for b in (bmd, bpdf, blin):
            rc.addWidget(b)
        vc.addLayout(rc)
        self.compose_stat = QLabel("")
        self.compose_stat.setObjectName("sub")
        vc.addWidget(self.compose_stat)
        self.stack.addWidget(self.pane_compose)

        self._tab = "online"
        self.show_tab("online")

    # ---------- tabs
    def show_tab(self, key):
        self._tab = key
        for k, b in self.btn_tabs.items():
            b.setChecked(k == key)
        idx = {"online": 0, "hist": 1, "dash": 2, "compose": 3}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        try:
            if self.on_tab:
                self.on_tab(key)
        except Exception:
            traceback.print_exc()
        if key == "hist":
            self.reload_hist()
        elif key == "compose":
            self.reload_bank()
        elif key == "dash":
            self.dash._reload_papers()

    # ---------- 在线试卷
    def open_online(self, code):
        self.show_tab("online")
        self.online.load_code(code)

    # ---------- 历史试卷
    def reload_hist(self):
        self.hist.clear()
        papers = dict(config_store.load().get("papers") or {})
        if not papers:
            it = QListWidgetItem("还没有发布过在线试卷（去「组卷」发一份）")
            it.setFlags(Qt.NoItemFlags)
            self.hist.addItem(it)
            return
        rows = sorted(papers.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)
        for code, info in rows:
            t = info.get("t") or 0
            when = time.strftime("%m-%d %H:%M", time.localtime(t / 1000.0)) if t else ""
            it = QListWidgetItem("《%s》  %s  %s" % (info.get("title") or "未命名", code, when))
            it.setData(Qt.UserRole, (code, info.get("key") or "", info.get("title") or ""))
            self.hist.addItem(it)

    def _hist_sel(self):
        it = self.hist.currentItem()
        return (it.data(Qt.UserRole) if it else None) or ("", "", "")

    def open_hist(self):
        code = self._hist_sel()[0]
        if code:
            self.show_tab("online")
            self.online.load_code(code)

    def hist_dash(self):
        code, key, title = self._hist_sel()
        if code:
            self.show_tab("dash")
            idx = self.dash.pick.findData((code, key))
            if idx >= 0:
                self.dash.pick.setCurrentIndex(idx)
                self.dash._on_pick(idx)

    def copy_hist(self):
        code = self._hist_sel()[0]
        if code:
            QApplication.clipboard().setText(online_quiz.BASE + "/s/" + code)
            self.hist.setToolTip("已复制")

    # ---------- 组卷
    def reload_bank(self):
        self.qlist.clear()
        for it in qbank.items(limit=800):
            q1 = " ".join((it["q_text"] or "").split())[:44] or "（无题目）"
            item = QListWidgetItem(q1)
            item.setData(Qt.UserRole, it)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.qlist.addItem(item)
        if self.qlist.count() == 0:
            it = QListWidgetItem("题库是空的，先去搜几道题")
            it.setFlags(Qt.NoItemFlags)
            self.qlist.addItem(it)
        self.compose_stat.setText("共 %d 题" % self.qlist.count())

    def _check_all(self, on):
        for i in range(self.qlist.count()):
            it = self.qlist.item(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _checked(self):
        out = []
        for i in range(self.qlist.count()):
            it = self.qlist.item(i)
            if (it.flags() & Qt.ItemIsUserCheckable) and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole) or {})
        return out

    def _bank_page(self):
        """借用 BankPage 的 doc 构造/分享逻辑。"""
        return getattr(self._parent, "page_bank", None)

    def make_paper(self, fmt):
        items = self._checked()
        if not items:
            self.compose_stat.setText("先勾选题目")
            return
        bp = self._bank_page()
        if bp is None:
            return
        title = (self.paper_title.text() or "").strip() or (time.strftime("%Y-%m-%d") + " 组卷")
        from PyQt5.QtWidgets import QFileDialog
        import paper_export
        ext = "md" if fmt == "md" else "pdf"
        safe = re.sub(r'[\\/:*?"<>|]', "-", title)
        fp, _ = QFileDialog.getSaveFileName(self, "保存试卷", "%s.%s" % (safe, ext),
                                            "Markdown (*.md)" if fmt == "md" else "PDF (*.pdf)")
        if not fp:
            return
        try:
            if fmt == "md":
                md = paper_export.build_md(items, title=title, with_answer=True)
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(md)
            else:
                paper_export.export_pdf(items, fp, title=title, with_answer=True)
            self.compose_stat.setText("已导出 → %s" % os.path.basename(fp))
        except Exception as e:
            self.compose_stat.setText("导出失败：%s" % e)

    def share_paper(self):
        items = self._checked()
        if not items:
            self.compose_stat.setText("先勾选题目")
            return
        bp = self._bank_page()
        if bp is None:
            return
        # 复用 BankPage 的分享流程（它自带标题弹窗与后台密钥）
        bp._checked_items = lambda: items  # 临时覆盖，走同一套逻辑
        bp.share_paper()


class BankPage(QWidget):
    """本地题库：检索 / 复制 / 删除 / 导入导出（内嵌在主窗口里）。"""

    def __init__(self, parent=None, on_back=None):
        super().__init__(parent)
        self._on_back = on_back
        self.setObjectName("bankPage")
        self.setStyleSheet("")
        self._c = theme_colors(getattr(parent, "theme", LIGHT))

        self.items = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        t = QLabel("题库")
        t.setObjectName("pageTitle")
        lay.addWidget(t)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜题目或答案…（回车刷新）")
        self.search.returnPressed.connect(self.reload)
        self.stat = QLabel("")
        self.stat.setStyleSheet("color:#7f8896;font-size:12px;")
        top.addWidget(self.search, 1)
        top.addWidget(self.stat)
        lay.addLayout(top)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(260)
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setUniformItemSizes(True)
        self.list.currentRowChanged.connect(self.show_item)
        self.list.itemDoubleClicked.connect(lambda _i: self.edit_current())
        body.addWidget(self.list)

        right = QVBoxLayout()
        self.view = QTextBrowser()
        right.addWidget(self.view, 1)
        # ---- 按钮（先统一建好，再用网格摆，避免窗口变窄时被挤成一条）
        self.btn_edit = QPushButton("编辑这条")
        self.btn_edit.clicked.connect(self.edit_current)
        self.btn_new = QPushButton("＋ 新建")
        self.btn_new.clicked.connect(self.new_question)
        self.btn_del = QPushButton("删除这条")
        self.btn_del.clicked.connect(self.delete_current)

        # 全选 / 取消全选 合成一个按钮
        self.btn_sel = QPushButton("全选")
        self.btn_sel.clicked.connect(self.toggle_select_all)

        # 导出合成一个下拉
        self.btn_export_menu = QPushButton("导出 ▾")
        self.btn_export_menu.setStyleSheet(
            "QPushButton{background:#2f6feb;color:#ffffff;border:none;border-radius:8px;"
            "padding:7px 16px;}QPushButton:hover{background:#3f7ffb;}")
        m = QMenu(self.btn_export_menu)
        m.addAction("导出 Markdown（.md）", self.export_md)
        m.addAction("导出 PDF", self.export_pdf)
        m.addAction("导出 CSV（整个题库）", self.export_csv)
        self.btn_export_menu.setMenu(m)

        # 组卷：把勾选的题拼成一张卷子（带标题）
        self.btn_paper = QPushButton("组卷 ▾")
        self.btn_paper.setCursor(Qt.PointingHandCursor)
        mp = QMenu(self.btn_paper)
        mp.addAction("组卷 → Markdown", lambda: self.make_paper("md"))
        mp.addAction("组卷 → PDF", lambda: self.make_paper("pdf"))
        mp.addAction("组卷 → 在线分享链接", self.share_paper)
        mp.addAction("试卷后台…", self.open_dash)
        self.btn_paper.setMenu(mp)

        self.btn_import = QPushButton("导入题库")
        self.btn_import.setToolTip("支持 CSV / Markdown / TXT / JSON")
        self.btn_import.clicked.connect(self.import_file)
        self.btn_clear = QPushButton("清空题库")
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_close = QPushButton("← 返回")
        self.btn_close.clicked.connect(self._go_back)
        self.btn_close.setVisible(False)

        self.chk_answer = QCheckBox("导出含答案与解析")
        self.chk_answer.setStyleSheet("QCheckBox{color:#98a2b3;}")

        tools = QGridLayout()
        tools.setHorizontalSpacing(8)
        tools.setVerticalSpacing(8)
        cells = [
            (self.btn_edit, 0, 0), (self.btn_new, 0, 1),
            (self.btn_del, 0, 2), (self.btn_sel, 0, 3),
            (self.btn_export_menu, 1, 0), (self.btn_paper, 1, 1),
            (self.btn_import, 1, 2), (self.btn_clear, 1, 3),
            (self.btn_close, 2, 3),
        ]
        for b, r, col in cells:
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 30)
            tools.addWidget(b, r, col)
        tools.addWidget(self.chk_answer, 2, 0, 1, 3)
        tools.setColumnStretch(4, 1)
        right.addLayout(tools)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)

        self.reload()

    def reload(self):
        kw = self.search.text().strip()
        self.items = qbank.items(kw, limit=500)
        checked = {it.get("id") for it in self._checked_items()}
        self.list.clear()
        for it in self.items:
            q1 = " ".join((it["q_text"] or "").split())[:24] or "（无题目）"
            mark = "" if it["source"] == "ai" else f"·{it['source']}"
            t = it.get("qtype") or ""
            head = f"[{(t[:2] + '·') if t else ''}{it['hits']}] "
            item = QListWidgetItem(f"{head}{q1}{mark}")
            item.setToolTip((it["q_text"] or "").strip()[:400])
            item.setData(Qt.UserRole, it)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if it.get("id") in checked else Qt.Unchecked)
            self.list.addItem(item)
        st = qbank.stats()
        self.stat.setText(f"共 {st['count']} 题 · 命中 {st['hits']} 次")
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.view.setHtml("<div style='color:#7f8896;padding:20px'>"
                              "题库还是空的。识别并答对一次后会自动存进来。</div>")

    def _checked_items(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole) or {})
        return out

    def check_all(self, on):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        if hasattr(self, "btn_sel"):
            self.btn_sel.setText("全选" if not on else "取消全选")

    def toggle_select_all(self):
        total = self.list.count()
        if total == 0:
            return
        all_on = all(self.list.item(i).checkState() == Qt.Checked for i in range(total))
        self.check_all(not all_on)

    def show_item(self, row):
        if row < 0 or row >= self.list.count():
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        qtype = it.get("qtype") or ""
        head = (f"<div style='color:{self._c['muted']};font-size:12px'>存于 {it.get('created','')}"
                f" · 命中 {it.get('hits',0)} 次"
                + (f" · {HistoryPage._esc(qtype)}" if qtype else "")
                + "</div>")
        ans = (it.get("answer") or "").strip()
        sol = (it.get("solution") or "").strip()
        if not ans and sol:
            ans = _guess_answer(sol)
        ans_html = (md_render.to_html(ans, color=self._c["answer"], font_size=16) if ans
                    else "<div style='color:%s;font-size:13px;padding:4px 0'>"
                         "本题未单独给出答案，见下方解析</div>" % self._c["muted"])
        html = (
            head
            + "<div style='color:%s;font-size:12px;margin:12px 0 4px'>题目</div>" % self._c["accent"]
            + md_render.to_html(md_render.clean_text(it.get("q_text", "")),
                                color=self._c["text"], font_size=15)
            + "<div style='color:%s;font-size:12px;margin:14px 0 4px'>答案</div>" % self._c["accent"]
            + ans_html
            + "<div style='color:%s;font-size:12px;margin:14px 0 4px'>解析</div>" % self._c["accent"]
            + md_render.to_html(sol or "（无）", color=self._c["text"], font_size=15)
        )
        self.view.setHtml(html)
        self.view.verticalScrollBar().setValue(0)

    def edit_current(self):
        row = self.list.currentRow()
        if row < 0:
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        EditQuestionDialog(self, item=it, on_saved=self.reload).exec_()

    def new_question(self):
        EditQuestionDialog(self, item=None, on_saved=self.reload).exec_()

    def copy_current(self):
        row = self.list.currentRow()
        if row < 0:
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        QApplication.clipboard().setText(
            f"答案：{it.get('answer','')}\n解析：{it.get('solution','')}")
        self.stat.setText("已复制✓")

    def delete_current(self):
        row = self.list.currentRow()
        if row < 0:
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        if not ask_yes(self, "删除这条", "删掉这条题目？不可恢复。", ok_text="删除"):
            return
        qbank.delete(it.get("id"))
        self.reload()

    def export_csv(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getSaveFileName(self, "导出题库", "knowlib-bank.csv", "CSV (*.csv)")
        if not p:
            return
        ok, n = qbank.export_csv(p)
        self.stat.setText(f"已导出 {n} 题 ✓" if ok else f"导出失败：{n}")

    def import_file(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            self, "导入题库", "",
            "题库文件 (*.csv *.md *.markdown *.txt *.json);;"
            "CSV (*.csv);;Markdown (*.md *.markdown);;文本 (*.txt);;JSON (*.json)")
        if not p:
            return
        ok, n = qbank.import_any(p)
        self.stat.setText(f"已导入 {n} 题 ✓" if ok else f"导入失败：{n}")
        if ok:
            self.reload()

    # -------- 导出（MD / PDF）
    @staticmethod
    def _default_name(ext):
        return f"Knowlib题库-{time.strftime('%Y%m%d-%H%M')}.{ext}"

    def export_md(self):
        from PyQt5.QtWidgets import QFileDialog
        import paper_export
        items = self._checked_items()
        if not items:
            self.stat.setText("先勾选要导出的题目")
            return
        p, _ = QFileDialog.getSaveFileName(self, "导出 Markdown", self._default_name("md"),
                                           "Markdown (*.md)")
        if not p:
            return
        md = paper_export.build_md(items, with_answer=self.chk_answer.isChecked())
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(md)
            self.stat.setText(f"已导出 {len(items)} 题 → {os.path.basename(p)} ✓")
        except Exception as e:
            self.stat.setText(f"导出失败：{e}")

    def export_pdf(self):
        from PyQt5.QtWidgets import QFileDialog
        import paper_export
        items = self._checked_items()
        if not items:
            self.stat.setText("先勾选要导出的题目")
            return
        p, _ = QFileDialog.getSaveFileName(self, "导出 PDF", self._default_name("pdf"),
                                           "PDF (*.pdf)")
        if not p:
            return
        try:
            paper_export.export_pdf(items, p, with_answer=self.chk_answer.isChecked())
            self.stat.setText(f"已导出 {len(items)} 题 → {os.path.basename(p)} ✓")
        except Exception as e:
            self.stat.setText(f"导出失败：{type(e).__name__}: {e}")

    def clear_all(self):
        if not ask_yes(self, "清空题库", "确定删除全部题目？不可恢复。"):
            return
        qbank.clear()
        self.reload()

    def _items_to_doc(self, items, title):
        """把题库条目拼成服务器认的试卷 JSON。"""
        import paper_export as pe
        tmap = {"选择题": "choice", "判断题": "judge", "填空题": "blank",
                "简答题": "short", "计算题": "short"}
        qs = []
        for it in items:
            qtext = it.get("q_text") or ""
            ans = (it.get("answer") or "").strip()
            sol = (it.get("solution") or "").strip()
            tname = pe.detect_type(qtext, ans)
            qtype = tmap.get(tname, "short")
            try:
                stem, opts = pe.parse_options(qtext)
            except Exception:
                stem, opts = "", []
            if not opts:
                _stem2, _opts2 = online_quiz.parse_options_inline(qtext)
                if _opts2:
                    stem, opts = _stem2, _opts2
                    if tname not in ("选择题", "判断题"):
                        tname, qtype = "选择题", "choice"
            if not stem:
                try:
                    stem = pe.clean_stem(qtext) or qtext
                except Exception:
                    stem = qtext
            if qtype == "judge" and ans in ("正确", "错误"):
                ans = "对" if ans == "正确" else "错"
            score = float(it.get("score") or 0) or (2.0 if qtype in ("choice", "judge", "blank") else 5.0)
            qs.append({
                "type": qtype, "typeName": tname, "stem": stem,
                "score": score,
                "options": [{"label": k, "text": t} for k, t in (opts or [])],
                "answer": ans, "answerMd": ans, "analysis": sol,
                "img": "", "imgW": 0, "imgH": 0,
            })
        total = sum(q["score"] for q in qs)
        return {"title": title, "total": total, "used": "0 \u5206 0 \u79d2", "logo": "",
                "author": "", "exam": {"enabled": False}, "questions": qs}

    def share_paper(self):
        """组卷 → 上传到 knowlib.fun → 拿到在线分享链接。"""
        items = self._checked_items()
        if not items:
            self.stat.setText("先勾选要分享的题目")
            return
        title, ok = QInputDialog.getText(self, "在线分享", "试卷标题：",
                                         text="%s 组卷" % time.strftime("%Y-%m-%d"))
        if not ok:
            return
        title = (title or "").strip() or "组卷"
        self.stat.setText("正在生成在线试卷…")
        try:
            doc = self._items_to_doc(items, title)
            url = online_quiz.share(doc, author="PC-" + (os.environ.get("COMPUTERNAME") or ""))
        except Exception as e:
            self.stat.setText("生成链接失败：%s" % e)
            return
        self.stat.setText("已生成在线试卷 ✓")
        import secrets
        code = online_quiz.code_from(url) or ""
        # 作者密钥：跟同步密钥保持一致，这样任何设备用同一个同步码都能看后台
        owner_key = (config_store.load().get("sync_key") or "").strip()
        if len(owner_key) < 4:
            owner_key = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
        claimed = False
        try:
            claimed = online_quiz.claim_owner(code, owner_key) if code else False
        except Exception:
            claimed = False
        if claimed:
            try:
                cfg = config_store.load()
                papers = cfg.get("papers") or {}
                papers[code] = {"key": owner_key, "title": title, "t": int(time.time())}
                cfg["papers"] = papers
                config_store.save(cfg)
            except Exception:
                pass
        dash_url = online_quiz.BASE + "/dash.html#" + code

        dlg = QDialog(self)
        dlg.setWindowTitle("在线试卷已生成")
        dlg.resize(560, 300)
        v = QVBoxLayout(dlg)
        tip = QLabel("把链接发给同学，手机/电脑都能直接打开：")
        v.addWidget(tip)
        le = QLineEdit(url)
        le.setReadOnly(True)
        le.selectAll()
        v.addWidget(le)

        v.addWidget(QLabel("试卷后台（看谁做了、做了几次、用了多久）："))
        dle = QLineEdit(dash_url)
        dle.setReadOnly(True)
        v.addWidget(dle)
        if claimed:
            v.addWidget(QLabel("作者密钥（后台要用，请存好）："))
            kle = QLineEdit(owner_key)
            kle.setReadOnly(True)
            v.addWidget(kle)
        else:
            note = QLabel("（后台密钥没认领成功：这份卷子可能已经被别人认领，或网络不通）")
            note.setObjectName("sub")
            note.setWordWrap(True)
            v.addWidget(note)

        row = QHBoxLayout()
        row.addStretch(1)
        bcopy = QPushButton("复制链接")
        bkey = QPushButton("复制密钥")
        bdash = QPushButton("打开后台")
        bok = QPushButton("完成")
        for b in (bcopy, bkey, bdash, bok):
            row.addWidget(b)
        v.addLayout(row)
        bcopy.clicked.connect(lambda: (QApplication.clipboard().setText(url), tip.setText("链接已复制 ✓")))
        bkey.clicked.connect(lambda: (QApplication.clipboard().setText(owner_key), tip.setText("密钥已复制 ✓")))
        bdash.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(dash_url)))
        bok.clicked.connect(dlg.accept)
        dlg.exec_()

    def open_dash(self):
        """已生成的在线试卷 → 直接打开后台。"""
        cfg = config_store.load()
        papers = dict(cfg.get("papers") or {})
        if not papers:
            self.stat.setText("还没生成过在线试卷（先组卷 → 在线分享链接）")
            return
        rows = sorted(papers.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)
        dlg = QDialog(self)
        dlg.setWindowTitle("试卷后台")
        dlg.resize(560, 360)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("选一份试卷："))
        lw = QListWidget()
        for code, info in rows:
            it = QListWidgetItem("《%s》 %s" % (info.get("title") or "未命名", code))
            it.setData(Qt.UserRole, (code, info.get("key") or "", info.get("title") or ""))
            lw.addItem(it)
        v.addWidget(lw, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        bopen = QPushButton("打开后台")
        bkey = QPushButton("复制密钥")
        bok = QPushButton("关闭")
        row.addWidget(bopen)
        row.addWidget(bkey)
        row.addWidget(bok)
        v.addLayout(row)

        def _sel():
            it = lw.currentItem()
            return it.data(Qt.UserRole) if it else ("", "", "")

        def _open_selected():
            code, key, title = _sel()
            if not code:
                return
            DashDialog(self, code, key, title).exec_()

        bopen.clicked.connect(_open_selected)
        bkey.clicked.connect(lambda: (
            QApplication.clipboard().setText(_sel()[1]),
            self.stat.setText("密钥已复制")))
        bok.clicked.connect(dlg.accept)
        lw.itemDoubleClicked.connect(lambda _i: _open_selected())
        if lw.count():
            lw.setCurrentRow(0)
        dlg.exec_()

    def make_paper(self, fmt="md"):
        """组卷：把勾选的题拼成一张带标题的卷子（MD / PDF）。"""
        items = self._checked_items()
        if not items:
            self.stat.setText("先勾选要组卷的题目")
            return
        from PyQt5.QtWidgets import QFileDialog
        import paper_export
        title, ok = QInputDialog.getText(self, "组卷", "试卷标题：",
                                         text="%s 组卷" % time.strftime("%Y-%m-%d"))
        if not ok:
            return
        title = (title or "").strip() or "组卷"
        ext = "md" if fmt == "md" else "pdf"
        safe = re.sub(r'[\\/:*?"<>|]', "-", title)
        p, _ = QFileDialog.getSaveFileName(
            self, "保存试卷", "%s.%s" % (safe, ext),
            "Markdown (*.md)" if fmt == "md" else "PDF (*.pdf)")
        if not p:
            return
        try:
            if fmt == "md":
                md = paper_export.build_md(items, title=title,
                                           with_answer=self.chk_answer.isChecked())
                with open(p, "w", encoding="utf-8") as f:
                    f.write(md)
            else:
                paper_export.export_pdf(items, p, title=title,
                                        with_answer=self.chk_answer.isChecked())
            self.stat.setText("已生成试卷 → %s ✓" % os.path.basename(p))
        except Exception as e:
            self.stat.setText("组卷失败：%s" % (e,))

    def _go_back(self):
        if self._on_back:
            self._on_back()


# ---------------------------------------------------------------- 扫描线程
class ScanWorker(QObject):
    state = pyqtSignal(str, str)      # (文字, 颜色)
    detail = pyqtSignal(str)          # 一行细节/耗时
    ocr_done = pyqtSignal(str)        # 识别到的题目文字
    delta = pyqtSignal(str)           # 流式增量
    finished_answer = pyqtSignal(str, float)
    failed = pyqtSignal(str)
    prepare_capture = pyqtSignal()    # 截图前：先把答案窗挪开
    after_capture = pyqtSignal()      # 截图后：恢复

    def __init__(self, cfg, region, hwnd=None):
        super().__init__()
        self.cfg = cfg
        self.region = region
        self.hwnd = int(hwnd) if hwnd else 0
        self.running = True
        self.paused = False
        self.force_once = False
        self.last_sig = None
        self.last_q_key = ""
        self.idle_rounds = 0
        self.ocr = None
        self.bank_used = False
        self.bank_score = 0.0
        self.last_usage = {}

    def ensure_ocr(self):
        if self.ocr is None:
            self.state.emit("加载识别模型…", COLOR_WARN)
            self.ocr = ocr_ai.OcrEngine()
        return self.ocr

    def request_once(self):
        self.force_once = True

    @staticmethod
    def _ocr_poor(text, conf):
        """OCR 是否已经乱到不能用了（公式/图题典型表现）。"""
        t = (text or "").strip()
        if len(t) < 12:
            return True
        cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
        if cjk / max(1, len(t)) < 0.42:
            return True
        if conf and conf < 0.78:
            return True
        for bad in ("\u25a1", "\u53e3\u53e3"):
            if bad in t:
                return True
        return False

    def loop(self):
        import mss
        from PIL import Image

        self.state.emit("就绪，等待题目", COLOR_IDLE)
        sct = mss.mss()
        while self.running:
            if self.paused and not self.force_once:
                self.state.emit("已暂停", COLOR_WARN)
                time.sleep(0.2)
                continue
            try:
                t0 = time.time()
                # 先让主线程把答案窗藏起来，避免把答案本身拍进去（否则会死循环）
                self.prepare_capture.emit()
                time.sleep(0.06)
                if self.hwnd:
                    img = remote_server.capture(self.hwnd)
                    self.after_capture.emit()
                    if img is None:
                        self.state.emit("抓不到这个窗口（最小化 / 已关闭）", COLOR_WARN)
                        time.sleep(1.2)
                        continue
                else:
                    shot = sct.grab(self.region)
                    self.after_capture.emit()
                    img = Image.frombytes("RGB", shot.size, shot.rgb)
                # 感知哈希：缩小成灰度小图再比，容忍光标闪烁/抗锯齿等微小变化
                small = img.convert("L").resize((64, 40))
                sig = list(small.getdata())
                unchanged = False
                if self.cfg.get("dedupe", True) and self.last_sig is not None and not self.force_once:
                    diff = 0
                    for a, b in zip(sig, self.last_sig):
                        d = a - b
                        diff += d if d >= 0 else -d
                    unchanged = (diff / len(sig)) < 2.2
                self.last_sig = sig
                if unchanged:
                    # 没变化：不 OCR、不请求，逐步放慢（省 CPU）
                    self.idle_rounds = min(self.idle_rounds + 1, 8)
                    if self.idle_rounds == 3:
                        self.state.emit("监控中（画面没变化，已降频）", COLOR_IDLE)
                    time.sleep(min(0.4 * self.idle_rounds, 3.0))
                    continue
                self.idle_rounds = 0
                self.force_once = False

                self.state.emit("识别中…", COLOR_ACCENT)
                eng = self.ensure_ocr()
                text, conf, ocr_cost = eng.recognize(img)
                if not text or len(text) < 6:
                    self.detail.emit(f"OCR {ocr_cost:.2f}s · 没读到文字，跳过")
                    self.state.emit("没读到文字（区域里没内容？）", COLOR_WARN)
                    time.sleep(0.6)
                    continue

                q_key = "".join(text.split())[:300]
                if q_key == self.last_q_key and not self.force_once:
                    self.detail.emit(f"OCR {ocr_cost:.2f}s · 同一道题，跳过（不重复请求）")
                    self.state.emit("同一道题，已跳过", COLOR_IDLE)
                    time.sleep(max(1.0, float(self.cfg.get("scan_interval", 1.2))))
                    continue
                self.last_q_key = q_key
                self.force_once = False

                self.ocr_done.emit(text)
                self.detail.emit(f"OCR {ocr_cost:.2f}s · {len(text)} 字 · 置信 {conf:.2f}")

                # —— 先查本地题库（命中就秒回，不花 token）
                if self.cfg.get("bank_first", True):
                    hit = qbank.lookup(text)
                    if hit:
                        self.bank_used = True
                        self.bank_score = float(hit.get("score", 0.0))
                        full = f"答案：{hit['answer']}\n解析：{hit['solution']}"
                        self.finished_answer.emit(full, 0.0)
                        self.detail.emit(
                            f"题库命中（相似度 {hit['score']:.2f}）· 未调用 AI")
                        self.state.emit("本地题库命中", COLOR_OK)
                        time.sleep(max(0.3, float(self.cfg.get("scan_interval", 1.2))))
                        continue
                self.bank_used = False

                mode = str(self.cfg.get("vision_mode", "auto"))
                vmodel = (self.cfg.get("vision_model") or "").strip()
                use_vision = False
                if vmodel and mode == "always":
                    use_vision = True
                elif vmodel and mode == "auto" and self._ocr_poor(text, conf):
                    use_vision = True

                client = ocr_ai.AiClient(self.cfg)
                if use_vision:
                    self.state.emit("看图作答…", COLOR_ACCENT)
                    self.detail.emit("OCR 不可靠 → 直接把截图交给视觉模型")
                    try:
                        full, ai_cost = client.solve_image(
                            img, on_delta=lambda d: self.delta.emit(d))
                    except Exception as e:
                        use_vision = False
                        self.detail.emit(
                            f"图片作答失败（{type(e).__name__}），退回文字作答")
                        self.state.emit("请求 AI…", COLOR_ACCENT)
                        full, ai_cost = client.solve(
                            text, on_delta=lambda d: self.delta.emit(d))
                else:
                    self.state.emit("请求 AI…", COLOR_ACCENT)
                    full, ai_cost = client.solve(text, on_delta=lambda d: self.delta.emit(d))
                self.used_vision = use_vision
                self.last_usage = dict(getattr(client, "last_usage", {}) or {})
                if self.last_usage.get("total"):
                    self.detail.emit(
                        f"AI {ai_cost:.2f}s · 输入 {self.last_usage.get('in', 0)}"
                        f" / 输出 {self.last_usage.get('out', 0)} tokens")
                self.finished_answer.emit(full, ai_cost)
                self.detail.emit(f"OCR {ocr_cost:.2f}s + AI {ai_cost:.2f}s ≈ {ocr_cost + ai_cost:.2f}s")
                self.state.emit("完成", COLOR_OK)
                _ = t0
                time.sleep(max(0.2, float(self.cfg.get("scan_interval", 1.2)) - 0.3))
            except Exception as e:
                self.failed.emit(f"{type(e).__name__}: {e}")
                self.state.emit("出错（见下方日志）", COLOR_ERR)
                traceback.print_exc()
                time.sleep(1.5)


# ---------------------------------------------------------------- 主窗口
class TitleBar(QWidget):
    """无边框窗口的自绘标题栏：拖动 / 设置 / 最小化 / 关闭（收托盘）。"""

    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self._drag = None
        self.setFixedHeight(48)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 10, 0)
        lay.setSpacing(8)

        logo = QLabel()
        logo.setFixedSize(26, 26)
        logo.setAlignment(Qt.AlignCenter)
        pm = QPixmap(resource_path("logo.png"))
        if pm.isNull():
            logo.setText("K")
            logo.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5b9dff,stop:1 #7b61ff);"
                "border-radius:8px;color:#ffffff;font-weight:800;font-size:13px;")
        else:
            logo.setPixmap(pm.scaled(26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setStyleSheet("background:transparent;")
        lay.addWidget(logo)

        name = QLabel(APP_TITLE)
        name.setStyleSheet("color:#eef1f6;font-size:13px;font-weight:600;letter-spacing:.4px;")
        lay.addWidget(name)

        ver = QLabel(f"v{config_store.APP_VERSION}")
        ver.setStyleSheet("color:#8a93a3;background:rgba(255,255,255,10);"
                          "border-radius:9px;padding:2px 8px;font-size:10px;")
        lay.addWidget(ver)
        lay.addStretch(1)

        self.btn_settings = QPushButton("⚙  设置")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.clicked.connect(win.open_settings)
        lay.addWidget(self.btn_settings)

        self.btn_update = QPushButton("更新")
        self.btn_update.setObjectName("updatebtn")
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.setVisible(False)
        self.btn_update.clicked.connect(win.start_update)
        lay.addWidget(self.btn_update)

        self.btn_min = QPushButton("—")
        self.btn_min.setObjectName("winbtn")
        self.btn_min.setFixedSize(34, 30)
        self.btn_min.setCursor(Qt.PointingHandCursor)
        self.btn_min.setToolTip("最小化")
        self.btn_min.clicked.connect(win.showMinimized)
        lay.addWidget(self.btn_min)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("closebtn")
        self.btn_close.setFixedSize(34, 30)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip("关闭")
        self.btn_close.clicked.connect(win.close)
        lay.addWidget(self.btn_close)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._win.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None


# ---------------------------------------------------------------- Win11 设置风格主题

def system_is_light():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return bool(v)
    except Exception:
        return True


LIGHT = {
    "window": "#F3F3F3", "card": "#FFFFFF", "border": "#E5E5E5",
    "text": "#1B1B1B", "muted": "#616161", "hover": "rgba(0,0,0,14)",
    "sel": "rgba(0,0,0,22)", "input": "#FFFFFF", "inputBorder": "#D6D6D6",
    "btn": "#FBFBFB", "btnBorder": "#D6D6D6", "btnHover": "#F2F2F2",
    "accent": "#0067C0", "accentHover": "#1975C5", "accentText": "#FFFFFF",
    "log": "#FAFAFA",
    "sb": "#C6C6C6", "sbHover": "#A8A8A8",
}
DARK = {
    "window": "#202020", "card": "#2B2B2B", "border": "rgba(255,255,255,26)",
    "text": "#FFFFFF", "muted": "#C5C5C5", "hover": "rgba(255,255,255,14)",
    "sel": "rgba(255,255,255,24)", "input": "#2D2D2D", "inputBorder": "rgba(255,255,255,30)",
    "btn": "#323232", "btnBorder": "rgba(255,255,255,26)", "btnHover": "#3A3A3A",
    "accent": "#4CC2FF", "accentHover": "#5FC9FF", "accentText": "#00243D",
    "log": "#262626",
    "sb": "#5A5A5A", "sbHover": "#6E6E6E",
}


def theme_colors(theme=None):
    """页面预览用的配色：跟随系统深浅色，别拿死白字往白底上画。"""
    t = theme or LIGHT
    dark = (t.get("text") == "#FFFFFF")
    return {
        "text": t["text"],
        "muted": t["muted"],
        "accent": "#8fb6ff" if dark else t["accent"],
        "answer": "#7ee787" if dark else "#1E7B34",
        "warn": "#d29922" if dark else "#9A6700",
    }


NAV_TINT = {
    "home": "#0A84FF",      # 蓝
    "online": "#7C5CFF",    # 紫
    "history": "#12B886",   # 青绿
    "bank": "#F59F00",      # 璃珀
    "settings": "#5C7CFA",  # 靖蓝
    "about": "#E64980",     # 粉
}


def build_qss(t):
    return (
        "QWidget{font-family:'Segoe UI Variable Text','Microsoft YaHei UI';"
        f"font-size:13px;color:{t['text']};}}"
        f"QWidget#mainWin{{background:{t['window']};}}"
        "QWidget#sidebar{background:transparent;}"
        f"QLabel#brandName{{font-size:13px;font-weight:600;color:{t['text']};}}"
        f"QLabel#verLabel{{font-size:11px;color:{t['muted']};padding:6px 10px;}}"
        "QPushButton#navItem{text-align:left;padding:8px 10px;border:none;"
        f"border-radius:4px;background:transparent;color:{t['text']};font-size:13px;}}"
        f"QPushButton#navItem:hover{{background:{t['hover']};}}"
        f"QPushButton#navItem:checked{{background:{t['sel']};font-weight:600;"
        "border-left:3px solid #0A84FF;padding-left:7px;}"
        "QPushButton#navItem[key=\"home\"]:checked{border-left-color:#0A84FF;}"
        "QPushButton#navItem[key=\"online\"]:checked{border-left-color:#7C5CFF;}"
        "QPushButton#navItem[key=\"history\"]:checked{border-left-color:#12B886;}"
        "QPushButton#navItem[key=\"bank\"]:checked{border-left-color:#F59F00;}"
        "QPushButton#navItem[key=\"settings\"]:checked{border-left-color:#5C7CFA;}"
        "QPushButton#navItem[key=\"about\"]:checked{border-left-color:#E64980;}"
        f"QPushButton#navSub{{text-align:left;padding:6px 10px 6px 30px;border:none;"
        f"border-radius:4px;background:transparent;color:{t['muted']};font-size:12.5px;}}"
        f"QPushButton#navSub:hover{{background:{t['hover']};color:{t['text']};}}"
        f"QPushButton#navSub:checked{{background:{t['sel']};color:#7C5CFF;font-weight:600;}}"
        f"QPushButton#stepBtn{{background:{t['btn']};border:1px solid {t['btnBorder']};"
        f"border-radius:6px;padding:0px;font-size:15px;font-weight:600;}}"
        f"QPushButton#stepBtn:hover{{background:{t['btnHover']};}}"
        f"QLabel#pageTitle{{font-size:24px;font-weight:600;color:{t['text']};}}"
        "QPushButton:focus{outline:none;}"
        f"QListWidget#subNav{{background:transparent;border:none;padding:0px;}}"
        f"QListWidget#subNav::item{{padding:8px 10px;border-radius:4px;color:{t['text']};}}"
        f"QListWidget#subNav::item:hover{{background:{t['hover']};}}"
        f"QListWidget#subNav::item:selected{{background:rgba(92,124,250,0.16);color:#3D7EFF;font-weight:600;}}"
        "QComboBox::drop-down{border:none;width:26px;}"
        "QComboBox::down-arrow{image:none;width:0px;height:0px;}"
        f"QComboBox QAbstractItemView{{background:{t['card']};color:{t['text']};"
        f"border:1px solid {t['border']};border-radius:6px;padding:4px;outline:0;"
        f"selection-background-color:{t['sel']};selection-color:{t['text']};}}"
        f"QFrame#card{{background:{t['card']};border:1px solid {t['border']};border-radius:8px;}}"
        f"QLabel#sectionTitle{{font-size:14px;font-weight:600;color:{t['text']};}}"
        f"QLabel#sub{{font-size:12px;color:{t['muted']};}}"
        f"QPushButton{{background:{t['btn']};border:1px solid {t['btnBorder']};"
        f"border-radius:4px;padding:6px 14px;color:{t['text']};}}"
        f"QPushButton:hover{{background:{t['btnHover']};}}"
        f"QPushButton:disabled{{color:{t['muted']};}}"
        f"QPushButton#primary{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #3D7EFF, stop:1 #7C5CFF);"
        f"color:{t['accentText']};border:none;font-weight:600;}}"
        f"QPushButton#primary:hover{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #5A92FF, stop:1 #9276FF);}"
        f"QPushButton#updateBtn{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #FF9F0A, stop:1 #E64980);"
        f"color:#fff;border:none;font-weight:600;padding:5px 10px;}}"
        f"QLineEdit,QComboBox,QSpinBox{{background:{t['input']};color:{t['text']};"
        f"border:1px solid {t['inputBorder']};border-radius:4px;padding:5px 8px;}}"
        f"QPlainTextEdit{{background:{t['log']};color:{t['text']};"
        f"border:1px solid {t['border']};border-radius:4px;padding:6px;font-size:12px;}}"
        f"QTextBrowser{{background:{t['input']};color:{t['text']};"
        f"border:1px solid {t['border']};border-radius:6px;padding:10px;font-size:13px;}}"
        f"QListWidget{{background:{t['input']};color:{t['text']};"
        f"border:1px solid {t['border']};border-radius:6px;padding:4px;}}"
        "QListWidget::item{padding:7px 8px;border-radius:4px;}"
        f"QListWidget::item:selected{{background:rgba(10,132,255,0.14);color:{t['text']};}}"
        f"QCheckBox{{color:{t['text']};}}"
        "QScrollBar:vertical{background:transparent;width:10px;margin:0px;}"
        f"QScrollBar::handle:vertical{{background:{t['sb']};border-radius:4px;"
        "min-height:30px;margin:2px;}"
        f"QScrollBar::handle:vertical:hover{{background:{t['sbHover']};}}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;"
        "background:transparent;border:none;}"
        "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
        "QScrollBar:horizontal{background:transparent;height:10px;margin:0px;}"
        f"QScrollBar::handle:horizontal{{background:{t['sb']};border-radius:4px;"
        "min-width:30px;margin:2px;}"
        f"QScrollBar::handle:horizontal:hover{{background:{t['sbHover']};}}"
        "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0px;"
        "background:transparent;border:none;}"
        "QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent;}"
        "QScrollArea#pageScroll{background:transparent;border:none;}"
        "QScrollArea#pageScroll > QWidget > QWidget{background:transparent;}"
    )


def glyph_icon(char, color, size=16):
    """用 Segoe Fluent Icons 把字形画成 QIcon（避免按钮字体回退吃掉图标）。"""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    f = QFont("Segoe Fluent Icons")
    f.setPixelSize(int(size * 1.6))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(pm.rect(), Qt.AlignCenter, char)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def apply_backdrop(widget, dark=True):
    """Win11 Mica 背景 + 深色标题栏（系统设置同款）。"""
    try:
        hwnd = int(widget.winId())
        dw = ctypes.windll.dwmapi
        v = ctypes.c_int(1 if dark else 0)      # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dw.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
        v2 = ctypes.c_int(2)                    # DWMWA_SYSTEMBACKDROP_TYPE = 38, Mica = 2
        dw.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(v2), ctypes.sizeof(v2))
        # 固定标题栏颜色：否则会被系统强调色染成一条蓝
        cap = ctypes.c_int(0x00202020 if dark else 0x00F3F3F3)   # COLORREF 0x00BBGGRR
        dw.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
        txt = ctypes.c_int(0x00FFFFFF if dark else 0x001B1B1B)
        dw.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), ctypes.sizeof(txt))
        bd = ctypes.c_int(0x002B2B2B if dark else 0x00E5E5E5)
        dw.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(bd), ctypes.sizeof(bd))
    except Exception:
        pass


class AboutPage(QWidget):
    """关于：版本 / 更新 / 链接 / 作者。"""

    def __init__(self, parent=None, on_check=None):
        super().__init__(parent)
        self.setObjectName("aboutPage")
        self.setStyleSheet("")
        self._on_check = on_check

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        c.setContentsMargins(20, 18, 20, 18)
        c.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        _dark = getattr(parent, "theme", LIGHT) is DARK
        pm = QPixmap(resource_path("logo.png" if _dark else "logo_dark.png"))
        if pm.isNull():
            pm = QPixmap(resource_path("logo.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(logo)
        box = QVBoxLayout()
        box.setSpacing(2)
        nm = QLabel("Knowlib")
        nm.setObjectName("pageTitle")
        box.addWidget(nm)
        ver = QLabel(f"版本 {config_store.APP_VERSION}")
        ver.setObjectName("sub")
        box.addWidget(ver)
        head.addLayout(box)
        head.addStretch(1)
        self.btn_update = QPushButton("检查更新")
        self.btn_update.clicked.connect(self._check)
        head.addWidget(self.btn_update)
        c.addLayout(head)

        self.status = QLabel("")
        self.status.setObjectName("sub")
        c.addWidget(self.status)
        v.addWidget(card)

        card2 = QFrame()
        card2.setObjectName("card")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(20, 16, 20, 16)
        c2.setSpacing(10)

        t = QLabel("相关链接")
        t.setObjectName("sectionTitle")
        c2.addWidget(t)
        c2.addLayout(self._link_row("项目主页", "https://knowlib.fun"))
        c2.addLayout(self._link_row("GitHub", "https://github.com/Ch1ng-W/knowlib-pc"))
        c2.addLayout(self._link_row("反馈邮箱", "mailto:2391813554@qq.com"))
        v.addWidget(card2)

        card3 = QFrame()
        card3.setObjectName("card")
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(20, 16, 20, 16)
        c3.setSpacing(6)
        t3 = QLabel("关于作者")
        t3.setObjectName("sectionTitle")
        c3.addWidget(t3)
        a = QLabel("由 Ching 和小团 同步开发")
        c3.addWidget(a)
        b = QLabel("截图识别 → AI 解题 → 本地题库沉淀，全部在这台电脑上完成。")
        b.setObjectName("sub")
        b.setWordWrap(True)
        c3.addWidget(b)
        v.addWidget(card3)
        v.addStretch(1)

    def _link_row(self, label, url):
        row = QHBoxLayout()
        row.setSpacing(10)
        lb = QLabel(label)
        lb.setMinimumWidth(96)
        row.addWidget(lb)
        val = QLabel(url.replace("mailto:", ""))
        val.setObjectName("sub")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(val, 1)
        btn = QPushButton("打开")
        btn.setFixedWidth(72)
        btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        row.addWidget(btn)
        return row

    def _check(self):
        self.status.setText("正在检查更新…")
        if self._on_check:
            self._on_check(self.status)


class WindowPicker(QDialog):
    """选一个窗口当搜题来源（PrintWindow，被遮住也能抓）。"""

    def __init__(self, parent, wins=None):
        super().__init__(parent)
        self.setWindowTitle("按窗口搜题")
        self.setMinimumSize(540, 470)
        self.chosen = None
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        tip = QLabel("选一个窗口。之后点「开始」就只盯这个窗口截图，窗口被盖住也能抓到。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新列表")
        self.btn_refresh.clicked.connect(self.reload)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        b1 = QPushButton("取消")
        b2 = QPushButton("用这个窗口")
        b2.setObjectName("primary")
        b1.clicked.connect(self.reject)
        b2.clicked.connect(self.accept_sel)
        row.addWidget(b1)
        row.addWidget(b2)
        v.addLayout(row)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept_sel())
        self._wins = list(wins or [])
        self.reload()

    def reload(self):
        try:
            self._wins = remote_server.list_windows()
        except Exception:
            pass
        self.list.clear()
        for w in self._wins:
            it = QListWidgetItem("%s    ·  %d×%d%s" % (
                w.get("title") or "(无标题)", w.get("w") or 0, w.get("h") or 0,
                "  （最小化）" if w.get("min") else ""))
            it.setData(Qt.UserRole, w)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def accept_sel(self):
        it = self.list.currentItem()
        if it is None:
            return
        w = it.data(Qt.UserRole) or {}
        if w.get("min"):
            QMessageBox.information(self, "窗口已最小化", "这个窗口最小化了，先还原再选。")
            return
        self.chosen = w
        self.accept()


class MainWindow(QWidget):
    hotkey = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cfg = config_store.load()
        self.region = dict(self.cfg.get("region") or {})
        self.worker = None
        self.thread = None
        self.overlay = AnswerOverlay(self)
        self.overlay.hide()
        self.last_question = ""
        self._quitting = False
        self._tray_tip_shown = False
        self.tray = None
        self._was_minimized = False
        self._mini_anim = None
        self._snap = None
        self._last_pos = None
        self._page_anim = None
        self._page_name = None
        self._saved_sizes = {}
        self._fade_effects = {}

        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(Qt.Window)
        self.setObjectName("mainWin")
        self.theme = LIGHT if system_is_light() else DARK
        self.resize(980, 680)
        self.setMinimumSize(880, 560)
        apply_backdrop(self, dark=(self.theme is DARK))
        self.setStyleSheet(
            "QWidget#mainWin{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #171a21,stop:1 #0f1115);}"
            "QWidget{color:#e6e9ef;font-family:'Microsoft YaHei';}"
            "QFrame#shell{background:transparent;border:none;}"
            "QFrame#card{background:rgba(255,255,255,9);"
            "border:1px solid rgba(255,255,255,14);border-radius:12px;}"
            "QLabel{color:#e6e9ef;background:transparent;}"
            "QPushButton{background:rgba(255,255,255,14);color:#e6e9ef;border:none;"
            "border-radius:9px;padding:9px 10px;font-size:13px;}"
            "QPushButton:hover{background:rgba(255,255,255,26);}"
            "QPushButton:pressed{background:rgba(255,255,255,36);}"
            "QPushButton:disabled{color:#6b7280;background:rgba(255,255,255,6);}"
            "QPushButton#primary{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #4f8dfd,stop:1 #7b61ff);color:#ffffff;font-weight:600;}"
            "QPushButton#primary:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #639bff,stop:1 #8a74ff);}"
            "QPushButton#danger{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #e0556a,stop:1 #c93c57);color:#ffffff;font-weight:600;}"
            "QPushButton#danger:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #eb6679,stop:1 #d54a64);}"
            "QPushButton#ghost{background:transparent;color:#9aa4b2;padding:5px 9px;font-size:12px;}"
            "QPushButton#ghost:hover{background:rgba(255,255,255,20);color:#e6e9ef;}"
            "QPushButton#updatebtn{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #2ecc71,stop:1 #22a35a);color:#ffffff;font-size:11px;"
            "border-radius:9px;padding:4px 9px;font-weight:600;}"
            "QPushButton#updatebtn:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #3ddc84,stop:1 #27b566);}"
            "QPushButton#winbtn{background:transparent;color:#9aa4b2;font-size:13px;"
            "border-radius:8px;padding:0;}"
            "QPushButton#winbtn:hover{background:rgba(255,255,255,22);color:#ffffff;}"
            "QPushButton#closebtn{background:transparent;color:#9aa4b2;font-size:13px;"
            "border-radius:8px;padding:0;}"
            "QPushButton#closebtn:hover{background:#e0435c;color:#ffffff;}"
            "QPlainTextEdit{background:rgba(0,0,0,80);color:#98a2b3;"
            "border:1px solid rgba(255,255,255,10);border-radius:11px;"
            "font-size:11px;padding:7px;}"
        )

        # ================= Win11 设置风格：左导航 + 右内容 =================
        self.setStyleSheet(build_qss(self.theme))

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 侧边导航
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(196)
        self.side = side
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 14, 10, 10)
        sl.setSpacing(2)

        sl.addSpacing(6)

        self.nav = {}
        self._nav_text = {}
        self.nav_sub = {}
        _PAPER_SUB = (("online", "在线试卷"), ("hist", "历史试卷"),
                      ("dash", "试卷后台"), ("compose", "组卷"))
        for key, icon, text in (("home", "\ue721", "答题"),
                                ("online", "\ue774", "试卷"),
                                ("history", "\ue81c", "历史记录"),
                                ("bank", "\ue8f1", "题库"),
                                ("settings", "\ue713", "设置"),
                                ("about", "\ue946", "关于")):
            b = QPushButton(text)
            b.setIcon(glyph_icon(icon, NAV_TINT.get(key, self.theme["text"]), 16))
            b.setProperty("key", key)
            b.setIconSize(QSize(16, 16))
            b.setObjectName("navItem")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            sl.addWidget(b)
            self.nav[key] = b
            self._nav_text[key] = text
            if key == "online":
                # 试卷的四个分区作为左侧二级菜单（不再放在页面顶上）
                for sk, stext in _PAPER_SUB:
                    sb = QPushButton(stext)
                    sb.setObjectName("navSub")
                    sb.setCheckable(True)
                    sb.setFocusPolicy(Qt.NoFocus)
                    sb.setCursor(Qt.PointingHandCursor)
                    sb.clicked.connect(lambda _=False, k=sk: self._open_paper_tab(k))
                    sl.addWidget(sb)
                    self.nav_sub[sk] = sb

        sl.addStretch(1)
        self.btn_collapse = QPushButton("收起主界面")
        self.btn_collapse.setObjectName("navItem")
        self.btn_collapse.setIcon(glyph_icon("\ue7b8", self.theme["text"], 15))
        self.btn_collapse.setIconSize(QSize(15, 15))
        self.btn_collapse.setFocusPolicy(Qt.NoFocus)
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.setToolTip("把主界面隐藏到托盘，只留答案窗（热键可再叫回来）")
        self.btn_collapse.clicked.connect(self.collapse_main)
        sl.addWidget(self.btn_collapse)
        self.btn_update = QPushButton("发现新版本")
        self.btn_update.setObjectName("updateBtn")
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.setVisible(False)
        self.btn_update.clicked.connect(self.start_update)
        sl.addWidget(self.btn_update)
        self.ver_label = QLabel(f"v{config_store.APP_VERSION}")
        self.ver_label.setObjectName("verLabel")
        sl.addWidget(self.ver_label)
        root.addWidget(side)

        # ---- 内容区
        content = QWidget()
        content.setObjectName("content")
        cl0 = QVBoxLayout(content)
        cl0.setContentsMargins(28, 14, 28, 20)
        cl0.setSpacing(0)
        self.stack = QStackedWidget()
        cl0.addWidget(self.stack)
        root.addWidget(content, 1)

        # ---- 答题页
        self.page_home = QWidget()
        self.page_home.setObjectName("homePage")
        lay_home = QVBoxLayout(self.page_home)
        lay_home.setContentsMargins(0, 0, 0, 0)
        lay_home.setSpacing(14)

        title = QLabel("答题")
        title.setObjectName("pageTitle")
        lay_home.addWidget(title)

        # 状态卡片
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet(f"background:{COLOR_IDLE};border-radius:5px;")
        self.status = QLabel("未开始")
        self.status.setObjectName("sectionTitle")
        top.addWidget(self.dot)
        top.addWidget(self.status)
        top.addStretch(1)
        self.region_label = QLabel("")
        self.region_label.setObjectName("sub")
        self.update_region_label()
        top.addWidget(self.region_label)
        cl.addLayout(top)

        self.detail = QLabel("先点「框选区域」把题目框住，再点「开始」")
        self.detail.setObjectName("sub")
        self.detail.setWordWrap(True)
        cl.addWidget(self.detail)
        lay_home.addWidget(card)

        # 操作卡片
        act = QFrame()
        act.setObjectName("card")
        al = QVBoxLayout(act)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(10)
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.btn_region = QPushButton("框选区域")
        self.btn_region.clicked.connect(self.select_region)
        self.btn_window = QPushButton("按窗口搜题")
        self.btn_window.setCursor(Qt.PointingHandCursor)
        self.btn_window.clicked.connect(self.pick_window)
        self.btn_start = QPushButton("开始")
        self.btn_start.setObjectName("primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self.toggle_run)
        self.btn_shot = QPushButton("立即识别")
        self.btn_shot.clicked.connect(self.shot_once)
        row1.addWidget(self.btn_region)
        row1.addWidget(self.btn_window)
        row1.addWidget(self.btn_start)
        row1.addWidget(self.btn_shot)
        row1.addStretch(1)
        al.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.btn_show = QPushButton("显示答案窗")
        self.btn_show.clicked.connect(self.toggle_overlay)
        self.btn_retry = QPushButton("重问一次")
        self.btn_retry.clicked.connect(self.ask_again)
        row2.addWidget(self.btn_show)
        row2.addWidget(self.btn_retry)
        row2.addStretch(1)
        al.addLayout(row2)
        lay_home.addWidget(act)

        # 日志卡片
        logcard = QFrame()
        logcard.setObjectName("card")
        ll = QVBoxLayout(logcard)
        ll.setContentsMargins(14, 12, 14, 12)
        ll.setSpacing(8)
        logtitle = QLabel("运行日志")
        logtitle.setObjectName("sectionTitle")
        ll.addWidget(logtitle)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        ll.addWidget(self.log, 1)
        lay_home.addWidget(logcard, 1)

        self.hint = QLabel("F1 框选 · F2 暂停 · F3 立即识别 · F4 重问 · F5 答案窗"
                           "　|　关窗后在托盘后台运行")
        self.hint.setObjectName("sub")
        self.hint.setWordWrap(True)
        lay_home.addWidget(self.hint)

        # ---- 其他页面
        self.stack.addWidget(self.page_home)
        self.page_history = HistoryPage(self, on_back=lambda: self.show_page("home"))
        self.page_paper = PaperHubPage(self)
        self.page_paper.on_tab = self._sync_paper_nav
        self.page_bank = BankPage(self, on_back=lambda: self.show_page("home"))
        self.page_settings = SettingsPage(self.cfg, self, on_saved=self._on_settings_saved,
                                          on_back=lambda: self.show_page("home"))
        self.page_settings.on_preview = self._preview_overlay
        # 设置页自己管滚动（左侧二级导航固定，右侧内容独立滚动）
        self.page_settings_scroll = self.page_settings
        self.page_about = AboutPage(self, on_check=self._about_check)
        for p in (self.page_history, self.page_paper, self.page_bank,
                  self.page_settings_scroll, self.page_about):
            self.stack.addWidget(p)
        self.stack.setCurrentWidget(self.page_home)
        self.nav["home"].setChecked(True)
        self._page_name = "home"

        self.setup_hotkeys()
        self.setup_tray()

        # 云同步：启动后自动同步一次，之后每 10 分钟
        self._sync_th = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(10 * 60 * 1000)
        self._sync_timer.timeout.connect(self._auto_sync)
        if self.cfg.get("sync_auto", True) and (self.cfg.get("sync_code") or "").strip():
            QTimer.singleShot(3500, self._auto_sync)
            self._sync_timer.start()

    # -------- 页面切换（窗口可自由缩放，记住每个页面的尺寸）
    PAGE_SIZES = {"home": (470, 492), "history": (900, 600),
                  "bank": (900, 620), "settings": (580, 762)}
    PAGE_MINS = {"home": (430, 404), "history": (700, 470),
                 "bank": (700, 440), "settings": (520, 600)}

    def _anim_on(self):
        return bool(self.cfg.get("animations", True))

    def _win_size(self, content):
        return (content[0] + SHADOW_MARGIN * 2, content[1] + SHADOW_MARGIN * 2)

    def show_page(self, name, animate=True):
        """导航切页（Win11 设置风格：即时切换，同步左侧导航高亮）。"""
        try:
            self._show_page(name, animate)
        except Exception:
            traceback.print_exc()
            try:
                self.add_log(f"切换页面出错：{name}")
            except Exception:
                pass

    def _open_paper_tab(self, key):
        """左侧「试卷」二级菜单：切页 + 切分区。"""
        self.show_page("online")
        try:
            self.page_paper.show_tab(key)
        except Exception:
            traceback.print_exc()

    @safe_slot
    def _sync_paper_nav(self, key):
        for k, b in getattr(self, "nav_sub", {}).items():
            b.setChecked(k == key)

    def _show_page(self, name, animate=True):
        page = {"home": self.page_home, "history": self.page_history,
                "online": getattr(self, "page_paper", None),
                "bank": self.page_bank,
                "settings": getattr(self, "page_settings_scroll", self.page_settings),
                "about": getattr(self, "page_about", None)}.get(name)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self._page_name = name
        for k, b in getattr(self, "nav", {}).items():
            b.setChecked(k == name)
        if name == "online":
            try:
                self._sync_paper_nav(self.page_paper._tab)
            except Exception:
                pass
        if name == "history":
            self.page_history.reload()
        elif name == "bank":
            self.page_bank.reload()
    def _resize_to(self, w, h, page=None, animate=True):
        """窗口尺寸/位置过渡（不再锁死尺寸，用户随时可以拖边缩放）。"""
        start = self.geometry()
        end = self._clamped_rect(QRect(start.x(), start.y(), w, h))
        if not animate or start.size() == end.size():
            self.resize(end.size())
            self.move(end.topLeft())
            if page is not None:
                self._fade_in(page)
            return
        if self._page_anim is not None:
            self._page_anim.stop()
        a = QPropertyAnimation(self, b"geometry", self)
        a.setDuration(230)
        a.setStartValue(start)
        a.setEndValue(end)
        a.setEasingCurve(QEasingCurve.OutCubic)

        def done():
            self._keep_on_screen()
            if page is not None:
                self._fade_in(page)

        a.finished.connect(done)
        self._page_anim = a
        a.start()

    def _clamped_rect(self, r):
        """把矩形拉回屏幕可用区域内。"""
        scr = QApplication.primaryScreen()
        if not scr:
            return r
        g = scr.availableGeometry()
        x = min(max(r.x(), g.left() + 8), max(g.left() + 8, g.right() - r.width() - 8))
        y = min(max(r.y(), g.top() + 8), max(g.top() + 8, g.bottom() - r.height() - 8))
        return QRect(x, y, r.width(), r.height())

    def _fade_in(self, page):
        """页面淡入：已停用。QGraphicsOpacityEffect 会让悬停重绘错位（实测 21 万像素错乱）。"""
        return

    def _keep_on_screen(self):
        self.move(self._clamped_rect(self.geometry()).topLeft())

    # -------- 原生窗口行为（缩放 / 贴边 / 最小化动画交给系统）
    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            was, now = self._was_minimized, self.isMinimized()
            if now and not was:
                self._was_minimized = True
            elif was and not now:
                self._was_minimized = False
        super().changeEvent(e)

    def nativeEvent(self, eventType, message):
        return super().nativeEvent(eventType, message)

    def _pop_in(self):
        """从任务栏恢复时：淡入 + 向上小位移。"""
        if not self._anim_on() or not self.isVisible():
            return
        self._keep_on_screen()
        cur = self.pos()
        want = getattr(self, "_last_pos", None)
        target = cur
        if want is not None:
            probe = QRect(want.x() + self.width() // 2, want.y() + self.height() // 2, 1, 1)
            if QGuiApplication.screenAt(probe.topLeft()) is not None:
                target = self._clamped_rect(QRect(want, self.size())).topLeft()
        a1 = QPropertyAnimation(self, b"pos", self)
        a1.setDuration(220)
        a1.setStartValue(QPoint(target.x(), target.y() + 14))
        a1.setEndValue(target)
        a1.setEasingCurve(QEasingCurve.OutCubic)
        a2 = QPropertyAnimation(self, b"windowOpacity", self)
        a2.setDuration(200)
        a2.setStartValue(0.0)
        a2.setEndValue(1.0)
        a2.setEasingCurve(QEasingCurve.OutCubic)
        self.move(target.x(), target.y() + 14)
        self.setWindowOpacity(0.0)
        self._pop_anims = (a1, a2)
        a1.start()
        a2.start()

    def _about_check(self, label):
        """关于页的「检查更新」（后台线程，避免卡界面）。"""
        label.setText("正在检查更新…")

        class Chk(QThread):
            done = pyqtSignal(dict)

            def run(self):
                self.done.emit(updater.check())

        t = Chk(self)

        def got(r):
            if not r.get("ok"):
                label.setText("检查失败：" + str(r.get("error"))[:60])
            elif r.get("has_update"):
                self._update_info = r
                label.setText(f"发现新版本 v{r['latest']}，可在左侧「发现新版本」下载")
                self.btn_update.setText(f"更新 {r['latest']}")
                self.btn_update.setVisible(True)
            else:
                label.setText(f"已是最新版本 v{updater.current_version()}")

        t.done.connect(got)
        self._about_t = t
        t.start()

    def _preview_overlay(self, opts):
        """设置页拖滑块 → 答案窗实时预览，并自动落下配置（不用点保存）。"""
        demo = ("答案：**A**\n"
                "解析：由 $I_a=\\frac{U}{R_a}$ 得 $I_a=\\frac{220}{0.5}=440A$，故选 A。\n"
                "（预览效果，松手 8 秒后自动收起）")
        try:
            self.overlay.preview(opts, demo)
        except Exception as e:
            print("preview failed:", e)
        # 实时写入配置：拖完就已经生效并保存，关掉设置也不丢
        self._pending_look = dict(opts or {})
        t = getattr(self, "_look_t", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(500)
            t.timeout.connect(self._persist_look)
            self._look_t = t
        t.start()

    def _persist_look(self):
        opts = getattr(self, "_pending_look", None) or {}
        if not opts:
            return
        try:
            c = config_store.load()
            for k, v in opts.items():
                c[k] = v if isinstance(v, str) else int(v)
            config_store.save(c)
            self.cfg = c
            self.overlay._override = {}
            self.overlay.apply_settings()
        except Exception as e:
            print("persist look failed:", e)

    def _on_settings_saved(self, c):
        try:
            self.overlay._override = {}
            self.overlay.title.setText("答案")
        except Exception:
            pass
        self.cfg = config_store.load()
        self.add_log("设置已保存")
        self.setup_hotkeys()
        # 手机遥控：按设置开/关
        try:
            if self.cfg.get("remote_on"):
                ok, msg, _url = remote_server.start(self.cfg, int(self.cfg.get("remote_port", 18765) or 18765))
                if not ok:
                    self.add_log("手机遥控启动失败：" + msg)
            else:
                remote_server.stop()
        except Exception as e:
            self.add_log("手机遥控异常：%s" % e)
        try:
            self.overlay.apply_settings()
        except Exception:
            pass
        if self.worker:
            self.worker.cfg = self.cfg

    # -------- 托盘
    def _app_icon(self):
        for name in ("app.ico", "app.png", "logo.png"):
            p = resource_path(name)
            if os.path.exists(p):
                return QIcon(p)
        return self.style().standardIcon(QStyle.SP_ComputerIcon)

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(self._app_icon(), self)
        self.tray.setToolTip("Knowlib · 截图搜题")
        menu = QMenu()
        self.act_show = QAction("显示主界面", self)
        self.act_show.triggered.connect(self.show_main)
        self.act_run = QAction("开始扫描", self)
        self.act_run.triggered.connect(self.toggle_run)
        self.act_overlay = QAction("显示/隐藏答案窗", self)
        self.act_overlay.triggered.connect(self.toggle_overlay)
        self.act_hist = QAction("历史记录", self)
        self.act_hist.triggered.connect(self.open_history)
        self.act_quit = QAction("退出", self)
        self.act_quit.triggered.connect(self.quit_app)
        for a in (self.act_show, self.act_run, self.act_overlay, self.act_hist):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_main()

    def show_main(self):
        was_hidden = not self.isVisible()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if was_hidden and self._anim_on():
            self._pop_in()

    def quit_app(self):
        self._quitting = True
        self.stop_run()
        self.overlay.close()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    # -------- 历史 / 题库 / 设置（都在主窗口里切页，不再弹窗）
    def open_history(self):
        self.show_page("history")

    def open_bank(self):
        self.show_page("bank")

    # -------- 自动更新
    def check_updates_async(self, silent=True):
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True
        if not silent:
            self.add_log("正在检查更新…")

        class Check(QThread):
            done = pyqtSignal(dict)

            def run(self):
                self.done.emit(updater.check())

        self._uc = Check(self)
        self._uc.done.connect(lambda r: self._on_update_result(r, silent))
        self._uc.start()

    def _on_update_result(self, r, silent):
        self._upd_busy = False
        if not r.get("ok"):
            if not silent:
                self.add_log("检查更新失败：" + str(r.get("error"))[:80])
            return
        if r.get("has_update"):
            self._update_info = r
            self.btn_update.setText(f"更新 {r['latest']}")
            self.btn_update.setVisible(True)
            self.add_log(f"发现新版本 v{r['latest']}（当前 v{updater.current_version()}）")
            if silent and self.tray:
                self.tray.showMessage(
                    "发现新版本", f"v{r['latest']} 可用，点窗口右上角「更新」",
                    QSystemTrayIcon.Information, 4000)
        elif not silent:
            self.add_log(f"已是最新版本 v{updater.current_version()}")

    def _on_dl_progress(self, got, total):
        if not total:
            return
        pct = int(got * 100 / total)
        if pct // 10 != getattr(self, "_dl_pct", -1) // 10:
            self._dl_pct = pct
            self.add_log(f"下载中 {pct}%（{got/1048576:.0f}/{total/1048576:.0f}MB）")

    def _on_dl_done(self, path):
        self.add_log("下载完成，正在启动安装…")
        try:
            updater.run_installer(path)
        except Exception as e:
            self.add_log(f"启动安装失败：{e}")
            self.btn_update.setEnabled(True)
            return
        QTimer.singleShot(400, self.quit_app)

    def _on_dl_fail(self, msg):
        self.add_log("下载失败：" + msg)
        self.btn_update.setEnabled(True)

    def start_update(self):
        info = getattr(self, "_update_info", None)
        if not info or not info.get("url"):
            self.check_updates_async(silent=False)
            return
        size_mb = (info.get("size") or 0) / 1048576
        tip = (f"下载 v{info['latest']}（{size_mb:.0f}MB）并安装？\n"
               "安装时本程序会自动关闭，装完重新打开即可。")
        if not ask_yes(self, "更新 Knowlib", tip):
            return
        self.btn_update.setEnabled(False)
        url = info["url"]

        class Dl(QThread):
            prog = pyqtSignal(int, int)
            done = pyqtSignal(str)
            fail = pyqtSignal(str)

            def run(self):
                try:
                    p = updater.download(url, on_progress=lambda a, b: self.prog.emit(a, b))
                    self.done.emit(p)
                except Exception as e:
                    self.fail.emit(f"{type(e).__name__}: {e}")

        self._dl_pct = -1
        self._dl = Dl(self)
        self._dl.prog.connect(self._on_dl_progress)
        self._dl.done.connect(self._on_dl_done)
        self._dl.fail.connect(self._on_dl_fail)
        self.add_log("开始下载更新…")
        self._dl.start()

    # -------- 云同步
    @safe_slot
    def _auto_sync(self):
        if not self.cfg.get("sync_auto", True):
            return
        if not (self.cfg.get("sync_code") or "").strip():
            return
        if self._sync_th is not None and self._sync_th.isRunning():
            return

        class _S(QThread):
            done = pyqtSignal(object)

            def run(self_):
                try:
                    import cloud_sync
                    self_.done.emit(cloud_sync.sync_now())
                except Exception as e:
                    self_.done.emit({"ok": False, "msg": "%s: %s" % (type(e).__name__, e)})

        th = _S(self)
        th.done.connect(self._on_sync_done)
        self._sync_th = th
        th.start()

    @safe_slot
    def _on_sync_done(self, r):
        if r.get("ok"):
            self.cfg["sync_last"] = time.strftime("%m-%d %H:%M 已同步")
            if r.get("devices"):
                self.cfg["sync_devices"] = r.get("devices")
            try:
                config_store.save(self.cfg)
            except Exception:
                pass
            self.add_log("云同步：" + (r.get("msg") or "完成"))
        else:
            self.add_log("云同步失败：" + (r.get("msg") or "未知原因"))

    # -------- 状态
    @safe_slot
    def set_state(self, text, color):
        self.status.setText(text)
        self.dot.setStyleSheet(f"background:{color};border-radius:5px;")

    def _set_start_look(self, running):
        self.btn_start.setObjectName("danger" if running else "primary")
        self.btn_start.style().unpolish(self.btn_start)
        self.btn_start.style().polish(self.btn_start)

    @safe_slot
    def add_log(self, text):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")

    def update_region_label(self):
        if self.cfg.get("src") == "window" and self.cfg.get("win_hwnd"):
            t = (self.cfg.get("win_title") or "窗口").strip()
            self.region_label.setText("窗口：" + (t[:14] + "…" if len(t) > 14 else t))
            self.region_label.setToolTip("监控窗口：" + t)
            self.region_label.setStyleSheet(
                "color:#b9a4ff;background:rgba(124,92,255,30);border-radius:9px;"
                "padding:2px 8px;font-size:10.5px;")
            return
        if self.region and self.region.get("width"):
            r = self.region
            self.region_label.setText(f"{r['width']}×{r['height']}")
            self.region_label.setToolTip(f"监控区域 @ ({r['left']},{r['top']})")
            self.region_label.setStyleSheet(
                "color:#8fbcff;background:rgba(91,157,255,26);border-radius:9px;"
                "padding:2px 8px;font-size:10.5px;")
        else:
            self.region_label.setText("未设置区域")
            self.region_label.setToolTip("")
            self.region_label.setStyleSheet(
                "color:#8a93a3;background:rgba(255,255,255,10);border-radius:9px;"
                "padding:2px 8px;font-size:10.5px;")

    # -------- 热键
    def _on_hotkey(self, name):
        {"region": self.select_region, "pause": self.toggle_pause,
         "shot": self.shot_once, "again": self.ask_again,
         "overlay": self.toggle_overlay,
         "f1": self.select_region, "f2": self.toggle_pause, "f3": self.shot_once,
         "f4": self.ask_again, "f5": self.toggle_overlay}.get(name, lambda: None)()

    def setup_hotkeys(self):
        self.hotkey.connect(self._on_hotkey)  # 跨线程：热键线程 → Qt 主线程
        try:
            from pynput import keyboard as pk
            hks = dict(self.cfg.get("hotkeys") or {})
            keymap = {}
            for action, kn in hks.items():
                k = getattr(pk.Key, str(kn).lower(), None)
                if k is not None:
                    keymap[k] = action

            def on_press(key):
                name = keymap.get(key)
                if name:
                    self.hotkey.emit(name)

            if getattr(self, "_listener", None) is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
            self._listener = pk.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
            shown = "/".join(hks.get(a, "?") for a, _ in
                             (("region", 0), ("pause", 0), ("shot", 0), ("again", 0), ("overlay", 0)))
            self.add_log(f"热键已注册：{shown}")
        except Exception as e:
            self.add_log(f"热键注册失败：{e}")

    # -------- 设置
    def open_settings(self):
        self.page_settings.cfg = dict(self.cfg)
        self.show_page("settings")

    # -------- 区域
    def select_region(self):
        self.hide()
        QApplication.processEvents()
        sel = RegionSelector()
        sel.selected.connect(self._on_region)
        sel.showFullScreen()
        self._sel = sel

    def _on_region(self, r):
        self.region = r
        self.cfg["region"] = r
        self.cfg["src"] = "region"
        self.cfg.pop("win_hwnd", None)
        self.cfg.pop("win_title", None)
        config_store.save(self.cfg)
        self.update_region_label()
        self.show()
        self.add_log(f"已框选：{r['width']}×{r['height']} @ ({r['left']},{r['top']})")
        self.set_state("区域已设置", COLOR_OK)

    def pick_window(self):
        try:
            wins = remote_server.list_windows()
        except Exception as e:
            self.add_log("枚举窗口失败：%s" % e)
            return
        if not wins:
            self.add_log("没找到可选窗口（都被过滤或都最小化了）")
            return
        dlg = WindowPicker(self, wins)
        if dlg.exec_() != QDialog.Accepted or not dlg.chosen:
            return
        w = dlg.chosen
        self.cfg["src"] = "window"
        self.cfg["win_hwnd"] = int(w["id"])
        self.cfg["win_title"] = w.get("title") or ""
        config_store.save(self.cfg)
        self.update_region_label()
        self.add_log("已选窗口：%s（%d×%d）" % (self.cfg["win_title"], w.get("w") or 0, w.get("h") or 0))
        self.set_state("已选窗口，点「开始」", COLOR_OK)

    # -------- 运行
    def toggle_run(self):
        if self.thread and self.thread.isRunning():
            self.stop_run()
        else:
            self.start_run()

    def start_run(self):
        if not self.cfg.get("api_key"):
            self.add_log("还没配 API Key，先点右上角「设置」")
            self.set_state("未配置 API Key", COLOR_WARN)
            self.open_settings()
            return
        hwnd = self.cfg.get("win_hwnd") if self.cfg.get("src") == "window" else 0
        if not hwnd:
            if not self.region or not self.region.get("width"):
                self.add_log("还没框选区域，先点「框选区域」或「按窗口搜题」")
                self.set_state("未选监控范围", COLOR_WARN)
                return

        self.worker = ScanWorker(self.cfg, self.region, hwnd=hwnd)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.loop)

        self.worker.state.connect(self.set_state)
        self.worker.detail.connect(lambda s: self.add_log(s))
        self.worker.ocr_done.connect(self._on_ocr)
        self.worker.delta.connect(self._on_delta)
        self.worker.finished_answer.connect(self._on_answer)
        self.worker.failed.connect(lambda s: self.add_log("错误：" + s))
        self.worker.prepare_capture.connect(self._hide_overlay_for_capture)
        self.worker.after_capture.connect(self._restore_overlay_after_capture)

        self.thread.start()
        self.btn_start.setText("停止")
        self._set_start_look(True)
        if self.tray:
            self.act_run.setText("停止扫描")
        self.add_log("已开始扫描")
        self.set_state("启动中…", COLOR_WARN)

    def stop_run(self):
        if self.worker:
            self.worker.running = False
        if self.thread:
            self.thread.quit()
            self.thread.wait(2500)
        self.thread = None
        self.worker = None
        self.btn_start.setText("开始")
        self._set_start_look(False)
        if self.tray:
            self.act_run.setText("开始扫描")
        self.set_state("已停止", COLOR_IDLE)
        self.add_log("已停止扫描")

    def toggle_pause(self):
        if not self.worker:
            self.add_log("还没开始")
            return
        self.worker.paused = not self.worker.paused
        self.set_state("已暂停" if self.worker.paused else "继续扫描",
                       COLOR_WARN if self.worker.paused else COLOR_ACCENT)
        self.add_log("暂停" if self.worker.paused else "继续")

    def shot_once(self):
        if not self.worker:
            # 没在跑：临时起一次
            self.start_run()
            return
        self.worker.request_once()
        self.add_log("手动识别一次")

    # -------- 答案
    def _hide_overlay_for_capture(self):
        # 只有答案窗真的盖住了监控区域才藏（否则每秒闪一下）
        if self.overlay.isVisible() and self.overlay.overlaps(self.region):
            self._overlay_was_visible = True
            self.overlay.hide()
        else:
            self._overlay_was_visible = False

    def _restore_overlay_after_capture(self):
        if getattr(self, "_overlay_was_visible", False):
            self._overlay_was_visible = False
            self.overlay.show()

    @safe_slot
    def _on_ocr(self, text):
        trace("ocr len=%d" % len(text or ""))
        if text != self.last_question:
            self.overlay.set_text("")      # 新题：清掉上一题答案，避免越堆越长
            self.last_question = text
            self.add_log("识别到：" + text.replace("\n", " ")[:60])

    @safe_slot
    def _on_delta(self, d):
        if not self.overlay.isVisible():
            self.overlay.place_avoiding(self.region)
            self.overlay.show()
        self.overlay.append_text(d)

    @safe_slot
    def _on_answer(self, full, cost):
        trace("answer start len=%d" % len(full or ""))
        # 题库命中 / 非流式返回时没有任何 delta，这里补齐，否则答案窗只显示「…」
        if (getattr(self.overlay, "_full", "") or "") != (full or ""):
            self.overlay.set_text(full or "")
        if not self.overlay.isVisible():
            self.overlay.place_avoiding(self.region)
            self.overlay.show()
        from_bank = bool(self.worker is not None and getattr(self.worker, "bank_used", False))
        if from_bank:
            score = float(getattr(self.worker, "bank_score", 0.0) or 0.0)
            self.overlay.foot.setText(f"本地题库命中 · 相似度 {score:.2f} · 0 token（可点「纠错」改答案）")
        else:
            usage = getattr(self.worker, "last_usage", None) if self.worker else None
            src = "图片作答" if getattr(self.worker, "used_vision", False) else "AI"
            if usage and usage.get("total"):
                self.overlay.foot.setText(
                    f"{src} {cost:.2f}s · 输入 {usage.get('in', 0)}"
                    f" / 输出 {usage.get('out', 0)} tokens")
            else:
                self.overlay.foot.setText(f"{src} {cost:.2f}s · 可拖动 / 可纠错")
        self.overlay.raise_()

        # 存本地题库（题库命中的不重复存）
        if (not from_bank and self.last_question
                and self.cfg.get("auto_save_bank", False)):
            try:
                if qbank.save(self.last_question, full):
                    self.add_log("已存入本地题库")
            except Exception:
                pass

        # 存历史（jsonl）
        try:
            import json
            hp = os.path.join(os.path.dirname(config_store.config_path()), "history.jsonl")
            with open(hp, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "q": self.last_question, "a": full,
                                    "cost": round(cost, 2)}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        trace("answer done")

    def apply_correction(self, text):
        """把用户改好的答案覆盖进题库 + 记一条历史。"""
        q = (self.last_question or "").strip()
        try:
            if q:
                qbank.save(q, text)
            import json
            hp = os.path.join(os.path.dirname(config_store.config_path()), "history.jsonl")
            with open(hp, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "q": q, "a": text, "cost": 0, "fixed": True},
                                   ensure_ascii=False) + "\n")
            self.add_log("已纠错：本地题库和历史都已更新")
            self.overlay.foot.setText("已纠错 · 下次同一题直接用新答案")
        except Exception as e:
            self.add_log(f"纠错保存失败：{e}")

    def on_overlay_moved(self, pos):
        self.cfg["overlay_pos"] = [int(pos.x()), int(pos.y())]
        config_store.save(self.cfg)

    @safe_slot
    def collapse_main(self):
        """收起左侧导航：只留图标（再点一次展开）。"""
        self._side_collapsed = not getattr(self, "_side_collapsed", False)
        try:
            start = self.side.width()
            end = 58 if self._side_collapsed else 196
            a1 = QPropertyAnimation(self.side, b"minimumWidth", self)
            a2 = QPropertyAnimation(self.side, b"maximumWidth", self)
            for a in (a1, a2):
                a.setDuration(220)
                a.setStartValue(start)
                a.setEndValue(end)
                a.setEasingCurve(QEasingCurve.OutCubic)
                a.start(QAbstractAnimation.DeleteWhenStopped)
            self._side_anims = (a1, a2)
        except Exception:
            pass
        for k, b in self.nav.items():
            b.setText("" if self._side_collapsed else self._nav_text.get(k, ""))
            b.setToolTip(self._nav_text.get(k, ""))
            b.setStyleSheet("QPushButton#navItem{text-align:center;padding:8px 0;}" if self._side_collapsed else "")
        self.btn_collapse.setText("»" if self._side_collapsed else "收起主界面")
        self.btn_collapse.setToolTip("展开左侧导航" if self._side_collapsed else "收起左侧导航（只留图标）")
        try:
            self.btn_update.setText("" if self._side_collapsed else "发现新版本")
        except Exception:
            pass
        try:
            self.ver_label.setVisible(not self._side_collapsed)
        except Exception:
            pass

    @safe_slot
    def open_online(self, code):
        """从链接/深链打开在线试卷。"""
        code = (code or "").strip()
        if not code:
            return
        self.show_page("online")
        try:
            self.page_paper.open_online(code)
        except Exception:
            traceback.print_exc()
        self.show_main()

    def toggle_overlay(self):
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.place_avoiding(self.region)
            self.overlay.show()
            self.overlay.raise_()

    def ask_again(self):
        if not self.last_question:
            self.add_log("还没有识别到题目")
            return
        self.add_log("重问中…")
        self.set_state("重问中…", COLOR_ACCENT)
        self.overlay.set_text("")

        class ReAsk(QThread):
            delta = pyqtSignal(str)
            done = pyqtSignal(str, float)
            fail = pyqtSignal(str)

            def __init__(self, cfg, q):
                super().__init__()
                self.cfg, self.q = cfg, q

            def run(self):
                try:
                    client = ocr_ai.AiClient(self.cfg)
                    full, cost = client.solve(self.q, on_delta=lambda d: self.delta.emit(d))
                    self.done.emit(full, cost)
                except Exception as e:
                    self.fail.emit(f"{type(e).__name__}: {e}")

        self._re = ReAsk(self.cfg, self.last_question)
        self._re.delta.connect(self._on_delta)
        self._re.done.connect(lambda t, c: (self._on_answer(t, c), self.set_state("完成", COLOR_OK)))
        self._re.fail.connect(lambda s: (self.add_log("重问失败：" + s), self.set_state("出错", COLOR_ERR)))
        self._re.start()

    def closeEvent(self, e):
        # 关窗行为可在设置里选：最小化到托盘 / 直接退出 / 每次问
        if self.tray is not None and not self._quitting:
            act = self.cfg.get("close_action", "tray")
            if act == "ask":
                act = "tray" if ask_yes(
                    self, "关闭 Knowlib",
                    "要最小化到托盘，让它在后台继续待命吗？\n\n选「否」= 直接退出程序。") else "exit"
            if act == "tray":
                e.ignore()
                self._hide_to_tray()
                return
            e.ignore()
            self.quit_app()
            return
        self.stop_run()
        self.overlay.close()
        e.accept()
        if self.tray is None:
            QApplication.quit()

    def _hide_to_tray(self):
        if self._anim_on():
            a = QPropertyAnimation(self, b"windowOpacity", self)
            a.setDuration(140)
            a.setStartValue(self.windowOpacity())
            a.setEndValue(0.0)
            a.setEasingCurve(QEasingCurve.InCubic)

            def done():
                self.hide()
                self.setWindowOpacity(1.0)

            a.finished.connect(done)
            self._hide_anim = a
            a.start()
        else:
            self.hide()
        if not self._tray_tip_shown:
            self._tray_tip_shown = True
            if self.tray:
                self.tray.showMessage(
                    "Knowlib 还在后台",
                    "点托盘图标可以再打开；要彻底退出请右键托盘 → 退出。",
                    QSystemTrayIcon.Information, 3000)


SINGLE_NAME = "knowlib-pc-single-instance"

_TRACE_F = None


def trace(msg):
    """关键步骤落盘到 error.log：万一闪退，看最后一行就知道死在哪一步。"""
    if _TRACE_F is None:
        return
    try:
        _TRACE_F.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        _TRACE_F.flush()
    except Exception:
        pass


def _install_crash_log():
    """把未捕获异常 / 硬崩溃信息写进 %APPDATA%\\Knowlib\\error.log，方便事后查。"""
    global _TRACE_F
    try:
        import faulthandler
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Knowlib")
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, "error.log"), "a", encoding="utf-8", buffering=1)
        f.write("\n==== %s 启动 v%s ====\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                              getattr(config_store, "APP_VERSION", "?")))
        _TRACE_F = f
        sys.stderr = f
        sys.stdout = f

        def _hook(t, v, tb):
            f.write("".join(traceback.format_exception(t, v, tb)))
            f.flush()
        sys.excepthook = _hook

        import threading

        def _thook(a):
            f.write("".join(traceback.format_exception(
                a.exc_type, a.exc_value, a.exc_traceback)))
            f.flush()
        threading.excepthook = _thook
        faulthandler.enable(file=f, all_threads=True)
    except Exception:
        pass


def register_protocol():
    """把 knowlib:// 注册给当前 exe（HKCU，无需管理员），网页/微信里点链接就能拉起 PC 端。"""
    try:
        import winreg
        if getattr(sys, "frozen", False):
            exe = sys.executable
        else:
            exe = os.path.abspath(sys.argv[0])
        base = r"Software\Classes\knowlib"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Knowlib Protocol")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, '"%s" "%%1"' % exe)
    except Exception:
        pass


def main():
    _install_crash_log()
    register_protocol()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 关窗收托盘，真要退出走 quit_app

    # 单实例：已经在跑就把旧窗口叫出来，不再开第二个
    server = None
    try:
        from PyQt5.QtNetwork import QLocalSocket, QLocalServer
        probe = QLocalSocket()
        probe.connectToServer(SINGLE_NAME)
        if probe.waitForConnected(300):
            _payload = " ".join(sys.argv[1:]).strip() or "show"
            probe.write(_payload.encode("utf-8"))
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return
        QLocalServer.removeServer(SINGLE_NAME)
        server = QLocalServer()
        server.listen(SINGLE_NAME)
    except Exception:
        server = None

    w = MainWindow()
    app.setWindowIcon(w._app_icon())

    # 命令行/深链打开在线试卷（knowlib://s/xxxx 或 https://knowlib.fun/s/xxxx）
    for _a in sys.argv[1:]:
        _c = online_quiz.code_from(_a)
        if _c:
            QTimer.singleShot(500, lambda c=_c: w.open_online(c))
            break

    if server is not None:
        def _on_conn():
            c = server.nextPendingConnection()
            if not c:
                return

            def _read():
                try:
                    msg = bytes(c.readAll()).decode("utf-8", "replace").strip()
                except Exception:
                    msg = ""
                c.disconnectFromServer()
                _code = online_quiz.code_from(msg) if msg and msg != "show" else ""
                if _code:
                    w.open_online(_code)
                else:
                    w.show_main()

            c.readyRead.connect(_read)
            c.disconnected.connect(c.deleteLater)
        server.newConnection.connect(_on_conn)
        w._local_server = server   # 保持引用，别被回收

    # 开机自启用 --tray：静默进托盘，不弹窗（除非还没配 Key）
    cfg = config_store.load()
    # 手机遥控：启动时自动开
    try:
        remote_server.set_cfg(cfg)
        if cfg.get("remote_on"):
            _ok, _msg, _u = remote_server.start(cfg, int(cfg.get("remote_port", 18765) or 18765))
            if not _ok:
                w.add_log("手机遥控启动失败：" + _msg)
    except Exception as _e:
        w.add_log("手机遥控异常：%s" % _e)

    if "--silent" in sys.argv:
        w.hide()
    elif "--tray" in sys.argv and cfg.get("api_key"):
        if w.tray:
            w.hide()
        else:
            w.show()
    else:
        w.show()

    if not cfg.get("api_key"):
        QTimer.singleShot(300, w.open_settings)

    if cfg.get("check_update", True) and "--tray" not in sys.argv:
        QTimer.singleShot(2500, lambda: w.check_updates_async(True))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
