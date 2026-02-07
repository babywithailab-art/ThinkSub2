from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QLabel,
)
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QFont, QColor


class SubtitleOverlay(QWidget):
    """
    A floating, frameless window for displaying subtitles.
    Split into History (Top) and Live (Bottom).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(5, 5, 5, 5)
        self._layout.setSpacing(5)

        # Top: History (QListWidget) wrapped in a container for clean rounded corners
        self.history_container = QWidget()
        self.history_container_layout = QVBoxLayout(self.history_container)
        self.history_container_layout.setContentsMargins(6, 6, 6, 6)
        self.history_container_layout.setSpacing(2)

        self.history_list = QListWidget()
        self.history_list.setFrameShape(QListWidget.Shape.NoFrame)
        self.history_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.history_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.history_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.history_list.setWordWrap(True)
        self.history_list.setSpacing(2)
        self.history_container_layout.addWidget(self.history_list)

        # Bottom: Live (QLabel)
        self.live_label = QLabel("")
        self.live_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.live_label.setWordWrap(True)

        self._layout.addWidget(self.history_container, stretch=1)
        self._layout.addWidget(self.live_label, stretch=0)

        # Resize Grip
        from PySide6.QtWidgets import QSizeGrip

        self.grip = QSizeGrip(self)
        self.grip.setStyleSheet("background-color: transparent;")
        self.grip.resize(20, 20)

        # Default settings
        self.font_size = 25
        self.opacity = 0.8
        self.mode = 2  # 0:Top, 1:Bottom, 2:Both

        self.update_style()
        self.resize(600, 200)

        # Dragging support
        self._old_pos = None

        # Install event filters for dragging on child widgets
        self.history_container.installEventFilter(self)
        self.history_list.installEventFilter(self)
        self.live_label.installEventFilter(self)

    def eventFilter(self, source, event):
        """Handle drag events from child widgets."""
        if source in (self.history_container, self.history_list, self.live_label):
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._old_pos = event.globalPosition().toPoint()
                    # Consume event to ensure drag starts immediately (disables internal handling)
                    return True
            elif event.type() == QEvent.Type.MouseMove:
                if self._old_pos:
                    delta = event.globalPosition().toPoint() - self._old_pos
                    self.move(self.pos() + delta)
                    self._old_pos = event.globalPosition().toPoint()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._old_pos = None
                return True

        return super().eventFilter(source, event)

    def set_view_mode(self, mode: int):
        """0: Top Only, 1: Bottom Only, 2: Both"""
        self.mode = mode
        if mode == 0:
            self.history_container.show()
            self.live_label.hide()
        elif mode == 1:
            self.history_container.hide()
            self.live_label.show()
        elif mode == 2:
            self.history_container.show()
            self.live_label.show()

    def append_history(self, text: str):
        """Append finalized text to history."""
        if not text:
            return
        item = QListWidgetItem(text)
        self.history_list.addItem(item)
        self.history_list.scrollToBottom()

        while self.history_list.count() > 50:
            self.history_list.takeItem(0)

    def set_live_text(self, text: str):
        """Set current live draft text."""
        self.live_label.setText(text)

    def update_style(
        self, font_size=None, max_chars=None, max_lines=None, opacity=None
    ):
        """Updates visual capability based on settings."""
        if font_size is not None:
            self.font_size = font_size
        if opacity is not None:
            self.opacity = opacity

        alpha = int(self.opacity * 255)
        bg_color = f"rgba(0, 0, 0, {alpha})"
        text_color = "white"
        radius = "10px"
        padding = "5px"

        # Base style for container
        base_style = f"""
            background-color: {bg_color};
            color: {text_color};
            border-radius: {radius};
            padding: {padding};
        """

        # History container gets rounded background; list stays transparent
        self.history_container.setStyleSheet(f"""
            QWidget {{
                {base_style}
            }}
        """)
        self.history_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                outline: 0;
            }
            QListWidget::item {
                background: transparent;
                border: none;
                margin-bottom: 0px;
            }
        """)

        # Live Label (Identical Style)
        self.live_label.setStyleSheet(f"""
            QLabel {{
                {base_style}
            }}
        """)

        font = QFont("Arial", self.font_size, QFont.Weight.Bold)
        self.history_list.setFont(font)
        self.live_label.setFont(font)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self._old_pos:
            delta = event.globalPosition().toPoint() - self._old_pos
            self.move(self.pos() + delta)
            self._old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._old_pos = None

    def resizeEvent(self, event):
        rect = self.rect()
        self.grip.move(rect.right() - 20, rect.bottom() - 20)
        super().resizeEvent(event)
