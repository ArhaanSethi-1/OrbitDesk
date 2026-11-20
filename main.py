"""Single-runtime PySide6 dashboard and voice controller for OrbitDesk."""

import datetime
import logging
import math
import os
import sys
import tempfile
import threading
import html
from logging.handlers import RotatingFileHandler

_log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "OrbitDesk", "logs")
try:
    os.makedirs(_log_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[RotatingFileHandler(os.path.join(_log_dir, "orbitdesk.log"), maxBytes=2_000_000, backupCount=4, encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )
except OSError:
    try:
        _fallback_log_dir = os.path.join(tempfile.gettempdir(), "OrbitDesk", "logs")
        os.makedirs(_fallback_log_dir, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            handlers=[RotatingFileHandler(os.path.join(_fallback_log_dir, "orbitdesk.log"), maxBytes=2_000_000, backupCount=2, encoding="utf-8")],
            format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        return sys.__excepthook__(exc_type, exc_value, exc_traceback)
    logging.critical("Uncaught application exception", exc_info=(exc_type, exc_value, exc_traceback))


def _uncaught_thread(args):
    logging.critical(
        "Uncaught exception in thread %s", args.thread.name if args.thread else "unknown",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


sys.excepthook = _uncaught_exception
threading.excepthook = _uncaught_thread

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QPushButton, QSystemTrayIcon, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from brain import OrbitDeskBrain
import automation
from orb import OrbitDeskOrb
from speech_output import VOICE, is_speaking, speak, stop_speaking

APP_STYLE = """
QMainWindow, QWidget { background: #0a0d14; color: #eaf2ff; font-family: 'Segoe UI'; }
QTabWidget::pane { border: 1px solid #1c2d42; border-radius: 9px; }
QTabBar::tab { background: #111a27; padding: 10px 18px; border: 1px solid #1c2d42; }
QTabBar::tab:selected { color: #00e5ff; border-bottom: 2px solid #00e5ff; }
QTextBrowser, QListWidget { background: #0e1420; border: 1px solid #1b2a3d; border-radius: 8px; padding: 10px; }
QLineEdit { background: #101927; border: 1px solid #28415d; border-radius: 8px; padding: 12px; color: white; }
QPushButton { background: #102535; color: #00e5ff; border: 1px solid #1b6172; border-radius: 7px; padding: 9px 15px; }
QPushButton:hover { background: #173849; }
QTextBrowser pre, QTextBrowser div.codehilite { background: #080d15; border: 1px solid #1b2a3d; border-radius: 6px; padding: 8px; }
QTextBrowser code { color: #7ee7ff; font-family: 'Cascadia Code'; }
"""


class PermissionBridge(QObject):
    requested = Signal(object)
    audit = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._pending = None

    def ask(self, action, target, summary):
        event = threading.Event()
        request = {"action": action, "target": target, "summary": summary, "event": event, "choice": "deny"}
        with self._lock:
            if self._pending is not None:
                return "deny"
            self._pending = request
        self.requested.emit(request)
        event.wait(120)
        with self._lock:
            if self._pending is request:
                self._pending = None
        choice = request["choice"]
        self.audit.emit(action, "Allowed once" if choice == "once" else "Always allowed" if choice == "always" else "Denied")
        return choice

    def resolve(self, choice):
        with self._lock:
            request = self._pending
            if request is None:
                return False
            request["choice"] = choice if choice in {"once", "always"} else "deny"
            request["event"].set()
            self._pending = None
        return True

    def has_pending(self):
        with self._lock:
            return self._pending is not None


class PermissionDialog(QDialog):
    def __init__(self, request, broker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OrbitDesk permission")
        self.broker = broker
        self.resolved = False
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setStyleSheet("QDialog{background:#0c1420;color:#edf7ff;} QLabel{font-size:12pt;} QPushButton{padding:9px 13px;}")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Permission requested"))
        layout.addWidget(QLabel(request["summary"]))
        buttons = QHBoxLayout()
        for label, choice in (("Allow Once", "once"), ("Always Allow", "always"), ("Deny", "deny")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, c=choice: self._finish(broker, c))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setFixedWidth(440)

    def _finish(self, broker, choice):
        self.resolved = True
        broker.resolve(choice)
        self.close()

    def closeEvent(self, event):
        if not self.resolved:
            self.broker.resolve("deny")
        event.accept()


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.target_level = 0.0
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)
        self.setMinimumHeight(70)

    def set_active(self, active):
        self.active = active
        if not active:
            self.target_level = 0.02
        self.update()

    def set_audio_level(self, level):
        self.active = level > 0.015
        self.target_level = min(1.0, max(0.02, float(level) * 18.0))

    def _tick(self):
        self.phase += 0.13
        self.level += (self.target_level - self.level) * 0.32
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#00e5ff"), 2))
        mid = self.height() / 2
        points = []
        for x in range(0, self.width(), 4):
            amp = (8 + 20 * self.level) * (0.3 + 0.7 * abs(math.sin(x * 0.035 + self.phase)))
            points.append((x, mid + math.sin(x * 0.07 + self.phase) * amp))
        for left, right in zip(points, points[1:]):
            painter.drawLine(int(left[0]), int(left[1]), int(right[0]), int(right[1]))


def _tray_icon():
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#00e5ff"))
    painter.drawEllipse(3, 3, 26, 26)
    painter.setBrush(QColor("#0a0d14"))
    painter.drawEllipse(9, 9, 14, 14)
    painter.end()
    return QIcon(pixmap)


class OrbitDeskWindow(QMainWindow):
    message_added = Signal(str, str)
    status_changed = Signal(str)
    wake_changed = Signal(bool)
    wake_triggered = Signal()
    audio_level_changed = Signal(float)
    permission_announcement = Signal(str)
    permission_resolved = Signal()
    shutdown_requested = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.closing = False
        self.mic_enabled = True
        self.voice_enabled = True
        self.busy = False
        self.command_lock = threading.Lock()
        self.permission_bridge = PermissionBridge()
        self.brain = OrbitDeskBrain(permission_request=self.permission_bridge.ask)
        self.brain.audit_callback = lambda action, result: self.permission_bridge.audit.emit(action, result)
        self.brain.notify = lambda message: self.message_added.emit("OrbitDesk", message)
        self.speech_module = None
        self.wake_stop = threading.Event()
        self.wake_thread = None
        self.pending_dialog = None
        self.message_added.connect(self._append_message)
        self.status_changed.connect(self._set_status)
        self.wake_changed.connect(self._set_wake_active)
        self.wake_triggered.connect(self._show_wake)
        self.permission_announcement.connect(self._announce_permission)
        self.permission_resolved.connect(self._dismiss_permission_dialog)
        self.shutdown_requested.connect(self.exit_app)
        self.permission_bridge.requested.connect(self._show_permission)
        self.permission_bridge.audit.connect(self._audit_permission)

        self.setWindowTitle("OrbitDesk | Jarvis Assistant")
        self.setMinimumSize(850, 610)
        self.resize(1080, 740)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self.audio_level_changed.connect(self.waveform.set_audio_level)
        self._build_tray()

        self.orb = OrbitDeskOrb()
        self.orb.dashboard_requested.connect(self.show_dashboard)
        self.orb.settings_requested.connect(self.show_settings)
        self.orb.mute_requested.connect(self.toggle_microphone)
        self.orb.background_requested.connect(self.hide_to_tray)
        self.orb.exit_requested.connect(self.exit_app)
        self.message_added.emit("JARVIS", "Systems online. Say ‘Hey Jarvis’ to begin, or add a Jarvis wake model to use the single-word phrase.")
        self.status_changed.emit("Starting wake word engine…")
        self.root_timer = QTimer(self)
        self.root_timer.timeout.connect(self._update_clock)
        self.root_timer.start(1000)
        QTimer.singleShot(250, self.start_wake_listener)
        QTimer.singleShot(1000, self.showMinimized)

    def _build_ui(self):
        center = QWidget()
        root = QVBoxLayout(center)
        header = QHBoxLayout()
        title = QLabel("ORBITDESK  /  JARVIS")
        title.setStyleSheet("font-size:22px;font-weight:700;color:#00e5ff;letter-spacing:2px")
        header.addWidget(title)
        header.addStretch()
        self.model_label = QLabel(
            "● CLOUD API FALLBACK · Optional .env keys" if automation.cloud_mode_enabled()
            else "● LOCAL MODEL · QWEN 2.5:3B"
        )
        self.model_label.setStyleSheet("color:#63b6ff")
        header.addWidget(self.model_label)
        root.addLayout(header)

        self.status_label = QLabel("● ONLINE")
        self.status_label.setStyleSheet("color:#00e5ff;padding:7px 0")
        root.addWidget(self.status_label)
        self.waveform = Waveform()
        root.addWidget(self.waveform)

        self.tabs = QTabWidget()
        chat_tab = QWidget()
        chat_layout = QVBoxLayout(chat_tab)
        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(True)
        chat_layout.addWidget(self.history, 1)
        compose = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Jarvis…")
        self.input.returnPressed.connect(self.send_keyboard)
        send = QPushButton("Send")
        send.clicked.connect(self.send_keyboard)
        compose.addWidget(self.input, 1)
        compose.addWidget(send)
        chat_layout.addLayout(compose)
        controls = QHBoxLayout()
        self.mic_button = QPushButton("Microphone: On")
        self.mic_button.clicked.connect(self.toggle_microphone)
        self.voice_button = QPushButton("Voice: On")
        self.voice_button.clicked.connect(self.toggle_voice)
        controls.addWidget(self.mic_button)
        controls.addWidget(self.voice_button)
        controls.addStretch()
        chat_layout.addLayout(controls)
        self.tabs.addTab(chat_tab, "Dashboard")

        permission_tab = QWidget()
        permission_layout = QVBoxLayout(permission_tab)
        permission_layout.addWidget(QLabel("Permission and action audit"))
        self.audit_list = QListWidget()
        permission_layout.addWidget(self.audit_list)
        self.tabs.addTab(permission_tab, "Permissions")

        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        settings_layout.addWidget(QLabel(f"Voice: {VOICE}  ·  Wake phrase: Hey Jarvis (optional single-word Jarvis model supported)"))
        settings_layout.addWidget(QLabel("Local transcription: faster-whisper base.en, CPU int8"))
        settings_layout.addWidget(QLabel("Close behaviour: minimize to system tray"))
        settings_layout.addStretch()
        self.tabs.addTab(settings_tab, "Settings")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(center)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(_tray_icon(), self)
        self.tray.setToolTip("Jarvis is listening")
        menu = self.tray.contextMenu()
        if menu is None:
            from PySide6.QtWidgets import QMenu
            menu = QMenu(self)
        menu.addAction("Open Dashboard", self.show_dashboard)
        menu.addAction("Mute Microphone", self.toggle_microphone)
        menu.addAction("Run in Background", self.hide_to_tray)
        menu.addAction("Settings", lambda: (self.show_dashboard(), self.tabs.setCurrentIndex(2)))
        menu.addSeparator()
        menu.addAction("Exit", self.exit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_dashboard() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def _append_message(self, speaker, message):
        color = "#00e5ff" if speaker in {"JARVIS", "OrbitDesk"} else "#8ab4ff" if speaker == "YOU" else "#ffbd69"
        try:
            import markdown
            body = markdown.markdown(str(message), extensions=["fenced_code", "codehilite"])
        except ImportError:
            body = html.escape(str(message)).replace("\n", "<br>")
        self.history.append(f'<p><b style="color:{color}">{speaker}</b></p>{body}')

    def _set_status(self, text):
        if text.casefold().startswith("listening") and (self.busy or is_speaking()):
            return
        self.status_label.setText("● " + text.upper())

    def _set_wake_active(self, active):
        self.waveform.set_active(active)

    def _update_clock(self):
        self.statusBar().showMessage(datetime.datetime.now().strftime("%A, %d %B %Y  ·  %I:%M:%S %p"))

    def _audit_permission(self, action, result):
        self.audit_list.insertItem(0, f"{datetime.datetime.now():%H:%M:%S}  ·  {action}  ·  {result}")

    def _show_permission(self, request):
        if self.closing:
            request["choice"] = "deny"
            request["event"].set()
            return
        self.pending_dialog = PermissionDialog(request, self.permission_bridge, self)
        self.pending_dialog.show()
        self.pending_dialog.raise_()
        self.permission_announcement.emit(request["summary"])

    def _announce_permission(self, summary):
        phrase = f"Sir, this action requires your permission to {summary}. Should I proceed?"
        threading.Thread(target=speak, args=(phrase,), name="JarvisPermissionPrompt", daemon=True).start()

    def _dismiss_permission_dialog(self):
        if self.pending_dialog is not None:
            self.pending_dialog.resolved = True
            self.pending_dialog.close()
            self.pending_dialog = None

    def _show_wake(self):
        if self.closing:
            return
        if self.busy or is_speaking():
            return
        self.orb.request_session("Listening…")
        self.orb.request_state("Listening")
        self.status_changed.emit("Listening")

    def start_wake_listener(self):
        if self.closing or not self.mic_enabled:
            return
        if self.wake_thread and self.wake_thread.is_alive():
            if self.wake_stop.is_set():
                QTimer.singleShot(300, self.start_wake_listener)
            return
        self.wake_stop.clear()
        self.wake_thread = threading.Thread(target=self._wake_loop, name="JarvisWakeWord", daemon=True)
        self.wake_thread.start()

    def _wake_loop(self):
        try:
            import speech
            self.speech_module = speech
            while not self.closing and not self.wake_stop.is_set():
                command = speech.listen_for_wake_command(
                    self.wake_stop,
                    on_wake=lambda: self.wake_triggered.emit(),
                    on_activity=lambda level, _text: self.audio_level_changed.emit(level),
                    on_ready=lambda: self.status_changed.emit("Listening for Hey Jarvis"),
                    is_speaking=is_speaking,
                    on_interrupt=stop_speaking,
                )
                if self.wake_stop.is_set() or self.closing:
                    break
                if command:
                    self.message_added.emit("YOU", command)
                    threading.Thread(target=self.process_command, args=(command,), name="JarvisCommand", daemon=True).start()
                else:
                    self.orb.request_hide()
                self.wake_changed.emit(False)
        except Exception as error:
            logging.exception("Wake listener failed")
            self.status_changed.emit(f"Microphone unavailable: {error}")

    def send_keyboard(self):
        command = self.input.text().strip()
        if not command:
            return
        self.input.clear()
        self.message_added.emit("YOU", command)
        threading.Thread(target=self.process_command, args=(command,), name="JarvisCommand", daemon=True).start()

    def process_command(self, command):
        normalized = " ".join(command.casefold().replace(",", " ").replace(".", " ").split())
        if normalized in {"stop", "stop speaking", "cancel", "be quiet"}:
            stop_speaking()
            self.status_changed.emit("Speech interrupted")
            return
        if self.permission_bridge.has_pending() and normalized in {"yes", "yes proceed", "allow", "allow once", "always allow", "deny", "no", "cancel"}:
            choice = "always" if normalized == "always allow" else "once" if normalized in {"yes", "yes proceed", "allow", "allow once"} else "deny"
            self.permission_bridge.resolve(choice)
            self.permission_resolved.emit()
            self.message_added.emit("JARVIS", "Permission approved." if choice != "deny" else "Permission denied.")
            return
        if not self.command_lock.acquire(blocking=False):
            self.message_added.emit("JARVIS", "I’m finishing the current request; please try again shortly.")
            return
        self.busy = True
        self.status_changed.emit("Thinking")
        self.orb.request_state("Thinking")
        self.orb.request_overlay_text("Thinking…")
        try:
            response = self.brain.respond(command)
            self.message_added.emit("JARVIS", response)
            self.orb.request_overlay_text(response)
            if self.voice_enabled and not self.closing:
                self.status_changed.emit("Speaking")
                self.orb.request_state("Speaking")
                speak(response)
        except Exception as error:
            logging.exception("Command processing failed")
            self.message_added.emit("SYSTEM", f"Something went wrong: {error}")
        finally:
            self.busy = False
            self.command_lock.release()
            if not self.closing:
                if not self.brain.running:
                    self.shutdown_requested.emit()
                else:
                    self.status_changed.emit("Listening for Hey Jarvis" if self.mic_enabled else "Online")
                    self.orb.request_hide()

    def toggle_microphone(self):
        self.mic_enabled = not self.mic_enabled
        self.mic_button.setText("Microphone: On" if self.mic_enabled else "Microphone: Off")
        if self.mic_enabled:
            self.start_wake_listener()
            self.status_changed.emit("Listening for Hey Jarvis")
        else:
            self.wake_stop.set()
            self.status_changed.emit("Microphone off")
        self._audit_permission("microphone", "Enabled" if self.mic_enabled else "Disabled")

    def toggle_voice(self):
        self.voice_enabled = not self.voice_enabled
        self.voice_button.setText("Voice: On" if self.voice_enabled else "Voice: Off")
        if not self.voice_enabled:
            stop_speaking()

    def show_dashboard(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_settings(self):
        self.show_dashboard()
        self.tabs.setCurrentIndex(2)

    def hide_to_tray(self):
        self.hide()
        self.orb.request_hide()

    def closeEvent(self, event):
        event.ignore()
        self.hide_to_tray()

    def exit_app(self):
        if self.closing:
            return
        self.closing = True
        self.wake_stop.set()
        self.permission_bridge.resolve("deny")
        stop_speaking()
        if self.wake_thread and self.wake_thread is not threading.current_thread():
            self.wake_thread.join(timeout=1.5)
        self.orb.close()
        self.tray.hide()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    window = OrbitDeskWindow(app)
    app.setStyleSheet(APP_STYLE)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
