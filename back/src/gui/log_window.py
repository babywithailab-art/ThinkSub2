"""
Log Window for ThinkSub2.
Auto-pops on Live start. Shows real-time logs from Transcriber.
"""

import os
import tempfile
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont, QTextCursor
from src.gui.magnetic import MagneticDialog
from src.gui import i18n


class LogWindow(MagneticDialog):
    """
    Log window that displays real-time transcriber and system logs.
    Features: Auto-scroll (toggle), Copy, Clear.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("ThinkSub2 - 로그"))
        self.setMinimumSize(600, 400)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)

        self._auto_scroll = True
        self._user_interacting = False
        self.log_file_path = self._init_log_file()

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # Log text area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.log_text)

        # Track user interaction for auto-scroll pause
        self.log_text.verticalScrollBar().sliderPressed.connect(
            self._on_user_scroll_start
        )
        self.log_text.verticalScrollBar().sliderReleased.connect(
            self._on_user_scroll_end
        )

        # Button bar
        btn_layout = QHBoxLayout()

        self.chk_auto_scroll = QCheckBox(i18n.tr("자동 스크롤"))
        self.chk_auto_scroll.setChecked(True)
        self.chk_auto_scroll.toggled.connect(self._on_auto_scroll_toggled)
        btn_layout.addWidget(self.chk_auto_scroll)

        btn_layout.addStretch()

        self.btn_copy = QPushButton(i18n.tr("복사"))
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        btn_layout.addWidget(self.btn_copy)

        self.btn_clear = QPushButton(i18n.tr("지우기"))
        self.btn_clear.clicked.connect(self._clear_log)
        btn_layout.addWidget(self.btn_clear)

        layout.addLayout(btn_layout)

    def retranslate_ui(self):
        self.setWindowTitle(i18n.tr("ThinkSub2 - 로그"))
        self.chk_auto_scroll.setText(i18n.tr("자동 스크롤"))
        self.btn_copy.setText(i18n.tr("복사"))
        self.btn_clear.setText(i18n.tr("지우기"))

    def _init_log_file(self) -> str:
        log_dir = os.path.join(tempfile.gettempdir(), "thinksub_logs")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(log_dir, f"thinksub_log_{timestamp}.txt")

    def _on_user_scroll_start(self):
        """Called when user starts scrolling manually."""
        self._user_interacting = True

    def _on_user_scroll_end(self):
        """Called when user stops scrolling. Resume auto-scroll after delay."""
        self._user_interacting = False

    def _on_auto_scroll_toggled(self, checked: bool):
        self._auto_scroll = checked

    @pyqtSlot(str)
    def append_log(self, message: str):
        """Append a log message. Thread-safe via signal."""
        self.log_text.append(message)

        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except OSError:
            pass

        # Auto-scroll if enabled and user is not interacting
        if self._auto_scroll and not self._user_interacting:
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)

    def _copy_to_clipboard(self):
        """Copy all log text to clipboard."""
        self.log_text.selectAll()
        self.log_text.copy()
        # Deselect
        cursor = self.log_text.textCursor()
        cursor.clearSelection()
        self.log_text.setTextCursor(cursor)

    def _clear_log(self):
        """Clear all log text."""
        self.log_text.clear()
