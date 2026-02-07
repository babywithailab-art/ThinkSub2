"""
Subtitle Editor Widget for ThinkSub2.
Supports Split/Merge/Delete with Undo/Redo.
"""

from typing import List, Optional
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QPushButton,
    QMenu,
    QStyledItemDelegate,
    QPlainTextEdit,
)
from PyQt6.QtCore import (
    pyqtSignal,
    Qt,
    QEvent,
)
from PyQt6.QtGui import QKeySequence, QShortcut, QTextOption

from src.engine.subtitle import SubtitleSegment, SubtitleManager
from src.gui import i18n
from enum import Enum, auto


class MultilineDelegate(QStyledItemDelegate):
    """Delegate for multi-line text editing in TableWidget."""

    def _sync_editor_height(self, editor, index):
        doc_height = int(editor.document().size().height())
        margins = editor.contentsMargins()
        height = doc_height + margins.top() + margins.bottom() + editor.frameWidth() * 2
        if height <= 0:
            return

        table = editor.parent()
        if isinstance(table, QTableWidget):
            current = table.rowHeight(index.row())
            table.setRowHeight(index.row(), max(current, height))

        rect = editor.geometry()
        if rect.height() < height:
            rect.setHeight(height)
            editor.setGeometry(rect)

    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setTabChangesFocus(True)  # Tab moves to next cell
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Auto-resize row on text change
        # self.parent() is the TableWidget
        table = self.parent()
        editor.textChanged.connect(lambda: self._sync_editor_height(editor, index))

        # Cursor Move -> Waveform Sync
        # table.parent() is the SubtitleEditor (QWidget)
        subtitle_editor = table.parent()
        if hasattr(subtitle_editor, "_on_editor_cursor_moved"):
            editor.cursorPositionChanged.connect(
                lambda: subtitle_editor._on_editor_cursor_moved(editor, index)
            )

        return editor

    def setEditorData(self, editor, index):
        text = index.model().data(index, Qt.ItemDataRole.DisplayRole)
        editor.setPlainText(text)
        self._sync_editor_height(editor, index)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        doc_height = int(editor.document().size().height())
        margins = editor.contentsMargins()
        height = doc_height + margins.top() + margins.bottom() + editor.frameWidth() * 2
        rect.setHeight(max(rect.height(), height))
        editor.setGeometry(rect)
        self._sync_editor_height(editor, index)

    def eventFilter(self, editor, event):
        # Handle Ctrl+Enter to finish editing
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self.commitData.emit(editor)
                self.closeEditor.emit(editor)
                return True
        return super().eventFilter(editor, event)


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
    segment_selected = pyqtSignal(str)  # segment_id
    data_changed = pyqtSignal()  # Emitted when segments are modified
    full_refresh_requested = pyqtSignal()
    segments_updated = pyqtSignal(list)
    segments_removed = pyqtSignal(list)
    segments_diff = pyqtSignal(list, list, list)  # added_ids, removed_ids, updated_ids
    playback_requested = pyqtSignal(str)  # segment_id
    playback_toggle_requested = pyqtSignal()  # Space key
    split_requested_at_cursor = pyqtSignal(str)  # Request split at waveform cursor
    editor_cursor_time_changed = pyqtSignal(float)  # Cursor moved in text editor

    def __init__(self, subtitle_manager: SubtitleManager, parent=None):
        super().__init__(parent)
        self._manager = subtitle_manager
        self._updating = False  # Prevent recursive updates
        self._current_playback_id = None
        self._playback_state = PlaybackState.IDLE

        self._setup_ui()
        self._setup_shortcuts()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Table widget
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            [
                i18n.tr("시작"),
                i18n.tr("종료"),
                i18n.tr("길이"),
                i18n.tr("재생"),
                i18n.tr("텍스트"),
            ]
        )

        # Column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 40)

        # Selection mode
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        # Signals
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Set Multiline Delegate for Text Column (4)
        self.table.setItemDelegateForColumn(4, MultilineDelegate(self.table))
        self.table.setStyleSheet(
            """
            QTableWidget::item:focus {
                outline: none;
            }
            """
        )

        layout.addWidget(self.table)

    def retranslate_ui(self):
        self.table.setHorizontalHeaderLabels(
            [
                i18n.tr("시작"),
                i18n.tr("종료"),
                i18n.tr("길이"),
                i18n.tr("재생"),
                i18n.tr("텍스트"),
            ]
        )

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Undo: Ctrl+Z
        undo_sc = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        undo_sc.activated.connect(self._on_undo)

        # Redo: Ctrl+Y or Ctrl+Shift+Z
        redo_sc = QShortcut(QKeySequence("Ctrl+Y"), self)
        redo_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        redo_sc.activated.connect(self._on_redo)

        # Delete: Delete key
        del_sc = QShortcut(QKeySequence.StandardKey.Delete, self)
        del_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        del_sc.activated.connect(self._on_delete_selected)

    def _format_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{mins:02d}:{secs:02d}.{millis:03d}"

    def _format_duration(self, seconds: float) -> str:
        if seconds < 1:
            return f"{int(seconds * 1000)}ms"
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def _full_refresh(self):
        """Full refresh from manager."""
        self._updating = True
        try:
            self.table.setRowCount(len(self._manager.segments))
            for row, segment in enumerate(self._manager.segments):
                self._set_row_data(row, segment)
            self.table.resizeRowsToContents()
        finally:
            self._updating = False
        self.data_changed.emit()

    def _refresh_table(self):
        """Refresh the table from manager."""
        self._updating = True
        try:
            self.table.setRowCount(len(self._manager.segments))
            for row, segment in enumerate(self._manager.segments):
                self._set_row_data(row, segment)
            self.table.resizeRowsToContents()
        finally:
            self._updating = False
        self.data_changed.emit()

    def _set_row_data(self, row: int, segment: SubtitleSegment):
        """Set table row data from segment."""
        # Start time
        start_item = QTableWidgetItem(self._format_time(segment.start))
        start_item.setFlags(start_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        start_item.setData(Qt.ItemDataRole.UserRole, segment.id)
        self.table.setItem(row, 0, start_item)

        # End time
        end_item = QTableWidgetItem(self._format_time(segment.end))
        end_item.setFlags(end_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 1, end_item)

        # Duration
        duration = segment.end - segment.start
        duration_item = QTableWidgetItem(self._format_duration(duration))
        duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 2, duration_item)

        # Play button (indicator)
        play_item = QTableWidgetItem("▶")
        play_item.setFlags(play_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        play_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 3, play_item)

        # Text
        text_item = QTableWidgetItem(segment.text)
        text_item.setFlags(text_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 4, text_item)

    def _get_segment_id_at_row(self, row: int) -> Optional[str]:
        if 0 <= row < self.table.rowCount():
            item = self.table.item(row, 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _get_row_of_segment(self, segment_id: str) -> int:
        for row in range(self.table.rowCount()):
            if self._get_segment_id_at_row(row) == segment_id:
                return row
        return -1

    def _on_selection_changed(self):
        """Handle selection change."""
        selected_ids = []
        for row in set(index.row() for index in self.table.selectedIndexes()):
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                selected_ids.append(seg_id)

        if len(selected_ids) == 1:
            self.segment_selected.emit(selected_ids[0])

    def _on_item_changed(self, item: QTableWidgetItem):
        """Handle item edit completion."""
        if self._updating:
            return

        row = item.row()
        col = item.column()
        seg_id = self._get_segment_id_at_row(row)

        if col == 4 and seg_id:  # Text column
            self._manager.update_text(seg_id, item.text())
            self.data_changed.emit()

    def _on_cell_clicked(self, row: int, col: int):
        """Handle cell click - playback button (col 3)."""
        if col == 3:  # Play button column
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                self._on_playback_requested(seg_id)

    def _show_context_menu(self, pos):
        """Show context menu."""
        menu = QMenu(self)

        # Get segment ID from selected row
        selected_rows = set(index.row() for index in self.table.selectedIndexes())

        split_action = menu.addAction(i18n.tr("분할 (선택 행)"))
        split_action.triggered.connect(self._on_split_selected)

        merge_action = menu.addAction(i18n.tr("병합 (선택 행)"))
        merge_action.triggered.connect(self._on_merge_selected)

        delete_action = menu.addAction(i18n.tr("삭제 (선택 행)"))
        delete_action.triggered.connect(self._on_delete_selected)

        menu.exec(self.table.mapToGlobal(pos))

    def _on_split_selected(self):
        """Split selected rows."""
        selected_rows = sorted(
            set(index.row() for index in self.table.selectedIndexes()), reverse=True
        )
        for row in selected_rows:
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                self._manager.split_segment(seg_id)
        self._refresh_table()

    def _on_merge_selected(self):
        """Merge selected rows."""
        selected_rows = sorted(
            set(index.row() for index in self.table.selectedIndexes()), reverse=True
        )
        for row in selected_rows:
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                self._manager.merge_segment(seg_id)
        self._refresh_table()

    def _on_delete_selected(self):
        """Delete selected rows."""
        selected_rows = sorted(
            set(index.row() for index in self.table.selectedIndexes()), reverse=True
        )
        removed_ids = []
        for row in selected_rows:
            seg_id = self._get_segment_id_at_row(row)
            if seg_id:
                self._manager.remove_segment(seg_id)
                removed_ids.append(seg_id)

        if removed_ids:
            self._refresh_table()
            self.segments_removed.emit(removed_ids)

    def _on_playback_requested(self, segment_id: str):
        """Handle playback request from table."""
        self.playback_requested.emit(segment_id)

    def undo(self):
        """Undo last action."""
        if self._manager.undo():
            self._refresh_table()

    def redo(self):
        """Redo last undone action."""
        if self._manager.redo():
            self._refresh_table()

    def _on_undo(self):
        self.undo()

    def _on_redo(self):
        self.redo()

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
            self.table.selectRow(row)

    def scroll_to_segment(self, segment_id: str):
        """Scroll to segment by ID."""
        row = self._get_row_of_segment(segment_id)
        if row >= 0:
            item = self.table.item(row, 0)
            if item:
                self.table.scrollToItem(item)

    def scroll_to_segment_at_y(self, segment_id: str, y: int):
        """Scroll to segment at specific Y position."""
        row = self._get_row_of_segment(segment_id)
        if row >= 0:
            item = self.table.item(row, 0)
            if item:
                # Scroll to item and adjust position
                self.table.scrollToItem(item)

    def refresh(self):
        """Refresh display (e.g. after external changes)."""
        self._refresh_table()

    def set_playback_state(self, segment_id: Optional[str], state: PlaybackState):
        """Update playback state visuals."""
        self._current_playback_id = segment_id
        self._playback_state = state
        self._refresh_table()

    def _get_segment_under_playback(self) -> Optional[SubtitleSegment]:
        """Get the segment currently under playback."""
        if self._playback_state == PlaybackState.PLAYING and self._current_playback_id:
            return self._manager.get_segment(self._current_playback_id)
        return None
