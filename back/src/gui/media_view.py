"""Internal media player view for ThinkSub2."""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QRectF, QSizeF, QEvent, QSize, QTimer
from PyQt6.QtGui import QAction, QFont, QColor, QBrush, QTextOption, QPen
from PyQt6.QtWidgets import (
    QGraphicsView,
    QGraphicsScene,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QMenu,
    QMainWindow,
    QWidget,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

from src.engine.subtitle import SubtitleManager


class MediaView(QGraphicsView):
    """Media view with QGraphicsVideoItem and text overlays."""

    time_changed = pyqtSignal(float)  # seconds
    playback_toggle_requested = pyqtSignal()
    debug_log = pyqtSignal(str)

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__(parent)

        os.environ.setdefault("QT_FFMPEG_HWACCEL", "none")

        self._right: Optional[SubtitleManager] = None
        self._left: Optional[SubtitleManager] = None
        self._show_left = True

        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._current_path: Optional[str] = None
        self._media_loaded = False
        self._debug_last_log_time = -1.0

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QBrush(QColor(0, 0, 0)))
        self.viewport().setStyleSheet("background-color: black;")
        self.viewport().installEventFilter(self)

        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)
        self._player.setVideoOutput(self._video_item)

        self._subtitle_top_bg, self._subtitle_top = self._make_subtitle_items()
        self._subtitle_bottom_bg, self._subtitle_bottom = self._make_subtitle_items()
        self._scene.addItem(self._subtitle_top_bg)
        self._scene.addItem(self._subtitle_top)
        self._scene.addItem(self._subtitle_bottom_bg)
        self._scene.addItem(self._subtitle_bottom)

        self._last_viewport_size = QSize(0, 0)
        self._last_layout_log = QSize(0, 0)
        self._layout_timer = QTimer(self)
        self._layout_timer.setInterval(200)
        self._layout_timer.timeout.connect(self._poll_viewport_resize)
        self._layout_timer.start()

        self._font_size = 18
        self._bg_alpha = 160
        self._bg_color = QColor(0, 0, 0, self._bg_alpha)
        self.update_style()

        self._player.positionChanged.connect(self._on_position_changed)

    def _make_subtitle_items(self):
        bg = QGraphicsRectItem(QRectF(0, 0, 0, 0))
        bg.setPen(QPen(Qt.PenStyle.NoPen))
        text = QGraphicsTextItem("")
        text.setDefaultTextColor(QColor("white"))
        text.document().setDefaultTextOption(QTextOption(Qt.AlignmentFlag.AlignHCenter))
        return bg, text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = QRectF(self.viewport().rect())
        self.setSceneRect(rect)
        self._update_video_layout()
        self._layout_subtitles()

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._layout_timer.stop()

    def _poll_viewport_resize(self):
        size = self.viewport().size()
        if size != self._last_viewport_size:
            self._last_viewport_size = size
            rect = QRectF(self.viewport().rect())
            self.setSceneRect(rect)
            self._update_video_layout()
            self._layout_subtitles()
            self.debug_log.emit(
                f"[MediaView] viewport resized: {size.width()}x{size.height()}"
            )

    def _update_video_layout(self):
        rect = QRectF(self.viewport().rect())
        target_w = 854
        target_h = 480
        if rect.width() <= 0 or rect.height() <= 0:
            return
        scale = min(rect.width() / target_w, rect.height() / target_h)
        width = target_w * scale
        height = target_h * scale
        x = (rect.width() - width) / 2.0
        y = (rect.height() - height) / 2.0
        self._video_item.setSize(QSizeF(width, height))
        self._video_item.setPos(x, y)

    def eventFilter(self, obj, event):
        if obj is self.viewport() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.PolishRequest,
        ):
            self._layout_subtitles()
        return super().eventFilter(obj, event)

    def set_media(self, path: str):
        url = QUrl.fromLocalFile(path)
        if self._current_path == path and self._media_loaded:
            self._player.pause()
            return
        self._player.setSource(url)
        self._current_path = path
        self._media_loaded = True
        self._player.pause()

    def ensure_media_loaded(self, path: str):
        if not path:
            return
        if self._current_path == path and self._media_loaded:
            return
        self.set_media(path)

    def set_muted(self, muted: bool):
        self._audio.setMuted(muted)

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def set_position(self, ms: int):
        self._player.setPosition(ms)
        self._on_position_changed(ms)

    def set_time(self, seconds: float):
        if seconds < 0:
            seconds = 0.0
        self._on_position_changed(int(seconds * 1000))

    def mousePressEvent(self, event):
        if event and event.button() == Qt.MouseButton.LeftButton:
            self.playback_toggle_requested.emit()
        super().mousePressEvent(event)

    def update_style(
        self, font_size: int = 18, opacity: float = 0.6, bg_color: str = "0, 0, 0"
    ):
        self._font_size = int(font_size)
        alpha = int(max(0.0, min(1.0, opacity)) * 255)
        parts = [int(p.strip()) for p in bg_color.split(",") if p.strip()]
        if len(parts) < 3:
            parts = [0, 0, 0]
        self._bg_color = QColor(parts[0], parts[1], parts[2], alpha)

        font = QFont()
        font.setPixelSize(int(self._font_size))
        self._subtitle_top.setFont(font)
        self._subtitle_bottom.setFont(font)
        self._subtitle_top.setDefaultTextColor(QColor("white"))
        self._subtitle_bottom.setDefaultTextColor(QColor("white"))
        self._layout_subtitles()

    def set_managers(
        self, right: SubtitleManager, left: Optional[SubtitleManager] = None
    ):
        self._right = right
        self._left = left

    def player(self) -> QMediaPlayer:
        return self._player

    def _pick_text_at_time(self, mgr: Optional[SubtitleManager], t: float) -> str:
        if mgr is None:
            return ""
        for seg in mgr.segments:
            if getattr(seg, "is_hidden", False):
                continue
            if seg.start <= t <= seg.end:
                return (seg.text or "").strip()
        return ""

    def _layout_subtitles(self):
        rect = QRectF(self.viewport().rect())
        padding_x = 12
        padding_y = 4
        font_px = max(12, int(rect.height() * 0.045))
        font = QFont()
        font.setPixelSize(font_px)
        self._subtitle_top.setFont(font)
        self._subtitle_bottom.setFont(font)
        bottom_margin = 18
        line_gap = 4

        self._subtitle_bottom.setTextWidth(-1)
        bottom_bounds = self._subtitle_bottom.boundingRect()
        bottom_x = (rect.width() - bottom_bounds.width()) / 2.0
        bottom_y = rect.height() - bottom_margin - bottom_bounds.height()
        self._subtitle_bottom.setPos(bottom_x, bottom_y)

        bottom_bg_rect = QRectF(
            bottom_x - padding_x,
            bottom_y - padding_y,
            bottom_bounds.width() + padding_x * 2,
            bottom_bounds.height() + padding_y * 2,
        )
        self._subtitle_bottom_bg.setRect(bottom_bg_rect)
        self._subtitle_bottom_bg.setBrush(QBrush(self._bg_color))

        self._subtitle_top.setTextWidth(-1)
        top_bounds = self._subtitle_top.boundingRect()
        top_x = (rect.width() - top_bounds.width()) / 2.0
        top_y = bottom_y - line_gap - top_bounds.height()
        self._subtitle_top.setPos(top_x, top_y)

        top_bg_rect = QRectF(
            top_x - padding_x,
            top_y - padding_y,
            top_bounds.width() + padding_x * 2,
            top_bounds.height() + padding_y * 2,
        )
        self._subtitle_top_bg.setRect(top_bg_rect)
        self._subtitle_top_bg.setBrush(QBrush(self._bg_color))

        if rect.size().toSize() != self._last_layout_log:
            self._last_layout_log = rect.size().toSize()
            self.debug_log.emit(
                f"[MediaView] layout size={rect.width():.0f}x{rect.height():.0f} font_px={font_px}"
            )

    def _set_subtitle_text(self, text_item, bg_item, text: str) -> None:
        text_item.setPlainText(text)
        visible = bool(text)
        text_item.setVisible(visible)
        bg_item.setVisible(visible)
        if visible:
            base_font = QFont(text_item.font())
            base_font.setPointSize(self._font_size)
            text_item.setFont(base_font)
        self._layout_subtitles()

    def _on_position_changed(self, ms: int):
        t = float(ms) / 1000.0
        self.time_changed.emit(t)

        right_text = self._pick_text_at_time(self._right, t)
        left_text = self._pick_text_at_time(self._left, t) if self._show_left else ""

        self._set_subtitle_text(
            self._subtitle_bottom, self._subtitle_bottom_bg, right_text
        )
        self._set_subtitle_text(self._subtitle_top, self._subtitle_top_bg, left_text)

        if self._debug_last_log_time < 0 or abs(t - self._debug_last_log_time) >= 1.0:
            self._debug_last_log_time = t
            self.debug_log.emit(
                f"[MediaView] t={t:.2f} right_len={len(right_text)} left_len={len(left_text)}"
            )

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_show_left = QAction("좌측 자막 표시", self)
        act_show_left.setCheckable(True)
        act_show_left.setChecked(self._show_left)
        act_show_left.triggered.connect(self._toggle_show_left)
        menu.addAction(act_show_left)
        if event:
            menu.exec(event.globalPos())

    def _toggle_show_left(self, checked: bool):
        self._show_left = bool(checked)
        self._on_position_changed(int(self._player.position()))


class MediaViewWindow(QMainWindow):
    """A simple top-level window wrapper for MediaView."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("미디어뷰")
        self._view = MediaView(self)
        self.setCentralWidget(self._view)

    def view(self) -> MediaView:
        return self._view
