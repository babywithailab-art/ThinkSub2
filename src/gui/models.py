"""
Data Models for QT Views.
Implements Virtual Table logic for high performance.
"""

from PySide6.QtCore import (
    QAbstractTableModel,
    Qt,
    QModelIndex,
    Signal
)
from src.gui import i18n
from src.engine.subtitle import SubtitleManager

class SubtitleTableModel(QAbstractTableModel):
    """
    Virtual Model for Subtitle Editor.
    Reads directly from SubtitleManager.
    """
    
    # Custom constants
    COL_START = 0
    COL_END = 1
    COL_DURATION = 2
    COL_PLAY = 3
    COL_TEXT = 4
    
    def __init__(self, manager: SubtitleManager):
        super().__init__()
        self._manager = manager
        self._playback_segment_id: str | None = None
        self._headers = [
            i18n.tr("시작"),
            i18n.tr("종료"),
            i18n.tr("길이"),
            i18n.tr("재생"),
            i18n.tr("텍스트"),
        ]

    def set_playback_segment(self, segment_id: str | None):
        """Update playback state locally to refresh icon."""
        if self._playback_segment_id == segment_id:
            return
            
        old_id = self._playback_segment_id
        self._playback_segment_id = segment_id
        
        # Determine rows to update
        rows_to_update = []
        for i, seg in enumerate(self._manager.segments):
            if seg.id == old_id or seg.id == segment_id:
                rows_to_update.append(i)
                if len(rows_to_update) >= 2: break
        
        for r in rows_to_update:
            idx = self.index(r, self.COL_PLAY)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])

    def rowCount(self, parent=QModelIndex()):
        return len(self._manager.segments)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def headerData(self, section, orientation, role):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        
        if index.column() == self.COL_TEXT:
            flags |= Qt.ItemFlag.ItemIsEditable
            
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
            
        row = index.row()
        col = index.column()
        
        if row >= len(self._manager.segments):
            return None
            
        seg = self._manager.segments[row]
        
        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.COL_START:
                return self._format_time(seg.start)
            elif col == self.COL_END:
                return self._format_time(seg.end)
            elif col == self.COL_DURATION:
                return f"{seg.end - seg.start:.2f}"
            elif col == self.COL_PLAY:
                if seg.id == self._playback_segment_id:
                     return "🟥"
                return "▶"
            elif col == self.COL_TEXT:
                return seg.text

        elif role == Qt.ItemDataRole.EditRole:
            if col == self.COL_TEXT:
                return seg.text

        elif role == Qt.ItemDataRole.UserRole:
            return seg.id
            
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col == self.COL_PLAY:
                return Qt.AlignmentFlag.AlignCenter

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
            
        if role == Qt.ItemDataRole.EditRole and index.column() == self.COL_TEXT:
            row = index.row()
            if row < len(self._manager.segments):
                seg = self._manager.segments[row]
                # Use manager to update (supports Undo)
                self._manager.update_text(seg.id, str(value))
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
                return True
        return False

    def refresh(self):
        """Force full reload (e.g. after sort/filter changes externally)."""
        self.beginResetModel()
        self.endResetModel()

    def _format_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{mins:02d}:{secs:02d}.{millis:03d}"
