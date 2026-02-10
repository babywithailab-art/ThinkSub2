"""
Subtitle Editor Widget for ThinkSub2.
Supports Split/Merge/Delete with Undo/Redo.
"""

import copy
from typing import List, Optional
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableView,
    QHeaderView,
    QAbstractItemView,
    QPushButton,
    QMenu,
    QStyledItemDelegate,
    QPlainTextEdit,
    QLabel,
    QToolButton,
    QFrame,
    QSizeGrip,
)
from PySide6.QtWidgets import QStyle
from PySide6.QtCore import (
    Signal,
    Qt,
    QEvent,
    QAbstractTableModel,
    QModelIndex,
    QTimer,
    QPersistentModelIndex,
    QPoint,
    QRect,
)
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
    QTextOption,
    QColor,
    QPolygon,
    QPainter,
)

from src.engine.subtitle import SubtitleSegment, SubtitleManager
from src.engine.commands import (
    Command,
    SplitSegmentCommand,
    MergeSegmentsCommand,
    DeleteSegmentsCommand,
    UpdateTextCommand,
    GenericSnapshotCommand,
)
from src.gui import i18n
from enum import Enum, auto
from collections import deque


class SubtitleTableModel(QAbstractTableModel):
    """Table model backed by SubtitleManager.

    Designed for large row counts (200+). Uses stable segment-id list and
    supports incremental updates.
    """

    COL_START = 0
    COL_END = 1
    COL_DURATION = 2
    COL_PLAY = 3
    COL_TEXT = 4
    COL_COUNT = 5

    def __init__(self, manager: SubtitleManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._ids: list[str] = [s.id for s in self._manager.segments]
        self._playback_segment_id: Optional[str] = None

    def rowCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return self.COL_COUNT

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):  # type: ignore[override]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation != Qt.Orientation.Horizontal:
            return None
        headers = [
            i18n.tr("시작"),
            i18n.tr("종료"),
            i18n.tr("길이"),
            i18n.tr("재생"),
            i18n.tr("텍스트"),
        ]
        if 0 <= section < len(headers):
            return headers[section]
        return None

    def flags(self, index) -> Qt.ItemFlag:  # type: ignore[override]
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.column() == self.COL_TEXT:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def segment_id_at_row(self, row: int) -> Optional[str]:
        if 0 <= row < len(self._ids):
            return self._ids[row]
        return None

    def row_for_segment_id(self, segment_id: str) -> int:
        try:
            return self._ids.index(segment_id)
        except ValueError:
            return -1

    def set_playback_segment(self, segment_id: Optional[str]):
        prev = self._playback_segment_id
        self._playback_segment_id = segment_id
        # Update only affected rows
        for sid in [prev, segment_id]:
            if not sid:
                continue
            row = self.row_for_segment_id(sid)
            if row >= 0:
                top_left = self.index(row, self.COL_PLAY)
                bottom_right = self.index(row, self.COL_PLAY)
                self.dataChanged.emit(top_left, bottom_right)

    def sync_from_manager(self):
        self.beginResetModel()
        self._ids = [s.id for s in self._manager.segments]
        self.endResetModel()

    def apply_diff(self, added: list[str], removed: list[str], updated: list[str]):
        """Apply incremental updates to id list based on manager state."""
        # Remove rows (from end)
        if removed:
            for sid in sorted(
                removed, key=lambda x: self.row_for_segment_id(x), reverse=True
            ):
                row = self.row_for_segment_id(sid)
                if row < 0:
                    continue
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._ids[row]
                self.endRemoveRows()

        # Insert rows (in manager order)
        if added:
            manager_ids = [s.id for s in self._manager.segments]
            for sid in added:
                if sid not in manager_ids:
                    continue
                if sid in self._ids:
                    continue
                insert_at = manager_ids.index(sid)
                # Clamp
                insert_at = max(0, min(insert_at, len(self._ids)))
                self.beginInsertRows(QModelIndex(), insert_at, insert_at)
                self._ids.insert(insert_at, sid)
                self.endInsertRows()

        # Update rows
        if updated:
            for sid in updated:
                row = self.row_for_segment_id(sid)
                if row < 0:
                    continue
                top_left = self.index(row, 0)
                bottom_right = self.index(row, self.COL_COUNT - 1)
                self.dataChanged.emit(top_left, bottom_right)

    def data(self, index, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        segment_id = self.segment_id_at_row(index.row())
        if not segment_id:
            return None
        seg = self._manager.get_segment(segment_id)
        if not seg:
            return None

        if role == Qt.ItemDataRole.UserRole:
            return seg.id

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if index.column() == self.COL_START:
                mins = int(seg.start // 60)
                secs = int(seg.start % 60)
                millis = int((seg.start - int(seg.start)) * 1000)
                return f"{mins:02d}:{secs:02d}.{millis:03d}"
            if index.column() == self.COL_END:
                mins = int(seg.end // 60)
                secs = int(seg.end % 60)
                millis = int((seg.end - int(seg.end)) * 1000)
                return f"{mins:02d}:{secs:02d}.{millis:03d}"
            if index.column() == self.COL_DURATION:
                return f"{(seg.end - seg.start):.2f}"
            if index.column() == self.COL_PLAY:
                return "🟥" if self._playback_segment_id == seg.id else "▶"
            if index.column() == self.COL_TEXT:
                # Display text with actual line breaks (not indicators)
                text = seg.text or ""
                return text

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() in (
                self.COL_START,
                self.COL_END,
                self.COL_DURATION,
                self.COL_PLAY,
            ):
                return int(Qt.AlignmentFlag.AlignCenter)

        return None

    def setData(self, index, value, role: int = Qt.ItemDataRole.EditRole):  # type: ignore[override]
        if not index.isValid():
            return False
        if role != Qt.ItemDataRole.EditRole or index.column() != self.COL_TEXT:
            return False
        segment_id = self.segment_id_at_row(index.row())
        if not segment_id:
            return False
        seg = self._manager.get_segment(segment_id)
        if not seg:
            return False

        text = value or ""
        if isinstance(text, str):
            normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
        else:
            normalized = str(text)
        self._manager.update_text(segment_id, normalized)
        top_left = self.index(index.row(), self.COL_TEXT)
        self.dataChanged.emit(top_left, top_left)
        return True


class NoFocusDelegate(QStyledItemDelegate):
    """Delegate that removes focus indicator."""

    def paint(self, painter, option, index):
        opt = option
        # 포커스 표시 상태 제거
        opt.state &= ~QStyle.State_HasFocus
        super().paint(painter, opt, index)


class SubtitleTextEditor(QPlainTextEdit):
    """Custom QPlainTextEdit that handles Enter key for multi-line editing."""

    def __init__(self, parent=None, delegate=None):
        super().__init__(parent)
        self.delegate = delegate  # Reference to delegate for auto-save
        self._is_closing = False  # Track if we're already closing
        self._has_focus = False

    def focusOutEvent(self, event):
        """Handle focus lost: Auto-commit when user clicks elsewhere."""
        # Prevent double-close
        if self._is_closing:
            super().focusOutEvent(event)
            return

        # Only commit if there's a delegate and we're not just switching focus within * same widget
        if (
            self.delegate
            and self._has_focus
            and not event.reason()
            in (
                Qt.FocusReason.PopupFocusReason,
                Qt.FocusReason.MenuBarFocusReason,
            )
        ):
            # Mark as closing to prevent double-save
            self._is_closing = True
            # Auto-commit on focus lost (clicking elsewhere, tab away, etc.)
            self.delegate.commitData.emit(self)
            self.delegate.closeEditor.emit(self)
        self._has_focus = False
        super().focusOutEvent(event)

    def focusInEvent(self, event):
        """Track when the editor actually receives focus."""
        self._has_focus = True
        super().focusInEvent(event)


class SubtitleTextDelegate(QStyledItemDelegate):
    """Delegate for multi-line text editing without per-row auto resize.

    Keeps performance stable for large tables by avoiding resizeRowsToContents.
    """

    def __init__(self, parent=None, editor_widget=None):
        super().__init__(parent)
        self.playback_segment_id = None  # Currently playing segment ID
        self.table = parent  # Store reference to table for row resize
        self.editor_widget = (
            editor_widget  # Store reference to SubtitleEditor for _calculate_row_height
        )

    def paint(self, painter, option, index):
        """Custom paint to highlight currently playing segment."""
        # Check if this row is the currently playing segment
        segment_id = index.model().data(index, Qt.ItemDataRole.UserRole)
        is_playing = segment_id == self.playback_segment_id

        if is_playing:
            # Draw playback highlight background
            painter.save()
            painter.fillRect(
                option.rect, QColor(255, 215, 0, 100)
            )  # Golden yellow with alpha
            painter.restore()

        # Call parent paint to draw text
        super().paint(painter, option, index)

    def createEditor(self, parent, option, index):
        editor = SubtitleTextEditor(parent, delegate=self)
        editor.setTabChangesFocus(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Install event filter to handle Enter key
        editor.installEventFilter(self)
        editor._delegate_index = QPersistentModelIndex(index)
        if self.table is not None:
            editor._initial_row_height = self.table.rowHeight(index.row())
        editor.textChanged.connect(lambda: self._on_editor_text_changed(editor))
        editor.setPlainText("")
        return editor

    def setEditorData(self, editor, index):
        # For EditRole, get text without line break indicators
        text = index.model().data(index, Qt.ItemDataRole.EditRole) or ""
        # Convert Windows line endings to Unix for editing
        text = text.replace("\r\n", "\n")
        # Remove line break indicators if present
        if " ¶ " in text:
            text = text.replace(" ¶ ", "\n")
        editor.setPlainText(text)

    def updateEditorGeometry(self, editor, option, index):
        """Update editor geometry to match row height."""
        # Get the calculated row height for this row
        if self.editor_widget and self.table:
            text = index.model().data(index, Qt.ItemDataRole.EditRole) or ""
            # Convert to Unix line endings for counting
            text = text.replace("\r\n", "\n")
            height = self.editor_widget._calculate_row_height(text)
            # Set editor height to match row height
            editor.setFixedHeight(height)
        super().updateEditorGeometry(editor, option, index)

    def setModelData(self, editor, model, index):
        text = editor.toPlainText()
        # Remove line break indicators before saving and convert to Windows line endings
        if text:
            # Replace line break indicators with \r\n
            if " ¶ " in text:
                text = text.replace(" ¶ ", "\r\n")
            # Convert \n to \r\n for Windows line endings
            # But don't double-convert if already has \r\n
            text = text.replace("\n", "\r\n")
            # Fix double \r\r\n that might occur from conversion
            text = text.replace("\r\r\n", "\r\n")
        model.setData(index, text, Qt.ItemDataRole.EditRole)
        # Resize row to fit new text content (2x the line count)
        if self.table and self.editor_widget:
            row = index.row()
            height = self.editor_widget._calculate_row_height(text)
            self.table.setRowHeight(row, height)

    def _on_editor_text_changed(self, editor: SubtitleTextEditor) -> None:
        """Keep manager and row height synced while editing."""
        idx = getattr(editor, "_delegate_index", None)
        if not idx or not idx.isValid():
            return
        row = idx.row()
        text = editor.toPlainText()
        normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
        if self.table:
            target_height = self.editor_widget._calculate_row_height(normalized)
            initial_height = getattr(editor, "_initial_row_height", target_height)
            height = max(initial_height, target_height)
            self.table.setRowHeight(row, height)

    def eventFilter(self, editor, event):
        """Handle Enter key: Insert newline (Enter) or commit (Ctrl+Enter)."""
        # Check if editor is our SubtitleTextEditor and not already closing
        if isinstance(editor, SubtitleTextEditor) and hasattr(editor, "_is_closing"):
            if editor._is_closing:
                # Editor is already closing, ignore events
                return super().eventFilter(editor, event)

        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    # Ctrl+Enter: Commit and close editor
                    if hasattr(editor, "_is_closing"):
                        editor._is_closing = True
                    self.commitData.emit(editor)
                    self.closeEditor.emit(editor)
                    return True
                else:
                    # Enter: Insert newline, don't close editor
                    # Let the editor handle the key press normally
                    # But prevent the delegate from closing the editor
                    return False  # False means we don't handle it, but the event continues to editor
        return super().eventFilter(editor, event)


class SubtitlePopupEditor(QFrame):
    """Floating overlay for editing subtitle text outside the table."""

    text_saved = Signal(QPersistentModelIndex, str)
    playback_requested = Signal(str)
    prev_requested = Signal()
    next_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._index: Optional[QPersistentModelIndex] = None
        self._segment_id: Optional[str] = None
        self._owner_editor: Optional["SubtitleEditor"] = None
        self._is_dirty = False
        self._drag_offset: Optional[QPoint] = None
        self._manual_position: Optional[QPoint] = None
        self._last_position: Optional[QPoint] = None

        self.setStyleSheet(
            """
            QFrame {
                background-color: rgba(24, 26, 34, 0.96);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
            QPushButton { border: none; color: white; }
            QPlainTextEdit { background-color: transparent; color: white; border: none; }
            """
        )

        self._play_button = QPushButton("▶")
        self._play_button.setCheckable(True)
        self._play_button.clicked.connect(self._on_play_clicked)

        close_button = QToolButton()
        close_button.setText("✕")
        close_button.clicked.connect(self.close)
        close_button.setCursor(Qt.PointingHandCursor)

        header = QHBoxLayout()
        header.addWidget(self._play_button)
        header.addStretch()
        header.addWidget(close_button)

        self._info_label = QLabel()
        self._info_label.setStyleSheet("color: #d1d5db;")

        self._text_edit = QPlainTextEdit()
        self._text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._text_edit.textChanged.connect(self._on_text_changed)

        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(6)
        self._prev_button = QPushButton("< 이전행")
        self._prev_button.clicked.connect(lambda: self.prev_requested.emit())
        self._next_button = QPushButton("다음행 >")
        self._next_button.clicked.connect(lambda: self.next_requested.emit())
        nav_layout.addWidget(self._prev_button)
        nav_layout.addStretch()
        nav_layout.addWidget(self._next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addLayout(nav_layout)
        layout.addWidget(self._info_label)
        layout.addWidget(self._text_edit)
        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(16, 16)
        layout.addWidget(
            self._size_grip,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        )
        self.setMinimumSize(320, 180)

    def open(
        self,
        index: QModelIndex,
        text: str,
        info: str,
        playing: bool,
        owner: Optional["SubtitleEditor"] = None,
    ) -> None:
        self._index = QPersistentModelIndex(index)
        self._segment_id = index.model().data(index, Qt.ItemDataRole.UserRole)
        self._is_dirty = False
        self._owner_editor = owner
        self._info_label.setText(info)
        self._play_button.setChecked(playing)
        self._play_button.setText("⏹" if playing else "▶")
        self._text_edit.setPlainText(text or "")
        self._text_edit.setFocus()
        self._text_edit.selectAll()
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            pos = event.globalPosition().toPoint() - self._drag_offset
            self._manual_position = pos
            self._last_position = pos
            self.move(pos)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        self._commit_text()
        self._last_position = self.pos()
        super().closeEvent(event)

    def _on_text_changed(self):
        self._is_dirty = True

    def _commit_text(self) -> None:
        if not self._index or not self._is_dirty:
            return
        text = self._text_edit.toPlainText()
        self.text_saved.emit(self._index, text)
        self._is_dirty = False

    def save_and_hide(self) -> None:
        if self.isVisible():
            self._commit_text()
            self._last_position = self.pos()
            self.hide()

    def set_playback_state(self, playing: bool) -> None:
        self._play_button.setChecked(playing)
        self._play_button.setText("⏹" if playing else "▶")

    def update_playback_indicator(self, active_segment_id: Optional[str]) -> None:
        self.set_playback_state(
            active_segment_id is not None and active_segment_id == self._segment_id
        )

    def _on_play_clicked(self):
        if self._segment_id:
            self.playback_requested.emit(self._segment_id)

    @property
    def current_index(self) -> Optional[QPersistentModelIndex]:
        return self._index

    @property
    def owner_editor(self) -> Optional["SubtitleEditor"]:
        return self._owner_editor


class PlayButtonDelegate(QStyledItemDelegate):
    """Custom delegate to paint and interact with playback buttons."""

    def __init__(self, parent=None, editor_widget=None):
        super().__init__(parent)
        self.table = parent
        self.editor_widget = editor_widget
        self.playback_segment_id = None

    def paint(self, painter, option, index):
        segment_id = index.model().data(index, Qt.ItemDataRole.UserRole)
        is_playing = segment_id == self.editor_widget._playback_segment_id
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        bg_color = (
            QColor(34, 197, 94, 200) if is_playing else QColor(156, 163, 175, 170)
        )
        painter.setBrush(bg_color)
        size = min(option.rect.width(), option.rect.height()) - 12
        size = max(size, 16)
        center = option.rect.center()
        circle_rect = QRect(
            center.x() - size // 2,
            center.y() - size // 2,
            size,
            size,
        )
        painter.drawEllipse(circle_rect)
        painter.setBrush(Qt.white)
        if is_playing:
            bar_width = max(size * 0.16, 4)
            bar_height = size * 0.5
            stop_rect = QRect(
                int(center.x() - bar_width / 2),
                int(center.y() - bar_height / 2),
                int(bar_width),
                int(bar_height),
            )
            painter.drawRect(stop_rect)
        else:
            triangle = QPolygon(
                [
                    QPoint(
                        circle_rect.left() + size * 0.2, circle_rect.top() + size * 0.15
                    ),
                    QPoint(
                        circle_rect.left() + size * 0.2,
                        circle_rect.bottom() - size * 0.15,
                    ),
                    QPoint(circle_rect.right() - size * 0.15, circle_rect.center().y()),
                ]
            )
            painter.drawPolygon(triangle)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            segment_id = model.data(index, Qt.ItemDataRole.UserRole)
            if segment_id and self.editor_widget:
                self.editor_widget.playback_requested.emit(segment_id)
                if self.table and self.table.selectionModel() is not None:
                    self.table.scrollTo(
                        index, QAbstractItemView.ScrollHint.PositionAtCenter
                    )
                return True
        return False


class PlaybackState(Enum):
    IDLE = auto()
    PLAYING = auto()
    DISABLED = auto()


class SubtitleEditor(QWidget):
    """
    Subtitle editor with table view.
    Supports: Selection sync, Split/Merge/Delete, Undo/Redo.
    """

    # Signals
    segment_selected = Signal(str)  # segment_id
    data_changed = Signal()  # Emitted when segments are modified
    full_refresh_requested = Signal()
    segments_updated = Signal(list)
    segments_removed = Signal(list)
    segments_diff = Signal(list, list, list)  # added_ids, removed_ids, updated_ids
    playback_requested = Signal(str)  # segment_id
    playback_toggle_requested = Signal()  # Space key
    split_requested_at_cursor = Signal(str)  # Request split at waveform cursor
    editor_cursor_time_changed = Signal(float)  # Cursor moved in text editor
    merge_requested = Signal(list)  # Request merge of segment_ids
    text_edit_requested = Signal(QPersistentModelIndex)

    def __init__(self, subtitle_manager: SubtitleManager, parent=None):
        super().__init__(parent)
        self._manager = subtitle_manager
        self._updating = False  # Prevent recursive updates
        self._playback_segment_id = None
        self._playback_state = PlaybackState.IDLE
        self._is_programmatic_scroll = False  # Prevent scroll loops

        # Command stacks for Undo/Redo
        self._undo_stack: deque[Command] = deque(maxlen=50)
        self._redo_stack: deque[Command] = deque(maxlen=50)

        self._setup_ui()
        self._setup_shortcuts()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Table view + model (optimized for 200+ rows)
        self.table = QTableView()
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(True)  # Enable word wrap for text display
        # Some type stubs miss this API; guard for safety.
        if hasattr(self.table, "setUniformRowHeights"):
            self.table.setUniformRowHeights(False)  # Allow different row heights
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_text_cell_double_clicked)

        self._model = SubtitleTableModel(self._manager, self.table)
        self.table.setModel(self._model)
        self._text_delegate = SubtitleTextDelegate(self.table, self)
        self._play_delegate = PlayButtonDelegate(self.table, self)
        self.table.setItemDelegateForColumn(
            SubtitleTableModel.COL_TEXT, self._text_delegate
        )
        self.table.setItemDelegateForColumn(
            SubtitleTableModel.COL_PLAY, self._play_delegate
        )

        # Connect model data changes to adjust row heights
        self._model.dataChanged.connect(self._on_model_data_changed)

        # Adjust row heights after model is loaded
        QTimer.singleShot(
            100, lambda: self._adjust_all_row_heights()
        )  # Delayed call to ensure model is loaded

        # Apply NoFocusDelegate to all columns (including non-text columns)
        self.table.setItemDelegate(NoFocusDelegate(self.table))

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            SubtitleTableModel.COL_START, QHeaderView.ResizeMode.Fixed
        )
        header.setSectionResizeMode(
            SubtitleTableModel.COL_END, QHeaderView.ResizeMode.Fixed
        )
        header.setSectionResizeMode(
            SubtitleTableModel.COL_DURATION, QHeaderView.ResizeMode.Fixed
        )
        header.setSectionResizeMode(
            SubtitleTableModel.COL_PLAY, QHeaderView.ResizeMode.Fixed
        )
        header.setSectionResizeMode(
            SubtitleTableModel.COL_TEXT, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(SubtitleTableModel.COL_START, 80)
        self.table.setColumnWidth(SubtitleTableModel.COL_END, 80)
        self.table.setColumnWidth(SubtitleTableModel.COL_DURATION, 60)
        self.table.setColumnWidth(SubtitleTableModel.COL_PLAY, 40)

        # Enable manual row resizing (interactive mode)
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.verticalHeader().setDefaultSectionSize(40)  # Default height (1x)

        # Signals
        if self.table.selectionModel() is not None:
            self.table.selectionModel().selectionChanged.connect(
                lambda *_: self._on_selection_changed()
            )
        # Keep selected row visible when scrolling
        self._last_selected_index = None
        self.table.selectionModel().selectionChanged.connect(
            self._on_selection_changed_store
        )
        scrollbar = self.table.verticalScrollBar()
        if scrollbar is not None:
            # Only restore selection after scrolling, don't interfere with scrolling itself
            scrollbar.sliderReleased.connect(self._on_scroll_released)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Install event filter for space key handling
        self.table.installEventFilter(self)

        layout.addWidget(self.table)

    def retranslate_ui(self):
        # Trigger header redraw
        if hasattr(self, "_model"):
            self._model.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 0, SubtitleTableModel.COL_COUNT - 1
            )

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Undo: Ctrl+Z
        undo_sc = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo_sc.activated.connect(self._on_undo)

        # Redo: Ctrl+Y or Ctrl+Shift+Z
        redo_sc = QShortcut(QKeySequence("Ctrl+Y"), self)
        redo_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        redo_sc.activated.connect(self._on_redo)

        # Delete: Delete key
        del_sc = QShortcut(QKeySequence.StandardKey.Delete, self)
        del_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        del_sc.activated.connect(self.delete_selected)

    def eventFilter(self, obj, event):
        """Handle space key for playback toggle."""
        if obj == self.table and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Space:
                # Toggle playback for the selected row
                self._on_space_pressed()
                return True
        return super().eventFilter(obj, event)

    def _on_space_pressed(self):
        """Toggle playback for the last selected row."""
        # Get the last selected row index
        if not self._last_selected_index or not self._last_selected_index.isValid():
            return

        row = self._last_selected_index.row()
        if row < 0:
            return

        # Get segment ID at the row
        seg_id = self._get_segment_id_at_row(row)
        if not seg_id:
            return

        # Emit playback request
        self.playback_requested.emit(seg_id)

    def _format_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{mins:02d}:{secs:02d}.{millis:03d}"

    def _format_duration(self, seconds: float) -> str:
        return f"{seconds:.2f}"

    def _calculate_row_height(self, text: str) -> int:
        """Calculate row height based on text line count (1x the line count)."""
        # Handle both Windows (\r\n) and Unix (\n) line breaks
        # Replace \r\n with \n first to handle Windows line endings, then count \n
        if text:
            normalized_text = text.replace("\r\n", "\n")
            line_count = normalized_text.count("\n") + 1
        else:
            line_count = 1

        # Base height per line (including padding)
        base_line_height = 20  # pixels per line
        new_height = line_count * base_line_height
        min_height = 40  # Minimum height (1 line * 1 * 40)
        max_height = 200  # Maximum height to prevent excessively tall rows
        final_height = max(min_height, min(new_height, max_height))
        return final_height

    def _adjust_all_row_heights(self):
        """Adjust all row heights based on text content."""
        if not self.table:
            return

        for row in range(self._model.rowCount()):
            seg_id = self._model.segment_id_at_row(row)
            if seg_id:
                segment = self._manager.get_segment(seg_id)
                if segment:
                    height = self._calculate_row_height(segment.text or "")
                    self.table.setRowHeight(row, height)

    def _on_model_data_changed(self, top_left, bottom_right, roles):
        """Handle model data changes to adjust row heights."""
        # Only adjust if text column was changed
        if SubtitleTableModel.COL_TEXT in range(
            top_left.column(), bottom_right.column() + 1
        ):
            if (
                Qt.ItemDataRole.EditRole in roles
                or Qt.ItemDataRole.DisplayRole in roles
            ):
                for row in range(top_left.row(), bottom_right.row() + 1):
                    seg_id = self._model.segment_id_at_row(row)
                    if seg_id:
                        segment = self._manager.get_segment(seg_id)
                        if segment:
                            height = self._calculate_row_height(segment.text or "")
                            self.table.setRowHeight(row, height)

    def _full_refresh(self):
        """Full refresh from manager."""
        self._updating = True
        try:
            self._model.sync_from_manager()
        finally:
            self._updating = False
        self.data_changed.emit()

    def refresh(self):
        """Public alias for full refresh."""
        self._full_refresh()

    def _refresh_table(self):
        """Refresh the table from manager (Alias)."""
        self._full_refresh()

    def _get_segment_id_at_row(self, row: int) -> Optional[str]:
        return self._model.segment_id_at_row(row)

    def _get_row_of_segment(self, segment_id: str) -> int:
        return self._model.row_for_segment_id(segment_id)

    def _set_playback_segment(self, segment_id: Optional[str]):
        """Update the currently playing segment and refresh UI."""
        self._playback_segment_id = segment_id

        # Update delegate's playback segment ID
        if hasattr(self, "_text_delegate"):
            self._text_delegate.playback_segment_id = segment_id
        if hasattr(self, "_play_delegate"):
            self._play_delegate.playback_segment_id = segment_id

            # Find the row of the segment and repaint just that row
            if segment_id:
                row = self._model.row_for_segment_id(segment_id)
                if row >= 0:
                    top_left = self._model.index(row, 0)
                    bottom_right = self._model.index(
                        row, SubtitleTableModel.COL_COUNT - 1
                    )
                    # Repaint the entire row
                    self.table.viewport().update()
            else:
                # Clear all playback highlighting
                self.table.viewport().update()

    def _on_selection_changed(self):
        """Handle selection change."""
        selected_ids: list[str] = []
        sm = self.table.selectionModel()
        if sm is None:
            return
        for idx in sm.selectedRows():
            seg_id = self._get_segment_id_at_row(idx.row())
            if seg_id:
                selected_ids.append(seg_id)

        if len(selected_ids) == 1:
            self.segment_selected.emit(selected_ids[0])

    def _on_selection_changed_store(self):
        """Store the last selected index to maintain selection during scroll."""
        sm = self.table.selectionModel()
        if sm is None:
            return
        selected = sm.selectedRows()
        if selected:
            self._last_selected_index = selected[0]
        else:
            self._last_selected_index = None

    def _on_scroll_value_changed(self, value):
        """Handle scroll value changes - don't interfere with scrolling."""
        pass  # Let the user scroll freely

    def _on_scroll_released(self):
        """Restore selection visibility after scrolling."""
        # Only restore if there's a selection and it's not visible
        if self._last_selected_index and self._last_selected_index.isValid():
            rect = self.table.visualRect(self._last_selected_index)
            if rect.isEmpty() or not self.table.rect().contains(rect):
                # Selected row is not visible, scroll to it
                self._is_programmatic_scroll = True
                self.table.scrollTo(
                    self._last_selected_index,
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )
                # Restore selection
                sm = self.table.selectionModel()
                if sm is not None:
                    sm.clearSelection()
                    sm.select(self._last_selected_index, sm.SelectionFlag.Select)
                self._is_programmatic_scroll = False

    def _show_context_menu(self, pos):
        """Show context menu."""
        menu = QMenu(self)

        split_action = menu.addAction(i18n.tr("분할 (선택 행)"))
        split_action.triggered.connect(self.split_selected)

        merge_action = menu.addAction(i18n.tr("병합 (선택 행)"))
        merge_action.triggered.connect(self.merge_selected)

        delete_action = menu.addAction(i18n.tr("삭제 (선택 행)"))
        delete_action.triggered.connect(self.delete_selected)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_text_cell_double_clicked(self, index: QModelIndex):
        if not index.isValid():
            return
        if index.column() != SubtitleTableModel.COL_TEXT:
            return
        self.text_edit_requested.emit(QPersistentModelIndex(index))

    def split_selected(self):
        """Split selected rows using cursor position."""
        # Use the standard split logic which relies on the waveform cursor
        self._split_at_cursor()

    def merge_selected(self):
        """Merge selected rows."""
        sm = self.table.selectionModel()
        if sm is None:
            return
        selected_rows = sorted({idx.row() for idx in sm.selectedRows()})
        if len(selected_rows) < 2:
            return

        ids_to_merge = []
        for row in selected_rows:
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                ids_to_merge.append(seg_id)

        if ids_to_merge:
            self.merge_requested.emit(ids_to_merge)

    def delete_selected(self):
        """Delete selected rows using Command pattern."""
        sm = self.table.selectionModel()
        if sm is None:
            return
        selected_rows = sorted({idx.row() for idx in sm.selectedRows()}, reverse=True)
        ids_to_remove = []
        for row in selected_rows:
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                ids_to_remove.append(seg_id)

        if ids_to_remove:
            # Create and execute delete command
            command = DeleteSegmentsCommand(self._manager, ids_to_remove)
            self.execute_command(command)
            self.segments_removed.emit(ids_to_remove)

    def undo(self):
        """Undo last action using Command pattern."""
        if not self._undo_stack:
            return

        old_segments = copy.deepcopy(self._manager.segments)

        # Pop command from undo stack and execute undo
        command = self._undo_stack.pop()
        command.undo()

        # Push to redo stack
        self._redo_stack.append(command)

        added, removed, updated = self._calculate_and_emit_diff(
            old_segments, self._manager.segments, emit=True
        )
        self._model.apply_diff(added, removed, updated)

    def execute_command(self, command: Command) -> bool:
        """Execute a command and add it to undo stack.

        This method is called by main_window to execute split/merge/delete commands.
        """
        old_segments = copy.deepcopy(self._manager.segments)

        # Execute the command
        success = command.execute()

        if success:
            # Push to undo stack and clear redo stack
            self._undo_stack.append(command)
            self._redo_stack.clear()

            added, removed, updated = self._calculate_and_emit_diff(
                old_segments, self._manager.segments, emit=True
            )
            self._model.apply_diff(added, removed, updated)

        return success

    def redo(self):
        """Redo last undone action using Command pattern."""
        if not self._redo_stack:
            return

        old_segments = copy.deepcopy(self._manager.segments)

        # Pop command from redo stack and execute redo
        command = self._redo_stack.pop()
        command.redo()

        # Push back to undo stack
        self._undo_stack.append(command)

        added, removed, updated = self._calculate_and_emit_diff(
            old_segments, self._manager.segments, emit=True
        )
        self._model.apply_diff(added, removed, updated)

    def _calculate_and_emit_diff(self, old_segments, new_segments, emit: bool):
        """Calculate diff between two segment lists.

        Returns: (added_ids, removed_ids, updated_ids)
        """
        old_map = {s.id: s for s in old_segments}
        new_map = {s.id: s for s in new_segments}

        added = []
        removed = []
        updated = []

        # Check for added and updated
        for s in new_segments:
            if s.id not in old_map:
                added.append(s.id)
            else:
                # Check if changed (start/end/text)
                old_s = old_map[s.id]
                if (
                    abs(old_s.start - s.start) > 0.001
                    or abs(old_s.end - s.end) > 0.001
                    or old_s.text != s.text
                ):
                    updated.append(s.id)

        # Check for removed
        for s in old_segments:
            if s.id not in new_map:
                removed.append(s.id)

        if emit and (added or removed or updated):
            self.segments_diff.emit(added, removed, updated)

        return added, removed, updated

    def _on_undo(self):
        # Context-aware Undo
        # If editing text, undo text. If not, undo segment changes.
        from PySide6.QtWidgets import QApplication, QPlainTextEdit, QLineEdit

        focus_widget = QApplication.focusWidget()

        if isinstance(focus_widget, (QPlainTextEdit, QLineEdit)):
            # We are editing text
            focus_widget.undo()
        else:
            # We are in navigation mode
            self.undo()

    def _on_redo(self):
        self.redo()

    def update_playback_status(self, segment_id: str, is_playing: bool):
        """Update visual playback status (play icon)."""
        new_segment_id = segment_id if is_playing else None
        self._playback_segment_id = new_segment_id
        self._model.set_playback_segment(self._playback_segment_id)

        # Update playback highlight in editor
        self._set_playback_segment(new_segment_id)

    def _clear_playback_highlight(self, segment_id: str):
        """Backward-compatible no-op (icons handled by model)."""
        self._model.set_playback_segment(self._playback_segment_id)

    def _on_editor_cursor_moved(self, editor, index):
        """Handle cursor movement in text editor - emit time for sync."""
        if hasattr(editor, "textCursor"):
            # Get the segment start time for this row
            row = index.row() if index else 0
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                segment = self._manager.get_segment(seg_id)
                if segment:
                    self.editor_cursor_time_changed.emit(segment.start)

    def select_segment(self, segment_id: str):
        """Select a segment by ID."""
        row = self._get_row_of_segment(segment_id)
        if row >= 0:
            idx = self._model.index(row, 0)
            sm = self.table.selectionModel()
            if sm is not None:
                sm.select(idx, sm.SelectionFlag.ClearAndSelect | sm.SelectionFlag.Rows)
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def scroll_to_segment(self, segment_id: str):
        """Scroll to segment by ID."""
        row = self._get_row_of_segment(segment_id)
        if row >= 0:
            idx = self._model.index(row, 0)
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def scroll_to_segment_at_y(self, segment_id: str, y: int):
        """Scroll to segment at specific Y position."""
        row = self._get_row_of_segment(segment_id)
        if row >= 0:
            # QTableView doesn't support pixel-precise targeting easily; best-effort center scroll.
            _ = y
            idx = self._model.index(row, 0)
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def refresh(self):
        """Refresh display (e.g. after external changes)."""
        self._refresh_table()

    def set_playback_state(self, segment_id: Optional[str], state: PlaybackState):
        """Update playback state visuals."""
        self._playback_segment_id = (
            segment_id if state == PlaybackState.PLAYING else None
        )
        self._model.set_playback_segment(self._playback_segment_id)
        self._set_playback_segment(self._playback_segment_id)

    def _update_playback_icon(self, row: int, state: PlaybackState):
        """Legacy no-op (icons handled by model)."""
        _ = row
        _ = state

    def _get_segment_under_playback(self):
        """Get the segment currently under playback."""
        if self._playback_segment_id:
            return self._manager.get_segment(self._playback_segment_id)
        return None

    def update_single_segment(self, segment):
        """Update a specific row for the given segment."""
        row = self._get_row_of_segment(segment.id)
        if row >= 0:
            top_left = self._model.index(row, 0)
            bottom_right = self._model.index(row, SubtitleTableModel.COL_COUNT - 1)
            self._model.dataChanged.emit(top_left, bottom_right)

    def insert_segment_at(self, segment):
        """Insert a new row for the given segment."""
        # Segment already exists in manager. Insert into model based on manager order.
        self._model.apply_diff([segment.id], [], [])

    def _split_at_cursor(self):
        """Handle UI action to split at cursor."""
        # Get selected segment
        idx = self.table.currentIndex()
        if not idx.isValid():
            return

        row = idx.row()
        segment_id = self._get_segment_id_at_row(row)
        if segment_id:
            self.split_requested_at_cursor.emit(segment_id)
