"""Batch STT dialog for ThinkSub2."""

from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
)

from src.gui.magnetic import MagneticDialog
from src.gui import i18n


class BatchSttDialog(MagneticDialog):
    """Batch STT queue dialog with drag-drop list."""

    files_added = pyqtSignal(list)
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("STT 일괄 작업"))
        self.setMinimumSize(720, 300)
        self.setAcceptDrops(True)

        self._rows: dict[str, int] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([i18n.tr("파일명"), i18n.tr("진행률")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_add = QPushButton(i18n.tr("파일 추가"))
        self.btn_add.clicked.connect(self._add_files_dialog)
        btn_row.addWidget(self.btn_add)

        self.btn_start = QPushButton(i18n.tr("STT 시작"))
        self.btn_start.setCheckable(True)
        self.btn_start.toggled.connect(self._on_start_toggled)
        btn_row.addWidget(self.btn_start)

        layout.addLayout(btn_row)

    def _add_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            i18n.tr("파일 추가"),
            "",
            "Audio/Video (*.mp3 *.wav *.m4a *.mp4 *.mkv *.flac *.aac)",
        )
        if paths:
            self.add_files(paths)
            self.files_added.emit(paths)

    def _on_start_toggled(self, checked: bool):
        if checked:
            self.btn_start.setText(i18n.tr("STT 중단"))
            self.start_requested.emit()
        else:
            self.btn_start.setText(i18n.tr("STT 시작"))
            self.stop_requested.emit()

    def add_files(self, paths: Iterable[str]):
        for path in paths:
            if path in self._rows:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(path))
            self.table.setItem(row, 1, QTableWidgetItem(i18n.tr("대기")))
            self._rows[path] = row

    def update_progress(self, path: str, percent: int):
        if path not in self._rows:
            return
        row = self._rows[path]
        self.table.setItem(row, 1, QTableWidgetItem(f"{percent}%"))

    def update_status(self, path: str, status: str):
        if path not in self._rows:
            return
        row = self._rows[path]
        self.table.setItem(row, 1, QTableWidgetItem(i18n.tr(status)))

    def files(self) -> list[str]:
        return list(self._rows.keys())

    def retranslate_ui(self):
        self.setWindowTitle(i18n.tr("STT 일괄 작업"))
        self.table.setHorizontalHeaderLabels([i18n.tr("파일명"), i18n.tr("진행률")])
        self.btn_add.setText(i18n.tr("파일 추가"))
        if self.btn_start.isChecked():
            self.btn_start.setText(i18n.tr("STT 중단"))
        else:
            self.btn_start.setText(i18n.tr("STT 시작"))

    def dragEnterEvent(self, event):
        if event and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event:
            return
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        media_exts = {".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".flac", ".aac"}
        paths = [p for p in paths if p and p.lower().endswith(tuple(media_exts))]
        if paths:
            self.add_files(paths)
            self.files_added.emit(paths)
        event.acceptProposedAction()
