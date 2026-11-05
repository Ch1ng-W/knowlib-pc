"""小团搜题 — 截图 + OCR + AI 解题，带状态反馈的桌面小工具。"""
import io
import os
import sys
import time
import hashlib
import traceback

# ⚠️ 先导入 OCR（onnxruntime），再导入 PyQt5，否则会 0xC0000005 崩溃
import ocr_ai  # noqa: F401  (顺序敏感)

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer, QPoint, QRect
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush, QIcon, QPixmap, QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QDialog, QLineEdit, QComboBox, QFormLayout, QMessageBox,
    QTextBrowser, QCheckBox, QSpinBox, QSizePolicy, QFrame,
)

import config_store

APP_TITLE = "Knowlib"
COLOR_OK = "#3ddc84"
COLOR_WARN = "#ffb020"
COLOR_ERR = "#ff5c5c"
COLOR_IDLE = "#8a8f98"
COLOR_ACCENT = "#5b9dff"


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
        self.setFixedWidth(520)
        self._drag = None

        root = QFrame(self)
        root.setObjectName("card")
        root.setStyleSheet(
            "#card{background:rgba(22,24,29,235);border-radius:14px;border:1px solid rgba(255,255,255,28);}"
            "QLabel{color:#e8eaed;}"
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

        self.body = QTextBrowser()
        self.body.setStyleSheet("background:transparent;border:none;color:#f2f4f8;font-size:15px;")
        self.body.setMinimumHeight(120)
        self.body.setMaximumHeight(420)
        inner.addWidget(self.body)

        self.foot = QLabel("")
        self.foot.setStyleSheet("color:#7f8896;font-size:11px;")
        inner.addWidget(self.foot)

        self.btn_copy.clicked.connect(self.copy_text)
        self.btn_retry.clicked.connect(host.ask_again)
        self.btn_close.clicked.connect(self.hide)

    def set_text(self, text):
        self.body.setPlainText(text)
        self.body.moveCursor(QTextCursor.End)

    def append_text(self, delta):
        cur = self.body.textCursor()
        cur.movePosition(QTextCursor.End)
        self.body.setTextCursor(cur)
        self.body.insertPlainText(delta)
        sb = self.body.verticalScrollBar()
        sb.setValue(sb.maximum())

    def copy_text(self):
        QApplication.clipboard().setText(self.body.toPlainText())
        self.foot.setText("已复制到剪贴板")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

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
class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self.setWindowTitle("设置 · Knowlib")
        self.setMinimumWidth(560)
        self.setStyleSheet("QDialog{background:#1b1d22;} QLabel{color:#dfe3ea;}"
                           "QLineEdit,QComboBox,QSpinBox{background:#262a31;color:#e8eaed;"
                           "border:1px solid #3a3f47;border-radius:6px;padding:6px;}"
                           "QPushButton{background:#2f3540;color:#e8eaed;border:none;"
                           "border-radius:6px;padding:7px 14px;}"
                           "QPushButton:hover{background:#3a4150;}"
                           "QPlainTextEdit{background:#262a31;color:#e8eaed;border:1px solid #3a3f47;"
                           "border-radius:6px;}")

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

        lay.addLayout(form)

        self.test_result = QLabel("尚未测试")
        self.test_result.setStyleSheet("color:#8a8f98;")
        lay.addWidget(self.test_result)

        row = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self.do_test)
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.do_save)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_test)
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

    def do_save(self):
        c = self._collect()
        if not c["base_url"] or not c["model"]:
            QMessageBox.warning(self, "还没填完", "API 端点和模型不能为空")
            return
        if not c["api_key"]:
            r = QMessageBox.question(self, "没填 Key", "没填 API Key，多数服务会调用失败。仍然保存？")
            if r != QMessageBox.Yes:
                return
        ok, info = config_store.save(c)
        if not ok:
            QMessageBox.critical(self, "保存失败", info)
            return
        self.cfg = c
        self.accept()


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
        self.idle_rounds = 0
        self.ocr = None

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

                self.ocr_done.emit(text)
                self.detail.emit(f"OCR {ocr_cost:.2f}s · {len(text)} 字 · 置信 {conf:.2f}")
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

        self.setWindowTitle(APP_TITLE)
        self.setFixedSize(430, 330)
        self.setStyleSheet(
            "QWidget{background:#15171c;color:#e6e9ef;font-family:'Microsoft YaHei';}"
            "QLabel{color:#e6e9ef;}"
            "QPushButton{background:#2b303a;color:#e6e9ef;border:none;border-radius:8px;padding:8px 10px;font-size:13px;}"
            "QPushButton:hover{background:#39404d;}"
            "QPushButton:disabled{color:#6b7280;background:#22262d;}"
            "QPlainTextEdit{background:#101216;color:#9aa4b2;border:1px solid #262a31;"
            "border-radius:8px;font-size:11px;}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color:{COLOR_IDLE};font-size:16px;")
        self.status = QLabel("未开始")
        self.status.setStyleSheet("font-size:15px;font-weight:600;")
        top.addWidget(self.dot)
        top.addWidget(self.status)
        top.addStretch(1)
        self.btn_settings = QPushButton("设置")
        self.btn_settings.clicked.connect(self.open_settings)
        top.addWidget(self.btn_settings)
        lay.addLayout(top)

        self.detail = QLabel("先点「框选区域」把题目框住，再点「开始」")
        self.detail.setStyleSheet("color:#8a8f98;font-size:12px;")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        row1 = QHBoxLayout()
        self.btn_region = QPushButton("框选区域")
        self.btn_region.clicked.connect(self.select_region)
        self.btn_start = QPushButton("开始")
        self.btn_start.clicked.connect(self.toggle_run)
        self.btn_shot = QPushButton("立即识别")
        self.btn_shot.clicked.connect(self.shot_once)
        row1.addWidget(self.btn_region)
        row1.addWidget(self.btn_start)
        row1.addWidget(self.btn_shot)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_show = QPushButton("显示答案窗")
        self.btn_show.clicked.connect(self.toggle_overlay)
        self.btn_retry = QPushButton("重问一次")
        self.btn_retry.clicked.connect(self.ask_again)
        row2.addWidget(self.btn_show)
        row2.addWidget(self.btn_retry)
        lay.addLayout(row2)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        lay.addWidget(self.log, 1)

        self.hint = QLabel("热键：F1 框选 · F2 暂停/继续 · F3 立即识别 · F4 重问 · F5 显示/隐藏答案窗")
        self.hint.setStyleSheet("color:#6b7280;font-size:11px;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        self.region_label = QLabel("")
        self.update_region_label()
        lay.insertWidget(2, self.region_label)

        self.setup_hotkeys()

    # -------- 状态
    def set_state(self, text, color):
        self.status.setText(text)
        self.dot.setStyleSheet(f"color:{color};font-size:16px;")

    def add_log(self, text):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")

    def update_region_label(self):
        if self.region and self.region.get("width"):
            r = self.region
            self.region_label.setText(
                f"监控区域：{r['width']}×{r['height']} @ ({r['left']},{r['top']})")
        else:
            self.region_label.setText("监控区域：未设置")
        self.region_label.setStyleSheet("color:#7f8896;font-size:11px;")

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
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec_() == QDialog.Accepted:
            self.cfg = config_store.load()
            self.add_log(f"设置已保存 → {config_store.config_path()}")
            if self.worker:
                self.worker.cfg = self.cfg

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
        self.overlay.foot.setText(f"{cost:.2f}s · 深色可拖动")
        self.overlay.raise_()
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
        self.stop_run()
        self.overlay.close()
        e.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    w = MainWindow()
    w.show()
    cfg = config_store.load()
    if not cfg.get("api_key"):
        QTimer.singleShot(300, w.open_settings)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
