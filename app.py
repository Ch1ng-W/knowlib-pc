"""小团搜题 — 截图 + OCR + AI 解题，带状态反馈的桌面小工具。"""
import io
import os
import sys
import time
import ctypes
import ctypes.wintypes  # noqa: F401
import hashlib
import traceback

# ⚠️ 先导入 OCR（onnxruntime），再导入 PyQt5，否则会 0xC0000005 崩溃
import ocr_ai  # noqa: F401  (顺序敏感)

from PyQt5.QtCore import (Qt, QThread, pyqtSignal, QObject, QTimer, QPoint, QRect, QSize,
                          QEvent, QPropertyAnimation, QEasingCurve, QVariantAnimation,
                          QAbstractAnimation)
from PyQt5.QtGui import (QColor, QFont, QPainter, QPen, QBrush, QIcon, QPixmap,
                         QTextCursor, QGuiApplication)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QDialog, QLineEdit, QComboBox, QFormLayout, QMessageBox,
    QTextBrowser, QCheckBox, QSpinBox, QSizePolicy, QFrame, QMenu, QAction,
    QListWidget, QListWidgetItem, QSystemTrayIcon, QStyle, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QStackedWidget,
)

import config_store
import question_bank as qbank
import updater

APP_TITLE = "Knowlib"

# 窗口四周留给阴影的透明边距（同时是原生拖拽缩放的命中区）
SHADOW_MARGIN = 22

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
    """加上原生缩放边框样式（只留能力，不画系统标题栏）。"""
    try:
        hwnd = int(widget.winId())
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        u.SetWindowLongW(hwnd, GWL_STYLE,
                         style | WS_THICKFRAME | WS_MINIMIZEBOX
                         | WS_MAXIMIZEBOX | WS_SYSMENU)
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


def ask_yes(parent, title, text):
    box = _box(parent, QMessageBox.Question, title, text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    return box.exec_() == QMessageBox.Yes


def warn_box(parent, title, text):
    box = _box(parent, QMessageBox.Warning, title, text)
    box.setStandardButtons(QMessageBox.Ok)
    box.exec_()


def error_box(parent, title, text):
    box = _box(parent, QMessageBox.Critical, title, text)
    box.setStandardButtons(QMessageBox.Ok)
    box.exec_()



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
class AnswerOverlay(QWidget):
    def __init__(self, host):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.host = host
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(560)
        self._drag = None
        self._full = ""

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
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(root)

        inner = QVBoxLayout(root)
        inner.setContentsMargins(16, 12, 16, 12)

        head = QHBoxLayout()
        self.title = QLabel("答案")
        self.title.setStyleSheet("font-size:13px;color:#9aa4b2;")
        self.btn_copy = QPushButton("复制")
        self.btn_retry = QPushButton("重问")
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedWidth(30)
        for b in (self.btn_copy, self.btn_retry, self.btn_close):
            b.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.btn_copy)
        head.addWidget(self.btn_retry)
        head.addWidget(self.btn_close)
        inner.addLayout(head)

        # 大号答案
        self.answer = QLabel("")
        self.answer.setWordWrap(True)
        self.answer.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.answer.setStyleSheet(
            "font-size:34px;font-weight:800;color:#7ee787;"
            "padding:10px 14px;border-radius:12px;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 rgba(126,231,135,26),stop:1 rgba(126,231,135,10));"
        )
        self.answer.setMinimumHeight(56)
        inner.addWidget(self.answer)

        # 解析
        self.part_label = QLabel("解析")
        self.part_label.setStyleSheet("color:#7f8896;font-size:11px;margin-top:6px;")
        inner.addWidget(self.part_label)

        self.body = QTextBrowser()
        self.body.setStyleSheet("background:transparent;border:none;color:#d5dae3;font-size:14px;line-height:1.55;")
        self.body.setMinimumHeight(90)
        self.body.setMaximumHeight(360)
        inner.addWidget(self.body)

        self.foot = QLabel("")
        self.foot.setStyleSheet("color:#7f8896;font-size:11px;")
        inner.addWidget(self.foot)

        self.btn_copy.clicked.connect(self.copy_text)
        self.btn_retry.clicked.connect(host.ask_again)
        self.btn_close.clicked.connect(self.hide)

    def set_text(self, text):
        self._full = text or ""
        self._render()

    def append_text(self, delta):
        self._full = getattr(self, "_full", "") + (delta or "")
        self._render()

    def _render(self):
        import re as _re
        full = getattr(self, "_full", "")
        # 拆出「答案」和「解析」
        idx = -1
        for marker in ("解析：", "解析:"):
            i = full.find(marker)
            if i >= 0:
                idx = i
                break
        head = full if idx < 0 else full[:idx]
        tail = "" if idx < 0 else full[idx:]

        m = _re.search(r"答\s*案\s*[:：]\s*([\s\S]*)", head)
        if m:
            ans = m.group(1).strip()
        else:
            ans = head.strip()
        ans = ans.strip().strip("：:").strip()

        if ans:
            self.answer.setText(ans)
            self.answer.show()
            self.part_label.show()
        else:
            self.answer.setText("…")
            self.part_label.hide()

        tail = tail.replace("解析：", "").replace("解析:", "").strip()
        self.body.setPlainText(tail)
        sb = self.body.verticalScrollBar()
        sb.setValue(0)

    def copy_text(self):
        # 复制完整内容（答案 + 解析），而不是只有解析
        text = (getattr(self, "_full", "") or "").strip()
        if not text:
            text = (self.answer.text() + "\n" + self.body.toPlainText()).strip()
        QApplication.clipboard().setText(text)
        self.foot.setText("已复制到剪贴板")

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

        # 用户拖动过：优先回到他放的位置（只要不压住监控区域）
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
                (rx + rw + m, ry),                     # 右侧
                (rx - w - m, ry),                      # 左侧
                (rx, ry + rh + m),                     # 下方
                (rx, ry - h - m),                      # 上方
            ]
        cands += [(scr.right() - w - 20, scr.top() + 80)]

        best = None
        for x, y in cands:
            if x < scr.left() + 8 or y < scr.top() + 8:
                continue
            if x + w > scr.right() - 8 or y + h > scr.bottom() - 8:
                continue
            # 与监控区域有重叠就跳过
            if rw and rh and not (x + w < rx or x > rx + rw or y + h < ry or y > ry + rh):
                continue
            best = (x, y)
            break

        if best is None:
            best = (scr.right() - w - 20, scr.top() + 80)
        self.move(*best)

    def overlaps(self, region):
        try:
            g = self.frameGeometry()
            rx, ry = int(region.get("left", 0)), int(region.get("top", 0))
            rw, rh = int(region.get("width", 0)), int(region.get("height", 0))
            if not rw or not rh:
                return False
            return not (g.right() < rx or g.left() > rx + rw or g.bottom() < ry or g.top() > ry + rh)
        except Exception:
            return False


# ---------------------------------------------------------------- 设置对话框
class SettingsPage(QWidget):
    def __init__(self, cfg, parent=None, on_saved=None, on_back=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self._on_saved = on_saved
        self._on_back = on_back
        self.setObjectName("settingsPage")
        self.setStyleSheet("QWidget#settingsPage{background:transparent;}"
                           "QLabel{color:#dfe3ea;}"
                           "QLineEdit,QComboBox,QSpinBox{background:#262a31;color:#e8eaed;"
                           "border:1px solid #3a3f47;border-radius:6px;padding:6px;}"
                           "QPushButton{background:#2f3540;color:#e8eaed;border:none;"
                           "border-radius:6px;padding:7px 14px;}"
                           "QPushButton:hover{background:#3a4150;}"
                           "QPlainTextEdit{background:#262a31;color:#e8eaed;border:1px solid #3a3f47;"
                           "border-radius:6px;}"
                           "QCheckBox{color:#dfe3ea;}")

        lay = QVBoxLayout(self)
        tip = QLabel("① 选服务商 → ② 填 Key → ③ 点「测试连接」看到成功再保存")
        tip.setStyleSheet("color:#8fb6ff;font-size:12px;")
        lay.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(10)

        self.provider = QComboBox()
        self.provider.addItems(list(config_store.PROVIDERS.keys()))
        if self.cfg.get("provider") in config_store.PROVIDERS:
            self.provider.setCurrentText(self.cfg["provider"])
        form.addRow("服务商", self.provider)

        self.base_url = QLineEdit(self.cfg.get("base_url", ""))
        self.base_url.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow("API 端点", self.base_url)

        self.api_key = QLineEdit(self.cfg.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("sk-...")
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key)
        self.show_key = QPushButton("显示")
        self.show_key.setCheckable(True)
        self.show_key.setFixedWidth(58)
        self.show_key.toggled.connect(
            lambda on: self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.show_key)
        kw = QWidget()
        kw.setLayout(key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("API Key", kw)

        self.model = QLineEdit(self.cfg.get("model", ""))
        self.model.setPlaceholderText("deepseek-chat")
        form.addRow("模型", self.model)

        self.interval = QSpinBox()
        self.interval.setRange(500, 5000)
        self.interval.setSingleStep(100)
        self.interval.setValue(int(float(self.cfg.get("scan_interval", 1.2)) * 1000))
        self.interval.setSuffix(" ms")
        form.addRow("扫描间隔", self.interval)

        self.autostart = QCheckBox("开机自动启动（静默进托盘）")
        self.autostart.setChecked(config_store.autostart_enabled())
        form.addRow("启动", self.autostart)

        self.bank_first = QCheckBox("优先查本地题库（命中不花 token）")
        self.bank_first.setChecked(bool(self.cfg.get("bank_first", True)))
        form.addRow("题库", self.bank_first)

        self.check_update = QCheckBox("启动时检查更新")
        self.check_update.setChecked(bool(self.cfg.get("check_update", True)))
        form.addRow("更新", self.check_update)

        self.close_action = QComboBox()
        self.close_action.addItem("最小化到托盘（后台继续待命）", "tray")
        self.close_action.addItem("直接退出程序", "exit")
        self.close_action.addItem("每次问我", "ask")
        _ci = self.close_action.findData(self.cfg.get("close_action", "tray"))
        self.close_action.setCurrentIndex(_ci if _ci >= 0 else 0)
        form.addRow("关闭按钮", self.close_action)

        self.animations = QCheckBox("界面动画（页面切换 / 淡入）")
        self.animations.setChecked(bool(self.cfg.get("animations", True)))
        form.addRow("外观", self.animations)

        lay.addLayout(form)

        self.test_result = QLabel("尚未测试")
        self.test_result.setStyleSheet("color:#8a8f98;")
        lay.addWidget(self.test_result)

        row = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self.do_test)
        self.btn_upd = QPushButton("检查更新")
        self.btn_upd.clicked.connect(self.do_check_update)
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.do_save)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._go_back)
        row.addWidget(self.btn_test)
        row.addWidget(self.btn_upd)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_save)
        lay.addLayout(row)

        adv = QLabel("解题提示词（可改）")
        adv.setStyleSheet("color:#8a8f98;font-size:12px;margin-top:6px;")
        lay.addWidget(adv)
        self.prompt = QPlainTextEdit(self.cfg.get("prompt", ""))
        self.prompt.setFixedHeight(110)
        lay.addWidget(self.prompt)

        self.provider.currentTextChanged.connect(self._on_provider)

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
        return c

    def do_test(self):
        c = self._collect()
        if not c["base_url"] or not c["model"]:
            self.test_result.setText("请先填 API 端点和模型")
            self.test_result.setStyleSheet(f"color:{COLOR_WARN};")
            return
        self.btn_test.setEnabled(False)
        self.test_result.setText("正在测试…（最多等 30 秒）")
        self.test_result.setStyleSheet(f"color:{COLOR_IDLE};")
        QApplication.processEvents()
        ok, msg, cost = ocr_ai.AiClient(c).test_connection()
        self.btn_test.setEnabled(True)
        if ok:
            self.test_result.setText(f"✅ {msg} · {cost:.2f}s")
            self.test_result.setStyleSheet(f"color:{COLOR_OK};")
        else:
            self.test_result.setText(f"❌ {msg}")
            self.test_result.setStyleSheet(f"color:{COLOR_ERR};")

    def do_check_update(self):
        self.btn_upd.setEnabled(False)
        self.test_result.setText("正在查 GitHub…")
        self.test_result.setStyleSheet(f"color:{COLOR_IDLE};")
        QApplication.processEvents()
        r = updater.check()
        self.btn_upd.setEnabled(True)
        if not r.get("ok"):
            self.test_result.setText("检查失败：" + str(r.get("error"))[:80])
            self.test_result.setStyleSheet(f"color:{COLOR_ERR};")
            return
        if r.get("has_update"):
            self.test_result.setText(f"发现新版本 v{r['latest']}（当前 v{updater.current_version()}）")
            self.test_result.setStyleSheet(f"color:{COLOR_OK};")
        else:
            self.test_result.setText(f"已是最新版本 v{updater.current_version()} ✓")
            self.test_result.setStyleSheet(f"color:{COLOR_OK};")

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
        self._go_back()

    def _go_back(self):
        if self._on_back:
            self._on_back()


# ---------------------------------------------------------------- 历史记录窗
class HistoryPage(QWidget):
    """浏览 history.jsonl：左边列表，右边题目 + 答案（内嵌在主窗口里）。"""

    def __init__(self, parent=None, on_back=None):
        super().__init__(parent)
        self._on_back = on_back
        self.setObjectName("historyPage")
        self.setStyleSheet(
            "QWidget#historyPage{background:transparent;}"
            "QLabel{color:#dfe3ea;}"
            "QListWidget{background:#101216;color:#dfe3ea;border:1px solid #262a31;"
            "border-radius:8px;padding:4px;}"
            "QListWidget::item{padding:7px 8px;border-radius:6px;}"
            "QListWidget::item:selected{background:#2b3546;}"
            "QTextBrowser{background:#101216;color:#dfe3ea;border:1px solid #262a31;"
            "border-radius:8px;padding:10px;font-size:14px;}"
            "QPushButton{background:#2b303a;color:#e6e9ef;border:none;border-radius:8px;"
            "padding:7px 14px;}"
            "QPushButton:hover{background:#39404d;}"
            "QLineEdit{background:#262a31;color:#e8eaed;border:1px solid #3a3f47;"
            "border-radius:6px;padding:6px;}"
        )

        self.items = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

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
        self.list = QListWidget()
        self.list.setFixedWidth(250)
        self.list.currentRowChanged.connect(self.show_item)
        body.addWidget(self.list)

        right = QVBoxLayout()
        self.view = QTextBrowser()
        right.addWidget(self.view, 1)
        tools = QHBoxLayout()
        self.btn_copy = QPushButton("复制这条")
        self.btn_copy.clicked.connect(self.copy_current)
        self.btn_add = QPushButton("加入题库")
        self.btn_add.clicked.connect(self.add_to_bank)
        self.btn_add_all = QPushButton("有效项全部入库")
        self.btn_add_all.clicked.connect(self.add_all_to_bank)
        self.btn_clean = QPushButton("清理无效")
        self.btn_clean.clicked.connect(self.clean_invalid)
        self.btn_del = QPushButton("清空全部")
        self.btn_del.clicked.connect(self.clear_all)
        self.btn_close = QPushButton("← 返回")
        self.btn_close.clicked.connect(self._go_back)
        tools.addWidget(self.btn_copy)
        tools.addWidget(self.btn_add)
        tools.addWidget(self.btn_add_all)
        tools.addWidget(self.btn_clean)
        tools.addWidget(self.btn_del)
        tools.addStretch(1)
        tools.addWidget(self.btn_close)
        right.addLayout(tools)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)

        self.reload()

    # --
    def reload(self):
        self.items = config_store.load_history(limit=500)
        self.apply_filter()

    def _visible_items(self):
        """按「只看有效」过滤 + 同题（含模糊重复）去重，保留最新。"""
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
        self.list.clear()
        for it in vis:
            q = (it.get("q") or "")
            q1 = " ".join(q.split())[:26] or "（无题目）"
            item = QListWidgetItem(f"{it.get('t', '')[5:16]}  {q1}")
            item.setData(Qt.UserRole, it)
            self.list.addItem(item)
        txt = f"共 {len(vis)} 条"
        if dropped:
            txt += f" · 已隐藏 {dropped} 条无效/重复"
        if self.search.text().strip():
            txt += "（已搜索）"
        self.count.setText(txt)
        if vis:
            self.list.setCurrentRow(min(max(cur, 0), len(vis) - 1))
        else:
            self.view.setHtml("<div style='color:#7f8896;padding:20px'>"
                              "没有可显示的记录。取消勾选「只看有效」可以看全部。</div>")

    def show_item(self, row):
        if row < 0 or row >= self.list.count():
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        q = (it.get("q") or "").strip()
        a = (it.get("a") or "").strip()
        cost = it.get("cost")
        cost_s = f" · {cost}s" if isinstance(cost, (int, float)) else ""
        html = (
            f"<div style='color:#7f8896;font-size:12px'>{it.get('t','')}{cost_s}</div>"
            f"<div style='color:#8fb6ff;font-size:12px;margin-top:10px'>题目</div>"
            f"<div style='white-space:pre-wrap;margin-bottom:14px'>{self._esc(q)}</div>"
            f"<div style='color:#8fb6ff;font-size:12px'>答案与解析</div>"
            f"<div style='white-space:pre-wrap'>{self._esc(a)}</div>"
        )
        self.view.setHtml(html)

    @staticmethod
    def _esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def copy_current(self):
        row = self.list.currentRow()
        if row < 0:
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        QApplication.clipboard().setText(
            f"题目：\n{it.get('q','')}\n\n{it.get('a','')}".strip())
        self.count.setText("已复制✓")

    def clear_all(self):
        if not ask_yes(self, "清空历史", "确定删除所有历史记录？不可恢复。"):
            return
        config_store.clear_history()
        self.reload()

    def _go_back(self):
        if self._on_back:
            self._on_back()

    def add_to_bank(self):
        row = self.list.currentRow()
        if row < 0:
            self.count.setText("先选一条")
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        q = (it.get("q") or "").strip()
        a = (it.get("a") or "").strip()
        if not q or not a:
            self.count.setText("这条没有答案，存不了")
            return
        ok = qbank.save(q, a, source="history")
        self.count.setText("已加入题库 ✓" if ok else "加入失败（题目太短？）")

    def add_all_to_bank(self):
        vis, _ = self._visible_items()
        if not vis:
            self.count.setText("没有可入库的记录")
            return
        if not ask_yes(self, "批量入库", f"把当前列表里的 {len(vis)} 条全部加入题库？"):
            return
        ok = 0
        for it in vis:
            q = (it.get("q") or "").strip()
            a = (it.get("a") or "").strip()
            if q and a and qbank.save(q, a, source="history"):
                ok += 1
        self.count.setText(f"已入库 {ok} 条 ✓")

    def clean_invalid(self):
        if not ask_yes(self, "清理历史",
                       "删除重复记录和无效记录（无法作答 / 界面文字）？\n"
                       "会自动备份一份 history.jsonl.bak。"):
            return
        r = config_store.clean_history(keep_valid=True, dedupe=True)
        self.reload()
        self.count.setText(f"清理完成：{r['before']} → {r['after']} 条")


# ---------------------------------------------------------------- 本地题库窗
class BankPage(QWidget):
    """本地题库：检索 / 复制 / 删除 / 导入导出（内嵌在主窗口里）。"""

    def __init__(self, parent=None, on_back=None):
        super().__init__(parent)
        self._on_back = on_back
        self.setObjectName("bankPage")
        self.setStyleSheet(
            "QWidget#bankPage{background:transparent;}"
            "QLabel{color:#dfe3ea;}"
            "QListWidget{background:#101216;color:#dfe3ea;border:1px solid #262a31;"
            "border-radius:8px;padding:4px;}"
            "QListWidget::item{padding:7px 8px;border-radius:6px;}"
            "QListWidget::item:selected{background:#2b3546;}"
            "QTextBrowser{background:#101216;color:#dfe3ea;border:1px solid #262a31;"
            "border-radius:8px;padding:10px;font-size:14px;}"
            "QPushButton{background:#2b303a;color:#e6e9ef;border:none;border-radius:8px;"
            "padding:7px 14px;}"
            "QPushButton:hover{background:#39404d;}"
            "QLineEdit{background:#262a31;color:#e8eaed;border:1px solid #3a3f47;"
            "border-radius:6px;padding:6px;}"
        )

        self.items = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

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
        self.list.setFixedWidth(280)
        self.list.currentRowChanged.connect(self.show_item)
        body.addWidget(self.list)

        right = QVBoxLayout()
        self.view = QTextBrowser()
        right.addWidget(self.view, 1)
        tools1 = QHBoxLayout()
        self.btn_copy = QPushButton("复制答案")
        self.btn_copy.clicked.connect(self.copy_current)
        self.btn_del = QPushButton("删除这条")
        self.btn_del.clicked.connect(self.delete_current)
        self.btn_all = QPushButton("全选")
        self.btn_all.clicked.connect(lambda: self.check_all(True))
        self.btn_none = QPushButton("全不选")
        self.btn_none.clicked.connect(lambda: self.check_all(False))
        tools1.addWidget(self.btn_copy)
        tools1.addWidget(self.btn_del)
        tools1.addWidget(self.btn_all)
        tools1.addWidget(self.btn_none)
        tools1.addStretch(1)
        right.addLayout(tools1)

        tools1b = QHBoxLayout()
        self.chk_answer = QCheckBox("导出含答案与解析")
        self.chk_answer.setStyleSheet("QCheckBox{color:#98a2b3;}")
        self.btn_md = QPushButton("导出 MD")
        self.btn_md.clicked.connect(self.export_md)
        self.btn_pdf = QPushButton("导出 PDF")
        self.btn_pdf.clicked.connect(self.export_pdf)
        self.btn_pdf.setStyleSheet(
            "QPushButton{background:#2f6feb;color:#ffffff;border:none;border-radius:8px;"
            "padding:7px 14px;}QPushButton:hover{background:#3f7ffb;}")
        tools1b.addWidget(self.chk_answer)
        tools1b.addStretch(1)
        tools1b.addWidget(self.btn_md)
        tools1b.addWidget(self.btn_pdf)
        right.addLayout(tools1b)

        tools2 = QHBoxLayout()
        self.btn_import = QPushButton("导入 CSV")
        self.btn_import.clicked.connect(self.import_csv)
        self.btn_export = QPushButton("导出 CSV")
        self.btn_export.clicked.connect(self.export_csv)
        self.btn_clear = QPushButton("清空题库")
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_close = QPushButton("← 返回")
        self.btn_close.clicked.connect(self._go_back)
        tools2.addWidget(self.btn_import)
        tools2.addWidget(self.btn_export)
        tools2.addWidget(self.btn_clear)
        tools2.addStretch(1)
        tools2.addWidget(self.btn_close)
        right.addLayout(tools2)
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

    def show_item(self, row):
        if row < 0 or row >= self.list.count():
            return
        it = self.list.item(row).data(Qt.UserRole) or {}
        esc = HistoryPage._esc
        html = (
            f"<div style='color:#7f8896;font-size:12px'>存于 {it.get('created','')}"
            f" · 命中 {it.get('hits',0)} 次</div>"
            f"<div style='color:#8fb6ff;font-size:12px;margin-top:10px'>题目</div>"
            f"<div style='white-space:pre-wrap;margin-bottom:14px'>{esc(it.get('q_text',''))}</div>"
            f"<div style='color:#8fb6ff;font-size:12px'>答案</div>"
            f"<div style='white-space:pre-wrap;color:#7ee787;font-size:16px;"
            f"margin-bottom:10px'>{esc(it.get('answer',''))}</div>"
            f"<div style='white-space:pre-wrap;color:#c9d1d9'>{esc(it.get('solution',''))}</div>"
        )
        self.view.setHtml(html)

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
        qbank.delete(it.get("id"))
        self.reload()

    def export_csv(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getSaveFileName(self, "导出题库", "knowlib-bank.csv", "CSV (*.csv)")
        if not p:
            return
        ok, n = qbank.export_csv(p)
        self.stat.setText(f"已导出 {n} 题 ✓" if ok else f"导出失败：{n}")

    def import_csv(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(self, "导入题库", "", "CSV (*.csv)")
        if not p:
            return
        ok, n = qbank.import_csv(p)
        self.stat.setText(f"已导入 {n} 题 ✓" if ok else f"导入失败：{n}")
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

    def __init__(self, cfg, region):
        super().__init__()
        self.cfg = cfg
        self.region = region
        self.running = True
        self.paused = False
        self.force_once = False
        self.last_sig = None
        self.last_q_key = ""
        self.idle_rounds = 0
        self.ocr = None
        self.bank_used = False

    def ensure_ocr(self):
        if self.ocr is None:
            self.state.emit("加载识别模型…", COLOR_WARN)
            self.ocr = ocr_ai.OcrEngine()
        return self.ocr

    def request_once(self):
        self.force_once = True

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
                        full = f"答案：{hit['answer']}\n解析：{hit['solution']}"
                        self.finished_answer.emit(full, 0.0)
                        self.detail.emit(
                            f"题库命中（相似度 {hit['score']:.2f}）· 未调用 AI")
                        self.state.emit("本地题库命中", COLOR_OK)
                        time.sleep(max(0.3, float(self.cfg.get("scan_interval", 1.2))))
                        continue
                self.bank_used = False

                self.state.emit("请求 AI…", COLOR_ACCENT)

                client = ocr_ai.AiClient(self.cfg)
                full, ai_cost = client.solve(text, on_delta=lambda d: self.delta.emit(d))
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
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(470 + SHADOW_MARGIN * 2, 492 + SHADOW_MARGIN * 2)
        self.setMinimumSize(430 + SHADOW_MARGIN * 2, 404 + SHADOW_MARGIN * 2)
        enable_native_frame(self)
        self.setStyleSheet(
            "QWidget{color:#e6e9ef;font-family:'Microsoft YaHei';}"
            "QFrame#shell{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #171a21,stop:1 #0f1115);"
            "border-radius:16px;border:1px solid rgba(255,255,255,20);}"
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        self.shell = QFrame()
        self.shell.setObjectName("shell")
        outer.addWidget(self.shell)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.shell.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self.shell)
        lay.setContentsMargins(16, 4, 16, 14)
        lay.setSpacing(11)

        self.titlebar = TitleBar(self)
        self.btn_settings = self.titlebar.btn_settings
        lay.addWidget(self.titlebar)

        # ---- 页面容器：答题 / 历史 / 题库 / 设置 都在同一个窗口里切换
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.page_home = QWidget()
        self.page_home.setObjectName("homePage")
        lay_home = QVBoxLayout(self.page_home)
        lay_home.setContentsMargins(0, 0, 0, 0)
        lay_home.setSpacing(11)

        # ---- 状态卡片
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet(f"background:{COLOR_IDLE};border-radius:5px;")
        self.status = QLabel("未开始")
        self.status.setStyleSheet("font-size:16px;font-weight:700;color:#f2f4f8;")
        top.addWidget(self.dot)
        top.addWidget(self.status)
        top.addStretch(1)
        self.region_label = QLabel("")
        self.update_region_label()
        top.addWidget(self.region_label)
        cl.addLayout(top)

        self.detail = QLabel("先点「框选区域」把题目框住，再点「开始」")
        self.detail.setStyleSheet("color:#8a93a3;font-size:12px;")
        self.detail.setWordWrap(True)
        cl.addWidget(self.detail)
        lay_home.addWidget(card)

        # ---- 操作按钮
        row1 = QHBoxLayout()
        row1.setSpacing(9)
        self.btn_region = QPushButton("框选区域")
        self.btn_region.clicked.connect(self.select_region)
        self.btn_start = QPushButton("开始")
        self.btn_start.setObjectName("primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self.toggle_run)
        self.btn_shot = QPushButton("立即识别")
        self.btn_shot.clicked.connect(self.shot_once)
        row1.addWidget(self.btn_region, 1)
        row1.addWidget(self.btn_start, 1)
        row1.addWidget(self.btn_shot, 1)
        lay_home.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(9)
        self.btn_show = QPushButton("显示答案窗")
        self.btn_show.clicked.connect(self.toggle_overlay)
        self.btn_retry = QPushButton("重问一次")
        self.btn_retry.clicked.connect(self.ask_again)
        self.btn_history = QPushButton("历史")
        self.btn_history.clicked.connect(self.open_history)
        self.btn_bank = QPushButton("题库")
        self.btn_bank.clicked.connect(self.open_bank)
        row2.addWidget(self.btn_show, 1)
        row2.addWidget(self.btn_retry, 1)
        row2.addWidget(self.btn_history, 1)
        row2.addWidget(self.btn_bank, 1)
        lay_home.addLayout(row2)

        # ---- 日志
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        lay_home.addWidget(self.log, 1)

        self.hint = QLabel("F1 框选 · F2 暂停 · F3 立即识别 · F4 重问 · F5 答案窗"
                           "　|　关窗后在托盘后台运行")
        self.hint.setStyleSheet("color:#5f6875;font-size:10.5px;")
        self.hint.setWordWrap(True)
        lay_home.addWidget(self.hint)

        # ---- 其他页面（同一个窗口内切换）
        self.stack.addWidget(self.page_home)
        self.page_history = HistoryPage(self, on_back=lambda: self.show_page("home"))
        self.page_bank = BankPage(self, on_back=lambda: self.show_page("home"))
        self.page_settings = SettingsPage(self.cfg, self,
                                          on_saved=self._on_settings_saved,
                                          on_back=lambda: self.show_page("home"))
        for p in (self.page_history, self.page_bank, self.page_settings):
            self.stack.addWidget(p)
        # 页面自身的最小尺寸会让窗口在动画中"弹"到布局最小尺寸，压成 1x1 避免
        for p in (self.page_home, self.page_history, self.page_bank, self.page_settings):
            p.setMinimumSize(1, 1)
        self.stack.setMinimumSize(1, 1)
        self.stack.setCurrentWidget(self.page_home)

        self.setup_hotkeys()
        self.setup_tray()

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
        """带保护的切页入口：UI 出错也绝不把整个进程带崩。"""
        try:
            self._show_page(name, animate)
        except Exception:
            traceback.print_exc()
            try:
                self.add_log(f"切换页面出错：{name}")
            except Exception:
                pass

    def _show_page(self, name, animate=True):
        page = {"home": self.page_home, "history": self.page_history,
                "bank": self.page_bank, "settings": self.page_settings}.get(name)
        if page is None:
            return
        if self._page_name:
            # 切换中（动画未跑完）时存目标尺寸，避免存到中间态
            if (self._page_anim is not None
                    and self._page_anim.state() == QAbstractAnimation.Running):
                end = self._page_anim.endValue()
                self._saved_sizes[self._page_name] = (end.width(), end.height())
            else:
                self._saved_sizes[self._page_name] = (self.width(), self.height())
        self.stack.setCurrentWidget(page)
        self._page_name = name
        mw, mh = self._win_size(self.PAGE_MINS.get(name, (430, 404)))
        self.setMinimumSize(mw, mh)
        saved = self._saved_sizes.get(name)
        if not saved:
            saved = self._win_size(self.PAGE_SIZES.get(name, (470, 492)))
        w, h = max(saved[0], mw), max(saved[1], mh)
        self._resize_to(w, h, page, animate and self._anim_on())
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
        """页面内容淡入（效果对象常驻，避免动画中途被销毁）。"""
        eff = self._fade_effects.get(page)
        if eff is None:
            eff = QGraphicsOpacityEffect(page)
            eff.setOpacity(1.0)
            page.setGraphicsEffect(eff)
            self._fade_effects[page] = eff
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(190)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim = a
        a.start()

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
        """无边框 + WS_THICKFRAME：不画系统标题栏，但保留系统缩放/贴边。"""
        try:
            if eventType == b"windows_generic_MSG":
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == WM_NCCALCSIZE and msg.wParam:
                    # 客户区 = 整个窗口
                    return True, 0
                if msg.message == WM_NCHITTEST:
                    r = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(r))
                    x = ctypes.c_short(int(msg.lParam) & 0xFFFF).value
                    y = ctypes.c_short((int(msg.lParam) >> 16) & 0xFFFF).value
                    b = SHADOW_MARGIN + 6
                    l = x < r.left + b
                    rr = x >= r.right - b
                    t = y < r.top + b
                    bt = y >= r.bottom - b
                    hit = 0
                    if t and l:
                        hit = 13
                    elif t and rr:
                        hit = 14
                    elif bt and l:
                        hit = 16
                    elif bt and rr:
                        hit = 17
                    elif l:
                        hit = 10
                    elif rr:
                        hit = 11
                    elif t:
                        hit = 12
                    elif bt:
                        hit = 15
                    if hit:
                        return True, hit
        except Exception:
            pass
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

    def _on_settings_saved(self, c):
        self.cfg = config_store.load()
        self.add_log("设置已保存")
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
            self.titlebar.btn_update.setText(f"更新 {r['latest']}")
            self.titlebar.btn_update.setVisible(True)
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
            self.titlebar.btn_update.setEnabled(True)
            return
        QTimer.singleShot(400, self.quit_app)

    def _on_dl_fail(self, msg):
        self.add_log("下载失败：" + msg)
        self.titlebar.btn_update.setEnabled(True)

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
        self.titlebar.btn_update.setEnabled(False)
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

    # -------- 状态
    def set_state(self, text, color):
        self.status.setText(text)
        self.dot.setStyleSheet(f"background:{color};border-radius:5px;")

    def _set_start_look(self, running):
        self.btn_start.setObjectName("danger" if running else "primary")
        self.btn_start.style().unpolish(self.btn_start)
        self.btn_start.style().polish(self.btn_start)

    def add_log(self, text):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")

    def update_region_label(self):
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
        {"f1": self.select_region, "f2": self.toggle_pause, "f3": self.shot_once,
         "f4": self.ask_again, "f5": self.toggle_overlay}.get(name, lambda: None)()

    def setup_hotkeys(self):
        self.hotkey.connect(self._on_hotkey)  # 跨线程：热键线程 → Qt 主线程
        try:
            from pynput import keyboard as pk
            keymap = {pk.Key.f1: 'f1', pk.Key.f2: 'f2', pk.Key.f3: 'f3',
                      pk.Key.f4: 'f4', pk.Key.f5: 'f5'}

            def on_press(key):
                name = keymap.get(key)
                if name:
                    self.hotkey.emit(name)

            self._listener = pk.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
            self.add_log("热键已注册：F1/F2/F3/F4/F5")
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
        config_store.save(self.cfg)
        self.update_region_label()
        self.show()
        self.add_log(f"已框选：{r['width']}×{r['height']} @ ({r['left']},{r['top']})")
        self.set_state("区域已设置", COLOR_OK)

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
        if not self.region or not self.region.get("width"):
            self.add_log("还没框选区域，先点「框选区域」")
            self.set_state("未框选区域", COLOR_WARN)
            return

        self.worker = ScanWorker(self.cfg, self.region)
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

    def _on_ocr(self, text):
        if text != self.last_question:
            self.overlay.set_text("")      # 新题：清掉上一题答案，避免越堆越长
            self.last_question = text
            self.add_log("识别到：" + text.replace("\n", " ")[:60])

    def _on_delta(self, d):
        if not self.overlay.isVisible():
            self.overlay.place_avoiding(self.region)
            self.overlay.show()
        self.overlay.append_text(d)

    def _on_answer(self, full, cost):
        if not self.overlay.isVisible():
            self.overlay.place_avoiding(self.region)
            self.overlay.show()
        from_bank = bool(self.worker is not None and getattr(self.worker, "bank_used", False))
        if from_bank:
            self.overlay.foot.setText("来自本地题库 · 未消耗 token")
        else:
            self.overlay.foot.setText(f"{cost:.2f}s · 深色可拖动")
        self.overlay.raise_()

        # 存本地题库（题库命中的不重复存）
        if not from_bank and self.last_question:
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

    def on_overlay_moved(self, pos):
        self.cfg["overlay_pos"] = [int(pos.x()), int(pos.y())]
        config_store.save(self.cfg)

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


def main():
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
            probe.write(b"show")
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

    if server is not None:
        def _on_conn():
            c = server.nextPendingConnection()
            if c:
                c.readyRead.connect(lambda: (w.show_main(), c.disconnectFromServer()))
                c.disconnected.connect(c.deleteLater)
        server.newConnection.connect(_on_conn)
        w._local_server = server   # 保持引用，别被回收

    # 开机自启用 --tray：静默进托盘，不弹窗（除非还没配 Key）
    cfg = config_store.load()
    if "--tray" in sys.argv and cfg.get("api_key"):
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
