#!/usr/bin/env python3
"""
mpv AI Companion — Floating PyQt6 Panel
Snaps to the right edge of the mpv window. Dark translucent, collapsible.
"""

import argparse
import os
import platform
import sys
import tempfile
import time

from PyQt6.QtCore import (
    Qt, QTimer, QThread, QPropertyAnimation, QEasingCurve, pyqtSignal,
)
from PyQt6.QtGui import QAction, QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QMenu,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import (
    DEFAULT_MODEL,
    MAX_HISTORY_TURNS,
    MPV_SOCKET,
    PROVIDERS,
    SYSTEM_PROMPT,
    MpvIPC,
    create_client,
)

SYSTEM = platform.system()
PANEL_WIDTH = 320
COLLAPSED_WIDTH = 36


def _downscale_image(path: str, max_width: int = 720):
    """Downscale a PNG to max_width using QImage (in-place)."""
    img = QImage(path)
    if not img.isNull() and img.width() > max_width:
        scaled = img.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
        scaled.save(path, "PNG")


# ---------------------------------------------------------------------------
# mpv window detection
# ---------------------------------------------------------------------------
def get_mpv_window_rect():
    """Return (x, y, w, h) of the mpv window, or None."""
    if SYSTEM == "Darwin":
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )
            for w in CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            ):
                if w.get("kCGWindowOwnerName", "") == "mpv":
                    b = w["kCGWindowBounds"]
                    return int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"])
        except ImportError:
            pass
    elif SYSTEM == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            result = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def enum_cb(hwnd, _):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if "mpv" in buf.value.lower():
                        rect = wintypes.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        result.append((
                            rect.left, rect.top,
                            rect.right - rect.left, rect.bottom - rect.top,
                        ))
                return True

            user32.EnumWindows(enum_cb, 0)
            if result:
                return result[0]
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Query worker (off UI thread)
# ---------------------------------------------------------------------------
class QueryWorker(QThread):
    finished = pyqtSignal(str, str)  # response, timestamp

    def __init__(self, state: dict, user_input: str):
        super().__init__()
        self.state = state
        self.user_input = user_input

    def run(self):
        s = self.state
        mpv: MpvIPC = s["mpv"]
        llm = s["llm"]

        try:
            t = mpv.get_time_pos()
            if t is None:
                raise ConnectionError("no time position")
        except Exception:
            self.finished.emit(
                "Not connected to mpv yet. Open a video in mpv first.", "00:00"
            )
            return

        # Capture up to 3 frames spread over 5 seconds for temporal context
        raw_ts = [max(0.0, t - 5.0), max(0.0, t - 2.5), t]
        timestamps = list(dict.fromkeys(raw_ts))  # deduplicate, preserve order
        image_paths: list[str] = []
        tmp_dir = tempfile.gettempdir()

        try:
            for i, ts in enumerate(timestamps):
                mpv.seek(ts)
                time.sleep(0.15)
                path = os.path.join(tmp_dir, f"mpv_comp_{i}_{int(ts * 1000)}.png")
                if mpv.screenshot(path):
                    _downscale_image(path)
                    image_paths.append(path)

            # Seek back to original position
            mpv.seek(t)

            mins, secs = int(t // 60), int(t % 60)
            ts_str = f"{mins:02d}:{secs:02d}"

            if not s["history"]:
                prompt = (
                    f"[System: {SYSTEM_PROMPT}]\n\n"
                    f"Film: {s['media_title']}\n"
                    f"Timestamp: {ts_str}\n\n"
                    f"{self.user_input}"
                )
            else:
                prompt = f"[{ts_str}] {self.user_input}"

            response = llm.query(prompt, image_paths or None, s["history"])
        except Exception as e:
            response = f"Error: {e}"
            mins, secs = int(t // 60), int(t % 60)
            ts_str = f"{mins:02d}:{secs:02d}"
        finally:
            for p in image_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass

        if not response.startswith("Error:"):
            s["history"].append({"role": "user", "content": prompt})
            s["history"].append({"role": "assistant", "content": response})

            max_msgs = MAX_HISTORY_TURNS * 2
            if len(s["history"]) > max_msgs:
                s["history"][:] = s["history"][-max_msgs:]

        self.finished.emit(response, ts_str)


# ---------------------------------------------------------------------------
# Draggable header
# ---------------------------------------------------------------------------
class DragHeader(QWidget):
    def __init__(self, parent_window):
        super().__init__()
        self._window = parent_window
        self._drag_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------------------
# Multi-line chat input: Enter sends, Shift+Enter inserts newline
# ---------------------------------------------------------------------------
class _ChatInput(QTextEdit):
    def __init__(self, on_submit, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit
        self.setFixedHeight(56)
        self.setAcceptRichText(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)  # insert newline
            else:
                self._on_submit()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
class CompanionPanel(QWidget):
    def __init__(self, model: str, ollama_url: str):
        super().__init__()
        self.collapsed = False
        self.worker = None
        self._connected = False

        self._provider_id = "ollama"
        self._ollama_url = ollama_url

        self.state = {
            "mpv": MpvIPC(MPV_SOCKET),
            "llm": create_client("ollama", model, base_url=ollama_url),
            "history": [],
            "media_title": "Unknown",
        }

        self._setup_window()
        self._build_ui(model, ollama_url)
        self._apply_style()

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_escape)

        # Pulsing opacity animation on header status during queries
        self._status_opacity = QGraphicsOpacityEffect(self.header_status)
        self.header_status.setGraphicsEffect(self._status_opacity)
        self._status_opacity.setOpacity(1.0)
        self._pulse_anim = QPropertyAnimation(self._status_opacity, b"opacity")
        self._pulse_anim.setDuration(900)
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setEndValue(0.3)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse_anim.finished.connect(self._pulse_reverse)

        # Snap to mpv on a timer
        self.snap_timer = QTimer()
        self.snap_timer.timeout.connect(self._snap_to_mpv)
        self.snap_timer.start(100)

        # Defer blocking calls past window show
        QTimer.singleShot(0, self._connect_mpv)
        QTimer.singleShot(0, self._refresh_models)

    def _setup_window(self):
        self.setWindowTitle("mpv Companion")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(PANEL_WIDTH, 600)

    # -- UI -----------------------------------------------------------------
    def _build_ui(self, model: str, ollama_url: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # === expanded container ===
        self.container = QWidget()
        self.container.setObjectName("container")
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(10, 0, 10, 10)
        cl.setSpacing(6)

        # header (draggable)
        self.header = DragHeader(self)
        self.header.setObjectName("header")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(0, 8, 0, 4)

        # Status text is the header text (provider + model + media)
        self.header_status = QLabel("Looking for mpv...")
        self.header_status.setObjectName("headerStatus")
        hl.addWidget(self.header_status)
        hl.addStretch()

        # Clear chat button
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedHeight(24)
        self.clear_btn.setToolTip("Clear conversation")
        self.clear_btn.clicked.connect(self._clear_chat)
        hl.addWidget(self.clear_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("headerBtn")
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._toggle_settings)
        hl.addWidget(self.settings_btn)

        self.collapse_btn = QPushButton("»")
        self.collapse_btn.setObjectName("headerBtn")
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setToolTip("Hide panel")
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        hl.addWidget(self.collapse_btn)

        cl.addWidget(self.header)

        # settings panel (hidden by default)
        self.settings_widget = QWidget()
        self.settings_widget.setObjectName("settingsPanel")
        sl = QVBoxLayout(self.settings_widget)
        sl.setContentsMargins(4, 6, 4, 6)
        sl.setSpacing(4)

        sl.addWidget(QLabel("Provider"))
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("modelCombo")
        for pid, info in PROVIDERS.items():
            label = info["name"]
            env = info["env_key"]
            if env:
                has_key = bool(os.environ.get(env, ""))
                label += "  ✓" if has_key else "  (needs setup)"
            self.provider_combo.addItem(label, pid)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        sl.addWidget(self.provider_combo)

        sl.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self.model_combo.setEditable(True)
        self.model_combo.setCurrentText(model)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        sl.addWidget(self.model_combo)

        refresh_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("refreshBtn")
        self.refresh_btn.clicked.connect(self._refresh_models)
        refresh_row.addWidget(self.refresh_btn)
        refresh_row.addStretch()
        sl.addLayout(refresh_row)

        self.url_label = QLabel("Ollama URL")
        sl.addWidget(self.url_label)
        self.url_input = QLineEdit(ollama_url)
        self.url_input.setObjectName("urlInput")
        self.url_input.editingFinished.connect(self._on_url_changed)
        sl.addWidget(self.url_input)

        self.settings_widget.hide()
        cl.addWidget(self.settings_widget)

        # chat display
        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setObjectName("chat")
        cl.addWidget(self.chat, 1)

        # input row
        inp = QHBoxLayout()
        self.input_bar = _ChatInput(on_submit=self._on_submit)
        self.input_bar.setObjectName("inputBar")
        self.input_bar.setPlaceholderText("Ask about this scene...")
        inp.addWidget(self.input_bar)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.clicked.connect(self._on_submit)
        inp.addWidget(self.send_btn)
        cl.addLayout(inp)

        root.addWidget(self.container)

        # === collapsed strip ===
        self.collapsed_strip = QWidget()
        self.collapsed_strip.setObjectName("collapsedStrip")
        self.collapsed_strip.setFixedWidth(COLLAPSED_WIDTH)
        strip_layout = QVBoxLayout(self.collapsed_strip)
        strip_layout.setContentsMargins(4, 8, 4, 8)

        expand_btn = QPushButton("«")
        expand_btn.setObjectName("headerBtn")
        expand_btn.setFixedSize(24, 24)
        expand_btn.setToolTip("Show panel")
        expand_btn.clicked.connect(self._toggle_collapse)
        strip_layout.addWidget(expand_btn)
        strip_layout.addStretch()

        self.collapsed_strip.hide()
        root.addWidget(self.collapsed_strip)

        # Disable input until connected
        self.input_bar.setReadOnly(True)
        self.send_btn.setEnabled(False)

    # -- stylesheet ---------------------------------------------------------
    def _apply_style(self):
        font = "-apple-system, 'Segoe UI', sans-serif"
        self.setStyleSheet(f"""
            * {{ font-family: {font}; }}
            #container {{
                background-color: rgba(15, 15, 20, 235);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 12);
            }}
            #collapsedStrip {{
                background-color: rgba(15, 15, 20, 235);
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 12);
            }}
            #settingsPanel {{
                background-color: rgba(255, 255, 255, 4);
                border-top: 1px solid rgba(255, 255, 255, 8);
                border-bottom: 1px solid rgba(255, 255, 255, 8);
                border-radius: 6px;
                margin: 2px 0;
            }}
            #headerStatus {{
                color: rgba(255, 255, 255, 150);
                font-size: 11px;
            }}
            QLabel {{
                color: rgba(255, 255, 255, 100);
                font-size: 11px;
            }}
            #chat {{
                background-color: rgba(0, 0, 0, 40);
                color: #d4d4d4;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                padding: 10px;
                selection-background-color: rgba(100, 180, 255, 60);
            }}
            #inputBar {{
                background-color: rgba(255, 255, 255, 6);
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 14px;
            }}
            #inputBar:focus {{
                border: 1px solid rgba(100, 180, 255, 80);
                background-color: rgba(255, 255, 255, 8);
            }}
            #inputBar[readOnly="true"] {{
                background-color: rgba(255, 255, 255, 3);
                color: rgba(200, 200, 200, 40);
                border-color: rgba(255, 255, 255, 8);
            }}
            #urlInput {{
                background-color: rgba(255, 255, 255, 6);
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 11px;
            }}
            #urlInput:focus {{
                border: 1px solid rgba(100, 180, 255, 80);
                background-color: rgba(255, 255, 255, 8);
            }}
            #modelCombo {{
                background-color: rgba(255, 255, 255, 6);
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 11px;
            }}
            #modelCombo QAbstractItemView {{
                background-color: rgb(25, 25, 30);
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 12);
                selection-background-color: rgba(100, 180, 255, 50);
                padding: 2px;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 6);
                color: rgba(255, 255, 255, 140);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 6px;
                font-size: 11px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 15);
                color: rgba(255, 255, 255, 200);
            }}
            #sendBtn {{
                background-color: rgba(100, 180, 255, 25);
                color: rgba(100, 180, 255, 220);
                border: 1px solid rgba(100, 180, 255, 40);
                font-size: 11px;
                font-weight: 600;
                padding: 6px 14px;
                border-radius: 8px;
            }}
            #sendBtn:hover {{
                background-color: rgba(100, 180, 255, 45);
            }}
            #sendBtn:disabled {{
                background-color: rgba(255, 255, 255, 3);
                color: rgba(100, 180, 255, 30);
                border-color: rgba(255, 255, 255, 8);
            }}
            #clearBtn {{
                padding: 3px 10px;
                font-size: 10px;
                color: rgba(255, 255, 255, 70);
                border: none;
            }}
            #clearBtn:hover {{
                color: rgba(255, 255, 255, 150);
            }}
            #refreshBtn {{
                padding: 5px 12px;
                font-size: 11px;
            }}
        """)

    # -- connections --------------------------------------------------------
    def _connect_mpv(self):
        try:
            self.state["mpv"].connect()
            self.state["media_title"] = self.state["mpv"].get_media_title()
            self._connected = True
            self._input_set_enabled(True)
            self.input_bar.setFocus()
            self._update_status()
            title = self.state["media_title"]
            self._append_msg(
                "assistant",
                f"Connected to {title}. Ask me anything about what you're watching.",
            )
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            self.header_status.setText(
                "mpv isn't open yet — start a video in mpv, then relaunch"
            )

    def _update_status(self):
        model = self.state["llm"].model
        title = self.state["media_title"]
        provider = PROVIDERS[self._provider_id]["name"]
        self.header_status.setText(f"▶ {title}  ·  {provider}: {model}")

    def _refresh_models(self):
        names = self.state["llm"].list_models()

        self.model_combo.blockSignals(True)
        current = self.model_combo.currentText()
        self.model_combo.clear()
        if names:
            self.model_combo.addItems(names)
            self.model_combo.setEnabled(True)
        else:
            if self._provider_id == "ollama":
                self.model_combo.addItem("No models found — is Ollama running?")
            else:
                provider_name = PROVIDERS[self._provider_id]["name"]
                self.model_combo.addItem(f"No models — check {provider_name} setup")
            self.model_combo.setEnabled(False)
        idx = self.model_combo.findText(current)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        elif current and names:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)

    def _on_provider_changed(self, index: int):
        pid = self.provider_combo.itemData(index)
        if pid == self._provider_id:
            return
        if self.worker and self.worker.isRunning():
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(
                self.provider_combo.findData(self._provider_id)
            )
            self.provider_combo.blockSignals(False)
            return

        # Close old client
        self.state["llm"].close()

        self._provider_id = pid
        # Show/hide Ollama URL field
        is_ollama = pid == "ollama"
        self.url_label.setVisible(is_ollama)
        self.url_input.setVisible(is_ollama)

        # Create new client
        if is_ollama:
            self.state["llm"] = create_client(
                "ollama", self.model_combo.currentText() or DEFAULT_MODEL,
                base_url=self._ollama_url,
            )
        else:
            self.state["llm"] = create_client(pid)

        self.state["history"].clear()
        self._refresh_models()
        self._update_status()

    def _on_model_changed(self, name: str):
        if self.worker and self.worker.isRunning():
            return
        if name and not name.startswith("No models"):
            self.state["llm"].model = name
            self._update_status()

    def _on_url_changed(self):
        if self.worker and self.worker.isRunning():
            return
        if self._provider_id != "ollama":
            return
        url = self.url_input.text().strip()
        if url:
            self._ollama_url = url.rstrip("/")
            self.state["llm"].base_url = self._ollama_url
            self._refresh_models()

    def _toggle_settings(self):
        self.settings_widget.setVisible(not self.settings_widget.isVisible())

    # -- collapse / expand --------------------------------------------------
    def _toggle_collapse(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.container.hide()
            self.collapsed_strip.show()
            self.resize(COLLAPSED_WIDTH, self.height())
        else:
            self.collapsed_strip.hide()
            self.container.show()
            self.resize(PANEL_WIDTH, self.height())
        self._snap_to_mpv()

    def _snap_to_mpv(self):
        rect = get_mpv_window_rect()
        if rect:
            x, y, w, h = rect
            pw = COLLAPSED_WIDTH if self.collapsed else PANEL_WIDTH
            self.move(x + w, y)
            self.resize(pw, h)

    # -- thinking animation -------------------------------------------------
    def _start_thinking(self):
        self.header_status.setText("Thinking...")
        self._pulse_anim.setDirection(QPropertyAnimation.Direction.Forward)
        self._pulse_anim.start()

    def _stop_thinking(self):
        self._pulse_anim.stop()
        self._status_opacity.setOpacity(1.0)
        self._update_status()

    def _pulse_reverse(self):
        # Don't restart if we were explicitly stopped (stop() emits finished too)
        if not (self.worker and self.worker.isRunning()):
            return
        if self._pulse_anim.direction() == QPropertyAnimation.Direction.Forward:
            self._pulse_anim.setDirection(QPropertyAnimation.Direction.Backward)
        else:
            self._pulse_anim.setDirection(QPropertyAnimation.Direction.Forward)
        self._pulse_anim.start()

    # -- input handling -----------------------------------------------------
    def _on_escape(self):
        self.input_bar.clear()
        self.input_bar.setFocus()

    def _input_set_enabled(self, enabled: bool):
        self.input_bar.setReadOnly(not enabled)
        self.send_btn.setEnabled(enabled)

    def _clear_chat(self):
        if self.worker and self.worker.isRunning():
            return
        self.state["history"].clear()
        self.chat.clear()
        self._update_status()

    def _on_submit(self):
        text = self.input_bar.toPlainText().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return

        self.input_bar.clear()
        self._input_set_enabled(False)
        self.clear_btn.setEnabled(False)
        self._start_thinking()

        self._append_msg("user", text)

        self.worker = QueryWorker(self.state, text)
        self.worker.finished.connect(self._on_response)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _on_response(self, response: str, ts: str):
        self._stop_thinking()
        self._append_msg("assistant", response, ts)
        self._input_set_enabled(True)
        self.clear_btn.setEnabled(True)
        self.input_bar.setFocus()

    def _append_msg(self, role: str, text: str, ts: str = ""):
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        ts_html = (
            f'<span style="color:#555555; font-weight:400;'
            f' margin-left:6px;">{ts}</span>' if ts else ""
        )

        if role == "user":
            self.chat.append(
                f'<div style="margin-top:14px; background:#131820;'
                f' border-radius:8px; padding:8px 10px;">'
                f'<div style="text-align:right; font-size:11px; color:#808080;'
                f' margin-bottom:4px;">You{ts_html}</div>'
                f'<div style="color:#d4d4d4; font-size:14px; line-height:1.6;">{escaped}</div>'
                f'</div>'
            )
        else:
            self.chat.append(
                f'<div style="margin-top:14px; border-left:2px solid #4a7a5c;'
                f' padding:4px 0 4px 10px;">'
                f'<div style="font-size:11px; color:#808080;'
                f' margin-bottom:4px;">Companion{ts_html}</div>'
                f'<div style="color:#d4d4d4; font-size:14px; line-height:1.6;">{escaped}</div>'
                f'</div>'
            )

        sb = self.chat.verticalScrollBar()
        sb.setValue(sb.maximum())

    # -- context menu -------------------------------------------------------
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: rgb(30, 30, 35); color: #e0e0e0; }
            QMenu::item:selected { background-color: rgba(136, 204, 255, 80); }
        """)
        quit_action = QAction("Quit Companion", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    # -- cleanup ------------------------------------------------------------
    def closeEvent(self, event):
        # If mpv isn't connected or panel is already collapsed, quit for real
        if not self._connected or self.collapsed:
            event.accept()
            self._quit()
            return
        # First close collapses instead
        event.ignore()
        self._toggle_collapse()

    def _quit(self):
        self.snap_timer.stop()
        self._pulse_anim.finished.disconnect(self._pulse_reverse)
        self._pulse_anim.stop()
        if self.worker and self.worker.isRunning():
            self.worker.finished.disconnect()
            self.worker.wait(3000)
        self.state["mpv"].close()
        self.state["llm"].close()
        QApplication.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="mpv AI Companion — floating panel")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    panel = CompanionPanel(model=args.model, ollama_url=args.ollama_url)
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
