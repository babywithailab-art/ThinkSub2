"""
Main Window for ThinkSub2.
Handles Live button logic, layout, and component orchestration.
"""

import time
import os
import hashlib
import tempfile
import re
import uuid
from src.gui.batch_stt_dialog import BatchSttDialog
from src.gui import i18n
from enum import Enum, auto
from typing import Optional, Any, cast
import json
import threading
import subprocess
import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QToolBar,
    QPushButton,
    QLabel,
    QStatusBar,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QDockWidget,
    QApplication,
    QPlainTextEdit,
    QLineEdit,
    QTextEdit,
    QAbstractItemView,
    QProgressDialog,
)
from PyQt6.QtCore import (
    Qt,
    QTimer,
    pyqtSlot,
    QSettings,
    pyqtSignal,
    QEvent,
    QMetaObject,
)
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtGui import (
    QAction,
    QIcon,
    QKeySequence,
    QKeyEvent,
    QShortcut,
    QDragEnterEvent,
    QDropEvent,
    QCloseEvent,
)

from src.engine.subtitle import SubtitleManager, SubtitleSegment, Word, SegmentStatus
from src.engine.audio import AudioRecorder, VADProcessor, AudioChunk
from src.engine.transcriber import WhisperTranscriberProcess, TranscribeResult

from src.gui.editor import SubtitleEditor
from src.gui.waveform import WaveformWidget
from src.gui.log_window import LogWindow
from src.gui.settings import SettingsDialog
from src.gui.overlay import SubtitleOverlay
from src.gui.editor import PlaybackState
from src.gui.media_view import MediaViewWindow


class AppState(Enum):
    """Application state machine."""

    IDLE = auto()
    LOADING = auto()  # Model loading
    READY = auto()  # Model loaded, waiting to record
    RECORDING = auto()  # Live transcription active


class MainWindow(QMainWindow):
    """
    Main window for ThinkSub2.
    Orchestrates all components and handles Live button logic.
    """

    waveform_audio_loaded = pyqtSignal(object)  # np.ndarray
    media_proxy_ready = pyqtSignal(str, str)

    DEFAULT_ABBREV_WHITELIST = [
        "mr.",
        "mrs.",
        "ms.",
        "miss.",
        "dr.",
        "prof.",
        "jr.",
        "sr.",
        "a.m.",
        "p.m.",
        "etc.",
        "vs.",
        "e.g.",
        "i.e.",
        "ph.d.",
        "m.d.",
        "u.s.",
        "u.k.",
        "e.u.",
    ]

    def eventFilter(self, a0, a1):
        if isinstance(a1, QKeyEvent) and a1.type() == QEvent.Type.KeyPress:
            focus = QApplication.focusWidget()
            if isinstance(focus, QPlainTextEdit) or isinstance(focus, QLineEdit):
                return False
            if self._settings_dialog and self._settings_dialog.isVisible():
                return False

            if a1.key() == Qt.Key.Key_Space:
                if self._active_waveform:
                    self._toggle_active_waveform(self._active_waveform)
                return True

            if a1.matches(QKeySequence.StandardKey.Undo):
                active = self._get_active_editor()
                active.undo()
                return True
        return super().eventFilter(a0, a1)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ThinkSub2")
        self.setMinimumSize(1200, 700)
        self.setAcceptDrops(True)

        # State
        self._state = AppState.IDLE

        self._subtitle_manager = SubtitleManager()
        self._file_subtitle_manager = SubtitleManager()  # Manager for File STT
        self._audio_recorder = AudioRecorder()
        self._subtitle_manager = SubtitleManager()
        self._audio_recorder = AudioRecorder()

        # Load VAD settings
        settings = QSettings("ThinkSub", "ThinkSub2")
        vad_threshold = float(settings.value("vad_threshold", 0.02))
        vad_silence = float(settings.value("vad_silence_duration", 0.5))

        # Post-Processing Settings
        self._min_text_length = int(settings.value("min_text_length", 0))
        self._rms_threshold = float(settings.value("rms_threshold", 0.002))
        self._enable_post_processing = (
            str(settings.value("enable_post_processing", "true")).lower() == "true"
        )
        self._live_abbrev_whitelist = self._load_abbrev_whitelist(
            settings, "live_abbrev_whitelist"
        )
        self._stt_abbrev_whitelist = self._load_abbrev_whitelist(
            settings, "stt_abbrev_whitelist"
        )
        self._stt_seg_endmin = float(settings.value("stt_seg_endmin", 0.05))
        self._stt_extend_on_touch = (
            str(settings.value("stt_extend_on_touch", "false")).lower() == "true"
        )
        self._stt_pad_before = float(settings.value("stt_pad_before", 0.1))
        self._stt_pad_after = float(settings.value("stt_pad_after", 0.1))
        self._live_wordtimestamp_offset = float(
            settings.value("live_wordtimestamp_offset", 0.0)
        )
        self._live_pad_before = float(settings.value("live_pad_before", 0.1))
        self._live_pad_after = float(settings.value("live_pad_after", 0.1))

        self._vad_processor = VADProcessor(
            threshold=vad_threshold, min_silence_duration=vad_silence
        )
        self._transcriber = WhisperTranscriberProcess()

        # Subtitle formatting/splitting params (fw_*)
        self._fw_sentence = bool(
            str(settings.value("fw_sentence", "true")).lower() == "true"
        )
        self._fw_max_gap = float(settings.value("fw_max_gap", 0.8))
        self._fw_max_line_width = int(settings.value("fw_max_line_width", 55))
        self._fw_max_line_count = int(settings.value("fw_max_line_count", 2))
        self._fw_max_comma_cent = int(settings.value("fw_max_comma_cent", 70))
        self._fw_one_word = int(settings.value("fw_one_word", 0))

        # Windows
        self._log_window: Optional[LogWindow] = None
        self._settings_dialog: Optional[SettingsDialog] = None
        self._media_dock: Optional[QDockWidget] = None
        self._media_view: Optional[Any] = None

        # Subtitle Overlay
        self.overlay = SubtitleOverlay()

        # Waveform display mode: top / bottom / split
        self._waveform_mode = "top"
        self._active_waveform: Optional[WaveformWidget] = None

        # File STT state
        self._selected_media_file: Optional[str] = None
        self._file_stt_running = False
        self._transcriber_ready = False
        self._pending_file_transcribe: Optional[str] = None

        # Timers
        self._result_timer = QTimer()
        self._result_timer.timeout.connect(self._poll_results)

        self._log_timer = QTimer()
        self._log_timer.timeout.connect(self._poll_logs)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(80)
        self._pending_status = None
        self._status_timer.timeout.connect(self._flush_status)

        self._media_sync_timer = QTimer(self)
        self._media_sync_timer.setInterval(100)
        self._media_sync_timer.timeout.connect(self._sync_media_time_from_waveform)

        self._media_srt_update_timer = QTimer(self)
        self._media_srt_update_timer.setSingleShot(True)
        self._media_srt_update_timer.setInterval(400)
        self._media_srt_update_timer.timeout.connect(self._update_media_srt_and_proxy)

        self._scroll_sync_active = False
        self._scroll_sync_lock_until = 0.0
        self._scroll_sync_source = None
        self._scroll_sync_debug = False
        self._last_cursor_sync_time = 0.0
        self._last_cursor_sync_t = None
        self._cursor_sync_only = False
        self._scroll_sync_time = None
        self._scroll_sync_pending = None
        self._scroll_sync_timer = QTimer(self)
        self._scroll_sync_timer.setSingleShot(True)
        self._scroll_sync_timer.timeout.connect(self._flush_scroll_sync)
        self._last_active_editor = None
        self._playback_active = False
        self._suppress_cursor_sync_until = 0.0
        self._suppress_selection_sync_until = 0.0
        self._suppress_selection_sync_once = False
        self._waveform_load_dialog = None

        # Live Update State
        self._last_live_update_time = 0.0
        self._live_update_interval = 0.5  # Update every 500ms while speaking
        # self._anchor_timestamp = None     # REMOVED: Pure Frame-Based Sync uses absolute time from AudioRecorder
        self._first_speech_detected = False  # Flag for Virtual Silence Chunk

        # Recording Session Audio Collection
        self._current_session_audio: list = []  # Collect audio chunks during recording
        self._current_session_wav_path: Optional[str] = None  # Path to saved WAV file

        self._setup_ui()
        self._setup_connections()
        self._media_proxy_tasks = set()
        self._media_pending_play = False
        self._media_pending_seek: Optional[float] = None
        self._media_debug_logged = False
        self._media_srt_path: Optional[str] = None
        self._use_media_proxy = False
        self._media_sync_debug_last = -1.0
        self._preview_proxy_path: Optional[str] = None
        self._batch_dialog: Optional[BatchSttDialog] = None
        self._batch_queue: list[str] = []
        self._batch_running = False
        self._batch_cancel_requested = False
        self._batch_current_file: Optional[str] = None

        # Global undo/redo shortcuts (work regardless of table/text focus)
        undo_sc = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        undo_sc.activated.connect(lambda: self._get_active_editor().undo())

        redo_sc = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        redo_sc.activated.connect(lambda: self._get_active_editor().redo())

        # Global spacebar playback toggle (ignored while typing in text fields)
        space_sc = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)

        def _toggle_playback_if_allowed():
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit)):
                return
            if self._active_waveform:
                self._toggle_active_waveform(self._active_waveform)

        space_sc.activated.connect(_toggle_playback_if_allowed)

        # Global Space handling (except when editing text)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

        self.waveform_audio_loaded.connect(self._on_waveform_audio_loaded)
        self.media_proxy_ready.connect(self._on_media_proxy_ready)

        # Apply dark theme
        self._apply_theme()

        # Enable Drag & Drop
        self.setAcceptDrops(True)

        # Apply initial overlay settings
        self._update_overlay_settings()

    def _update_overlay_settings(self):
        settings = QSettings("ThinkSub", "ThinkSub2")
        self.overlay.update_style(
            font_size=int(settings.value("subtitle_font_size", 25)),
            max_chars=int(settings.value("subtitle_max_chars", 40)),
            max_lines=int(settings.value("subtitle_max_lines", 2)),
            opacity=float(settings.value("subtitle_opacity", 80)) / 100.0,
        )
        # Also update pp settings
        self._min_text_length = int(settings.value("min_text_length", 0))
        self._min_duration = float(settings.value("min_duration", 0.0))
        self._max_duration = float(settings.value("max_duration", 29.9))
        self._rms_threshold = float(settings.value("rms_threshold", 0.002))
        self._enable_post_processing = (
            str(settings.value("enable_post_processing", "true")).lower() == "true"
        )
        self._live_abbrev_whitelist = self._load_abbrev_whitelist(
            settings, "live_abbrev_whitelist"
        )
        self._stt_abbrev_whitelist = self._load_abbrev_whitelist(
            settings, "stt_abbrev_whitelist"
        )
        self._stt_seg_endmin = float(settings.value("stt_seg_endmin", 0.05))
        self._stt_extend_on_touch = (
            str(settings.value("stt_extend_on_touch", "false")).lower() == "true"
        )
        self._stt_pad_before = float(settings.value("stt_pad_before", 0.1))
        self._stt_pad_after = float(settings.value("stt_pad_after", 0.1))
        self._live_wordtimestamp_offset = float(
            settings.value("live_wordtimestamp_offset", 0.0)
        )
        self._live_pad_before = float(settings.value("live_pad_before", 0.1))
        self._live_pad_after = float(settings.value("live_pad_after", 0.1))

        # fw_* settings used for local split/wrap
        self._fw_sentence = bool(
            str(settings.value("fw_sentence", "true")).lower() == "true"
        )
        self._fw_max_gap = float(settings.value("fw_max_gap", 0.8))
        self._fw_max_line_width = int(settings.value("fw_max_line_width", 55))
        self._fw_max_line_count = int(settings.value("fw_max_line_count", 2))
        self._fw_max_comma_cent = int(settings.value("fw_max_comma_cent", 70))
        self._fw_one_word = int(settings.value("fw_one_word", 0))

    def _normalize_abbrev_list(self, value) -> list[str]:
        if value is None:
            return []
        items = []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    items = parsed
                else:
                    items = [value]
            except Exception:
                items = [value]
        elif isinstance(value, (list, tuple)):
            items = list(value)
        else:
            items = [value]

        normalized = []
        seen = set()
        for item in items:
            text = str(item).strip().lower()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _load_abbrev_whitelist(self, settings: QSettings, key: str) -> list[str]:
        raw = settings.value(key, None)
        if raw is None:
            return list(self.DEFAULT_ABBREV_WHITELIST)
        return self._normalize_abbrev_list(raw)

    def _get_fw_format_config(self, mode: str = "live") -> dict:
        """Get effective formatting config; extra JSON overrides fw_* if present."""
        prefix = f"fw_{mode}_"

        settings = QSettings("ThinkSub", "ThinkSub2")

        cfg = {
            "sentence": bool(
                str(settings.value(f"{prefix}sentence", "true")).lower() == "true"
            ),
            "max_gap": float(settings.value(f"{prefix}max_gap", 0.8)),
            "max_line_width": int(settings.value(f"{prefix}max_line_width", 55)),
            "max_line_count": int(settings.value(f"{prefix}max_line_count", 2)),
            "max_comma_cent": int(settings.value(f"{prefix}max_comma_cent", 70)),
            "one_word": int(settings.value(f"{prefix}one_word", 0)),
        }

        # extra params override if same key exists
        try:
            raw = settings.value("faster_whisper_params", "{}")
            extra = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(extra, dict):
                for k in (
                    "sentence",
                    "max_gap",
                    "max_line_width",
                    "max_line_count",
                    "max_comma_cent",
                    "one_word",
                ):
                    if k in extra:
                        cfg[k] = extra[k]
        except Exception:
            pass

        return cfg

    def _pick_text_at_time(self, mgr: SubtitleManager, t: float) -> str:
        for seg in mgr.segments:
            if getattr(seg, "is_hidden", False):
                continue
            if seg.start <= t <= seg.end:
                return (seg.text or "").strip()
        return ""

    def _wrap_text(
        self, text: str, max_width: int, max_lines: int, max_comma_cent: int
    ) -> str:
        """Simple line wrapping for SRT-style multiline output."""
        t = (text or "").strip()
        if not t:
            return ""
        if max_width <= 0 or max_lines <= 0:
            return t

        def split_long_no_space(s: str) -> list[str]:
            return [s[i : i + max_width] for i in range(0, len(s), max_width)]

        lines: list[str] = []
        if " " in t:
            words = t.split()
            cur = ""
            for w in words:
                cand = w if not cur else f"{cur} {w}"
                if len(cand) <= max_width:
                    cur = cand
                else:
                    if cur:
                        lines.append(cur)
                        cur = w
                    else:
                        # single token too long
                        parts = split_long_no_space(w)
                        lines.extend(parts[:-1])
                        cur = parts[-1]
            if cur:
                lines.append(cur)
        else:
            lines = split_long_no_space(t)

        # Optional comma-based split: if a comma is very late in a line, split there
        if max_comma_cent > 0:
            out: list[str] = []
            for line in lines:
                if len(out) >= max_lines:
                    out.append(line)
                    continue
                comma_pos = max(line.rfind(","), line.rfind("，"))
                if comma_pos > 0:
                    pct = int((comma_pos / max(1, len(line))) * 100)
                    if pct >= max_comma_cent and len(out) + 1 < max_lines:
                        out.append(line[: comma_pos + 1].rstrip())
                        rest = line[comma_pos + 1 :].strip()
                        if rest:
                            out.append(rest)
                        continue
                out.append(line)
            lines = out

        if len(lines) > max_lines:
            head = lines[: max_lines - 1]
            tail = " ".join(lines[max_lines - 1 :]).strip()
            lines = head + ([tail] if tail else [])

        return "\n".join(lines)

    def _format_srt_time(self, seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        ms_total = int(round(seconds * 1000))
        ms = ms_total % 1000
        total_seconds = ms_total // 1000
        s = total_seconds % 60
        total_minutes = total_seconds // 60
        m = total_minutes % 60
        h = total_minutes // 60
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _parse_srt_time(self, value: str) -> Optional[float]:
        match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", value.strip())
        if not match:
            return None
        h, m, s, ms = match.groups()
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    def _parse_srt_file(self, path: str) -> list[SubtitleSegment]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            return []

        blocks = re.split(r"\n\s*\n", content.strip())
        segments: list[SubtitleSegment] = []
        for block in blocks:
            lines = [ln.strip("\r") for ln in block.splitlines() if ln.strip()]
            if len(lines) < 2:
                continue
            time_line = lines[1] if "-->" in lines[1] else lines[0]
            if "-->" not in time_line:
                continue
            start_str, end_str = [p.strip() for p in time_line.split("-->")]
            start = self._parse_srt_time(start_str)
            end = self._parse_srt_time(end_str)
            if start is None or end is None:
                continue
            text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
            text = "\n".join(text_lines).strip()
            if not text:
                continue
            segments.append(
                SubtitleSegment(
                    start=start, end=end, text=text, status=SegmentStatus.FINAL
                )
            )
        return segments

    def _write_srt_file(self, path: str, segments: list[SubtitleSegment]) -> bool:
        try:
            with open(path, "w", encoding="utf-8") as f:
                idx = 1
                for seg in sorted(segments, key=lambda s: s.start):
                    if getattr(seg, "is_hidden", False):
                        continue
                    f.write(f"{idx}\n")
                    f.write(
                        f"{self._format_srt_time(seg.start)} --> {self._format_srt_time(seg.end)}\n"
                    )
                    f.write((seg.text or "").strip() + "\n\n")
                    idx += 1
            return True
        except OSError:
            return False

    def _get_media_srt_path(self, src_path: str) -> str:
        base = os.path.abspath(src_path)
        digest = hashlib.md5(base.encode("utf-8")).hexdigest()
        proxy_dir = os.path.join(tempfile.gettempdir(), "thinksub_proxy")
        os.makedirs(proxy_dir, exist_ok=True)
        return os.path.join(proxy_dir, f"subs_{digest}.srt")

    def _ffmpeg_escape_filter_path(self, path: str) -> str:
        norm = path.replace("\\", "/")
        norm = norm.replace(":", "\\:")
        return norm

    def _schedule_media_srt_update(self):
        if not self._use_media_proxy:
            return
        if not self._selected_media_file:
            return
        if self._log_window:
            self._log_window.append_log("[MediaProxy] SRT update scheduled")
        self._media_srt_update_timer.start()

    def _update_media_srt_and_proxy(self):
        if not self._use_media_proxy:
            return
        if not self._selected_media_file:
            return
        segments = list(self._file_subtitle_manager.segments)
        if not segments:
            if self._log_window:
                self._log_window.append_log(
                    "[MediaProxy] SRT update skipped: no segments"
                )
            return
        srt_path = self._get_media_srt_path(self._selected_media_file)
        if not self._write_srt_file(srt_path, segments):
            if self._log_window:
                self._log_window.append_log("[MediaProxy] SRT write failed")
            return
        self._media_srt_path = srt_path
        if self._log_window:
            self._log_window.append_log(f"[MediaProxy] SRT updated: {srt_path}")
        proxy_path = self._ensure_media_proxy_async(self._selected_media_file, srt_path)
        if (
            proxy_path
            and self._media_view
            and self._media_dock
            and self._media_dock.isVisible()
        ):
            self._media_view.ensure_media_loaded(proxy_path)

    def _split_final_by_words(
        self, base: SubtitleSegment, cfg: dict
    ) -> list[SubtitleSegment]:
        """Split a FINAL segment using word timings + cfg."""
        words = list(base.words or [])
        if not words:
            base.text = self._wrap_text(
                base.text,
                int(cfg.get("max_line_width", 55)),
                int(cfg.get("max_line_count", 2)),
                int(cfg.get("max_comma_cent", 70)),
            )
            return [base]

        one_word = int(cfg.get("one_word", 0))
        if one_word == 1:
            out: list[SubtitleSegment] = []
            for w in words:
                out.append(
                    SubtitleSegment(
                        start=w.start,
                        end=w.end,
                        text=w.text.strip(),
                        words=[w],
                        status=SegmentStatus.FINAL,
                    )
                )
            return out

        max_gap = float(cfg.get("max_gap", 0.8))
        sentence = bool(cfg.get("sentence", True))
        end_punct = (".", "?", "!", "。", "？", "！", "…")

        breaks: set[int] = set()
        for i in range(len(words) - 1):
            gap = words[i + 1].start - words[i].end
            if max_gap > 0 and gap > max_gap:
                breaks.add(i)
            if sentence:
                txt = (words[i].text or "").strip()
                if txt.endswith(end_punct):
                    breaks.add(i)

        max_w = int(cfg.get("max_line_width", 55))
        max_l = int(cfg.get("max_line_count", 2))
        max_comma_cent = int(cfg.get("max_comma_cent", 70))

        out: list[SubtitleSegment] = []
        start_idx = 0
        for i in range(len(words)):
            if i == len(words) - 1 or i in breaks:
                part = words[start_idx : i + 1]
                text = "".join([w.text for w in part]).strip()
                text = self._wrap_text(text, max_w, max_l, max_comma_cent)
                out.append(
                    SubtitleSegment(
                        start=part[0].start,
                        end=part[-1].end,
                        text=text,
                        words=part,
                        status=SegmentStatus.FINAL,
                    )
                )
                start_idx = i + 1

        return out

    def _setup_ui(self):
        """Setup the main UI layout."""
        central = QWidget()
        self._central_widget = central
        central.setAcceptDrops(True)
        central.installEventFilter(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_status("준비")

        # Vertical splitter for Waveform (top) and Editors (bottom)
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)

        # 1. Waveform panel (top/bottom)
        self.waveform_panel = QWidget()
        waveform_panel_layout = QVBoxLayout(self.waveform_panel)
        waveform_panel_layout.setContentsMargins(0, 0, 0, 0)
        waveform_panel_layout.setSpacing(2)

        self.waveform_splitter = QSplitter(Qt.Orientation.Vertical)
        self.waveform_left = WaveformWidget(scrollbar_position="top")
        self.waveform_right = WaveformWidget()
        self.waveform_left.setMinimumHeight(120)
        self.waveform_right.setMinimumHeight(120)
        self.waveform_splitter.addWidget(self.waveform_left)
        self.waveform_splitter.addWidget(self.waveform_right)
        self.waveform_splitter.setSizes([140, 140])

        waveform_panel_layout.addWidget(self.waveform_splitter)
        self.main_splitter.addWidget(self.waveform_panel)

        # Back-compat alias (live/default)
        self.waveform = self.waveform_left

        # 2. Editors
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.live_editor = SubtitleEditor(self._subtitle_manager)
        self.editor_splitter.addWidget(self.live_editor)

        self.file_editor = SubtitleEditor(self._file_subtitle_manager)
        self.editor_splitter.addWidget(self.file_editor)
        self.file_editor.setAcceptDrops(True)
        self.file_editor.installEventFilter(self)
        if hasattr(self.file_editor, "table"):
            self.file_editor.table.setAcceptDrops(True)
            self.file_editor.table.installEventFilter(self)

        self._right_drop_overlay = QWidget(self.file_editor)
        self._right_drop_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._right_drop_overlay.setStyleSheet(
            "background-color: rgba(255, 255, 255, 120);"
        )
        self._right_drop_overlay.hide()

        self.editor_splitter.setSizes([500, 500])
        self.main_splitter.addWidget(self.editor_splitter)

        # Initial sizes: Waveform small (150), Editors big (remainder)
        self.main_splitter.setSizes([150, 600])

        # 3. Toolbar (Now safe)
        self._create_toolbar()

        # Apply waveform mode visibility
        self._apply_waveform_mode()

        # 4. Layout
        main_layout.addWidget(self.main_splitter)

        def _update_drop_overlay():
            if not hasattr(self, "file_editor"):
                return
            rect = self.file_editor.rect()
            self._right_drop_overlay.setGeometry(rect)
            self._right_drop_overlay.raise_()

        self.editor_splitter.splitterMoved.connect(
            lambda _a, _b: _update_drop_overlay()
        )
        QTimer.singleShot(0, _update_drop_overlay)

        # Auto-open settings on start
        QTimer.singleShot(100, self._show_settings)

        # Start Audio for monitoring (Settings calibration)
        # We start device 0 by default, Settings can change it.
        # self._audio_recorder.start() -> Wait, user might want to select device first.
        # But we need to start it to get RMS.
        # Let's start it.
        try:
            self._audio_recorder.start()
        except:
            pass  # Device might be missing

    def _apply_waveform_mode(self):
        """Apply current waveform mode to UI."""
        show_all = self.btn_waveform.isChecked()
        self.waveform_panel.setVisible(show_all)
        if not show_all:
            return

        if self._waveform_mode == "top":
            self.waveform_left.show()
            self.waveform_right.hide()
        elif self._waveform_mode == "bottom":
            self.waveform_left.hide()
            self.waveform_right.show()
            self.waveform_right.render_full_session()
        else:
            self.waveform_left.show()
            self.waveform_right.show()
            self.waveform_right.render_full_session()

    def _cycle_waveform_mode(self):
        """Cycle waveform display modes: top -> bottom -> split."""
        if self._waveform_mode == "top":
            self._waveform_mode = "bottom"
            if hasattr(self, "btn_waveform_mode"):
                self.btn_waveform_mode.setText(i18n.tr("↕ 웨이브폼 하단"))
        elif self._waveform_mode == "bottom":
            self._waveform_mode = "split"
            if hasattr(self, "btn_waveform_mode"):
                self.btn_waveform_mode.setText(i18n.tr("↕ 웨이브폼 분할"))
        else:
            self._waveform_mode = "top"
            if hasattr(self, "btn_waveform_mode"):
                self.btn_waveform_mode.setText(i18n.tr("↕ 웨이브폼 상단"))

        self._apply_waveform_mode()

    def _create_toolbar(self):
        """Create the main toolbar."""
        toolbar_top = QToolBar("표시줄")
        toolbar_top.setMovable(False)
        self.addToolBar(toolbar_top)

        self.addToolBarBreak()

        toolbar_bottom = QToolBar("표시줄 2")
        toolbar_bottom.setMovable(False)
        self.addToolBar(toolbar_bottom)

        # Row 1 (top): Live자막 화면전환 웨이브폼 웨이브폼 상단(모드) 스크롤 CC:전체 내보내기 STT실행 미디어뷰 파일열기 설정
        self.btn_live = QPushButton("▶ Live 자막")
        self.btn_live.setText(i18n.tr("▶ Live 자막"))
        self.btn_live.setCheckable(True)
        self.btn_live.setStyleSheet("""
            QPushButton {
                background-color: #22c55e;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:checked {
                background-color: #ef4444;
            }
            QPushButton:hover {
                background-color: #16a34a;
            }
            QPushButton:checked:hover {
                background-color: #dc2626;
            }
            QPushButton:disabled {
                background-color: #6b7280;
                color: #d1d5db;
                border: none;
            }
        """)
        self.btn_live.clicked.connect(self._on_live_clicked)
        toolbar_top.addWidget(self.btn_live)

        self.btn_view = QPushButton("📐 화면전환")
        self.btn_view.setText(i18n.tr("📐 화면전환"))
        self.btn_view.clicked.connect(self._toggle_view)
        toolbar_top.addWidget(self.btn_view)
        self._update_view_button_text()

        self.btn_waveform = QPushButton("📊 웨이브폼")
        self.btn_waveform.setText(i18n.tr("📊 웨이브폼"))
        self.btn_waveform.setCheckable(True)
        self.btn_waveform.setChecked(True)
        self.btn_waveform.clicked.connect(self._toggle_waveform)
        toolbar_top.addWidget(self.btn_waveform)

        # Waveform mode toggle: Top -> Bottom -> Split
        self.btn_waveform_mode = QPushButton("↕ 웨이브폼 상단")
        self.btn_waveform_mode.setText(i18n.tr("↕ 웨이브폼 상단"))
        self.btn_waveform_mode.setToolTip(
            "웨이브폼 표시 모드: 상단/하단/분할을 번갈아 전환합니다"
        )
        self.btn_waveform_mode.clicked.connect(self._cycle_waveform_mode)
        toolbar_top.addWidget(self.btn_waveform_mode)

        # Scroll sync toggle (time-based)
        self.btn_sync = QPushButton("🔗 스크롤")
        self.btn_sync.setText(i18n.tr("🔗 스크롤"))
        self.btn_sync.setCheckable(True)
        self.btn_sync.setChecked(True)
        self.btn_sync.setToolTip("좌우 에디터 스크롤을 시간 기준으로 동기화합니다")
        toolbar_top.addWidget(self.btn_sync)

        self.btn_overlay = QPushButton("CC: 전체")
        self.btn_overlay.setText(i18n.tr("CC: 전체"))
        self.btn_overlay.clicked.connect(self._toggle_overlay_mode)
        toolbar_top.addWidget(self.btn_overlay)

        self.btn_export = QPushButton("💾 내보내기")
        self.btn_export.setText(i18n.tr("💾 내보내기"))
        self.btn_export.clicked.connect(self._show_export_menu)
        toolbar_top.addWidget(self.btn_export)

        self.btn_stt_run = QPushButton("🎙 STT실행")
        self.btn_stt_run.setText(i18n.tr("🎙 STT실행"))
        self.btn_stt_run.clicked.connect(self._run_file_stt)
        self.btn_stt_run.setStyleSheet("""
            QPushButton {
                background-color: #22c55e;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #16a34a;
            }
            QPushButton:disabled {
                background-color: #6b7280;
                color: #d1d5db;
                border: none;
            }
        """)
        toolbar_top.addWidget(self.btn_stt_run)

        self.btn_stt_batch = QPushButton("🧾 STT일괄")
        self.btn_stt_batch.setText(i18n.tr("🧾 STT일괄"))
        self.btn_stt_batch.clicked.connect(self._run_batch_stt)
        toolbar_top.addWidget(self.btn_stt_batch)

        self.btn_media_view = QPushButton("🎬 미디어뷰")
        self.btn_media_view.setText(i18n.tr("🎬 미디어뷰"))
        self.btn_media_view.clicked.connect(self._open_media_view)
        toolbar_top.addWidget(self.btn_media_view)

        self.btn_file_open = QPushButton("📂 파일열기")
        self.btn_file_open.setText(i18n.tr("📂 파일열기"))
        self.btn_file_open.clicked.connect(self._open_media_file)
        toolbar_top.addWidget(self.btn_file_open)

        self.btn_settings = QPushButton("⚙ 설정")
        self.btn_settings.setText(i18n.tr("⚙ 설정"))
        self.btn_settings.clicked.connect(self._show_settings)
        toolbar_top.addWidget(self.btn_settings)

        # Row 2 (bottom, left aligned): 분할 병합 실행취소 삭제
        self.btn_save_work = QPushButton("💾 작업저장")
        self.btn_save_work.setText(i18n.tr("💾 작업저장"))
        self.btn_save_work.clicked.connect(self._on_save_work)
        toolbar_bottom.addWidget(self.btn_save_work)

        self.btn_load_work = QPushButton("📂 작업불러오기")
        self.btn_load_work.setText(i18n.tr("📂 작업불러오기"))
        self.btn_load_work.clicked.connect(self._on_load_work)
        toolbar_bottom.addWidget(self.btn_load_work)

        self.btn_split = QPushButton("✂ 분할")
        self.btn_split.setText(i18n.tr("✂ 분할"))
        self.btn_split.clicked.connect(self._on_split_clicked)
        toolbar_bottom.addWidget(self.btn_split)

        self.btn_merge = QPushButton("🔗 병합")
        self.btn_merge.setText(i18n.tr("🔗 병합"))
        self.btn_merge.clicked.connect(self._on_merge_clicked)
        toolbar_bottom.addWidget(self.btn_merge)

        self.btn_undo = QPushButton("↩ 실행취소")
        self.btn_undo.setText(i18n.tr("↩ 실행취소"))
        self.btn_undo.clicked.connect(self._on_undo_clicked)
        toolbar_bottom.addWidget(self.btn_undo)

        self.btn_delete = QPushButton("🗑 삭제")
        self.btn_delete.setText(i18n.tr("🗑 삭제"))
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        toolbar_bottom.addWidget(self.btn_delete)

    def _get_active_editor(self):
        """Determine which editor is active or should be targeted."""
        # 1. Check focus
        if self.file_editor.hasFocus() or self.file_editor.table.hasFocus():
            return self.file_editor
        if self.live_editor.hasFocus() or self.live_editor.table.hasFocus():
            return self.live_editor

        # 2. Check Waveform Mode
        if self._waveform_mode == "bottom":
            return self.file_editor
        if self._waveform_mode == "top":
            return self.live_editor

        if self._last_active_editor is not None:
            return self._last_active_editor

        # 3. Default to Live
        return self.live_editor

    def _on_split_clicked(self):
        if self._is_waveform_playing():
            self._update_status("재생 중에는 분할할 수 없습니다.")
            return
        editor = self._get_active_editor()
        editor._split_at_cursor()

    def _on_merge_clicked(self):
        if self._is_waveform_playing():
            self._update_status("재생 중에는 병합할 수 없습니다.")
            return
        editor = self._get_active_editor()
        editor.merge_selected()

    def _on_delete_clicked(self):
        editor = self._get_active_editor()
        editor.delete_selected()

    def _on_save_work(self):
        """Save current subtitle work to JSON file."""
        default_dir = os.path.join(os.getcwd(), "projects")
        os.makedirs(default_dir, exist_ok=True)
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            i18n.tr("작업 저장"),
            default_dir,
            i18n.tr("JSON 파일 (*.json)"),
        )
        if not file_path:
            return

        try:
            # Save/copy WAV file if audio was recorded
            audio_wav_path = None
            if self._current_session_wav_path and os.path.exists(
                self._current_session_wav_path
            ):
                # Use existing temp WAV file
                audio_dir = os.path.join(os.path.dirname(file_path), "audio")
                os.makedirs(audio_dir, exist_ok=True)
                audio_base = os.path.splitext(os.path.basename(file_path))[0]
                audio_wav_path = os.path.join(audio_dir, f"{audio_base}.wav")
                import shutil

                shutil.copy2(self._current_session_wav_path, audio_wav_path)
            elif self._current_session_audio:
                # Create new WAV from chunks (fallback)
                from scipy.io import wavfile

                audio_dir = os.path.join(os.path.dirname(file_path), "audio")
                os.makedirs(audio_dir, exist_ok=True)
                audio_base = os.path.splitext(os.path.basename(file_path))[0]
                audio_wav_path = os.path.join(audio_dir, f"{audio_base}.wav")
                full_audio = np.concatenate(self._current_session_audio)
                audio_int16 = (full_audio * 32767).astype(np.int16)
                wavfile.write(audio_wav_path, AudioRecorder.SAMPLE_RATE, audio_int16)

            data = {
                "live_subtitles": self._export_subtitles_to_dict(
                    self._subtitle_manager
                ),
                "file_subtitles": self._export_subtitles_to_dict(
                    self._file_subtitle_manager
                ),
                "audio_file": audio_wav_path if audio_wav_path else None,
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._update_status(f"작업이 저장되었습니다: {os.path.basename(file_path)}")
            if audio_wav_path:
                self._update_status(
                    f"녹음 파일도 저장되었습니다: {os.path.basename(audio_wav_path)}"
                )
        except Exception as e:
            QMessageBox.critical(self, i18n.tr("오류"), f"저장 실패:\n{str(e)}")

    def _on_load_work(self):
        """Load subtitle work from JSON file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            i18n.tr("작업 불러오기"),
            "",
            i18n.tr("JSON 파일 (*.json)"),
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Clear existing subtitles
            self._subtitle_manager.clear()
            self._file_subtitle_manager.clear()

            # Load live subtitles
            if "live_subtitles" in data:
                self._import_subtitles_from_dict(
                    self._subtitle_manager, data["live_subtitles"]
                )
                self.live_editor.refresh()
                self.waveform_left.refresh_segments(self._subtitle_manager.segments)

            # Load file subtitles
            if "file_subtitles" in data:
                self._import_subtitles_from_dict(
                    self._file_subtitle_manager, data["file_subtitles"]
                )
                self.file_editor.refresh()
                self.waveform_right.refresh_segments(
                    self._file_subtitle_manager.segments
                )

            self._update_status(f"작업이 불러왔습니다: {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, i18n.tr("오류"), f"불러오기 실패:\n{str(e)}")

    def _export_subtitles_to_dict(self, manager) -> list[dict]:
        """Export subtitle manager segments to dictionary format."""
        return [
            {
                "id": seg.id,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "status": seg.status.name
                if hasattr(seg.status, "name")
                else str(seg.status),
                "is_hidden": seg.is_hidden,
                "words": [
                    {"start": w.start, "end": w.end, "text": w.text} for w in seg.words
                ]
                if seg.words
                else [],
            }
            for seg in manager.segments
        ]

    def _import_subtitles_from_dict(self, manager, data: list[dict]):
        """Import segments from dictionary format to subtitle manager."""
        from src.engine.subtitle import SubtitleSegment, Word, SegmentStatus

        for seg_data in data:
            words = []
            if seg_data.get("words"):
                words = [
                    Word(start=w["start"], end=w["end"], text=w["text"])
                    for w in seg_data["words"]
                ]

            status = SegmentStatus.DRAFT
            if isinstance(seg_data.get("status"), str):
                try:
                    status = SegmentStatus[seg_data["status"]]
                except (KeyError, ValueError):
                    status = SegmentStatus.DRAFT

            segment = SubtitleSegment(
                id=seg_data.get("id", str(uuid.uuid4())),
                start=seg_data.get("start", 0.0),
                end=seg_data.get("end", 0.0),
                text=seg_data.get("text", ""),
                status=status,
                is_hidden=seg_data.get("is_hidden", False),
                words=words,
            )
            manager._segments.append(segment)

    def _setup_connections(self):
        """Setup signal connections."""
        # Audio -> UI
        self._audio_recorder.set_on_rms_update(self._on_rms_update)
        self._audio_recorder.set_on_audio_chunk(self._on_audio_chunk)

        # Editor <-> Waveform sync
        self.live_editor.segment_selected.connect(self._on_segment_selected)
        self.file_editor.segment_selected.connect(self._on_segment_selected)

        # Waveform -> Editor
        self.waveform_left.segment_clicked.connect(self.live_editor.select_segment)
        self.waveform_right.segment_clicked.connect(self.file_editor.select_segment)

        self.waveform_left.split_requested.connect(self._on_waveform_split)
        self.waveform_right.split_requested.connect(self._on_waveform_split)

        self.waveform_left.playback_started.connect(self._on_playback_started)
        self.waveform_left.playback_finished.connect(self._on_playback_finished)
        self.waveform_right.playback_started.connect(self._on_playback_started)
        self.waveform_right.playback_finished.connect(self._on_playback_finished)

        self.live_editor.full_refresh_requested.connect(self._on_live_full_refresh)
        self.file_editor.full_refresh_requested.connect(self._on_file_full_refresh)
        self.live_editor.segments_updated.connect(self._on_live_segments_updated)
        self.live_editor.segments_removed.connect(self._on_live_segments_removed)
        self.file_editor.segments_updated.connect(self._on_file_segments_updated)
        self.file_editor.segments_removed.connect(self._on_file_segments_removed)
        self.live_editor.segments_diff.connect(
            lambda a, r, u: self._on_segments_diff("left", a, r, u)
        )
        self.file_editor.segments_diff.connect(
            lambda a, r, u: self._on_segments_diff("right", a, r, u)
        )

        # Playback
        self.live_editor.playback_requested.connect(self._toggle_playback)
        self.file_editor.playback_requested.connect(self._toggle_playback)

        # Space Key (From Editors) -> Waveform Toggle
        self.live_editor.playback_toggle_requested.connect(
            lambda: self._toggle_active_waveform(self.waveform_left)
        )
        self.file_editor.playback_toggle_requested.connect(
            lambda: self._toggle_active_waveform(self.waveform_right)
        )

        # Split at Cursor
        self.live_editor.split_requested_at_cursor.connect(
            self._on_split_requested_at_cursor
        )
        self.file_editor.split_requested_at_cursor.connect(
            self._on_split_requested_at_cursor
        )

        # Editor Cursor -> Waveform Cursor Sync
        self.live_editor.editor_cursor_time_changed.connect(
            self.waveform_left.set_cursor_pos
        )
        self.file_editor.editor_cursor_time_changed.connect(
            self.waveform_right.set_cursor_pos
        )

        # Region Resize (Waveform Drag)
        self.waveform_left.region_changed.connect(self._on_region_changed)
        self.waveform_right.region_changed.connect(self._on_region_changed)

        # Scroll Sync (time-based)
        left_scroll = self.live_editor.table.verticalScrollBar()
        if left_scroll:
            left_scroll.valueChanged.connect(
                lambda value: self._sync_scroll_by_time("left")
            )
        right_scroll = self.file_editor.table.verticalScrollBar()
        if right_scroll:
            right_scroll.valueChanged.connect(
                lambda value: self._sync_scroll_by_time("right")
            )

        # Waveform Cursor -> Editor Scroll Sync
        self.waveform_left.cursor_time_changed.connect(
            lambda t: self._on_waveform_cursor_time_changed("left", t)
        )
        self.waveform_right.cursor_time_changed.connect(
            lambda t: self._on_waveform_cursor_time_changed("right", t)
        )

    def _on_split_requested_at_cursor(self, segment_id: str):
        """Handle split request from editor (uses waveform cursor)."""
        if self._is_waveform_playing():
            self._update_status("재생 중에는 분할할 수 없습니다.")
            return
        if self._subtitle_manager.get_segment(segment_id):
            cursor_time = self.waveform_left.get_cursor_time()
        else:
            cursor_time = self.waveform_right.get_cursor_time()
        if cursor_time < 0:
            self._update_status(
                "웨이브폼 커서가 설정되지 않았습니다. 웨이브폼을 클릭하세요."
            )
            return

        self._on_waveform_split(segment_id, cursor_time)

    def _on_live_full_refresh(self):
        self.waveform_left.refresh_segments(self._subtitle_manager.segments)

    def _on_file_full_refresh(self):
        self.waveform_right.refresh_segments(self._file_subtitle_manager.segments)

    def _on_live_segments_updated(self, ids: list):
        added, updated = [], []
        for sid in ids:
            seg = self._subtitle_manager.get_segment(sid)
            if seg:
                updated.append(seg)
        self.waveform_left.apply_diff(added, [], updated)
        self.live_editor.apply_diff(added, [], updated)

    def _on_live_segments_removed(self, ids: list):
        self.waveform_left.apply_diff([], ids, [])
        self.live_editor.apply_diff([], ids, [])

    def _on_file_segments_updated(self, ids: list):
        added, updated = [], []
        for sid in ids:
            seg = self._file_subtitle_manager.get_segment(sid)
            if seg:
                updated.append(seg)
        self.waveform_right.apply_diff(added, [], updated)
        self.file_editor.apply_diff(added, [], updated)
        self._schedule_media_srt_update()

    def _on_file_segments_removed(self, ids: list):
        self.waveform_right.apply_diff([], ids, [])
        self.file_editor.apply_diff([], ids, [])
        self._schedule_media_srt_update()

    def _on_segments_diff(
        self, side: str, added_ids: list, removed_ids: list, updated_ids: list
    ):
        if side == "left":
            mgr = self._subtitle_manager
            ed = self.live_editor
            wf = self.waveform_left
        else:
            mgr = self._file_subtitle_manager
            ed = self.file_editor
            wf = self.waveform_right

        added: list[SubtitleSegment] = []
        for sid in added_ids:
            seg = mgr.get_segment(sid)
            if seg is not None:
                added.append(seg)

        updated: list[SubtitleSegment] = []
        for sid in updated_ids:
            seg = mgr.get_segment(sid)
            if seg is not None:
                updated.append(seg)
        removed = removed_ids

        wf.apply_diff(added, removed, updated)
        ed.apply_diff(added, removed, updated)
        if side == "right":
            self._schedule_media_srt_update()

    def _on_editor_space_left(self):
        """Handle space key from left editor."""
        if self.live_editor._playback_state == PlaybackState.PLAYING:
            self.waveform_left.stop_playback()
            self.live_editor.set_playback_state(None, PlaybackState.IDLE)
            return
        self.waveform_left.toggle_playback()

    def _on_editor_space_right(self):
        """Handle space key from right editor."""
        if self.file_editor._playback_state == PlaybackState.PLAYING:
            self.waveform_right.stop_playback()
            self.file_editor.set_playback_state(None, PlaybackState.IDLE)
            return
        self.waveform_right.toggle_playback()

    def _on_region_changed(self, segment_id: str, start: float, end: float):
        """Handle segment resize from waveform."""
        # Update Live
        if self._subtitle_manager.get_segment(segment_id):
            start, end = self._clamp_segment_bounds(
                self._subtitle_manager, segment_id, start, end
            )
            self._subtitle_manager.update_segment(
                segment_id,
                start=start,
                end=end,
                resolve_collision=False,
                close_small_gaps=False,
            )
            segment = self._subtitle_manager.get_segment(segment_id)
            if segment:
                self.live_editor.update_single_segment(segment)
                self.waveform_left.update_segment_visual(segment)
            return

        # Update File
        if self._file_subtitle_manager.get_segment(segment_id):
            start, end = self._clamp_segment_bounds(
                self._file_subtitle_manager, segment_id, start, end
            )
            self._file_subtitle_manager.update_segment(
                segment_id,
                start=start,
                end=end,
                resolve_collision=False,
                close_small_gaps=False,
            )
            segment = self._file_subtitle_manager.get_segment(segment_id)
            if segment:
                self.file_editor.update_single_segment(segment)
                self.waveform_right.update_segment_visual(segment)
            return

    def _clamp_segment_bounds(
        self,
        manager: SubtitleManager,
        segment_id: str,
        start: float,
        end: float,
    ) -> tuple[float, float]:
        segments = sorted(manager.segments, key=lambda s: s.start)
        idx = None
        for i, seg in enumerate(segments):
            if seg.id == segment_id:
                idx = i
                break

        if idx is None:
            return start, end

        if idx > 0:
            prev_seg = segments[idx - 1]
            if start < prev_seg.end:
                start = prev_seg.end
        if idx < len(segments) - 1:
            next_seg = segments[idx + 1]
            if end > next_seg.start:
                end = next_seg.start

        if end < start:
            end = start
        return start, end

    def _toggle_playback(self, segment_id: str):
        """Toggle playback for a segment."""
        # Check if already playing this segment
        if (
            self.live_editor._current_playback_id == segment_id
            and self.live_editor._playback_state == PlaybackState.PLAYING
        ):
            self.waveform_left.stop_playback()
            return

        elif (
            self.file_editor._current_playback_id == segment_id
            and self.file_editor._playback_state == PlaybackState.PLAYING
        ):
            self.waveform_right.stop_playback()
            return

        # Start playback
        # Find segment in either manager
        segment = self._subtitle_manager.get_segment(segment_id)
        if segment:
            self.live_editor.set_playback_state(segment_id, PlaybackState.PLAYING)
            self.file_editor.set_playback_state(
                None, PlaybackState.IDLE
            )  # partial exclusive
            if self.btn_sync.isChecked():
                self.waveform_left.set_scroll_cursor_pos(
                    float(segment.start), emit=False
                )
                self.waveform_right.set_scroll_cursor_pos(
                    float(segment.start), emit=False
                )
                self._suppress_cursor_sync_until = time.monotonic() + 0.3
                self._suppress_selection_sync_until = time.monotonic() + 0.3
                self._suppress_selection_sync_once = True
            self.waveform_left.play_segment(segment.start, segment.end)
            return

        segment = self._file_subtitle_manager.get_segment(segment_id)
        if segment:
            self.file_editor.set_playback_state(segment_id, PlaybackState.PLAYING)
            self.live_editor.set_playback_state(None, PlaybackState.IDLE)
            if self.btn_sync.isChecked():
                self.waveform_left.set_scroll_cursor_pos(
                    float(segment.start), emit=False
                )
                self.waveform_right.set_scroll_cursor_pos(
                    float(segment.start), emit=False
                )
                self._suppress_cursor_sync_until = time.monotonic() + 0.3
                self._suppress_selection_sync_until = time.monotonic() + 0.3
                self._suppress_selection_sync_once = True
            self.waveform_right.play_segment(segment.start, segment.end)

    def _sync_scroll_to_right(self, value):
        """Sync Left -> Right."""
        if self.btn_sync.isChecked():
            v_scroll = self.file_editor.table.verticalScrollBar()
            if v_scroll:
                v_scroll.blockSignals(True)
                v_scroll.setValue(value)
                v_scroll.blockSignals(False)

    def _sync_scroll_to_left(self, value):
        """Sync Right -> Left."""
        if self.btn_sync.isChecked():
            v_scroll = self.live_editor.table.verticalScrollBar()
            if v_scroll:
                v_scroll.blockSignals(True)
                v_scroll.setValue(value)
                v_scroll.blockSignals(False)

    def _on_segment_selected(self, segment_id: str):
        """Handle segment selection from editors."""
        # Try finding in both managers
        segment = self._subtitle_manager.get_segment(segment_id)
        if not segment:
            segment = self._file_subtitle_manager.get_segment(segment_id)

        if segment:
            if self._subtitle_manager.get_segment(segment_id):
                self._last_active_editor = self.live_editor
            else:
                self._last_active_editor = self.file_editor

            if self._subtitle_manager.get_segment(segment_id):
                self.waveform_left.highlight_segment(segment_id)
                self.waveform_left.zoom_to_range(segment.start, segment.end)
            else:
                self.waveform_right.highlight_segment(segment_id)
                self.waveform_right.zoom_to_range(segment.start, segment.end)

            # Time-based scroll sync (selection-driven)
            if self.btn_sync.isChecked():
                if self._suppress_selection_sync_once:
                    self._suppress_selection_sync_once = False
                    return
                if time.monotonic() < self._suppress_selection_sync_until:
                    return
                self._scroll_sync_active = True
                try:
                    if self._subtitle_manager.get_segment(segment_id):
                        desired_top = self._get_row_y_for_segment(
                            self.live_editor, segment_id
                        )
                        self._scroll_other_editor_by_time_aligned(
                            source="left",
                            t=float(segment.start),
                            desired_top=desired_top,
                            force=True,
                        )
                    else:
                        desired_top = self._get_row_y_for_segment(
                            self.file_editor, segment_id
                        )
                        self._scroll_other_editor_by_time_aligned(
                            source="right",
                            t=float(segment.start),
                            desired_top=desired_top,
                            force=True,
                        )
                finally:
                    self._scroll_sync_active = False

    def _scroll_other_editor_by_time(self, source: str, t: float):
        """Scroll the opposite editor to the nearest segment by time (no selection change)."""
        if source == "left":
            other_mgr = self._file_subtitle_manager
            other_editor = self.file_editor
        else:
            other_mgr = self._subtitle_manager
            other_editor = self.live_editor

        nearest = self._find_nearest_segment_by_time(other_mgr, t)
        if not nearest:
            return
        other_editor.scroll_to_segment(nearest.id)

    def _scroll_other_editor_by_time_aligned(
        self,
        source: str,
        t: float,
        desired_top: Optional[int],
        force: bool = False,
    ) -> None:
        if desired_top is None:
            self._scroll_other_editor_by_time(source=source, t=t)
            return
        if self._scroll_sync_active and not force:
            return
        if source == "left":
            other_mgr = self._file_subtitle_manager
            other_editor = self.file_editor
        else:
            other_mgr = self._subtitle_manager
            other_editor = self.live_editor

        nearest = self._find_nearest_segment_by_time(other_mgr, t)
        if not nearest:
            return
        self._scroll_sync_active = True
        try:
            other_editor.scroll_to_segment_at_y(nearest.id, desired_top)
        finally:
            self._scroll_sync_active = False

    def _find_nearest_segment_by_time(
        self, manager: SubtitleManager, t: float
    ) -> Optional[SubtitleSegment]:
        candidates = [s for s in manager.segments if not getattr(s, "is_hidden", False)]
        if not candidates:
            return None

        def key_fn(s: SubtitleSegment):
            inside = 0 if s.start <= t <= s.end else 1
            return (inside, abs(s.start - t))

        return min(candidates, key=key_fn)

    def _get_row_y_for_segment(
        self, editor: SubtitleEditor, segment_id: str
    ) -> Optional[int]:
        if not segment_id:
            return None
        for row in range(editor.table.rowCount()):
            item = editor.table.item(row, 0)
            if not item:
                continue
            sid = item.data(Qt.ItemDataRole.UserRole)
            if sid != segment_id:
                continue
            rect = editor.table.visualItemRect(item)
            if rect.isNull():
                editor.table.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter
                )
                rect = editor.table.visualItemRect(item)
            return rect.center().y()
        return None

    def _sync_scroll_by_time(self, source: str) -> None:
        if self._cursor_sync_only:
            return
        if not self.btn_sync.isChecked():
            return
        if self._scroll_sync_active:
            return

        # Get the scrolling editor and its manager
        if source == "left":
            editor = self.live_editor
            manager = self._subtitle_manager
            other_editor = self.file_editor
            other_manager = self._file_subtitle_manager
        else:
            editor = self.file_editor
            manager = self._file_subtitle_manager
            other_editor = self.live_editor
            other_manager = self._subtitle_manager

        # Get time at center of the scrolling editor's viewport
        viewport_center_y = editor.table.viewport().rect().center().y()
        row = editor.table.rowAt(viewport_center_y)
        if row < 0:
            return
        item = editor.table.item(row, 0)
        if not item:
            return
        segment_id = item.data(Qt.ItemDataRole.UserRole)
        if not segment_id:
            return
        seg = manager.get_segment(segment_id)
        if not seg:
            return
        view_t = float(seg.start)

        print(f"[ScrollSync] source={source}, view_t={view_t:.3f}, row={row}")

        # Update both waveforms' blue lines (scroll_cursor_line)
        self.waveform_left.set_scroll_cursor_pos(view_t, emit=False)
        self.waveform_right.set_scroll_cursor_pos(view_t, emit=False)

        # Sync opposite editor to show segment at that time
        nearest = self._find_nearest_segment_by_time(other_manager, view_t)
        print(
            f"[ScrollSync] nearest segment: {nearest.id if nearest else None}, start={nearest.start if nearest else None:.3f}"
        )

        if nearest:
            other_editor.scroll_to_segment(nearest.id)

    def _get_time_at_view_y(
        self, editor: SubtitleEditor, manager: SubtitleManager, y: int
    ) -> Optional[float]:
        row = editor.table.rowAt(y)
        if row < 0:
            return None
        item = editor.table.item(row, 0)
        if not item:
            return None
        segment_id = item.data(Qt.ItemDataRole.UserRole)
        if not segment_id:
            return None
        seg = manager.get_segment(segment_id)
        if not seg:
            return None
        return float(seg.start)

    def _get_selected_row_y(self, editor: SubtitleEditor) -> Optional[int]:
        rows = editor.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        item = editor.table.item(row, 0)
        if not item:
            return None
        rect = editor.table.visualItemRect(item)
        if rect.isNull():
            return None
        return rect.center().y()

    def _get_row_y_by_time(
        self, editor: SubtitleEditor, manager: SubtitleManager, t: float
    ) -> Optional[int]:
        row = self._find_row_by_time(editor, manager, t)
        if row is None:
            return None
        item = editor.table.item(row, 0)
        if not item:
            return None
        rect = editor.table.visualItemRect(item)
        if rect.isNull():
            editor.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
            rect = editor.table.visualItemRect(item)
            if rect.isNull():
                return None
        return rect.center().y()

    def _find_row_by_time(
        self, editor: SubtitleEditor, manager: SubtitleManager, t: float
    ) -> Optional[int]:
        best_row = None
        best_key = None
        for row in range(editor.table.rowCount()):
            item = editor.table.item(row, 0)
            if not item:
                continue
            start_text = item.text()
            start_val = self._parse_editor_time_value(start_text)
            if start_val is not None:
                key = (abs(start_val - t),)
            else:
                sid = item.data(Qt.ItemDataRole.UserRole)
                if not sid:
                    continue
                seg = manager.get_segment(sid)
                if not seg or getattr(seg, "is_hidden", False):
                    continue
                key = (abs(seg.start - t),)
            if best_key is None or key < best_key:
                best_key = key
                best_row = row
        return best_row

    def _parse_editor_time_value(self, value: str) -> Optional[float]:
        if not value:
            return None
        parts = value.strip().split(":")
        if len(parts) != 2:
            return None
        try:
            minutes = int(parts[0])
            seconds = float(parts[1])
        except ValueError:
            return None
        return minutes * 60 + seconds

    def _scroll_editor_by_time_aligned(
        self,
        editor: SubtitleEditor,
        manager: SubtitleManager,
        t: float,
        desired_y: Optional[int],
    ) -> None:
        row = self._find_row_by_time(editor, manager, t)
        if row is None:
            return
        item = editor.table.item(row, 0)
        if not item:
            return
        segment_id = item.data(Qt.ItemDataRole.UserRole)
        if not segment_id:
            return
        if desired_y is None:
            editor.scroll_to_segment(segment_id)
        else:
            editor.scroll_to_segment_at_y(segment_id, desired_y)

    def _apply_scroll_cursor(
        self, t: float, source: str, sync_both: bool = False
    ) -> None:
        if self._scroll_sync_active:
            return
        self._scroll_sync_active = True
        self._scroll_sync_source = source
        self._scroll_sync_time = t
        try:
            self.waveform_left.set_scroll_cursor_pos(t, emit=False)
            self.waveform_right.set_scroll_cursor_pos(t, emit=False)

            if sync_both:
                self._scroll_editor_by_time_aligned(
                    self.live_editor, self._subtitle_manager, t, None
                )
                self._scroll_editor_by_time_aligned(
                    self.file_editor, self._file_subtitle_manager, t, None
                )
            elif source == "left":
                self._scroll_editor_by_time_aligned(
                    self.file_editor, self._file_subtitle_manager, t, None
                )
            else:
                self._scroll_editor_by_time_aligned(
                    self.live_editor, self._subtitle_manager, t, None
                )

            if self._scroll_sync_debug:
                center_y = self.live_editor.table.viewport().rect().center().y()
                self._report_scroll_sync_debug(source, t, center_y)
        finally:
            self._scroll_sync_active = False

    def _flush_scroll_sync(self) -> None:
        if not self._scroll_sync_pending:
            return
        source, t = self._scroll_sync_pending
        self._scroll_sync_pending = None
        self._apply_scroll_cursor(t, source, sync_both=False)

    def _apply_playback_cursor(self, t: float, source: str) -> None:
        if self._scroll_sync_active:
            return
        self._scroll_sync_active = True
        self._scroll_sync_source = source
        try:
            if source == "left":
                desired_y = self._get_row_y_by_time(
                    self.live_editor, self._subtitle_manager, t
                )
                self.waveform_right.set_cursor_pos(t, emit=False)
                self._scroll_editor_by_time_aligned(
                    self.file_editor, self._file_subtitle_manager, t, desired_y
                )
            else:
                desired_y = self._get_row_y_by_time(
                    self.file_editor, self._file_subtitle_manager, t
                )
                self.waveform_left.set_cursor_pos(t, emit=False)
                self._scroll_editor_by_time_aligned(
                    self.live_editor, self._subtitle_manager, t, desired_y
                )
        finally:
            self._scroll_sync_active = False

    def _on_waveform_cursor_time_changed(self, source: str, t: float) -> None:
        if not self.btn_sync.isChecked():
            return
        if time.monotonic() < self._suppress_cursor_sync_until:
            return
        if self._playback_active:
            return
        if self._scroll_sync_active:
            return
        self._apply_playback_cursor(t, source)

    def _report_scroll_sync_debug(self, source: str, t: float, y: int) -> None:
        if source == "left":
            other_editor = self.file_editor
            other_manager = self._file_subtitle_manager
        else:
            other_editor = self.live_editor
            other_manager = self._subtitle_manager

        other_t = self._get_time_at_view_y(other_editor, other_manager, y)
        if other_t is None:
            msg = f"Scroll sync: src={source} t={t:.2f}s other=none"
        else:
            msg = f"Scroll sync: src={source} t={t:.2f}s other={other_t:.2f}s"
        self._update_status(msg)

    def _import_subtitle_choose(self):
        target = self._choose_left_right(
            "자막 파일 열기", "좌측에 불러오기", "우측에 불러오기"
        )
        if not target:
            return
        self._import_file(target)

    def _import_file(self, target: str = "right"):
        """Import subtitle file to Left/Right editor."""
        path, _ = QFileDialog.getOpenFileName(
            self, "자막 파일 열기", "", "Subtitle Files (*.srt *.json)"
        )
        if not path:
            return
        self._load_subtitle_file(target=target, path=path)

    def _load_subtitle_file(self, target: str, path: str):
        """Load a subtitle file into left/right manager (no file dialog)."""
        if target == "right" and (
            self._file_subtitle_manager.segments
            or self.file_editor.table.rowCount() > 0
        ):
            reply = QMessageBox.question(
                self,
                i18n.tr("덮어쓰기 경고"),
                i18n.tr("우측 에디터의 기존 내용이 삭제됩니다.\n계속하시겠습니까?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if path.lower().endswith(".srt"):
                segments = SubtitleManager.parse_srt(content)
            else:
                self._update_status("아직 JSON 불러오기는 지원하지 않습니다.")
                return

            if target == "left":
                self.live_editor._manager.clear()
                for seg in segments:
                    self.live_editor._manager.add_segment(seg)
                self.live_editor.refresh()
                self.waveform_left.refresh_segments(self._subtitle_manager.segments)
                self._update_status(f"좌측 자막 불러옴: {path}")
                return

            # Right target
            self.file_editor._manager.clear()
            for seg in segments:
                self.file_editor._manager.add_segment(seg)
            self.file_editor.refresh()
            self.waveform_right.refresh_segments(self._file_subtitle_manager.segments)
            self._update_status(f"우측 자막 불러옴: {path}")

        except Exception as e:
            QMessageBox.critical(
                self, "오류", f"파일을 불러오는 중 오류가 발생했습니다:\n{e}"
            )

    def _apply_theme(self, theme: str | None = None):
        """Apply UI theme."""
        if theme is None:
            settings = QSettings("ThinkSub", "ThinkSub2")
            theme = str(settings.value("ui_theme", "dark"))

        app = QApplication.instance()
        style = ""

        if theme == "light":
            style = """
                QWidget { background-color: #f2f2ee; color: #1f2937; }
                QDialog { background-color: #f2f2ee; }
                QTabWidget::pane { border: 1px solid #e0e0da; }
                QTabBar::tab { background: #e8e8e2; padding: 8px 12px; margin-right: 2px; }
                QTabBar::tab:selected { background: #fbfbf7; border-bottom: 2px solid #3b82f6; }
                QGroupBox { border: 1px solid #e0e0da; margin-top: 1.5em; font-weight: bold; }
                QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; }
                QLabel { color: #1f2937; }
                QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: #fbfbf7; border: 1px solid #d6d6cf; padding: 4px; border-radius: 4px; color: #1f2937; }
                QTableView { background-color: #fbfbf7; gridline-color: #e0e0da; color: #1f2937; selection-background-color: #3b82f6; selection-color: white; }
                QTableWidget::item { padding: 4px; }
                QHeaderView::section { background-color: #e8e8e2; padding: 4px; border: 1px solid #d6d6cf; }
                QScrollBar:vertical { background: #f2f2ee; width: 12px; margin: 0px; }
                QScrollBar::handle:vertical { background: #d6d6cf; min-height: 20px; border-radius: 6px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                QToolBar { background-color: #fbfbf7; border-bottom: 1px solid #e0e0da; padding: 4px; spacing: 4px; }
                QPushButton { background-color: #e8e8e2; color: #1f2937; border: none; padding: 6px 12px; border-radius: 4px; }
                QPushButton:hover { background-color: #d6d6cf; }
                QPushButton:checked { background-color: #3b82f6; color: white; }
                QStatusBar { background-color: #fbfbf7; color: #4b5563; border-top: 1px solid #e0e0da; }
                QSplitter::handle { background-color: #d6d6cf; width: 2px; }
            """
        elif theme == "navy":
            style = """
                QWidget { background-color: #0a192f; color: #ccd6f6; }
                QDialog { background-color: #0a192f; }
                QTabWidget::pane { border: 1px solid #233554; }
                QTabBar::tab { background: #112240; color: #8892b0; padding: 8px 12px; margin-right: 2px; }
                QTabBar::tab:selected { background: #233554; color: #64ffda; border-bottom: 2px solid #64ffda; }
                QGroupBox { border: 1px solid #233554; margin-top: 1.5em; font-weight: bold; color: #64ffda; }
                QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; }
                QLabel { color: #ccd6f6; }
                QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: #112240; border: 1px solid #233554; padding: 4px; border-radius: 4px; color: #ccd6f6; }
                QTableView { background-color: #112240; gridline-color: #233554; color: #ccd6f6; selection-background-color: #233554; selection-color: #64ffda; }
                QTableWidget::item { padding: 4px; }
                QHeaderView::section { background-color: #0a192f; padding: 4px; border: 1px solid #233554; color: #8892b0; }
                QScrollBar:vertical { background: #0a192f; width: 12px; margin: 0px; }
                QScrollBar::handle:vertical { background: #233554; min-height: 20px; border-radius: 6px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                QToolBar { background-color: #112240; border: none; padding: 4px; spacing: 4px; }
                QPushButton { background-color: #233554; color: #ccd6f6; border: none; padding: 6px 12px; border-radius: 4px; }
                QPushButton:hover { background-color: #303c55; }
                QPushButton:checked { background-color: #64ffda; color: #0a192f; }
                QStatusBar { background-color: #112240; color: #8892b0; }
                QSplitter::handle { background-color: #233554; width: 2px; }
            """
        else:
            # Dark (Default)
            style = """
                QWidget { background-color: #0f0f1a; color: #e5e7eb; }
                QDialog { background-color: #0f0f1a; }
                QTabWidget::pane { border: 1px solid #374151; }
                QTabBar::tab { background: #1a1a2e; color: #9ca3af; padding: 8px 12px; margin-right: 2px; }
                QTabBar::tab:selected { background: #374151; color: #e5e7eb; border-bottom: 2px solid #3b82f6; }
                QGroupBox { border: 1px solid #374151; margin-top: 1.5em; font-weight: bold; color: #e5e7eb; }
                QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; }
                QLabel { color: #e5e7eb; }
                QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: #1a1a2e; border: 1px solid #374151; padding: 4px; border-radius: 4px; color: #e5e7eb; }
                QTableView { background-color: #1a1a2e; gridline-color: #374151; color: #e5e7eb; selection-background-color: #374151; selection-color: #3b82f6; }
                QTableWidget::item { padding: 4px; }
                QHeaderView::section { background-color: #0f0f1a; padding: 4px; border: 1px solid #374151; color: #9ca3af; }
                QScrollBar:vertical { background: #0f0f1a; width: 12px; margin: 0px; }
                QScrollBar::handle:vertical { background: #374151; min-height: 20px; border-radius: 6px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                QToolBar { background-color: #1a1a2e; border: none; padding: 4px; spacing: 4px; }
                QPushButton { background-color: #374151; color: #e5e7eb; border: none; padding: 6px 12px; border-radius: 4px; }
                QPushButton:hover { background-color: #4b5563; }
                QPushButton:checked { background-color: #3b82f6; }
                QStatusBar { background-color: #1a1a2e; color: #9ca3af; }
                QSplitter::handle { background-color: #374151; width: 2px; }
            """

        if app:
            app.setStyleSheet(style)
        else:
            self.setStyleSheet(style)

    def _update_status(self, message: str):
        """Update status bar (debounced)."""
        self._pending_status = message
        if not self._status_timer.isActive():
            self._status_timer.start()

    def _flush_status(self):
        if self._pending_status is not None:
            self.status_bar.showMessage(self._pending_status)
            self._pending_status = None

    def _set_state(self, state: AppState):
        """Update application state."""
        self._state = state

        if state == AppState.IDLE:
            self.btn_live.setText(i18n.tr("▶ Live 자막"))
            self.btn_live.setChecked(False)
            self._update_status(i18n.tr("준비"))
        elif state == AppState.LOADING:
            self.btn_live.setText(i18n.tr("⏹ 취소"))
            self.btn_live.setEnabled(True)  # Allow cancellation
            self.btn_live.setChecked(True)
            self._update_status(i18n.tr("모델 로딩 중..."))
        elif state == AppState.READY:
            self.btn_live.setText(i18n.tr("⏹ 정지"))
            self.btn_live.setEnabled(True)
            self._update_status(i18n.tr("녹음 중"))
        elif state == AppState.RECORDING:
            self.btn_live.setText(i18n.tr("⏹ 정지"))
            self._update_status(i18n.tr("Live 자막 진행 중..."))

        # Live/STT mutual exclusion
        if hasattr(self, "_update_file_stt_ui"):
            self._update_file_stt_ui()

    @pyqtSlot()
    def _on_live_clicked(self):
        """Handle Live button click."""
        if self._state == AppState.IDLE:
            self._start_live()
        elif self._state in (AppState.LOADING, AppState.READY, AppState.RECORDING):
            self._stop_live()

    # ... (skipping unchanged code)

    def _show_settings(self):
        """Show settings dialog."""
        if not self._settings_dialog:
            self._settings_dialog = SettingsDialog(self)
            self._settings_dialog.settings_changed.connect(self._on_settings_changed)
            self._settings_dialog.log_window_requested.connect(
                self._show_log_window_only
            )
        self._settings_dialog.show()
        self._settings_dialog.activateWindow()

    def _show_log_window_only(self):
        """Show log window without starting anything."""
        if not self._log_window:
            self._log_window = LogWindow(self)
        self._log_window.show()
        self._log_window.activateWindow()

    def _start_live(self):
        """Start Live transcription."""
        # 1. Confirmation if segments exist
        if self._subtitle_manager.segments:
            reply = QMessageBox.question(
                self,
                "새 세션 시작",
                "이전 자막 기록이 남아있습니다.\n초기화하고 새로 시작하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # 0. Don't clear yet. Wait for Model Ready.
        if not self._log_window:
            self._log_window = LogWindow(self)
        self._log_window.show()

        self._log_window.append_log("=== Live 자막 준비 중... (모델 로딩) ===")

        # 2. Set state to Loading
        self._set_state(AppState.LOADING)

        # 3. Start transcriber process
        # Retrieve from Settings
        settings = QSettings("ThinkSub", "ThinkSub2")
        config = {
            "model": settings.value("model", "large-v3-turbo"),
            "device": settings.value("device", "cuda"),
            "language": settings.value("language", "ko"),
            "compute_type": settings.value("compute_type", "float16"),
        }

        # Merge params tab + extra JSON (extra wins on conflicts)
        config["faster_whisper_params"] = self._build_fw_params_from_settings(
            settings, mode="live"
        )

        # Log Model Params
        self._log_window.append_log(
            f"[설정] 모델: {config['model']} | 장치: {config['device']} | 언어: {config['language']}"
        )

        if config.get("faster_whisper_params"):
            self._log_window.append_log(
                f"[Faster-Whisper] 매개변수: {config.get('faster_whisper_params')}"
            )

        # Log VAD Params
        vad_threshold = float(settings.value("vad_threshold", 0.02))
        vad_silence = float(settings.value("vad_silence_duration", 0.5))
        self._log_window.append_log(
            f"[VAD] 감도: {vad_threshold} | 최소 무음: {vad_silence}초"
        )

        # Log Audio Device
        mic_index = settings.value("mic_index", -1)
        # User request: remove desktop/loopback capture
        # Convert to int if needed (QSettings can return string)
        if mic_index is not None:
            try:
                mic_index = int(mic_index)
                if mic_index == -1:
                    mic_index = None  # Default
            except Exception:
                mic_index = None

        self._log_window.append_log(f"[오디오] 마이크 인덱스: {mic_index}")

        # RESTART RECORDER TO APPLY NEW DEVICE
        # Force stop to ensure frame counter reset (T=0)
        if self._audio_recorder.is_running:
            self._audio_recorder.stop()

        self._audio_recorder.set_device(mic_index)

        # DEBUG: Confirm Device Name
        print(f"[Main] Restarting audio recorder with device index: {mic_index}")
        try:
            import sounddevice as sd

            if mic_index is not None:
                device_info = sd.query_devices(mic_index, "input")
                if isinstance(device_info, dict) and "name" in device_info:
                    print(f"[Main] Selected Device Name: {device_info['name']}")
                else:
                    print(f"[Main] Selected Device Name: {device_info}")
            else:
                print(f"[Main] Using Default Device")
        except Exception as e:
            print(f"[Main] Could not query device info: {e}")

        # VAD Reset is Critical for new session
        self._vad_processor.reset()

        # Initialize audio collection for session recording
        self._current_session_audio = []

        self._audio_recorder.start()

        self._transcriber.start(config)
        self._transcriber.load_model()

        # 4. Start polling for results and logs
        self._result_timer.start(50)  # 50ms polling
        self._log_timer.start(100)

    def _stop_live(self):
        """Stop Live transcription."""
        # Stop audio
        self._audio_recorder.stop()

        # Save temporary WAV file from collected audio (in background)
        self._current_session_wav_path = None
        if self._current_session_audio:

            def save_wav_background(audio_chunks, state_ref):
                """Save WAV in background thread."""
                try:
                    from scipy.io import wavfile
                    import uuid

                    temp_dir = os.path.join(os.getcwd(), "projects", "temp")
                    os.makedirs(temp_dir, exist_ok=True)

                    temp_filename = f"recording_{uuid.uuid4().hex[:8]}.wav"
                    wav_path = os.path.join(temp_dir, temp_filename)

                    # Concatenate all audio chunks
                    full_audio = np.concatenate(audio_chunks)
                    # Convert float32 to int16 for WAV
                    audio_int16 = (full_audio * 32767).astype(np.int16)
                    wavfile.write(wav_path, AudioRecorder.SAMPLE_RATE, audio_int16)

                    # Set path back in main thread
                    state_ref._current_session_wav_path = wav_path

                    # Log in main thread
                    QMetaObject.invokeMethod(
                        state_ref,
                        lambda: state_ref._log_window.append_log(
                            f"임시 녹음 파일 저장: {temp_filename}"
                        )
                        if state_ref._log_window
                        else None,
                    )
                except Exception as e:
                    print(f"[Main] Failed to save temporary WAV: {e}")

            # Start background save
            audio_chunks = self._current_session_audio
            self._current_session_audio = None  # Prevent double-save
            threading.Thread(
                target=save_wav_background, args=(audio_chunks, self), daemon=True
            ).start()

        # Stop transcriber
        self._transcriber.shutdown()

        # Stop timers
        self._result_timer.stop()
        self._log_timer.stop()

        # Reset VAD
        self._vad_processor.reset()

        # Update waveform to show full duration
        if self._subtitle_manager.segments:
            last_seg = self._subtitle_manager.segments[-1]
            # self.waveform.set_total_duration(last_seg.end) # Old way

        # Render Full Session (GoldWave Style)
        self.waveform_left.render_full_session()

        # Log
        if self._log_window:
            self._log_window.append_log("=== Live 자막 종료 ===")

        self._set_state(AppState.IDLE)

    @pyqtSlot()
    def _poll_results(self):
        """Poll transcriber result queue."""
        try:
            while not self._transcriber.result_queue.empty():
                msg_type, data = self._transcriber.result_queue.get_nowait()

                if msg_type == "MODEL_READY":
                    self._transcriber_ready = True
                    if self._pending_file_transcribe:
                        pending = self._pending_file_transcribe
                        self._pending_file_transcribe = None
                        self._transcriber.transcribe_file(pending)
                    # If File STT is running, this is just a prerequisite, not Live start
                    if self._file_stt_running:
                        if self._log_window:
                            self._log_window.append_log("모델 준비 완료 (파일 변환용).")
                        continue

                    if self._log_window:
                        self._log_window.append_log(
                            "모델 준비 완료! 녹음을 시작합니다."
                        )

                    # --- SESSION START POINT (00:00:00) ---
                    # Pure Frame-Based Sync: No Anchor Needed.
                    # Audio engine sends 0-based time automatically.

                    # Clear subsystems
                    self.waveform_left.clear()
                    self.waveform_left.start_monitoring()  # Restart timer (but check live render flag)
                    self.waveform_left.set_live_render(
                        False
                    )  # Optimization: Disable live view
                    self._subtitle_manager.clear()
                    self._vad_processor.reset()  # Critical: Clear legacy buffers
                    self._first_speech_detected = False  # Reset flag for new session
                    self.live_editor.refresh()

                    # Update State
                    self._set_state(AppState.RECORDING)

                elif msg_type == "MODEL_ERROR":
                    self._transcriber_ready = False
                    self._pending_file_transcribe = None
                    if self._log_window:
                        self._log_window.append_log(f"오류: {data}")
                    self._stop_live()

                elif msg_type == "TRANSCRIPTION":
                    # Backward compatibility or file single updates (though file uses loop)
                    # Ideally we migrate everything to BATCH, but let's keep this as fallback
                    result: TranscribeResult = data
                    self._process_transcription(result)

                elif msg_type == "TRANSCRIPTION_BATCH":
                    results: list[TranscribeResult] = data
                    self._process_transcription_batch(results)

                elif msg_type == "FILE_ALL_SEGMENTS":
                    results: list[TranscribeResult] = data
                    self._process_file_full_batch(results)

                elif msg_type == "FILE_COMPLETED":
                    filename = data
                    if self._log_window:
                        self._log_window.append_log(f"파일 변환 완료: {filename}")
                        self._log_window.append_log(
                            f"[MediaView] File segments: {len(self._file_subtitle_manager.segments)}"
                        )
                        if self._file_subtitle_manager.segments:
                            first = self._file_subtitle_manager.segments[0]
                            last = self._file_subtitle_manager.segments[-1]
                            self._log_window.append_log(
                                f"[MediaView] First: {first.start:.2f}-{first.end:.2f}"
                            )
                            self._log_window.append_log(
                                f"[MediaView] Last: {last.start:.2f}-{last.end:.2f}"
                            )
                    self._update_media_srt_and_proxy()
                    if (
                        self._media_view
                        and self._media_dock
                        and self._media_dock.isVisible()
                    ):
                        if self._file_subtitle_manager.segments:
                            start_time = float(
                                self._file_subtitle_manager.segments[0].start
                            )
                        else:
                            start_time = 0.0
                        self._media_view.set_time(start_time)
                        self._media_sync_timer.start()
                    self.file_editor.refresh()
                    if self._batch_running:
                        self._save_srt_to_source(filename)
                        if self._batch_dialog:
                            self._batch_dialog.update_status(filename, "완료")
                            self._batch_dialog.update_progress(filename, 100)
                        self._start_next_batch_file()
                    else:
                        self._finish_file_stt(cancelled=False)

                elif msg_type == "FILE_CANCELLED":
                    filename = data
                    if self._log_window:
                        self._log_window.append_log(f"파일 변환 취소됨: {filename}")
                    if self._batch_running and self._batch_cancel_requested:
                        if self._batch_dialog:
                            self._batch_dialog.update_status(filename, "중단")
                        self._batch_running = False
                        self._batch_current_file = None
                        self._batch_cancel_requested = False
                        self._finish_file_stt(cancelled=True)
                    else:
                        self._finish_file_stt(cancelled=True)

                elif msg_type == "TRANSCRIPTION_ERROR":
                    err = str(data)
                    if self._log_window:
                        self._log_window.append_log(f"파일 변환 오류: {err}")
                    if self._batch_running:
                        if self._batch_dialog and self._batch_current_file:
                            self._batch_dialog.update_status(
                                self._batch_current_file, "오류"
                            )
                        self._batch_running = False
                        self._batch_current_file = None
                        self._batch_cancel_requested = False
                        self._finish_file_stt(error=err)
                    elif self._file_stt_running:
                        self._finish_file_stt(error=err)

        except Exception as e:
            print(f"Error polling results: {e}")
            import traceback

            traceback.print_exc()

    @pyqtSlot()
    def _poll_logs(self):
        """Poll transcriber log queue."""
        try:
            while not self._transcriber.log_queue.empty():
                log_msg = self._transcriber.log_queue.get_nowait()
                if self._log_window:
                    self._log_window.append_log(log_msg)
                if (
                    self._batch_running
                    and self._batch_dialog
                    and self._batch_current_file
                ):
                    match = re.search(r"\[진행률\]\s+(\d+)%", log_msg)
                    if match:
                        self._batch_dialog.update_progress(
                            self._batch_current_file, int(match.group(1))
                        )
        except:
            pass

    def _process_transcription_batch(self, results: list[TranscribeResult]):
        """Process a batch of transcription results (Live Only)."""
        if not results:
            return

        # Live uses this path. File STT uses _process_file_full_batch.
        source = results[0].source
        if source != "live":
            return  # Should not happen with new file flow, but safety check

        if source == "live":
            # Atomic update for Live: Clear drafts -> Add new batch
            self._subtitle_manager.delete_drafts()

            # Separate Text for Overlays
            live_texts = []

            for result in results:
                # --- Post-Processing Filters ---
                if self._enable_post_processing:
                    if (
                        hasattr(result, "avg_rms")
                        and result.avg_rms < self._rms_threshold
                    ):
                        continue
                    if len(result.text) < self._min_text_length:
                        continue
                    # Duration Check
                    duration = result.end - result.start
                    if duration < self._min_duration:
                        continue
                    if getattr(self, "_max_duration", 0.0) and duration >= float(
                        self._max_duration
                    ):
                        continue
                # -------------------------------

                # Check duplication/add
                segments = self._add_single_result(
                    result, self._subtitle_manager, mode="live"
                )

                for seg in segments:
                    # Update Waveform
                    self.waveform_left.add_segment_overlay(
                        seg.id, seg.start, seg.end, seg.status == SegmentStatus.FINAL
                    )

                    if seg.status == SegmentStatus.FINAL:
                        # Append to History Overlay (Top)
                        self.overlay.append_history(seg.text)

                        if seg.words:
                            word_tuples = [
                                (w.start, w.end, w.text, w.probability)
                                for w in seg.words
                            ]
                            self.waveform_left.add_word_timestamps(seg.id, word_tuples)
                    else:
                        # Collect Draft Text
                        live_texts.append(seg.text)

            # Update Live Overlay (Bottom) - Overwrite with current frame drafts
            if self.overlay.isVisible():
                self.overlay.set_live_text(" ".join(live_texts))

            self.live_editor.refresh()

        elif source == "file":
            for result in results:
                self._add_single_result(
                    result, self._file_subtitle_manager, mode="file"
                )
            self.waveform_right.refresh_segments(self._file_subtitle_manager.segments)
            self.file_editor.refresh()

    def _process_file_full_batch(self, results: list[TranscribeResult]):
        """Process full batch of file STT results (no post-processing filter)."""
        if not results:
            return

        # 1. Clear previous
        self._file_subtitle_manager.clear()

        # 2. Add all segments (apply formatting/word-splitting only)
        # We skip RMS/Duration filters because user requested raw file output.
        for result in results:
            # Check duplication/add
            # For File STT, we treat everything as 'FINAL' effectively if it came from Whisper's file mode
            # But the result.is_final should already be True
            segments = self._add_single_result(
                result, self._file_subtitle_manager, mode="file"
            )

            # Manually update overlays and word timestamps for File STT
            for seg in segments:
                self.waveform_right.add_segment_overlay(
                    seg.id, seg.start, seg.end, seg.status == SegmentStatus.FINAL
                )
                if seg.words:
                    word_tuples = [
                        (w.start, w.end, w.text, w.probability) for w in seg.words
                    ]
                    self.waveform_right.add_word_timestamps(seg.id, word_tuples)

        # 3. Refresh UI once
        self.waveform_right.refresh_segments(self._file_subtitle_manager.segments)
        self.file_editor.refresh()
        self._schedule_media_srt_update()

    def _add_single_result(
        self,
        result: TranscribeResult,
        manager: SubtitleManager,
        mode: str = "live",
    ) -> list[SubtitleSegment]:
        """Helper to create and add one or more segments from result."""
        clean_text = result.text.strip()
        if not clean_text:
            return []

        # Draft: keep as single segment
        if not result.is_final:
            segment = SubtitleSegment(
                start=result.start,
                end=result.end,
                text=clean_text,
                status=SegmentStatus.DRAFT,
            )
            if mode == "live":
                self._apply_live_time_adjustments(
                    [segment],
                    self._live_wordtimestamp_offset,
                    self._live_pad_before,
                    self._live_pad_after,
                )
            manager.add_segment(segment)
            return [segment]

        # Final: create base with words then apply sentence/gap/line wrap splitting
        base = SubtitleSegment(
            start=result.start,
            end=result.end,
            text=clean_text,
            status=SegmentStatus.FINAL,
        )
        if result.words:
            base.words = [
                Word(start=w[0], end=w[1], text=w[2], probability=w[3])
                for w in result.words
            ]

        cfg = self._get_fw_format_config(mode=mode)
        segments = self._split_final_by_words(base, cfg)

        if mode == "live":
            self._apply_live_time_adjustments(
                segments,
                self._live_wordtimestamp_offset,
                self._live_pad_before,
                self._live_pad_after,
            )

        if mode == "file":
            self._apply_stt_padding(
                segments,
                self._stt_pad_before,
                self._stt_pad_after,
            )
            self._apply_seg_endmin(segments, self._stt_seg_endmin)

        whitelist = (
            self._live_abbrev_whitelist
            if mode == "live"
            else self._stt_abbrev_whitelist
        )
        segments_to_add, updated_existing = self._merge_abbrev_segments(
            manager, segments, whitelist
        )
        for seg in segments_to_add:
            manager.add_segment(seg)

        return updated_existing + segments_to_add

    def _apply_seg_endmin(
        self, segments: list[SubtitleSegment], min_duration: float
    ) -> None:
        if min_duration <= 0:
            return
        for seg in segments:
            duration = seg.end - seg.start
            if duration < min_duration:
                seg.end = seg.start + min_duration

    def _apply_live_time_adjustments(
        self,
        segments: list[SubtitleSegment],
        offset: float,
        pad_before: float,
        pad_after: float,
    ) -> None:
        if not segments:
            return

        for seg in segments:
            if offset:
                seg.start += offset
                seg.end += offset
                if seg.words:
                    for w in seg.words:
                        w.start += offset
                        w.end += offset

            if pad_before:
                seg.start -= pad_before
            if pad_after:
                seg.end += pad_after

            if seg.start < 0:
                seg.start = 0.0
            if seg.end < seg.start:
                seg.end = seg.start

            if seg.words:
                for w in seg.words:
                    if w.start < 0:
                        w.start = 0.0
                    if w.end < w.start:
                        w.end = w.start

        ordered = sorted(segments, key=lambda s: (s.start, s.end))
        for idx in range(len(ordered) - 1):
            cur = ordered[idx]
            nxt = ordered[idx + 1]
            if cur.end <= nxt.start:
                continue

            mid = (cur.end + nxt.start) / 2.0
            if mid < cur.start:
                mid = cur.start
            if mid > nxt.end:
                mid = nxt.end

            cur.end = mid
            nxt.start = mid
            if cur.end < cur.start:
                cur.end = cur.start
            if nxt.end < nxt.start:
                nxt.end = nxt.start

            if cur.words:
                for w in cur.words:
                    if w.end > cur.end:
                        w.end = cur.end
                    if w.start > cur.end:
                        w.start = cur.end
            if nxt.words:
                for w in nxt.words:
                    if w.start < nxt.start:
                        w.start = nxt.start
                    if w.end < w.start:
                        w.end = w.start

    def _apply_stt_padding(
        self, segments: list[SubtitleSegment], pad_before: float, pad_after: float
    ) -> None:
        """Apply padding adjustments for STT segments."""
        if not segments or (pad_before == 0 and pad_after == 0):
            return

        for seg in segments:
            if pad_before:
                seg.start -= pad_before
            if pad_after:
                seg.end += pad_after

            if seg.start < 0:
                seg.start = 0.0
            if seg.end < seg.start:
                seg.end = seg.start

            if seg.words:
                for w in seg.words:
                    if w.start < 0:
                        w.start = 0.0
                    if w.end < w.start:
                        w.end = w.start

        # Resolve overlaps (same as live adjustments)
        ordered = sorted(segments, key=lambda s: (s.start, s.end))
        for idx in range(len(ordered) - 1):
            cur = ordered[idx]
            nxt = ordered[idx + 1]
            if cur.end <= nxt.start:
                continue

            mid = (cur.end + nxt.start) / 2.0
            if mid < cur.start:
                mid = cur.start
            if mid > nxt.end:
                mid = nxt.end

            cur.end = mid
            nxt.start = mid
            if cur.end < cur.start:
                cur.end = cur.start
            if nxt.end < nxt.start:
                nxt.end = nxt.start

            if cur.words:
                for w in cur.words:
                    if w.end > cur.end:
                        w.end = cur.end
                    if w.start > cur.end:
                        w.start = cur.end
            if nxt.words:
                for w in nxt.words:
                    if w.start < nxt.start:
                        w.start = nxt.start
                    if w.end < w.start:
                        w.end = w.start

    def _merge_abbrev_segments(
        self,
        manager: SubtitleManager,
        segments: list[SubtitleSegment],
        whitelist: list[str],
    ) -> tuple[list[SubtitleSegment], list[SubtitleSegment]]:
        if not segments or not whitelist:
            return segments, []

        normalized = set(self._normalize_abbrev_list(whitelist))
        merged_segments: list[SubtitleSegment] = []

        for seg in segments:
            if not merged_segments:
                merged_segments.append(seg)
                continue

            prev = merged_segments[-1]
            if self._should_merge_by_abbrev(prev, seg, normalized):
                self._merge_segment_into(prev, seg)
                continue
            merged_segments.append(seg)

        updated_existing: list[SubtitleSegment] = []
        if manager.segments and merged_segments:
            last_existing = manager.segments[-1]
            first_new = merged_segments[0]
            if self._should_merge_by_abbrev(last_existing, first_new, normalized):
                self._merge_segment_into(last_existing, first_new)
                updated_existing.append(last_existing)
                merged_segments = merged_segments[1:]

        return merged_segments, updated_existing

    def _should_merge_by_abbrev(
        self, left: SubtitleSegment, right: SubtitleSegment, whitelist: set[str]
    ) -> bool:
        if left.status != SegmentStatus.FINAL or right.status != SegmentStatus.FINAL:
            return False
        token = self._last_token(left.text)
        return token in whitelist

    def _merge_segment_into(
        self, target: SubtitleSegment, source: SubtitleSegment
    ) -> None:
        left = (target.text or "").rstrip()
        right = (source.text or "").lstrip()
        if left and right:
            target.text = f"{left} {right}"
        elif right:
            target.text = right
        else:
            target.text = left

        target.end = max(target.end, source.end)
        if target.words or source.words:
            target.words = list(target.words or []) + list(source.words or [])

    def _last_token(self, text: str) -> str:
        tokens = (text or "").strip().split()
        if not tokens:
            return ""
        return tokens[-1].strip().lower()

    def _process_transcription(self, result: TranscribeResult):
        """Process a SINGLE transcription result (Legacy/Fallback)."""
        # This acts as a wrapper for single result
        self._process_transcription_batch([result])

    def _on_rms_update(self, rms: float):
        """Handle RMS update from audio thread."""
        # Update Settings Dialog indicator if open
        try:
            if self._settings_dialog and self._settings_dialog.isVisible():
                self._settings_dialog.update_mic_indicator(rms)
        except RuntimeError:
            self._settings_dialog = None

    def _on_audio_chunk(self, chunk: AudioChunk):
        """Handle audio chunk from recorder."""
        try:
            # Collect audio data for session recording
            if (
                self._state == AppState.RECORDING
                and self._current_session_audio is not None
            ):
                data = chunk.data
                if hasattr(data, "flatten"):
                    data = data.flatten()
                self._current_session_audio.append(data)

            # Update waveform only if RECORDING (Model is Ready)
            if self._state == AppState.RECORDING:
                # Pure Frame-Based Sync
                # chunk.start_time is ALREADY 0-based relative to Record Start
                rel_time = chunk.start_time

                # Ensure 1D
                data = chunk.data
                if hasattr(data, "flatten"):
                    data = data.flatten()

                # print(f"[Main] Sending {len(data)} samples to waveform. RelTime: {rel_time:.2f}")
                self.waveform_left.update_audio(data, rel_time)

            # Skip VAD if not recording
            if self._state != AppState.RECORDING:
                return

            # Process through VAD
            phrase = self._vad_processor.process_chunk(chunk)

            current_time = time.time()

            if phrase:
                # Phrase ended - send for Final transcription
                audio_data, start_time, end_time = phrase

                # Pure Frame-Based Sync
                rel_start = start_time
                rel_end = end_time

                print(
                    f"[Main-Debug] Final Phrase: AbsStart={start_time:.3f} -> RelStart={rel_start:.3f}"
                )

                # --- Virtual Silence Chunk ---
                if not self._first_speech_detected:
                    self._first_speech_detected = True
                    if rel_start > 0.1:  # Threshold (100ms)
                        print(
                            f"[Main] Creating Virtual Silence Chunk (0.0 - {rel_start:.2f})"
                        )
                        silence_seg = SubtitleSegment(
                            start=0.0,
                            end=rel_start,
                            text="(Silence)",
                            status=SegmentStatus.FINAL,
                            is_hidden=True,
                        )
                        self._subtitle_manager.add_segment(silence_seg)
                # -----------------------------

                self._transcriber.transcribe_final(audio_data, rel_start, rel_end)

            else:
                # Still speaking check
                if (
                    current_time - self._last_live_update_time
                    >= self._live_update_interval
                ):
                    partial = self._vad_processor.get_current_phrase()
                    if partial:
                        audio_data, start_time, end_time = partial

                        # Apply Anchor Offset
                        # REMOVED: Anchor logic. Use VAD timestamps directly.
                        # if self._anchor_timestamp is not None:
                        #    rel_start = max(0.0, start_time - self._anchor_timestamp)
                        #    rel_end = max(0.0, end_time - self._anchor_timestamp)

                        self._transcriber.transcribe_live(
                            audio_data, start_time, end_time
                        )
                        self._last_live_update_time = current_time

        except Exception as e:
            print(f"[Main] Audio Chunk Error: {e}")
            import traceback

            traceback.print_exc()

    def _on_waveform_split(self, segment_id: str, split_time: float):
        """Handle split request from waveform."""
        if self._is_waveform_playing():
            self._update_status("재생 중에는 분할할 수 없습니다.")
            return
        sender = self.sender()

        # Determine target based on sender
        is_left = (sender == self.waveform_left) or (sender == self.live_editor)
        is_right = (sender == self.waveform_right) or (sender == self.file_editor)

        # If call wasn't from signal (direct call), check active editor
        if not is_left and not is_right:
            active = self._get_active_editor()
            if active == self.live_editor:
                is_left = True
            else:
                is_right = True

        if is_left:
            new_id, old_seg, new_seg = self._subtitle_manager.split_segment(
                segment_id, split_time
            )
            if new_id:
                if old_seg:
                    self.live_editor.update_single_segment(old_seg)
                    self.waveform_left.update_segment_visual(old_seg)
                if new_seg:
                    self.live_editor.insert_segment_at(new_seg)
                    self.waveform_left.add_segment_visual(new_seg)
                self.live_editor.data_changed.emit()
            return

        if is_right:
            new_id, old_seg, new_seg = self._file_subtitle_manager.split_segment(
                segment_id, split_time
            )
            if new_id:
                if old_seg:
                    self.file_editor.update_single_segment(old_seg)
                    self.waveform_right.update_segment_visual(old_seg)
                if new_seg:
                    self.file_editor.insert_segment_at(new_seg)
                    self.waveform_right.add_segment_visual(new_seg)
                self.file_editor.data_changed.emit()
            return

    def _toggle_view(self):
        """Toggle between view modes."""
        sizes = self.editor_splitter.sizes()
        if sizes[1] == 0:
            # Show both
            self.editor_splitter.setSizes([500, 500])
        elif sizes[0] == 0:
            # Show left only
            self.editor_splitter.setSizes([1000, 0])
        else:
            # Show right only
            self.editor_splitter.setSizes([0, 1000])

        self._update_view_button_text()

    def _update_view_button_text(self) -> None:
        if not hasattr(self, "btn_view"):
            return
        sizes = self.editor_splitter.sizes()
        if sizes[0] == 0:
            self.btn_view.setText(i18n.tr("📐 Editor 오른쪽"))
        elif sizes[1] == 0:
            self.btn_view.setText(i18n.tr("📐 Editor 왼쪽"))
        else:
            self.btn_view.setText(i18n.tr("📐 Editor 분할"))

    def _on_undo_clicked(self):
        """Handle undo button click."""
        editor = self._get_active_editor()
        editor.undo()

    def _toggle_overlay_mode(self):
        """Cycle through overlay modes: Hide -> Top -> Bottom -> Both -> Hide."""
        # 0:Top, 1:Bottom, 2:Both

        if not self.overlay.isVisible():
            # State: Hidden -> Top
            self.overlay.show()
            self.overlay.set_view_mode(0)
            self.btn_overlay.setText("CC: 상단")
            return

        current = self.overlay.mode
        # Current: 0(Top) -> 1(Bottom) -> 2(Both) -> Hide
        if current == 0:
            self.overlay.set_view_mode(1)
            self.btn_overlay.setText("CC: 하단")
        elif current == 1:
            self.overlay.set_view_mode(2)
            self.btn_overlay.setText("CC: 전체")
        elif current == 2:
            self.overlay.hide()
            self.btn_overlay.setText("CC: 끔")

    def _toggle_waveform(self):
        """Toggle waveform visibility."""
        self._apply_waveform_mode()

    def _toggle_overlay(self):
        """Toggle floating subtitle overlay visibility."""
        if self.btn_overlay.isChecked():
            self.overlay.show()
        else:
            self.overlay.hide()

    def _show_export_menu(self):
        """Show export options."""
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)

        srt_menu = menu.addMenu(i18n.tr("SRT 내보내기"))
        srt_left = srt_menu.addAction(i18n.tr("좌측 SRT"))
        srt_left.triggered.connect(self._export_srt_left)
        srt_right = srt_menu.addAction(i18n.tr("우측 SRT"))
        srt_right.triggered.connect(self._export_srt_right)

        meta_menu = menu.addMenu(i18n.tr("메타데이터 내보내기 (JSON)"))
        meta_left = meta_menu.addAction(i18n.tr("좌측 메타데이터"))
        meta_left.triggered.connect(lambda: self._export_metadata("left"))
        meta_right = meta_menu.addAction(i18n.tr("우측 메타데이터"))
        meta_right.triggered.connect(lambda: self._export_metadata("right"))

        lora_menu = menu.addMenu(i18n.tr("LoRA 데이터 내보내기"))
        lora_left = lora_menu.addAction(i18n.tr("좌측 LoRA"))
        lora_left.triggered.connect(self._export_lora_data_left)
        lora_right = lora_menu.addAction(i18n.tr("우측 LoRA"))
        lora_right.triggered.connect(self._export_lora_data_right)

        menu.addSeparator()

        wav_action = menu.addAction(i18n.tr("오디오 내보내기 (WAV)"))
        if wav_action:
            wav_action.triggered.connect(self._export_wav)

        import_menu = menu.addMenu(i18n.tr("자막 파일 열기"))
        import_left = import_menu.addAction(i18n.tr("좌측 자막"))
        import_left.triggered.connect(lambda: self._import_subtitle_choose_side("left"))
        import_right = import_menu.addAction(i18n.tr("우측 자막"))
        import_right.triggered.connect(
            lambda: self._import_subtitle_choose_side("right")
        )

        menu.exec(self.btn_export.mapToGlobal(self.btn_export.rect().bottomLeft()))

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available."""
        import shutil

        if shutil.which("ffmpeg") is None:
            QMessageBox.warning(
                self,
                "FFmpeg 없음",
                "ffmpeg가 시스템 경로에 없습니다.\n미디어 불러오기 및 오디오 추출이 불가능할 수 있습니다.\nffmpeg를 설치해주세요.",
            )
            return False
        return True

    def _open_media_file(self):
        """Select a media file for file STT."""
        if not self._check_ffmpeg():
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "미디어 파일 열기",
            "",
            "Audio/Video (*.mp3 *.wav *.m4a *.mp4 *.mkv *.flac *.aac)",
        )
        if path:
            self._open_media_path(path)

    def _open_media_path(self, path: str):
        self._selected_media_file = path
        self._update_status(f"미디어 선택됨: {path}")
        self._load_audio_background(path)
        # Prepare preview proxy (480p) to avoid 1080p playback freeze
        self._preview_proxy_path = self._get_media_proxy_path(path, None)
        self._ensure_media_proxy_async(path, None)
        # Switch to bottom waveform view automatically
        self._waveform_mode = "bottom"
        if hasattr(self, "btn_waveform_mode"):
            self.btn_waveform_mode.setText(i18n.tr("↕ 웨이브폼 하단"))
        self._apply_waveform_mode()
        self._load_sidecar_srt(path)

    def dragEnterEvent(self, a0):
        event = cast(Any, a0)
        mime = event.mimeData()
        if mime is None:
            return
        if mime.hasUrls():
            event.acceptProposedAction()
            try:
                pos = event.position().toPoint()
            except Exception:
                pos = event.pos()
            if self.file_editor.geometry().contains(pos):
                self._right_drop_overlay.show()
        else:
            event.ignore()

    def dragLeaveEvent(self, a0):
        if hasattr(self, "_right_drop_overlay"):
            self._right_drop_overlay.hide()

    def _load_sidecar_srt(self, media_path: str) -> None:
        base, _ = os.path.splitext(media_path)
        srt_path = f"{base}.srt"
        self._load_srt_file(srt_path)

    def _load_srt_file(self, srt_path: str) -> None:
        """Load an SRT file and display in the editor."""
        if not os.path.exists(srt_path):
            return
        segments = self._parse_srt_file(srt_path)
        if not segments:
            return
        self._file_subtitle_manager.clear()
        for seg in segments:
            self._file_subtitle_manager.add_segment(seg)
        self.waveform_right.refresh_segments(self._file_subtitle_manager.segments)
        self.file_editor.refresh()
        self._update_status(f"자막 불러옴: {srt_path}")
        self._schedule_media_srt_update()

    def _run_file_stt(self):
        """Toggle file STT run/stop."""
        if self._file_stt_running:
            self._stop_file_stt()
            return

        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                i18n.tr("STT"),
                i18n.tr("Live 실행 중에는 STT를 실행할 수 없습니다."),
            )
            return

        if not self._selected_media_file:
            self._open_media_file()
        if not self._selected_media_file:
            return

        if self._file_subtitle_manager.segments:
            reply = QMessageBox.question(
                self,
                i18n.tr("자막 덮어쓰기"),
                i18n.tr(
                    "현재 우측 자막을 지우고 STT 결과를 새로 생성합니다. 계속하시겠습니까?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._start_file_transcription(self._selected_media_file)

    def _run_batch_stt(self):
        if not self._batch_dialog:
            self._batch_dialog = BatchSttDialog(self)
            self._batch_dialog.start_requested.connect(self._start_batch_stt)
            self._batch_dialog.stop_requested.connect(self._stop_batch_stt)
        self._batch_dialog.show()
        self._batch_dialog.raise_()

    def _start_batch_stt(self):
        if not self._batch_dialog:
            return
        files = self._batch_dialog.files()
        if not files:
            QMessageBox.information(
                self, i18n.tr("STT"), i18n.tr("일괄 작업할 파일을 추가하세요.")
            )
            return
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                i18n.tr("STT"),
                i18n.tr("Live 실행 중에는 STT를 실행할 수 없습니다."),
            )
            return
        self._batch_queue = list(files)
        self._batch_running = True
        self._batch_cancel_requested = False
        self._batch_current_file = None
        self._file_stt_running = True
        self._update_file_stt_ui()
        self._start_next_batch_file()

    def _stop_batch_stt(self):
        if not self._batch_running:
            if self._batch_dialog:
                self._batch_dialog.btn_start.setChecked(False)
            return
        self._batch_cancel_requested = True
        self._batch_queue = []
        if self._batch_dialog:
            for path in self._batch_dialog.files():
                self._batch_dialog.update_status(path, "중단")
        self._stop_file_stt()

    def _start_next_batch_file(self):
        if not self._batch_queue:
            self._batch_running = False
            self._batch_current_file = None
            self._batch_cancel_requested = False
            self._finish_file_stt(cancelled=False)
            if self._batch_dialog:
                self._batch_dialog.btn_start.setChecked(False)
            return
        next_file = self._batch_queue.pop(0)
        self._batch_current_file = next_file
        if self._batch_dialog:
            self._batch_dialog.update_status(next_file, "작업중")
        self._start_file_transcription(next_file)

    def _update_file_stt_ui(self):
        """Update UI enable/labels for file STT and Live mutual exclusion."""
        if hasattr(self, "btn_stt_run"):
            if self._file_stt_running:
                self.btn_stt_run.setEnabled(True)
                self.btn_stt_run.setText(i18n.tr("🎙 STT중지"))
            else:
                self.btn_stt_run.setText(i18n.tr("🎙 STT실행"))
                self.btn_stt_run.setEnabled(
                    (self._state == AppState.IDLE) and (not self._batch_running)
                )

        if hasattr(self, "btn_file_open"):
            self.btn_file_open.setEnabled(
                (self._state == AppState.IDLE)
                and (not self._file_stt_running)
                and (not self._batch_running)
            )

        if hasattr(self, "btn_live"):
            self.btn_live.setEnabled(
                (not self._file_stt_running) and (not self._batch_running)
            )

        if hasattr(self, "btn_stt_batch"):
            self.btn_stt_batch.setEnabled(
                (self._state == AppState.IDLE) and (not self._file_stt_running)
            )

    def _stop_file_stt(self):
        """Request cancellation of current file STT."""
        try:
            self._transcriber.cancel_file()
        except Exception:
            pass
        self._update_status("파일 STT 취소 요청...")
        if hasattr(self, "btn_stt_run"):
            self.btn_stt_run.setEnabled(False)
            self.btn_stt_run.setText("취소중...")

    def _finish_file_stt(self, *, cancelled: bool = False, error: Optional[str] = None):
        if not self._batch_running:
            self._file_stt_running = False
        self._update_file_stt_ui()

        if error:
            self._update_status(f"파일 STT 오류: {error}")
            return
        if cancelled:
            self._update_status("파일 STT 취소됨")
        else:
            self._update_status("파일 STT 완료")

    def _save_srt_to_source(self, media_path: str) -> None:
        try:
            base, _ = os.path.splitext(media_path)
            out_path = f"{base}.srt"
            segments = list(self._file_subtitle_manager.segments)
            self._write_srt_file(out_path, segments)
            if self._log_window:
                self._log_window.append_log(f"SRT 저장: {out_path}")
        except Exception as e:
            if self._log_window:
                self._log_window.append_log(f"SRT 저장 실패: {e}")

    def _open_media_view(self):
        """Toggle MediaView dock widget."""
        # Check if dock exists, create if not
        if not self._media_dock:
            from src.gui.media_view import MediaView

            self._media_dock = QDockWidget("미디어뷰", self)
            self._media_dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea
                | Qt.DockWidgetArea.RightDockWidgetArea
            )
            self._media_dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                | QDockWidget.DockWidgetFeature.DockWidgetClosable
            )
            self._media_dock.setMinimumWidth(360)
            self._media_dock.dockLocationChanged.connect(
                lambda _: self._media_view._layout_subtitles()
                if self._media_view
                else None
            )
            self._media_dock.topLevelChanged.connect(
                lambda _: self._media_view._layout_subtitles()
                if self._media_view
                else None
            )
            self._media_dock.visibilityChanged.connect(
                lambda _: self._media_view._layout_subtitles()
                if self._media_view
                else None
            )
            self._media_dock.installEventFilter(self)
            self._media_view = MediaView(self._media_dock)
            self._media_dock.setWidget(self._media_view)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._media_dock)

            # Wire up
            self._media_view.set_managers(
                self._file_subtitle_manager, self._subtitle_manager
            )
            self._media_view.time_changed.connect(self._on_media_time_changed)
            self._media_view.playback_toggle_requested.connect(
                self.waveform_right.toggle_playback
            )
            self._media_view.debug_log.connect(self._append_media_log)

            # Initial styling
            self._update_media_view_style()

        self._media_debug_logged = False
        # Toggle visibility
        if self._media_dock.isVisible():
            self._media_dock.close()
            self._media_sync_timer.stop()
        else:
            self._media_dock.show()
            # If media file selected but not loaded, load it
            if self._selected_media_file and self._media_view:
                self._media_view.set_media(self._selected_media_file)
                cursor = self.waveform_right.get_playback_time()
                if cursor < 0 and self._file_subtitle_manager.segments:
                    cursor = float(self._file_subtitle_manager.segments[0].start)
                if cursor < 0:
                    cursor = 0.0
                self._media_view.set_time(cursor)
                self._media_sync_timer.start()

    def _update_media_view_style(self):
        """Push settings to media view."""
        if not self._media_view:
            return
        settings = QSettings("ThinkSub", "ThinkSub2")
        self._media_view.update_style(
            font_size=int(settings.value("subtitle_font_size", 25)),
            opacity=float(settings.value("subtitle_opacity", 80)) / 100.0,
            bg_color="0, 0, 0",  # Default black for now
        )

    def eventFilter(self, obj, event):
        if obj in (self.file_editor, getattr(self.file_editor, "table", None)):
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                mime = event.mimeData()
                if mime and mime.hasUrls():
                    if hasattr(self, "_right_drop_overlay"):
                        self._right_drop_overlay.setGeometry(self.file_editor.rect())
                        self._right_drop_overlay.raise_()
                    self._right_drop_overlay.show()
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            if event.type() == QEvent.Type.DragLeave:
                if hasattr(self, "_right_drop_overlay"):
                    self._right_drop_overlay.hide()
                return True
            if event.type() == QEvent.Type.Drop:
                mime = event.mimeData()
                if not mime:
                    return True
                files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
                media_exts = (".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".flac", ".aac")
                media_files = [f for f in files if f.lower().endswith(media_exts)]
                srt_files = [f for f in files if f.lower().endswith(".srt")]
                if media_files:
                    self._open_media_path(media_files[0])
                    event.acceptProposedAction()
                elif srt_files:
                    self._load_subtitle_file(target="right", path=srt_files[0])
                    event.acceptProposedAction()
                else:
                    event.ignore()
                if hasattr(self, "_right_drop_overlay"):
                    self._right_drop_overlay.hide()
                return True
        if obj is getattr(self, "_central_widget", None):
            if event.type() == QEvent.Type.DragEnter:
                self.dragEnterEvent(event)
                return True
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        if obj is self._media_dock and event.type() == QEvent.Type.Resize:
            if self._media_dock and not self._media_dock.isFloating():
                width = max(1, self._media_dock.width())
                height = int(width * 9 / 16)
                height = max(240, min(480, height))
                self._media_dock.setFixedHeight(height)
                if self._media_view:
                    self._media_view._layout_subtitles()
        return super().eventFilter(obj, event)

    def _on_media_time_changed(self, t: float):
        """Handle MediaView time changes - do NOT sync to waveform.

        Waveform is the master (drives playback), MediaView is the slave (mute).
        We only use this for logging/debugging, not for cursor sync.
        """
        # Logging only - do NOT sync to waveform to prevent cursor shaking
        if (
            not self._media_debug_logged
            and self._media_dock
            and self._media_dock.isVisible()
            and self._log_window
        ):
            right_text = self._pick_text_at_time(self._file_subtitle_manager, t)
            left_text = self._pick_text_at_time(self._subtitle_manager, t)
            self._log_window.append_log(
                f"[MediaView] t={t:.2f} right_len={len(right_text)} left_len={len(left_text)}"
            )
            self._media_debug_logged = True

    def _sync_media_time_from_waveform(self):
        if (
            not self._media_view
            or not self._media_dock
            or not self._media_dock.isVisible()
        ):
            return
        try:
            if (
                self._media_view.player().playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            ):
                return
        except Exception:
            pass
        cursor = self.waveform_right.get_playback_time()
        if self._file_subtitle_manager.segments:
            first_start = float(self._file_subtitle_manager.segments[0].start)
            if cursor < 0 or cursor < first_start:
                cursor = first_start
        if cursor < 0:
            return
        self._media_view.set_time(cursor)
        if self._log_window and (
            self._media_sync_debug_last < 0
            or abs(cursor - self._media_sync_debug_last) >= 1.0
        ):
            self._media_sync_debug_last = cursor
            self._log_window.append_log(
                f"[MediaSync] cursor={cursor:.2f} segs={len(self._file_subtitle_manager.segments)}"
            )

    def _append_media_log(self, message: str) -> None:
        if self._log_window:
            self._log_window.append_log(message)

    def _toggle_active_waveform(self, waveform: WaveformWidget) -> None:
        if waveform is None:
            return
        self._active_waveform = waveform
        # Stop the other waveform to avoid double playback
        if waveform is self.waveform_left:
            self.waveform_right.stop_playback()
        elif waveform is self.waveform_right:
            self.waveform_left.stop_playback()
        waveform.toggle_playback()

    def _is_waveform_playing(self) -> bool:
        return bool(self.waveform_left.is_playing() or self.waveform_right.is_playing())

    def _on_playback_started(self):
        """Handle playback start (Waveform driven)."""
        self._playback_active = True
        # Determine which waveform started
        sender = self.sender()
        if sender == self.waveform_left:
            self._active_waveform = self.waveform_left
            return
        if sender == self.waveform_right:
            self._active_waveform = self.waveform_right
            # Sync MediaView
            if self._media_view and self._media_dock and self._media_dock.isVisible():
                if self._selected_media_file:
                    self._media_view.set_media(self._selected_media_file)
                cursor = self.waveform_right.get_playback_time()
                if cursor < 0 and self._file_subtitle_manager.segments:
                    cursor = float(self._file_subtitle_manager.segments[0].start)
                self._media_pending_seek = cursor
                self._media_view.set_position(int(cursor * 1000))
                self._media_view.set_time(cursor)
                self._media_view.set_muted(True)
                self._media_view.play()
                self._media_pending_play = False
                self._media_sync_timer.start()

    def _on_playback_finished(self):
        """Reset buttons when playback finishes."""
        sender = self.sender()
        if sender == self.waveform_left:
            if self._active_waveform is self.waveform_left:
                self._active_waveform = self.waveform_left
        if sender == self.waveform_right:
            if self._active_waveform is self.waveform_right:
                self._active_waveform = self.waveform_right
            if self._media_view:
                self._media_view.pause()
            self._media_pending_play = False
            self._media_sync_timer.stop()

        self._playback_active = False

        self.live_editor.set_playback_state(None, PlaybackState.IDLE)
        self.file_editor.set_playback_state(None, PlaybackState.IDLE)

    def _on_waveform_audio_loaded(self, data: np.ndarray):
        """Handle audio loaded signal."""
        self._hide_waveform_loading()
        if data is None or len(data) == 0:
            self._update_status("오디오 추출 실패: 파형을 표시할 수 없습니다.")
            return
        self.waveform_right.clear()
        self.waveform_right.update_audio(data, 0.0)
        self.waveform_right.render_full_session()
        if self._file_subtitle_manager.segments:
            self.waveform_right.refresh_segments(self._file_subtitle_manager.segments)

    def _load_audio_background(self, path: str):
        """Load audio for waveform in background."""
        if not path:
            return
        self._show_waveform_loading()
        t = threading.Thread(target=self._ffmpeg_worker, args=(path,), daemon=True)
        t.start()

    def _ffmpeg_worker(self, path: str):
        """Background worker to extract audio using ffmpeg."""
        try:
            # Increase buffer size for large files
            # ffmpeg pipe output can be large.
            # Using communicate() is safer than run() for large outputs as it handles buffering.
            # However, subprocess.run with capture_output=True also uses communicate internally.

            cmd = [
                "ffmpeg",
                "-i",
                path,
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-vn",
                "-hide_banner",
                "-loglevel",
                "error",
                "pipe:1",
            ]

            # Use Popen to stream or ensure no buffer limit hit (though memory is the limit)
            # run() reads all into memory. If file is huge, this might crash.
            # For now, assuming typical files fit in RAM (1 hour @ 16k float32 ~ 230MB).
            # If "only tail visible" issue occurs, it might be due to pipe buffer overflow if not read properly,
            # but run() handles that.
            # CHECK: WaveformWidget might be auto-scrolling or scaling wrong.
            # Let's ensure we read everything.

            # Increase max buffer size if needed? No, run() handles it.

            proc = subprocess.run(cmd, capture_output=True)

            if proc.returncode == 0:
                data = np.frombuffer(proc.stdout, dtype=np.float32)
                # print(f"[Debug] Audio loaded: {len(data)} samples")
                self.waveform_audio_loaded.emit(data)
            else:
                err = proc.stderr.decode(errors="ignore")
                print(f"FFmpeg error: {err}")
                QTimer.singleShot(
                    0,
                    lambda: (
                        self._hide_waveform_loading(),
                        self._update_status("오디오 추출 실패: ffmpeg 오류"),
                    ),
                )

        except Exception as e:
            print(f"Audio load error: {e}")
            QTimer.singleShot(0, self._hide_waveform_loading)

    def _show_waveform_loading(self) -> None:
        if self._waveform_load_dialog and self._waveform_load_dialog.isVisible():
            return
        dialog = QProgressDialog(
            i18n.tr("파형 렌더링 중..."),
            "",
            0,
            0,
            self,
        )
        dialog.setWindowTitle(i18n.tr("오디오 로딩"))
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.show()
        self._waveform_load_dialog = dialog

    def _hide_waveform_loading(self) -> None:
        if self._waveform_load_dialog:
            self._waveform_load_dialog.close()
            self._waveform_load_dialog = None

    def _get_media_proxy_path(self, src_path: str, srt_path: Optional[str]) -> str:
        base = os.path.abspath(src_path)
        try:
            mtime = os.path.getmtime(base)
        except OSError:
            mtime = 0
        srt_mtime = 0
        if srt_path and os.path.exists(srt_path):
            try:
                srt_mtime = os.path.getmtime(srt_path)
            except OSError:
                srt_mtime = 0
        key = f"{base}|{mtime}|{srt_mtime}".encode("utf-8")
        digest = hashlib.md5(key).hexdigest()
        proxy_dir = os.path.join(tempfile.gettempdir(), "thinksub_proxy")
        os.makedirs(proxy_dir, exist_ok=True)
        return os.path.join(proxy_dir, f"proxy_{digest}_360p.mp4")

    def _ensure_media_proxy_async(
        self, src_path: str, srt_path: Optional[str] = None
    ) -> Optional[str]:
        if not src_path:
            return None
        proxy_path = self._get_media_proxy_path(src_path, srt_path)
        if os.path.exists(proxy_path):
            return proxy_path

        task_key = f"{src_path}|{proxy_path}"
        if task_key in self._media_proxy_tasks:
            return None
        self._media_proxy_tasks.add(task_key)

        if self._log_window:
            srt_note = srt_path if srt_path else "(no srt)"
            self._log_window.append_log(
                f"[MediaProxy] Build start: {proxy_path} | srt={srt_note}"
            )

        def _worker(proxy_path: str, srt_path: Optional[str]):
            try:
                temp_path = f"{proxy_path}.tmp"
                vf = "scale=-2:360"
                if srt_path and os.path.exists(srt_path):
                    escaped = self._ffmpeg_escape_filter_path(srt_path)
                    vf = f"subtitles='{escaped}',{vf}"
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-hwaccel",
                    "none",
                    "-i",
                    src_path,
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "32",
                    "-an",
                    "-movflags",
                    "+faststart",
                    temp_path,
                ]
                proc = subprocess.run(cmd, capture_output=True)
                if proc.returncode == 0 and os.path.exists(temp_path):
                    os.replace(temp_path, proxy_path)
                else:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    err = proc.stderr.decode(errors="ignore") if proc else ""
                    QTimer.singleShot(
                        0,
                        lambda: (
                            self._log_window.append_log(
                                f"[MediaProxy] FFmpeg error: {err.strip()}"
                            )
                            if self._log_window
                            else None
                        ),
                    )
            finally:
                self._media_proxy_tasks.discard(task_key)
                if os.path.exists(proxy_path):
                    self.media_proxy_ready.emit(src_path, proxy_path)

        t = threading.Thread(target=_worker, args=(proxy_path, srt_path), daemon=True)
        t.start()
        return None

    def _on_media_proxy_ready(self, src_path: str, proxy_path: str):
        if not self._media_view or not self._media_dock:
            return
        if not self._media_dock.isVisible():
            return
        if self._selected_media_file != src_path:
            return
        if self._log_window:
            try:
                size = os.path.getsize(proxy_path)
            except OSError:
                size = -1
            self._log_window.append_log(
                f"[MediaProxy] Ready: {proxy_path} ({size} bytes)"
            )
        self._media_view.ensure_media_loaded(proxy_path)
        cursor = self._media_pending_seek
        if cursor is None or cursor < 0:
            cursor = self.waveform_right.get_cursor_time()
        if cursor >= 0:
            self._media_view.set_position(int(cursor * 1000))
            self._media_view.set_time(cursor)
        if self._media_pending_play:
            self._media_view.set_muted(True)
            self._media_view.play()
            self._media_pending_play = False

    def _export_wav(self):
        """Export audio as WAV."""
        path, _ = QFileDialog.getSaveFileName(
            self, "오디오 저장", "", "WAV Files (*.wav)"
        )
        if path:
            try:
                self.waveform_left.save_to_wav(path)
                QMessageBox.information(
                    self, "성공", f"오디오가 저장되었습니다:\n{path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "오류", f"저장 중 오류가 발생했습니다:\n{e}")

    def _choose_left_right(
        self, title: str, left_label: str, right_label: str
    ) -> Optional[str]:
        """Return 'left', 'right', or None."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(title)
        left_btn = box.addButton(left_label, QMessageBox.ButtonRole.AcceptRole)
        right_btn = box.addButton(right_label, QMessageBox.ButtonRole.AcceptRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked == left_btn:
            return "left"
        if clicked == right_btn:
            return "right"
        return None

    def _export_srt_choose(self):
        target = self._choose_left_right(
            "SRT 내보내기", "좌측 SRT내보내기", "우측 SRT 내보내기"
        )
        if target == "left":
            self._export_srt_left()
        elif target == "right":
            self._export_srt_right()

    def _export_srt_left(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "좌측 SRT로 저장", "", "SRT Files (*.srt)"
        )
        if not path:
            return
        srt_content = self._subtitle_manager.export_srt()
        with open(path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        self._update_status(f"좌측 SRT 저장됨: {path}")

    def _export_srt_right(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "우측 SRT로 저장", "", "SRT Files (*.srt)"
        )
        if not path:
            return
        srt_content = self._file_subtitle_manager.export_srt()
        with open(path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        self._update_status(f"우측 SRT 저장됨: {path}")

    def _export_metadata(self, target: str = "left"):
        """Export metadata as JSON."""
        import json

        path, _ = QFileDialog.getSaveFileName(
            self, "메타데이터 저장", "", "JSON Files (*.json)"
        )
        if path:
            manager = (
                self._subtitle_manager
                if target == "left"
                else self._file_subtitle_manager
            )
            metadata = manager.export_metadata()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            self._update_status(f"메타데이터 저장됨: {path}")

    def _import_subtitle_choose_side(self, target: str):
        self._import_file(target)

    def _export_lora_data_choose(self):
        target = self._choose_left_right(
            "LoRA용 데이터 내보내기", "좌측 에디터 내용", "우측 에디터 내용"
        )
        if target == "left":
            self._export_lora_data_left()
        elif target == "right":
            self._export_lora_data_right()

    def _export_lora_data_left(self):
        """Export LoRA training data from LEFT (>= 5s segments) to ./whisper_lora_data."""
        from pathlib import Path
        import json
        import wave
        import numpy as np

        out_dir = Path.cwd() / "whisper_lora_data"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Filter segments from left editor manager
        segments = [
            s
            for s in self._subtitle_manager.segments
            if (not getattr(s, "is_hidden", False))
            and (s.text or "").strip()
            and (s.end - s.start) >= 5.0
        ]

        if not segments:
            QMessageBox.information(
                self,
                "내보내기",
                "5초 이상의 자막 구간이 없습니다.",
            )
            return

        manifest = []

        def _unique_base(base: str) -> str:
            # Avoid overwriting
            candidate = base
            n = 1
            while (out_dir / f"{candidate}.wav").exists() or (
                out_dir / f"{candidate}.txt"
            ).exists():
                candidate = f"{base}_{n}"
                n += 1
            return candidate

        for idx, seg in enumerate(segments, start=1):
            base = _unique_base(f"seg_{idx:04d}")
            wav_path = out_dir / f"{base}.wav"
            txt_path = out_dir / f"{base}.txt"

            audio = self.waveform_left.extract_audio(seg.start, seg.end)
            if audio is None or len(audio) == 0:
                continue

            # float32 [-1,1] -> int16
            audio_i16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)

            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_i16.tobytes())

            txt_path.write_text(seg.text.strip(), encoding="utf-8")

            manifest.append(
                {
                    "wav": str(wav_path.name),
                    "text": seg.text.strip(),
                    "start": float(seg.start),
                    "end": float(seg.end),
                }
            )

        (out_dir / "manifest.jsonl").write_text(
            "\n".join(json.dumps(m, ensure_ascii=False) for m in manifest) + "\n",
            encoding="utf-8",
        )

        QMessageBox.information(
            self,
            "내보내기",
            f"LoRA 데이터 {len(manifest)}개 저장됨:\n{out_dir}",
        )

    def _export_lora_data_right(self):
        from pathlib import Path
        import json
        import subprocess

        if not self._selected_media_file:
            QMessageBox.information(
                self,
                "내보내기",
                "우측 LoRA 내보내기는 먼저 파일열기/STT실행으로 미디어를 선택해야 합니다.",
            )
            return

        # Must have word timestamps
        segments = [
            s
            for s in self._file_subtitle_manager.segments
            if (not getattr(s, "is_hidden", False))
            and (s.text or "").strip()
            and (s.end - s.start) >= 5.0
            and getattr(s, "words", None)
            and len(getattr(s, "words", [])) > 0
        ]
        if not segments:
            QMessageBox.information(
                self,
                "내보내기",
                "우측 에디터에 5초 이상 + word timestamps가 있는 자막이 없습니다.",
            )
            return

        out_root = Path.cwd() / "whisper_lora_data"
        out_dir = out_root / "right"
        out_dir.mkdir(parents=True, exist_ok=True)

        manifest = []

        def _unique_base(base: str) -> str:
            candidate = base
            n = 1
            while (out_dir / f"{candidate}.wav").exists() or (
                out_dir / f"{candidate}.txt"
            ).exists():
                candidate = f"{base}_{n}"
                n += 1
            return candidate

        ffmpeg_missing = False
        ffmpeg_failed = 0

        for idx, seg in enumerate(segments, start=1):
            base = _unique_base(f"seg_{idx:04d}")
            wav_path = out_dir / f"{base}.wav"
            txt_path = out_dir / f"{base}.txt"

            # Extract audio via ffmpeg
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{float(seg.start):.3f}",
                "-to",
                f"{float(seg.end):.3f}",
                "-i",
                self._selected_media_file,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except FileNotFoundError:
                ffmpeg_missing = True
                break
            except subprocess.CalledProcessError:
                ffmpeg_failed += 1
                continue
            except Exception:
                ffmpeg_failed += 1
                continue

            txt_path.write_text(seg.text.strip(), encoding="utf-8")
            manifest.append(
                {
                    "wav": str(wav_path.name),
                    "text": seg.text.strip(),
                    "start": float(seg.start),
                    "end": float(seg.end),
                }
            )

        if ffmpeg_missing:
            QMessageBox.critical(
                self,
                "내보내기",
                "ffmpeg를 찾을 수 없습니다. ffmpeg를 설치하고 PATH에 추가한 뒤 다시 시도하세요.",
            )
            return

        if not manifest:
            QMessageBox.information(
                self,
                "내보내기",
                "오디오 추출에 성공한 구간이 없습니다. (ffmpeg 실패 또는 구간/파일 문제)",
            )
            return

        (out_dir / "manifest.jsonl").write_text(
            "\n".join(json.dumps(m, ensure_ascii=False) for m in manifest) + "\n",
            encoding="utf-8",
        )

        msg = f"우측 LoRA 데이터 {len(manifest)}개 저장됨:\n{out_dir}"
        if ffmpeg_failed:
            msg += f"\n(오디오 추출 실패: {ffmpeg_failed}개)"
        QMessageBox.information(self, "내보내기", msg)

    def _build_fw_params_from_settings(
        self, settings: QSettings, mode: str = "live"
    ) -> dict:
        """Build effective faster-whisper transcribe kwargs from QSettings.

        mode: "live" or "file"
        If a key exists in "추가 매개변수" JSON, that value overrides the value from the
        "매개변수" tab.
        """
        prefix = f"fw_{mode}_"

        # Helper to get setting with fallback to legacy keys if needed
        # But we assume settings are migrated/defaults set in SettingsDialog

        tab_params: dict = {
            "beam_size": int(settings.value(f"{prefix}beam_size", 5)),
            "best_of": int(settings.value(f"{prefix}best_of", 1)),
            "length_penalty": float(settings.value(f"{prefix}length_penalty", 0.9)),
            "compression_ratio_threshold": float(
                settings.value(f"{prefix}compression_ratio_threshold", 2.0)
            ),
            # faster-whisper uses log_prob_threshold
            "log_prob_threshold": float(
                settings.value(f"{prefix}logprob_threshold", -1.0)
            ),
        }

        vad_max = float(settings.value(f"{prefix}vad_max_speech_duration_s", 7.0))
        if vad_max > 0:
            tab_params["vad_parameters"] = {"max_speech_duration_s": vad_max}

        extra_raw = settings.value("faster_whisper_params", "{}")
        extra: dict = {}
        try:
            if isinstance(extra_raw, str) and extra_raw.strip():
                parsed = json.loads(extra_raw)
                if isinstance(parsed, dict):
                    extra = parsed
        except Exception:
            extra = {}

        merged = dict(tab_params)
        if isinstance(extra.get("vad_parameters"), dict):
            merged.pop("vad_parameters", None)
        merged.update(extra)
        return merged

    def _build_fw_params_from_dict(self, settings_dict: dict) -> dict:
        """Build effective faster-whisper transcribe kwargs from SettingsDialog dict."""
        tab_params: dict = {}
        try:
            tab_params = {
                "beam_size": int(settings_dict.get("fw_beam_size", 5)),
                "best_of": int(settings_dict.get("fw_best_of", 1)),
                "length_penalty": float(settings_dict.get("fw_length_penalty", 0.9)),
                "compression_ratio_threshold": float(
                    settings_dict.get("fw_compression_ratio_threshold", 2.0)
                ),
                "log_prob_threshold": float(
                    settings_dict.get("fw_logprob_threshold", -1.0)
                ),
            }
            vad_max = float(settings_dict.get("fw_vad_max_speech_duration_s", 7.0))
            if vad_max > 0:
                tab_params["vad_parameters"] = {"max_speech_duration_s": vad_max}
        except Exception:
            tab_params = {}

        extra: dict = {}
        raw = settings_dict.get("faster_whisper_params", "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                extra = parsed
        except Exception:
            extra = {}

        merged = dict(tab_params)
        if isinstance(extra.get("vad_parameters"), dict):
            merged.pop("vad_parameters", None)
        merged.update(extra)
        return merged

    def _on_settings_changed(self, settings: dict):
        """Handle settings changes."""
        # Update VAD parameters
        if "vad_threshold" in settings:
            self._vad_processor.threshold = float(settings["vad_threshold"])
        if "vad_silence_duration" in settings:
            self._vad_processor.min_silence_duration = float(
                settings["vad_silence_duration"]
            )

        # Post-Processing
        if "min_text_length" in settings:
            self._min_text_length = int(settings["min_text_length"])
        if "min_duration" in settings:
            self._min_duration = float(settings["min_duration"])
        if "max_duration" in settings:
            self._max_duration = float(settings["max_duration"])
        if "rms_threshold" in settings:
            self._rms_threshold = float(settings["rms_threshold"])
        if "enable_post_processing" in settings:
            self._enable_post_processing = (
                str(settings["enable_post_processing"]).lower() == "true"
            )
        if "live_abbrev_whitelist" in settings:
            self._live_abbrev_whitelist = self._normalize_abbrev_list(
                settings.get("live_abbrev_whitelist")
            )
        if "stt_abbrev_whitelist" in settings:
            self._stt_abbrev_whitelist = self._normalize_abbrev_list(
                settings.get("stt_abbrev_whitelist")
            )
        if "stt_seg_endmin" in settings:
            self._stt_seg_endmin = float(settings["stt_seg_endmin"])
        if "stt_extend_on_touch" in settings:
            self._stt_extend_on_touch = (
                str(settings["stt_extend_on_touch"]).lower() == "true"
            )
        if "stt_pad_before" in settings:
            self._stt_pad_before = float(settings["stt_pad_before"])
        if "stt_pad_after" in settings:
            self._stt_pad_after = float(settings["stt_pad_after"])

        if "live_wordtimestamp_offset" in settings:
            self._live_wordtimestamp_offset = float(
                settings["live_wordtimestamp_offset"]
            )
        if "live_pad_before" in settings:
            self._live_pad_before = float(settings["live_pad_before"])
        if "live_pad_after" in settings:
            self._live_pad_after = float(settings["live_pad_after"])

        if "ui_language" in settings:
            i18n.install_translator(str(settings["ui_language"]))
            self._retranslate_ui()
            if self._settings_dialog:
                self._settings_dialog.retranslate_ui()
            if self._batch_dialog:
                self._batch_dialog.retranslate_ui()

        if "ui_theme" in settings:
            self._apply_theme(str(settings["ui_theme"]))

        # Audio Device Change
        if "mic_index" in settings or "mic_loopback" in settings:
            idx = int(settings.get("mic_index", -1))
            # User request: remove desktop/loopback capture
            loopback = False
            self._audio_recorder.stop()
            self._audio_recorder.set_device(idx if idx >= 0 else None)
            self._audio_recorder.start()

        # fw_* formatting settings
        for key in (
            "fw_sentence",
            "fw_max_gap",
            "fw_max_line_width",
            "fw_max_line_count",
            "fw_max_comma_cent",
            "fw_one_word",
        ):
            if key in settings:
                self._update_overlay_settings()
                break

        # Propagate transcriber settings if running
        if self._transcriber.is_alive:
            payload = {}
            if "language" in settings:
                payload["language"] = settings["language"]

            # Merge '매개변수' tab with '추가 매개변수' JSON (JSON wins on conflicts)
            if ("faster_whisper_params" in settings) or any(
                k.startswith("fw_") for k in settings.keys()
            ):
                payload["faster_whisper_params"] = self._build_fw_params_from_dict(
                    settings
                )

            if payload:
                self._transcriber.update_settings(payload)

        self._update_overlay_settings()

    def _retranslate_ui(self):
        i18n.apply_widget_translations(self)
        if hasattr(self, "btn_live"):
            self.btn_live.setText(i18n.tr("▶ Live 자막"))
        if hasattr(self, "btn_view"):
            self._update_view_button_text()
        if hasattr(self, "btn_waveform"):
            self.btn_waveform.setText(i18n.tr("📊 웨이브폼"))
        if hasattr(self, "btn_waveform_mode"):
            mode_text = self.btn_waveform_mode.text()
            self.btn_waveform_mode.setText(i18n.tr(mode_text))
        if hasattr(self, "btn_sync"):
            self.btn_sync.setText(i18n.tr("🔗 스크롤"))
        if hasattr(self, "btn_overlay"):
            self.btn_overlay.setText(i18n.tr(self.btn_overlay.text()))
        if hasattr(self, "btn_export"):
            self.btn_export.setText(i18n.tr("💾 내보내기"))
        if hasattr(self, "btn_stt_run"):
            if self._file_stt_running:
                self.btn_stt_run.setText(i18n.tr("🎙 STT중지"))
            else:
                self.btn_stt_run.setText(i18n.tr("🎙 STT실행"))
        if hasattr(self, "btn_stt_batch"):
            self.btn_stt_batch.setText(i18n.tr("🧾 STT일괄"))
        if hasattr(self, "btn_media_view"):
            self.btn_media_view.setText(i18n.tr("🎬 미디어뷰"))
        if hasattr(self, "btn_file_open"):
            self.btn_file_open.setText(i18n.tr("📂 파일열기"))
        if hasattr(self, "btn_settings"):
            self.btn_settings.setText(i18n.tr("⚙ 설정"))
        if hasattr(self, "btn_split"):
            self.btn_split.setText(i18n.tr("✂ 분할"))
        if hasattr(self, "btn_merge"):
            self.btn_merge.setText(i18n.tr("🔗 병합"))
        if hasattr(self, "btn_undo"):
            self.btn_undo.setText(i18n.tr("↩ 실행취소"))
        if hasattr(self, "btn_delete"):
            self.btn_delete.setText(i18n.tr("🗑 삭제"))

        if hasattr(self, "live_editor"):
            self.live_editor.retranslate_ui()
        if hasattr(self, "file_editor"):
            self.file_editor.retranslate_ui()
        if hasattr(self, "waveform_left"):
            self.waveform_left.retranslate_ui()
        if hasattr(self, "waveform_right"):
            self.waveform_right.retranslate_ui()
        if hasattr(self, "_log_window") and self._log_window:
            self._log_window.retranslate_ui()

    def dragEnterEvent(self, a0: Optional[QDragEnterEvent]):
        if a0 is None:
            return
        event = cast(Any, a0)
        mime = event.mimeData()
        if mime is None:
            return
        if mime.hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, a0: Optional[QDropEvent]):
        from pathlib import Path

        if a0 is None:
            return
        event = cast(Any, a0)

        mime = event.mimeData()
        if mime is None:
            return

        files = [u.toLocalFile() for u in mime.urls()]
        files = [f for f in files if f]
        if not files:
            return

        # Determine drop target (left/right editor) based on cursor position
        try:
            pos = event.position().toPoint()
        except Exception:
            pos = event.pos()

        target = None
        w = self.childAt(pos)
        while w is not None:
            if w is self.live_editor:
                target = "left"
                break
            if w is self.file_editor:
                target = "right"
                break
            w = w.parentWidget()

        media_exts = {".mp3", ".wav", ".m4a", ".mp4", ".mkv", ".flac", ".aac"}

        srt_files = [f for f in files if Path(f).suffix.lower() == ".srt"]
        media_files = [f for f in files if Path(f).suffix.lower() in media_exts]

        # Default target: right (File)
        if target is None:
            target = "right"

        if target == "left":
            if not srt_files:
                self._update_status("좌측에는 .srt 파일만 드롭할 수 있습니다.")
                return
            # Left: load first SRT
            self._load_subtitle_file(target="left", path=srt_files[0])
            return

        # Right: allow media and/or SRT

        if srt_files:
            self._load_subtitle_file(target="right", path=srt_files[0])

            # If matching media exists, set it for media view/export convenience
            srt_path = Path(srt_files[0])
            for ext in media_exts:
                cand = srt_path.with_suffix(ext)
                if cand.exists():
                    self._selected_media_file = str(cand)
                    self._load_audio_background(str(cand))
                    break

        if media_files:
            media_path = Path(media_files[0])
            self._selected_media_file = str(media_path)

            # Auto-pair SRT if exists; if found, do not auto-run STT
            paired_srt = media_path.with_suffix(".srt")
            if paired_srt.exists():
                self._load_subtitle_file(target="right", path=str(paired_srt))
                self._update_status(f"미디어+자막 연결됨: {media_path.name}")
                self._load_audio_background(str(media_path))
                return

            # No subtitles: set media but do not run STT immediately
            self._update_status(f"미디어 선택됨: {media_path.name}")
            self._update_file_stt_ui()
            self._load_audio_background(str(media_path))

            # Switch to bottom waveform view automatically
            self._waveform_mode = "bottom"
            if hasattr(self, "btn_waveform_mode"):
                self.btn_waveform_mode.setText("웨이브폼 하단")
            self._apply_waveform_mode()
            return

        if not srt_files:
            ext = Path(files[0]).suffix.lower().lstrip(".")
            self._update_status(f"지원하지 않는 파일 형식: {ext}")

    def _start_file_transcription(self, file_path: str):
        """Start file transcription."""
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self, "STT", "Live 실행 중에는 STT를 실행할 수 없습니다."
            )
            return

        # Explicitly ensure mic recorder is stopped/released
        if self._audio_recorder and self._audio_recorder.is_running:
            self._audio_recorder.stop()

        self._selected_media_file = file_path
        self._file_stt_running = True
        self._update_file_stt_ui()
        self._load_audio_background(file_path)
        # 1. Open logs
        if not self._log_window:
            self._log_window = LogWindow(self)
        self._log_window.show()

        self._log_window.append_log(f"파일 자막 생성 시작: {file_path}")

        # 2. Clear previous file results
        self._file_subtitle_manager.clear()
        self.file_editor.refresh()

        # 3. Ensure model is loaded (start process if not)
        settings = QSettings("ThinkSub", "ThinkSub2")
        config = {
            "model": settings.value("model", "large-v3-turbo"),
            "device": settings.value("device", "cuda"),
            "language": settings.value("language", "ko"),
        }
        config["faster_whisper_params"] = self._build_fw_params_from_settings(
            settings, mode="file"
        )
        self._transcriber.start(config)
        if not self._transcriber_ready:
            self._pending_file_transcribe = file_path
            self._transcriber.load_model()
        else:
            # 4. Request transcription
            self._transcriber.transcribe_file(file_path)

        # 5. Start polling if not already
        if not self._result_timer.isActive():
            self._result_timer.start(50)
            self._log_timer.start(100)

    def closeEvent(self, a0: Optional[QCloseEvent]):
        """Handle window close."""
        if a0 is None:
            return
        event = cast(Any, a0)
        if self._state != AppState.IDLE:
            self._stop_live()

        if self._log_window:
            self._log_window.close()

        if self._settings_dialog:
            self._settings_dialog.close()

        event.accept()
