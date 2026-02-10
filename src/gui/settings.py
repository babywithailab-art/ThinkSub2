"""
Settings Dialog for ThinkSub2.
Handles Transcriber, UI, and Shortcut settings.
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QFileDialog,
)
from PySide6.QtCore import Signal, QSettings, QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from src.gui.magnetic import MagneticDialog
from src.gui import i18n

import json
import os
import sys
from typing import Any, cast


class NoScrollSpinBox(QSpinBox):
    """SpinBox that ignores wheel events."""

    def wheelEvent(self, e):
        e.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    """DoubleSpinBox that ignores wheel events."""

    def wheelEvent(self, e):
        e.ignore()


class SettingsDialog(MagneticDialog):
    """
    Settings dialog with tabs for different categories.
    """

    settings_changed = Signal(dict)
    log_window_requested = Signal()

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

    DEFAULT_SETTINGS = {
        "model": "large-v3-turbo",
        "language": "ko",
        "device": "cuda",
        "mic_index": -1,
        "mic_loopback": False,
        # JSON string of faster-whisper transcribe kwargs
        "faster_whisper_params": "{}",
        # --- Live Params (fw_live_*) ---
        "fw_live_sentence": True,
        "fw_live_max_gap": 0.8,
        "fw_live_max_line_width": 55,
        "fw_live_max_line_count": 2,
        "fw_live_max_comma_cent": 70,
        "fw_live_one_word": 0,
        "fw_live_vad_max_speech_duration_s": 7.0,
        "fw_live_vad_speech_pad_ms": 50,
        "fw_live_length_penalty": 0.9,
        "fw_live_beam_size": 5,
        "fw_live_best_of": 1,
        "fw_live_compression_ratio_threshold": 2.4,
        "fw_live_logprob_threshold": -1.0,
        "fw_live_no_speech_threshold": 0.6,
        # --- File Params (fw_file_*) ---
        "fw_file_sentence": True,
        "fw_file_max_gap": 0.8,
        "fw_file_max_line_width": 55,
        "fw_file_max_line_count": 2,
        "fw_file_max_comma_cent": 70,
        "fw_file_one_word": 0,
        "fw_file_vad_max_speech_duration_s": 7.0,
        "fw_file_vad_speech_pad_ms": 50,
        "fw_file_vad_min_speech_duration_ms": 250,
        "fw_file_vad_min_silence_duration_ms": 3000,
        "fw_file_vad_window_size_samples": 1536,
        "fw_file_vad_threshold": 0.45,
        "fw_file_vad_filter": True,
        "fw_file_word_timestamps": True,
        "fw_file_length_penalty": 0.9,
        "fw_file_beam_size": 5,
        "fw_file_best_of": 1,
        "fw_file_compression_ratio_threshold": 2.4,
        "fw_file_logprob_threshold": -1.0,
        "fw_file_no_speech_threshold": 0.6,
        "fw_file_condition_on_previous_text": True,
        "fw_file_prompt_reset_on_temperature": 0.5,
        "fw_file_temperature_increment_on_fallback": 0.2,
        "ui_language": "ko",
        "ui_theme": "dark",
        "vad_threshold": 0.5,
        "vad_silence_duration": 0.5,
        "subtitle_font_size": 20,
        "subtitle_max_chars": 40,
        "subtitle_max_lines": 2,
        "subtitle_opacity": 80,  # Percentage
        "min_text_length": 0,
        "min_duration": 0.0,  # New: Min Duration Filter
        "max_duration": 29.9,  # New: Max Duration Filter (>= removes)
        "rms_threshold": 0.002,
        "enable_post_processing": True,
        "live_wordtimestamp_offset": 0.0,
        "live_pad_before": 0.1,
        "live_pad_after": 0.1,
        "live_merge_short_len": 2,  # New: Merge if len <= N
        "live_merge_short_gap": 1.0,  # New: Merge if gap <= M
        "compute_type": "float16",
        "live_abbrev_whitelist": DEFAULT_ABBREV_WHITELIST,
        "stt_abbrev_whitelist": DEFAULT_ABBREV_WHITELIST,
        "stt_seg_endmin": 0.05,
        "stt_extend_on_touch": False,
        "stt_pad_before": 0.1,
        "stt_pad_after": 0.1,
        "stt_merge_short_len": 2,  # New
        "stt_merge_short_gap": 1.0,  # New
        # --- FFmpeg Segmentation (ffmpeg_*) ---
        "ffmpeg_segmentation_enabled": False,
        "ffmpeg_silence_threshold": -30.0,
        "ffmpeg_min_silence_duration": 0.5,
        "ffmpeg_padding_ms": 100,
        "ffmpeg_split_30min": False,
        "ffmpeg_padding_ms": 100,
        "ffmpeg_split_30min": False,
        # --- Custom Model ---
        "custom_model_path": "",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("ThinkSub2 - 설정"))
        self.setMinimumSize(500, 400)

        self._settings = QSettings("ThinkSub", "ThinkSub2")
        self._load_settings()
        self._original = self._current.copy()  # Backup for cancel/revert
        self._saved = False  # Track if settings were saved (OK clicked)

        self._setup_ui()
        # Fix UI Sizing (User Request)
        self.setStyleSheet("""
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
                min-height: 30px;
                font-size: 14px;
                padding: 2px;
            }
            QLabel {
                font-size: 14px;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                width: 20px;
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                width: 20px;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                width: 25px; /* Increased for better clickability */
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                width: 25px; /* Increased for better clickability */
            }
            QTabWidget::pane {
                border: 1px solid #444;
            }
        """)
        self.retranslate_ui()
        self._connect_changes()  # Connect signals for real-time updates

    def _connect_changes(self):
        """Connect widgets to real-time update handler."""
        # Transcriber
        self.combo_model.currentTextChanged.connect(self._on_change)
        self.combo_language.currentTextChanged.connect(self._on_change)
        self.combo_device.currentTextChanged.connect(self._on_change)
        self.combo_compute_type.currentTextChanged.connect(self._on_change)
        self.combo_mic.currentIndexChanged.connect(self._on_change)

        # Faster-Whisper Params (Live)
        self.chk_fw_live_sentence.toggled.connect(self._on_change)
        self.spin_fw_live_max_gap.valueChanged.connect(self._on_change)
        self.spin_fw_live_max_line_width.valueChanged.connect(self._on_change)
        self.spin_fw_live_max_line_count.valueChanged.connect(self._on_change)
        self.spin_fw_live_max_comma_cent.valueChanged.connect(self._on_change)
        self.spin_fw_live_one_word.valueChanged.connect(self._on_change)
        self.spin_fw_live_vad_max_speech_duration_s.valueChanged.connect(
            self._on_change
        )
        self.spin_fw_live_vad_speech_pad_ms.valueChanged.connect(self._on_change)
        self.spin_fw_live_length_penalty.valueChanged.connect(self._on_change)
        self.spin_fw_live_beam_size.valueChanged.connect(self._on_change)
        self.spin_fw_live_best_of.valueChanged.connect(self._on_change)
        self.spin_fw_live_compression_ratio_threshold.valueChanged.connect(
            self._on_change
        )
        self.spin_fw_live_logprob_threshold.valueChanged.connect(self._on_change)

        # Faster-Whisper Params (File)
        self.chk_fw_file_sentence.toggled.connect(self._on_change)
        self.spin_fw_file_max_gap.valueChanged.connect(self._on_change)
        self.spin_fw_file_max_line_width.valueChanged.connect(self._on_change)
        self.spin_fw_file_max_line_count.valueChanged.connect(self._on_change)
        self.spin_fw_file_max_comma_cent.valueChanged.connect(self._on_change)
        self.spin_fw_file_one_word.valueChanged.connect(self._on_change)
        self.spin_fw_file_vad_max_speech_duration_s.valueChanged.connect(
            self._on_change
        )
        self.chk_fw_file_vad_filter.toggled.connect(self._on_change)
        self.chk_fw_file_word_timestamps.toggled.connect(self._on_change)
        self.spin_fw_file_vad_speech_pad_ms.valueChanged.connect(self._on_change)
        self.spin_fw_file_vad_min_speech_duration_ms.valueChanged.connect(
            self._on_change
        )
        self.spin_fw_file_vad_min_silence_duration_ms.valueChanged.connect(
            self._on_change
        )
        self.spin_fw_file_vad_window_size_samples.valueChanged.connect(self._on_change)
        self.spin_fw_file_vad_threshold.valueChanged.connect(self._on_change)
        self.spin_fw_file_length_penalty.valueChanged.connect(self._on_change)
        self.spin_fw_file_beam_size.valueChanged.connect(self._on_change)
        self.spin_fw_file_best_of.valueChanged.connect(self._on_change)
        self.spin_fw_file_compression_ratio_threshold.valueChanged.connect(
            self._on_change
        )
        self.spin_fw_file_logprob_threshold.valueChanged.connect(self._on_change)
        self.spin_fw_file_no_speech_threshold.valueChanged.connect(self._on_change)

        # VAD
        self.spin_vad_threshold.valueChanged.connect(self._on_change)
        self.spin_silence_duration.valueChanged.connect(self._on_change)

        # Subtitle
        self.spin_font_size.valueChanged.connect(self._on_change)
        self.spin_max_chars.valueChanged.connect(self._on_change)
        self.spin_max_lines.valueChanged.connect(self._on_change)
        self.spin_opacity.valueChanged.connect(self._on_change)

        # Post-Processing
        self.chk_enable_pp.toggled.connect(self._on_change)
        self.spin_min_length.valueChanged.connect(self._on_change)
        self.spin_rms_filter.valueChanged.connect(self._on_change)
        self.spin_min_duration.valueChanged.connect(self._on_change)
        self.spin_max_duration.valueChanged.connect(self._on_change)
        self.spin_live_wordtimestamp_offset.valueChanged.connect(self._on_change)
        self.spin_live_pad_before.valueChanged.connect(self._on_change)
        self.spin_live_pad_after.valueChanged.connect(self._on_change)
        self.spin_live_merge_short_len.valueChanged.connect(self._on_change)
        self.spin_live_merge_short_gap.valueChanged.connect(self._on_change)

        # STT Post-Processing
        self.spin_stt_seg_endmin.valueChanged.connect(self._on_change)
        self.chk_stt_extend_on_touch.toggled.connect(self._on_change)
        self.spin_stt_pad_before.valueChanged.connect(self._on_change)
        self.spin_stt_pad_after.valueChanged.connect(self._on_change)
        self.spin_stt_merge_short_len.valueChanged.connect(self._on_change)
        self.spin_stt_merge_short_gap.valueChanged.connect(self._on_change)

        # UI
        self.combo_ui_language.currentIndexChanged.connect(self._on_change)
        self.combo_ui_theme.currentIndexChanged.connect(self._on_change)

        # FFmpeg Segmentation
        self.grp_ffmpeg.toggled.connect(self._on_change)
        self.spin_ffmpeg_silence_threshold.valueChanged.connect(self._on_change)
        self.spin_ffmpeg_min_silence_duration.valueChanged.connect(self._on_change)
        self.spin_ffmpeg_padding_ms.valueChanged.connect(self._on_change)
        self.chk_ffmpeg_split_30min.toggled.connect(self._on_change)

    def _on_change(self):
        """Handle any widget value change."""
        self._update_current_from_ui()
        self.settings_changed.emit(self._current)
        if "ui_language" in self._current:
            i18n.install_translator(str(self._current["ui_language"]))
            self.retranslate_ui()

    def _on_model_changed(self, text):
        self._update_custom_model_visibility()
        self._on_change()

    def _update_custom_model_visibility(self):
        is_custom = self.combo_model.currentText() == "Custom Model..."
        if hasattr(self, "custom_model_widget"):
            self.custom_model_widget.setVisible(is_custom)
            # Label visibility hack (QFormLayout manages labels internally)
            # We need to find the label for the row. 
            # Simplified approach: Just toggle widget, layout handles space?
            # QFormLayout sometimes leaves empty space.
            # Let's try to find the label if possible, or just rely on widget visibility.
            layout = self.custom_model_widget.parentWidget().layout()
            if isinstance(layout, QFormLayout):
                label = layout.labelForField(self.custom_model_widget)
                if label:
                    label.setVisible(is_custom)

    def _browse_custom_model(self):
        # Allow selecting the model file (model.bin) to make it easier to find the folder
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "모델 파일 선택 (model.bin)",
            "",
            "Model Files (*.bin);;All Files (*.*)",
        )
        if file_path:
            # Faster-Whisper expects the folder path, not the file path
            # But the user feels safer picking the file.
            # We will use the directory of the selected file.
            folder_path = os.path.dirname(file_path)
            self.line_custom_model.setText(folder_path)
            self._on_change()

    def _update_current_from_ui(self):
        """Update self._current dict from UI widgets."""
        self._current["model"] = self.combo_model.currentText()
        self._current["custom_model_path"] = self.line_custom_model.text()
        self._current["language"] = self.combo_language.currentText()
        self._current["device"] = self.combo_device.currentText()
        self._current["compute_type"] = self.combo_compute_type.currentText()
        self._current["vad_threshold"] = self.spin_vad_threshold.value()
        self._current["vad_silence_duration"] = self.spin_silence_duration.value()
        self._current["ui_language"] = (
            "ko" if self.combo_ui_language.currentIndex() == 0 else "en"
        )

        theme_idx = self.combo_ui_theme.currentIndex()
        if theme_idx == 1:
            self._current["ui_theme"] = "light"
        elif theme_idx == 2:
            self._current["ui_theme"] = "navy"
        else:
            self._current["ui_theme"] = "dark"

        self._current["subtitle_font_size"] = self.spin_font_size.value()
        self._current["subtitle_max_chars"] = self.spin_max_chars.value()
        self._current["subtitle_max_lines"] = self.spin_max_lines.value()
        self._current["subtitle_opacity"] = self.spin_opacity.value()

        self._current["min_text_length"] = self.spin_min_length.value()
        self._current["rms_threshold"] = self.spin_rms_filter.value()
        self._current["min_duration"] = self.spin_min_duration.value()
        self._current["max_duration"] = self.spin_max_duration.value()
        self._current["enable_post_processing"] = self.chk_enable_pp.isChecked()
        self._current["live_wordtimestamp_offset"] = (
            self.spin_live_wordtimestamp_offset.value()
        )
        self._current["live_pad_before"] = self.spin_live_pad_before.value()
        self._current["live_pad_after"] = self.spin_live_pad_after.value()
        self._current["live_merge_short_len"] = self.spin_live_merge_short_len.value()
        self._current["live_merge_short_gap"] = self.spin_live_merge_short_gap.value()

        self._current["stt_seg_endmin"] = self.spin_stt_seg_endmin.value()
        self._current["stt_extend_on_touch"] = self.chk_stt_extend_on_touch.isChecked()
        self._current["stt_pad_before"] = self.spin_stt_pad_before.value()
        self._current["stt_pad_after"] = self.spin_stt_pad_after.value()
        self._current["stt_merge_short_len"] = self.spin_stt_merge_short_len.value()
        self._current["stt_merge_short_gap"] = self.spin_stt_merge_short_gap.value()

        # Faster-Whisper Params (Live)
        self._current["fw_live_sentence"] = self.chk_fw_live_sentence.isChecked()
        self._current["fw_live_max_gap"] = self.spin_fw_live_max_gap.value()
        self._current["fw_live_max_line_width"] = (
            self.spin_fw_live_max_line_width.value()
        )
        self._current["fw_live_max_line_count"] = (
            self.spin_fw_live_max_line_count.value()
        )
        self._current["fw_live_max_comma_cent"] = (
            self.spin_fw_live_max_comma_cent.value()
        )
        self._current["fw_live_one_word"] = self.spin_fw_live_one_word.value()
        self._current["fw_live_vad_max_speech_duration_s"] = (
            self.spin_fw_live_vad_max_speech_duration_s.value()
        )
        self._current["fw_live_length_penalty"] = (
            self.spin_fw_live_length_penalty.value()
        )
        self._current["fw_live_beam_size"] = self.spin_fw_live_beam_size.value()
        self._current["fw_live_best_of"] = self.spin_fw_live_best_of.value()
        self._current["fw_live_compression_ratio_threshold"] = (
            self.spin_fw_live_compression_ratio_threshold.value()
        )
        self._current["fw_live_logprob_threshold"] = (
            self.spin_fw_live_logprob_threshold.value()
        )

        # Faster-Whisper Params (File)
        self._current["fw_file_sentence"] = self.chk_fw_file_sentence.isChecked()
        self._current["fw_file_max_gap"] = self.spin_fw_file_max_gap.value()
        self._current["fw_file_max_line_width"] = (
            self.spin_fw_file_max_line_width.value()
        )
        self._current["fw_file_max_line_count"] = (
            self.spin_fw_file_max_line_count.value()
        )
        self._current["fw_file_max_comma_cent"] = (
            self.spin_fw_file_max_comma_cent.value()
        )
        self._current["fw_file_one_word"] = self.spin_fw_file_one_word.value()
        self._current["fw_file_vad_max_speech_duration_s"] = (
            self.spin_fw_file_vad_max_speech_duration_s.value()
        )
        self._current["fw_file_vad_filter"] = self.chk_fw_file_vad_filter.isChecked()
        self._current["fw_file_word_timestamps"] = (
            self.chk_fw_file_word_timestamps.isChecked()
        )
        self._current["fw_file_vad_speech_pad_ms"] = (
            self.spin_fw_file_vad_speech_pad_ms.value()
        )
        self._current["fw_file_vad_min_speech_duration_ms"] = (
            self.spin_fw_file_vad_min_speech_duration_ms.value()
        )
        self._current["fw_file_vad_min_silence_duration_ms"] = (
            self.spin_fw_file_vad_min_silence_duration_ms.value()
        )
        self._current["fw_file_vad_window_size_samples"] = (
            self.spin_fw_file_vad_window_size_samples.value()
        )
        self._current["fw_file_vad_threshold"] = (
            self.spin_fw_file_vad_threshold.value()
        )
        self._current["fw_file_length_penalty"] = (
            self.spin_fw_file_length_penalty.value()
        )
        self._current["fw_file_beam_size"] = self.spin_fw_file_beam_size.value()
        self._current["fw_file_best_of"] = self.spin_fw_file_best_of.value()
        self._current["fw_file_compression_ratio_threshold"] = (
            self.spin_fw_file_compression_ratio_threshold.value()
        )
        self._current["fw_file_logprob_threshold"] = (
            self.spin_fw_file_logprob_threshold.value()
        )
        self._current["fw_file_no_speech_threshold"] = (
            self.spin_fw_file_no_speech_threshold.value()
        )

        # Save Mic Index via mapping
        # User request: desktop/loopback disabled
        self._current["mic_loopback"] = False
        idx = self.combo_mic.currentIndex()
        if hasattr(self, "_audio_devices") and 0 <= idx < len(self._audio_devices):
            entry = self._audio_devices[idx]
            self._current["mic_index"] = int(entry.get("index", -1))
            self._current["mic_loopback"] = False

        # FFmpeg Segmentation
        self._current["ffmpeg_segmentation_enabled"] = self.grp_ffmpeg.isChecked()
        self._current["ffmpeg_silence_threshold"] = self.spin_ffmpeg_silence_threshold.value()
        self._current["ffmpeg_min_silence_duration"] = self.spin_ffmpeg_min_silence_duration.value()
        self._current["ffmpeg_padding_ms"] = self.spin_ffmpeg_padding_ms.value()
        self._current["ffmpeg_split_30min"] = self.chk_ffmpeg_split_30min.isChecked()

    def _load_settings(self):
        """Load settings from QSettings."""
        self._current = {}
        for key, default in self.DEFAULT_SETTINGS.items():
            val = self._settings.value(key, default)
            # Ensure proper types
            if isinstance(default, float):
                self._current[key] = float(val)
            elif isinstance(default, int) and not isinstance(default, bool):
                self._current[key] = int(val)
            elif isinstance(default, bool):
                self._current[key] = str(val).lower() == "true"
            else:
                self._current[key] = val

        # Load any extra keys that may exist (forward compatibility)
        for key in self._settings.allKeys():
            if key not in self._current:
                self._current[key] = self._settings.value(key)

        self._current["live_abbrev_whitelist"] = self._normalize_abbrev_list(
            self._current.get("live_abbrev_whitelist", self.DEFAULT_ABBREV_WHITELIST)
        )
        self._current["stt_abbrev_whitelist"] = self._normalize_abbrev_list(
            self._current.get("stt_abbrev_whitelist", self.DEFAULT_ABBREV_WHITELIST)
        )

        # Safety Check for VAD
        if self._current["vad_threshold"] > 0.1:
            self._current["vad_threshold"] = 0.005
            self._settings.setValue("vad_threshold", 0.005)

    def _save_settings(self):
        """Save current settings to QSettings."""
        for key, value in self._current.items():
            self._settings.setValue(key, value)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        self.tabs = QTabWidget()

        # Transcriber Tab
        transcriber_tab = self._create_transcriber_tab()
        self.tabs.addTab(transcriber_tab, i18n.tr("Faster-Whisper"))

        # Params Tab (right of Faster-Whisper)
        self.tabs.addTab(self._create_fw_live_params_tab(), i18n.tr("Live 자막"))
        self.tabs.addTab(self._create_fw_file_params_tab(), i18n.tr("STT 실행"))

        # Subtitle Tab
        self.tabs.addTab(self._create_subtitle_tab(), i18n.tr("자막"))

        # UI Tab
        ui_tab = self._create_ui_tab()
        self.tabs.addTab(ui_tab, i18n.tr("UI"))

        # Shortcuts Tab
        shortcuts_tab = self._create_shortcuts_tab()
        self.tabs.addTab(shortcuts_tab, i18n.tr("단축키"))

        layout.addWidget(self.tabs)

        # Buttons
        btn_layout = QHBoxLayout()

        self.btn_default = QPushButton(i18n.tr("기본값 복원"))
        self.btn_default.clicked.connect(self._reset_to_defaults)
        btn_layout.addWidget(self.btn_default)

        self.btn_log = QPushButton(i18n.tr("로그창"))
        self.btn_log.clicked.connect(self.log_window_requested.emit)
        btn_layout.addWidget(self.btn_log)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton(i18n.tr("취소"))
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton(i18n.tr("확인"))
        self.btn_apply.clicked.connect(self._apply_settings)
        btn_layout.addWidget(self.btn_apply)

        layout.addLayout(btn_layout)

    def retranslate_ui(self):
        self.setWindowTitle(i18n.tr("ThinkSub2 - 설정"))
        if hasattr(self, "tabs"):
            self.tabs.setTabText(0, i18n.tr("Faster-Whisper"))
            self.tabs.setTabText(1, i18n.tr("Live 자막"))
            self.tabs.setTabText(2, i18n.tr("STT 실행"))
            self.tabs.setTabText(3, i18n.tr("자막"))
            self.tabs.setTabText(4, i18n.tr("UI"))
            self.tabs.setTabText(5, i18n.tr("단축키"))

        if hasattr(self, "combo_ui_language"):
            self.combo_ui_language.blockSignals(True)
            self.combo_ui_language.clear()
            self.combo_ui_language.addItems([i18n.tr("한국어"), i18n.tr("English")])
            current_ui_lang = self._current.get("ui_language", "ko")
            self.combo_ui_language.setCurrentIndex(0 if current_ui_lang == "ko" else 1)
            self.combo_ui_language.blockSignals(False)

        if hasattr(self, "combo_ui_theme"):
            self.combo_ui_theme.blockSignals(True)
            self.combo_ui_theme.clear()
            self.combo_ui_theme.addItems(
                [i18n.tr("다크 모드"), i18n.tr("라이트 모드"), i18n.tr("남색 모드")]
            )
            current_theme = self._current.get("ui_theme", "dark")
            idx = 0
            if current_theme == "light":
                idx = 1
            elif current_theme == "navy":
                idx = 2
            self.combo_ui_theme.setCurrentIndex(idx)
            self.combo_ui_theme.blockSignals(False)

        if hasattr(self, "btn_default"):
            self.btn_default.setText(i18n.tr("기본값 복원"))
        if hasattr(self, "btn_log"):
            self.btn_log.setText(i18n.tr("로그창"))
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.setText(i18n.tr("취소"))
        if hasattr(self, "btn_apply"):
            self.btn_apply.setText(i18n.tr("확인"))

        if hasattr(self, "btn_extra_params"):
            self.btn_extra_params.setText(i18n.tr("추가 매개변수..."))
            self.btn_extra_params.setToolTip(
                i18n.tr(
                    "faster-whisper WhisperModel.transcribe()에 전달할 추가 매개변수를 JSON으로 설정합니다."
                )
            )
        if hasattr(self, "chk_enable_pp"):
            self.chk_enable_pp.setToolTip(
                i18n.tr("체크 해제 시 모든 필터를 무시하고 모든 자막을 표시합니다.")
            )
        if hasattr(self, "spin_min_length"):
            self.spin_min_length.setToolTip(
                i18n.tr("지정된 글자 수보다 짧은 자막은 무시합니다. (0 = 끄기)")
            )
        if hasattr(self, "spin_rms_filter"):
            self.spin_rms_filter.setToolTip(
                i18n.tr("이 값보다 평균 볼륨(RMS)이 낮은 구간은 무시합니다.")
            )
        if hasattr(self, "spin_min_duration"):
            self.spin_min_duration.setToolTip(
                i18n.tr("지정된 시간보다 짧은 음성 구간은 무시합니다. (0 = 끄기)")
            )
        if hasattr(self, "spin_max_duration"):
            self.spin_max_duration.setToolTip(
                i18n.tr("지정된 시간 이상인 음성 구간은 무시합니다. (0 = 끄기)")
            )
        if hasattr(self, "spin_live_wordtimestamp_offset"):
            self.spin_live_wordtimestamp_offset.setToolTip(
                i18n.tr("Live 자막의 타임스탬프를 지정한 시간만큼 이동합니다.")
            )
        if hasattr(self, "spin_live_pad_before"):
            self.spin_live_pad_before.setToolTip(
                i18n.tr("Live 자막 시작 시간을 앞당겨 구간을 확장합니다.")
            )
        if hasattr(self, "spin_live_pad_after"):
            self.spin_live_pad_after.setToolTip(
                i18n.tr("Live 자막 종료 시간을 늦춰 구간을 확장합니다.")
            )
        if hasattr(self, "chk_fw_live_sentence"):
            self.chk_fw_live_sentence.setToolTip(
                i18n.tr(
                    "문장 단위로 끊어서 자막을 만들도록 시도합니다. (프로젝트에서 실제 적용 여부는 내보내기/후처리 구현에 따릅니다)"
                )
            )
        if hasattr(self, "spin_fw_live_max_gap"):
            self.spin_fw_live_max_gap.setToolTip(
                i18n.tr(
                    "자막 분할 시, 두 단어/세그먼트 사이의 최대 허용 간격(초)입니다."
                )
            )
        if hasattr(self, "spin_fw_live_max_line_width"):
            self.spin_fw_live_max_line_width.setToolTip(
                i18n.tr(
                    "자막 한 줄의 최대 문자 폭(대략적인 글자수)입니다. (SRT 줄바꿈에 사용)"
                )
            )
        if hasattr(self, "spin_fw_live_max_line_count"):
            self.spin_fw_live_max_line_count.setToolTip(
                i18n.tr("자막 한 항목에서 허용하는 최대 줄 수입니다.")
            )
        if hasattr(self, "spin_fw_live_max_comma_cent"):
            self.spin_fw_live_max_comma_cent.setToolTip(
                i18n.tr("쉼표(,) 기준으로 분할할 때의 기준 퍼센트 값입니다.")
            )
        if hasattr(self, "spin_fw_live_one_word"):
            self.spin_fw_live_one_word.setToolTip(
                i18n.tr(
                    "1이면 한 단어씩 자막으로 만들도록 강제합니다. 0이면 비활성화입니다."
                )
            )
        if hasattr(self, "spin_fw_live_vad_max_speech_duration_s"):
            self.spin_fw_live_vad_max_speech_duration_s.setToolTip(
                i18n.tr(
                    "(파일 STT에서) VAD가 한 번에 잡을 수 있는 최대 발화 길이(초)입니다."
                )
            )
        if hasattr(self, "spin_fw_live_length_penalty"):
            self.spin_fw_live_length_penalty.setToolTip(
                i18n.tr(
                    "디코딩에서 길이 패널티입니다. 값이 클수록 짧은 결과를 선호합니다."
                )
            )
        if hasattr(self, "spin_fw_live_beam_size"):
            self.spin_fw_live_beam_size.setToolTip(
                i18n.tr(
                    "Beam search 빔 크기입니다. 클수록 정확도가 올라갈 수 있지만 느려집니다."
                )
            )
        if hasattr(self, "spin_fw_live_best_of"):
            self.spin_fw_live_best_of.setToolTip(
                i18n.tr(
                    "샘플링 시 후보 중 best_of 개 중 최적을 선택합니다. (temperature>0에서 의미가 큼)"
                )
            )
        if hasattr(self, "spin_fw_live_compression_ratio_threshold"):
            self.spin_fw_live_compression_ratio_threshold.setToolTip(
                i18n.tr(
                    "압축 비율이 이 값보다 크면 (반복/이상 출력으로 판단) 해당 결과를 거를 수 있습니다."
                )
            )
        if hasattr(self, "spin_fw_live_logprob_threshold"):
            self.spin_fw_live_logprob_threshold.setToolTip(
                i18n.tr(
                    "평균 로그확률이 이 값보다 낮으면 결과를 거를 수 있습니다. (-1.0은 보통 관대한 값)"
                )
            )
        if hasattr(self, "spin_stt_seg_endmin"):
            self.spin_stt_seg_endmin.setToolTip(
                i18n.tr(
                    "세그먼트 최소 길이(초)입니다. 이 값보다 짧으면 끝 시간을 늘립니다."
                )
            )
        if hasattr(self, "chk_stt_extend_on_touch"):
            self.chk_stt_extend_on_touch.setToolTip(
                i18n.tr("자막 구간을 편집할 때 인접 구간과 맞닿도록 확장합니다.")
            )
        if hasattr(self, "chk_fw_file_sentence"):
            self.chk_fw_file_sentence.setToolTip(
                i18n.tr("문장 단위로 끊어서 자막을 만들도록 시도합니다.")
            )

        if hasattr(self, "spin_min_length"):
            self.spin_min_length.setSuffix(i18n.tr(" 자 (글자수)"))
        if hasattr(self, "spin_min_duration"):
            self.spin_min_duration.setSuffix(i18n.tr(" 초 (s)"))
        if hasattr(self, "spin_max_duration"):
            self.spin_max_duration.setSuffix(i18n.tr(" 초 (s)"))
        if hasattr(self, "spin_live_wordtimestamp_offset"):
            self.spin_live_wordtimestamp_offset.setSuffix(i18n.tr(" 초 (s)"))
        if hasattr(self, "spin_live_pad_before"):
            self.spin_live_pad_before.setSuffix(i18n.tr(" 초 (s)"))
        if hasattr(self, "spin_live_pad_after"):
            self.spin_live_pad_after.setSuffix(i18n.tr(" 초 (s)"))

        # Fix labels that may have English source text
        for label in self.findChildren(QLabel):
            text = label.text()
            if text in ("Device:", "장치:"):
                label.setProperty("i18n_source", "장치:")
                label.setText(i18n.tr("장치:"))
            elif text in ("Compute Type:", "정밀도:"):
                label.setProperty("i18n_source", "정밀도:")
                label.setText(i18n.tr("정밀도:"))

        for group in self.findChildren(QGroupBox):
            title = group.title()
            if title in ("VAD 설정", "전처리", "Pre-proc"):
                group.setProperty("i18n_source", "전처리")
                group.setTitle(i18n.tr("전처리"))

        i18n.apply_widget_translations(self)

    def _on_cancel(self):
        """Revert settings and close."""
        # Revert handled in closeEvent if not saved, but let's be explicit
        self.settings_changed.emit(self._original)
        self.close()

    def _create_transcriber_tab(self) -> QWidget:
        """Create the Faster-Whisper settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # --- Microphone Settings (New) ---
        mic_group = QGroupBox("마이크 설정")
        mic_layout = QHBoxLayout(mic_group)

        # Indicator / Test Button
        self.btn_mic_test = QPushButton("⚪")  # Default circle
        self.btn_mic_test.setFixedSize(30, 30)
        self.btn_mic_test.setStyleSheet("""
            QPushButton {
                background-color: #22c55e; /* Green (Silence) */
                border-radius: 15px;
                border: 2px solid #16a34a;
                font-weight: bold;
                color: white;
            }
        """)
        self.btn_mic_test.setToolTip("클릭하여 마이크 보정 (3초 무음 + 4초 소리)")
        self.btn_mic_test.clicked.connect(self._run_calibration)
        mic_layout.addWidget(self.btn_mic_test)

        # Mic Device Combo
        self.combo_mic = QComboBox()
        self._populate_audio_devices()  # Need to implement this
        mic_layout.addWidget(self.combo_mic, 1)  # Expand

        layout.addWidget(mic_group)

        # --- Model Settings ---
        model_group = QGroupBox("모델 설정")
        model_layout = QFormLayout(model_group)

        self.combo_model = QComboBox()
        self.combo_model.addItems(["large-v2", "large-v3", "large-v3-turbo", "Custom Model..."])
        self.combo_model.setCurrentText(
            str(self._current.get("model", "large-v3-turbo"))
        )
        self.combo_model.currentTextChanged.connect(self._on_model_changed)
        model_layout.addRow("모델:", self.combo_model)

        # Custom Model Path UI
        self.custom_model_widget = QWidget()
        custom_layout = QHBoxLayout(self.custom_model_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        
        self.line_custom_model = QLineEdit()
        self.line_custom_model.setReadOnly(True)
        self.line_custom_model.setPlaceholderText("모델 폴더 또는 파일 선택...")
        self.line_custom_model.setText(self._current.get("custom_model_path", ""))
        
        self.btn_browse_model = QPushButton("찾아보기...")
        self.btn_browse_model.clicked.connect(self._browse_custom_model)
        
        custom_layout.addWidget(self.line_custom_model)
        custom_layout.addWidget(self.btn_browse_model)
        
        # Add to layout but hide initially
        model_layout.addRow("모델 경로:", self.custom_model_widget)
        self._update_custom_model_visibility()

        self.combo_language = QComboBox()
        self.combo_language.addItems(["auto", "ko", "en", "ja", "zh"])
        self.combo_language.setCurrentText(str(self._current.get("language", "ko")))
        model_layout.addRow("언어:", self.combo_language)

        self.combo_device = QComboBox()
        self.combo_device.addItems(["cuda", "cpu"])
        self.combo_device.setCurrentText(str(self._current.get("device", "cuda")))
        model_layout.addRow("장치:", self.combo_device)

        self.combo_compute_type = QComboBox()
        self.combo_compute_type.addItems(["int8", "int8_float16", "float16", "float32"])
        self.combo_compute_type.setCurrentText(
            str(self._current.get("compute_type", "float16"))
        )
        model_layout.addRow("정밀도:", self.combo_compute_type)

        # Additional Faster-Whisper Params
        self.btn_extra_params = QPushButton("추가 매개변수...")
        self.btn_extra_params.setToolTip(
            "faster-whisper WhisperModel.transcribe()에 전달할 추가 매개변수를 JSON으로 설정합니다."
        )
        self.btn_extra_params.clicked.connect(self._open_extra_params_dialog)
        model_layout.addRow("추가 매개변수:", self.btn_extra_params)

        layout.addWidget(model_group)

        # --- VAD Settings ---
        vad_group = QGroupBox("전처리")
        vad_layout = QFormLayout(vad_group)

        self.spin_vad_threshold = NoScrollDoubleSpinBox()
        self.spin_vad_threshold.setRange(0.0, 1.0)  # Increased range for flexibility
        self.spin_vad_threshold.setSingleStep(0.01)
        self.spin_vad_threshold.setDecimals(3)
        self.spin_vad_threshold.setValue(
            float(self._current.get("vad_threshold", 0.02))
        )
        vad_layout.addRow("음성 감지 임계값 (VAD):", self.spin_vad_threshold)

        self.spin_silence_duration = NoScrollDoubleSpinBox()
        self.spin_silence_duration.setRange(0.1, 5.0)
        self.spin_silence_duration.setSingleStep(0.01)
        self.spin_silence_duration.setValue(
            float(self._current.get("vad_silence_duration", 0.5))
        )
        vad_layout.addRow("무음 시간 (초):", self.spin_silence_duration)

        layout.addWidget(vad_group)

        layout.addStretch()
        return widget

    def _create_fw_live_params_tab(self) -> QWidget:
        """Create the Faster-Whisper parameter tab for Live."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.grp_live_pp = QGroupBox("Live 후처리")
        self.grp_live_pp.setCheckable(True)
        self.grp_live_pp.setChecked(
            bool(self._current.get("enable_live_post_processing", True))
        )
        self.grp_live_pp.toggled.connect(self._on_change)
        pp_form = QFormLayout(self.grp_live_pp)

        self.chk_enable_pp = QCheckBox("후처리 필터 사용 (Enable Filters)")
        # Legacy compatibility: hide or remove
        self.chk_enable_pp.setVisible(False)

        self.spin_min_length = NoScrollSpinBox()
        self.spin_min_length.setRange(0, 100)
        self.spin_min_length.setValue(self._current.get("min_text_length", 0))
        self.spin_min_length.setSuffix(i18n.tr(" 자 (글자수)"))
        self.spin_min_length.setToolTip(
            "지정된 글자 수보다 짧은 자막은 무시합니다. (0 = 끄기)"
        )
        pp_form.addRow("최소 길이 제한:", self.spin_min_length)

        self.spin_rms_filter = NoScrollDoubleSpinBox()
        self.spin_rms_filter.setRange(0.0, 1.0)
        self.spin_rms_filter.setSingleStep(0.001)
        self.spin_rms_filter.setDecimals(4)
        self.spin_rms_filter.setValue(self._current.get("rms_threshold", 0.002))
        self.spin_rms_filter.setToolTip(
            "이 값보다 평균 볼륨(RMS)이 낮은 구간은 무시합니다."
        )
        pp_form.addRow("최소 볼륨 (RMS Cutoff):", self.spin_rms_filter)

        self.spin_min_duration = NoScrollDoubleSpinBox()
        self.spin_min_duration.setRange(0.0, 5.0)
        self.spin_min_duration.setSingleStep(0.01)
        self.spin_min_duration.setDecimals(2)
        self.spin_min_duration.setValue(self._current.get("min_duration", 0.0))
        self.spin_min_duration.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_min_duration.setToolTip(
            "지정된 시간보다 짧은 음성 구간은 무시합니다. (0 = 끄기)"
        )
        pp_form.addRow("최소 음성 길이:", self.spin_min_duration)

        self.spin_max_duration = NoScrollDoubleSpinBox()
        self.spin_max_duration.setRange(0.0, 120.0)
        self.spin_max_duration.setSingleStep(0.01)
        self.spin_max_duration.setDecimals(2)
        self.spin_max_duration.setValue(self._current.get("max_duration", 29.9))
        self.spin_max_duration.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_max_duration.setToolTip(
            "지정된 시간 이상인 음성 구간은 무시합니다. (0 = 끄기)"
        )
        pp_form.addRow("최대 음성 길이:", self.spin_max_duration)

        self.spin_live_wordtimestamp_offset = NoScrollDoubleSpinBox()
        self.spin_live_wordtimestamp_offset.setRange(-2.0, 2.0)
        self.spin_live_wordtimestamp_offset.setSingleStep(0.01)
        self.spin_live_wordtimestamp_offset.setDecimals(2)
        self.spin_live_wordtimestamp_offset.setValue(
            float(self._current.get("live_wordtimestamp_offset", 0.2))
        )
        self.spin_live_wordtimestamp_offset.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_live_wordtimestamp_offset.setToolTip(
            "Live 자막의 타임스탬프를 지정한 시간만큼 이동합니다."
        )
        pp_form.addRow("Wordtimestamp 보정:", self.spin_live_wordtimestamp_offset)

        self.spin_live_pad_before = NoScrollDoubleSpinBox()
        self.spin_live_pad_before.setRange(0.0, 2.0)
        self.spin_live_pad_before.setSingleStep(0.01)
        self.spin_live_pad_before.setDecimals(2)
        self.spin_live_pad_before.setValue(
            float(self._current.get("live_pad_before", 0.1))
        )
        self.spin_live_pad_before.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_live_pad_before.setToolTip(
            "Live 자막 시작 시간을 앞당겨 구간을 확장합니다."
        )
        pp_form.addRow("-padding:", self.spin_live_pad_before)

        self.spin_live_pad_after = NoScrollDoubleSpinBox()
        self.spin_live_pad_after.setRange(0.0, 2.0)
        self.spin_live_pad_after.setSingleStep(0.01)
        self.spin_live_pad_after.setDecimals(2)
        self.spin_live_pad_after.setValue(
            float(self._current.get("live_pad_after", 0.1))
        )
        self.spin_live_pad_after.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_live_pad_after.setToolTip(
            "Live 자막 종료 시간을 늦춰 구간을 확장합니다."
        )
        pp_form.addRow("+padding:", self.spin_live_pad_after)

        self.spin_live_merge_short_len = NoScrollSpinBox()
        self.spin_live_merge_short_len.setRange(0, 50)
        self.spin_live_merge_short_len.setValue(
            self._current.get("live_merge_short_len", 2)
        )
        self.spin_live_merge_short_len.setSuffix(i18n.tr(" 자"))
        self.spin_live_merge_short_len.setToolTip(
            i18n.tr("이 길이 이하인 짧은 자막을 다음 구간과 병합합니다.")
        )
        pp_form.addRow(
            i18n.tr("짧은 구간 병합 (길이):"), self.spin_live_merge_short_len
        )

        self.spin_live_merge_short_gap = NoScrollDoubleSpinBox()
        self.spin_live_merge_short_gap.setRange(0.0, 5.0)
        self.spin_live_merge_short_gap.setSingleStep(0.01)
        self.spin_live_merge_short_gap.setDecimals(2)
        self.spin_live_merge_short_gap.setValue(
            float(self._current.get("live_merge_short_gap", 1.0))
        )
        self.spin_live_merge_short_gap.setSuffix(i18n.tr(" 초"))
        self.spin_live_merge_short_gap.setToolTip(
            i18n.tr("다음 구간과의 간격이 이 값 이하여야 병합합니다.")
        )
        pp_form.addRow(
            i18n.tr("짧은 구간 병합 (간격):"), self.spin_live_merge_short_gap
        )

        self.btn_live_abbrev = QPushButton("약어 화이트리스트...")
        self.btn_live_abbrev.clicked.connect(
            lambda: self._open_abbrev_whitelist_dialog("live")
        )
        pp_form.addRow("약어 화이트리스트:", self.btn_live_abbrev)

        group = QGroupBox("Live 자막 매개변수")
        form = QFormLayout(group)

        self.chk_fw_live_sentence = QCheckBox("--sentence")
        self.chk_fw_live_sentence.setChecked(
            bool(self._current.get("fw_live_sentence", True))
        )
        self.chk_fw_live_sentence.setToolTip(
            "문장 단위로 끊어서 자막을 만들도록 시도합니다. (프로젝트에서 실제 적용 여부는 내보내기/후처리 구현에 따릅니다)"
        )
        form.addRow(self.chk_fw_live_sentence)

        self.spin_fw_live_max_gap = NoScrollDoubleSpinBox()
        self.spin_fw_live_max_gap.setRange(0.0, 10.0)
        self.spin_fw_live_max_gap.setSingleStep(0.01)
        self.spin_fw_live_max_gap.setDecimals(2)
        self.spin_fw_live_max_gap.setValue(
            float(self._current.get("fw_live_max_gap", 0.8))
        )
        self.spin_fw_live_max_gap.setToolTip(
            "자막 분할 시, 두 단어/세그먼트 사이의 최대 허용 간격(초)입니다."
        )
        form.addRow("--max_gap:", self.spin_fw_live_max_gap)

        self.spin_fw_live_max_line_width = NoScrollSpinBox()
        self.spin_fw_live_max_line_width.setRange(0, 200)
        self.spin_fw_live_max_line_width.setValue(
            int(self._current.get("fw_live_max_line_width", 55))
        )
        self.spin_fw_live_max_line_width.setToolTip(
            "자막 한 줄의 최대 문자 폭(대략적인 글자수)입니다. (SRT 줄바꿈에 사용)"
        )
        form.addRow("--max_line_width:", self.spin_fw_live_max_line_width)

        self.spin_fw_live_max_line_count = NoScrollSpinBox()
        self.spin_fw_live_max_line_count.setRange(0, 10)
        self.spin_fw_live_max_line_count.setValue(
            int(self._current.get("fw_live_max_line_count", 2))
        )
        self.spin_fw_live_max_line_count.setToolTip(
            "자막 한 항목에서 허용하는 최대 줄 수입니다."
        )
        form.addRow("--max_line_count:", self.spin_fw_live_max_line_count)

        self.spin_fw_live_max_comma_cent = NoScrollSpinBox()
        self.spin_fw_live_max_comma_cent.setRange(0, 100)
        self.spin_fw_live_max_comma_cent.setValue(
            int(self._current.get("fw_live_max_comma_cent", 70))
        )
        self.spin_fw_live_max_comma_cent.setToolTip(
            "쉼표(,) 기준으로 분할할 때의 기준 퍼센트 값입니다."
        )
        form.addRow("--max_comma_cent:", self.spin_fw_live_max_comma_cent)

        self.spin_fw_live_one_word = NoScrollSpinBox()
        self.spin_fw_live_one_word.setRange(0, 1)
        self.spin_fw_live_one_word.setValue(
            int(self._current.get("fw_live_one_word", 0))
        )
        self.spin_fw_live_one_word.setToolTip(
            "1이면 한 단어씩 자막으로 만들도록 강제합니다. 0이면 비활성화입니다."
        )
        form.addRow("--one_word:", self.spin_fw_live_one_word)

        self.spin_fw_live_vad_max_speech_duration_s = NoScrollDoubleSpinBox()
        self.spin_fw_live_vad_max_speech_duration_s.setRange(0.0, 60.0)
        self.spin_fw_live_vad_max_speech_duration_s.setSingleStep(0.01)
        self.spin_fw_live_vad_max_speech_duration_s.setDecimals(2)
        self.spin_fw_live_vad_max_speech_duration_s.setValue(
            float(self._current.get("fw_live_vad_max_speech_duration_s", 7.0))
        )
        self.spin_fw_live_vad_max_speech_duration_s.setToolTip(
            "(파일 STT에서) VAD가 한 번에 잡을 수 있는 최대 발화 길이(초)입니다."
        )
        form.addRow(
            "--vad_max_speech_duration_s:", self.spin_fw_live_vad_max_speech_duration_s
        )

        self.spin_fw_live_vad_speech_pad_ms = NoScrollSpinBox()
        self.spin_fw_live_vad_speech_pad_ms.setRange(0, 2000)
        self.spin_fw_live_vad_speech_pad_ms.setValue(
            int(self._current.get("fw_live_vad_speech_pad_ms", 50))
        )
        self.spin_fw_live_vad_speech_pad_ms.setSuffix(" ms")
        self.spin_fw_live_vad_speech_pad_ms.setToolTip(
            "VAD 음성 패딩(ms)입니다. 말 끝이 잘리는 경우 늘려보세요. 기본: 50"
        )
        form.addRow("--vad_speech_pad_ms:", self.spin_fw_live_vad_speech_pad_ms)

        self.spin_fw_live_length_penalty = NoScrollDoubleSpinBox()
        self.spin_fw_live_length_penalty.setRange(0.0, 2.0)
        self.spin_fw_live_length_penalty.setSingleStep(0.01)
        self.spin_fw_live_length_penalty.setDecimals(2)
        self.spin_fw_live_length_penalty.setDecimals(2)
        self.spin_fw_live_length_penalty.setValue(
            float(self._current.get("fw_live_length_penalty", 0.9))
        )
        self.spin_fw_live_length_penalty.setToolTip(
            "디코딩에서 길이 패널티입니다. 값이 클수록 짧은 결과를 선호합니다."
        )
        form.addRow("--length_penalty:", self.spin_fw_live_length_penalty)

        self.spin_fw_live_beam_size = NoScrollSpinBox()
        self.spin_fw_live_beam_size.setRange(1, 20)
        self.spin_fw_live_beam_size.setValue(
            int(self._current.get("fw_live_beam_size", 5))
        )
        self.spin_fw_live_beam_size.setToolTip(
            "Beam search 빔 크기입니다. 클수록 정확도가 올라갈 수 있지만 느려집니다."
        )
        form.addRow("--beam_size:", self.spin_fw_live_beam_size)

        self.spin_fw_live_best_of = NoScrollSpinBox()
        self.spin_fw_live_best_of.setRange(1, 20)
        self.spin_fw_live_best_of.setValue(int(self._current.get("fw_live_best_of", 1)))
        self.spin_fw_live_best_of.setToolTip(
            "샘플링 시 후보 중 best_of 개 중 최적을 선택합니다. (temperature>0에서 의미가 큼)"
        )
        form.addRow("--best_of:", self.spin_fw_live_best_of)

        self.spin_fw_live_compression_ratio_threshold = NoScrollDoubleSpinBox()
        self.spin_fw_live_compression_ratio_threshold.setRange(0.0, 10.0)
        self.spin_fw_live_compression_ratio_threshold.setSingleStep(0.01)
        self.spin_fw_live_compression_ratio_threshold.setDecimals(2)
        self.spin_fw_live_compression_ratio_threshold.setValue(
            float(self._current.get("fw_live_compression_ratio_threshold", 2.0))
        )
        self.spin_fw_live_compression_ratio_threshold.setToolTip(
            "압축 비율이 이 값보다 크면 (반복/이상 출력으로 판단) 해당 결과를 거를 수 있습니다."
        )
        form.addRow(
            "--compression_ratio_threshold:",
            self.spin_fw_live_compression_ratio_threshold,
        )

        self.spin_fw_live_logprob_threshold = NoScrollDoubleSpinBox()
        self.spin_fw_live_logprob_threshold.setRange(-10.0, 0.0)
        self.spin_fw_live_logprob_threshold.setSingleStep(0.01)
        self.spin_fw_live_logprob_threshold.setDecimals(2)
        self.spin_fw_live_logprob_threshold.setValue(
            float(self._current.get("fw_live_logprob_threshold", -1.0))
        )
        self.spin_fw_live_logprob_threshold.setToolTip(
            "평균 로그확률이 이 값보다 낮으면 결과를 거를 수 있습니다. (-1.0은 보통 관대한 값)"
        )
        form.addRow("--logprob_threshold:", self.spin_fw_live_logprob_threshold)

        note = QLabel(
            "주의: '추가 매개변수...' JSON에 같은 키가 있으면, 그 값이 우선 적용됩니다."
        )
        note.setStyleSheet("color: gray;")
        self.note_fw_live = note

        layout.addWidget(self.grp_live_pp)
        layout.addWidget(group)
        layout.addWidget(note)
        layout.addStretch()

        scroll.setWidget(widget)
        tab_layout.addWidget(scroll)
        return tab

    def _create_fw_file_params_tab(self) -> QWidget:
        """Create the Faster-Whisper parameter tab for File."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.grp_file_pp = QGroupBox("STT 후처리")
        self.grp_file_pp.setCheckable(True)
        self.grp_file_pp.setChecked(
            bool(self._current.get("enable_file_post_processing", True))
        )
        self.grp_file_pp.toggled.connect(self._on_change)
        pp_form = QFormLayout(self.grp_file_pp)

        self.btn_stt_abbrev = QPushButton("약어 화이트리스트...")
        self.btn_stt_abbrev.clicked.connect(
            lambda: self._open_abbrev_whitelist_dialog("stt")
        )
        pp_form.addRow("약어 화이트리스트:", self.btn_stt_abbrev)

        self.spin_stt_seg_endmin = NoScrollDoubleSpinBox()
        self.spin_stt_seg_endmin.setRange(0.05, 0.2)
        self.spin_stt_seg_endmin.setSingleStep(0.01)
        self.spin_stt_seg_endmin.setDecimals(2)
        self.spin_stt_seg_endmin.setValue(
            float(self._current.get("stt_seg_endmin", 0.05))
        )
        self.spin_stt_seg_endmin.setToolTip(
            "세그먼트 최소 길이(초)입니다. 이 값보다 짧으면 끝 시간을 늘립니다."
        )
        pp_form.addRow("Seg.Endmin:", self.spin_stt_seg_endmin)

        self.chk_stt_extend_on_touch = QCheckBox("Extend on touch")
        self.chk_stt_extend_on_touch.setChecked(
            bool(self._current.get("stt_extend_on_touch", False))
        )
        self.chk_stt_extend_on_touch.setToolTip(
            "자막 구간을 편집할 때 인접 구간과 맞닿도록 확장합니다."
        )
        pp_form.addRow(self.chk_stt_extend_on_touch)

        self.spin_stt_pad_before = NoScrollDoubleSpinBox()
        self.spin_stt_pad_before.setRange(0.0, 2.0)
        self.spin_stt_pad_before.setSingleStep(0.01)
        self.spin_stt_pad_before.setDecimals(2)
        self.spin_stt_pad_before.setValue(
            float(self._current.get("stt_pad_before", 0.1))
        )
        self.spin_stt_pad_before.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_stt_pad_before.setToolTip(
            "STT 자막 시작 시간을 앞당겨 구간을 확장합니다."
        )
        pp_form.addRow("-padding:", self.spin_stt_pad_before)

        self.spin_stt_pad_after = NoScrollDoubleSpinBox()
        self.spin_stt_pad_after.setRange(0.0, 2.0)
        self.spin_stt_pad_after.setSingleStep(0.05)
        self.spin_stt_pad_after.setDecimals(2)
        self.spin_stt_pad_after.setValue(float(self._current.get("stt_pad_after", 0.1)))
        self.spin_stt_pad_after.setSuffix(i18n.tr(" 초 (s)"))
        self.spin_stt_pad_after.setToolTip(
            "STT 자막 종료 시간을 늦춰 구간을 확장합니다."
        )
        pp_form.addRow("+padding:", self.spin_stt_pad_after)

        self.spin_stt_merge_short_len = NoScrollSpinBox()
        self.spin_stt_merge_short_len.setRange(0, 50)
        self.spin_stt_merge_short_len.setValue(
            self._current.get("stt_merge_short_len", 2)
        )
        self.spin_stt_merge_short_len.setSuffix(i18n.tr(" 자"))
        self.spin_stt_merge_short_len.setToolTip(
            i18n.tr("이 길이 이하인 짧은 자막을 다음 구간과 병합합니다.")
        )
        pp_form.addRow(i18n.tr("짧은 구간 병합 (길이):"), self.spin_stt_merge_short_len)

        self.spin_stt_merge_short_gap = NoScrollDoubleSpinBox()
        self.spin_stt_merge_short_gap.setRange(0.0, 5.0)
        self.spin_stt_merge_short_gap.setSingleStep(0.01)
        self.spin_stt_merge_short_gap.setDecimals(2)
        self.spin_stt_merge_short_gap.setValue(
            float(self._current.get("stt_merge_short_gap", 1.0))
        )
        self.spin_stt_merge_short_gap.setSuffix(i18n.tr(" 초"))
        self.spin_stt_merge_short_gap.setToolTip(
            i18n.tr("다음 구간과의 간격이 이 값 이하여야 병합합니다.")
        )
        pp_form.addRow(i18n.tr("짧은 구간 병합 (간격):"), self.spin_stt_merge_short_gap)

        group = QGroupBox("STT 실행 (파일) 매개변수")
        form = QFormLayout(group)

        self.chk_fw_file_sentence = QCheckBox("--sentence")
        self.chk_fw_file_sentence.setChecked(
            bool(self._current.get("fw_file_sentence", True))
        )
        self.chk_fw_file_sentence.setToolTip(
            "문장 단위로 끊어서 자막을 만들도록 시도합니다."
        )
        form.addRow(self.chk_fw_file_sentence)

        self.chk_fw_file_vad_filter = QCheckBox("--vad_filter (VAD 사용)")
        self.chk_fw_file_vad_filter.setChecked(
            bool(self._current.get("fw_file_vad_filter", True))
        )
        self.chk_fw_file_vad_filter.setToolTip(
            "활성화하면 Silero VAD로 음성이 없는 구간을 필터링합니다. (추천: 켜기)"
        )
        form.addRow(self.chk_fw_file_vad_filter)

        self.chk_fw_file_word_timestamps = QCheckBox("--word_timestamps (단어 시간)")
        self.chk_fw_file_word_timestamps.setChecked(
            bool(self._current.get("fw_file_word_timestamps", True))
        )
        self.chk_fw_file_word_timestamps.setToolTip(
            "단어 단위 타임스탬프를 추출합니다. (정밀도 향상 및 하이라이팅)"
        )
        form.addRow(self.chk_fw_file_word_timestamps)

        self.spin_fw_file_max_gap = NoScrollDoubleSpinBox()
        self.spin_fw_file_max_gap.setRange(0.0, 10.0)
        self.spin_fw_file_max_gap.setSingleStep(0.01)
        self.spin_fw_file_max_gap.setDecimals(2)
        self.spin_fw_file_max_gap.setValue(
            float(self._current.get("fw_file_max_gap", 0.8))
        )
        form.addRow("--max_gap:", self.spin_fw_file_max_gap)

        self.spin_fw_file_max_line_width = NoScrollSpinBox()
        self.spin_fw_file_max_line_width.setRange(0, 200)
        self.spin_fw_file_max_line_width.setValue(
            int(self._current.get("fw_file_max_line_width", 55))
        )
        form.addRow("--max_line_width:", self.spin_fw_file_max_line_width)

        self.spin_fw_file_max_line_count = NoScrollSpinBox()
        self.spin_fw_file_max_line_count.setRange(0, 10)
        self.spin_fw_file_max_line_count.setValue(
            int(self._current.get("fw_file_max_line_count", 2))
        )
        form.addRow("--max_line_count:", self.spin_fw_file_max_line_count)

        self.spin_fw_file_max_comma_cent = NoScrollSpinBox()
        self.spin_fw_file_max_comma_cent.setRange(0, 100)
        self.spin_fw_file_max_comma_cent.setValue(
            int(self._current.get("fw_file_max_comma_cent", 70))
        )
        form.addRow("--max_comma_cent:", self.spin_fw_file_max_comma_cent)

        self.spin_fw_file_one_word = NoScrollSpinBox()
        self.spin_fw_file_one_word.setRange(0, 1)
        self.spin_fw_file_one_word.setValue(
            int(self._current.get("fw_file_one_word", 0))
        )
        form.addRow("--one_word:", self.spin_fw_file_one_word)

        self.spin_fw_file_vad_max_speech_duration_s = NoScrollDoubleSpinBox()
        self.spin_fw_file_vad_max_speech_duration_s.setRange(0.0, 60.0)
        self.spin_fw_file_vad_max_speech_duration_s.setSingleStep(0.01)
        self.spin_fw_file_vad_max_speech_duration_s.setDecimals(2)
        self.spin_fw_file_vad_max_speech_duration_s.setValue(
            float(self._current.get("fw_file_vad_max_speech_duration_s", 7.0))
        )
        form.addRow(
            "--vad_max_speech_duration_s:", self.spin_fw_file_vad_max_speech_duration_s
        )

        self.spin_fw_file_vad_speech_pad_ms = NoScrollSpinBox()
        self.spin_fw_file_vad_speech_pad_ms.setRange(0, 2000)
        self.spin_fw_file_vad_speech_pad_ms.setValue(
            int(self._current.get("fw_file_vad_speech_pad_ms", 50))
        )
        self.spin_fw_file_vad_speech_pad_ms.setToolTip(
            "VAD 음성 패딩(ms)입니다. 기본 50ms"
        )
        form.addRow("--vad_speech_pad_ms:", self.spin_fw_file_vad_speech_pad_ms)

        self.spin_fw_file_vad_min_speech_duration_ms = NoScrollSpinBox()
        self.spin_fw_file_vad_min_speech_duration_ms.setRange(0, 5000)
        self.spin_fw_file_vad_min_speech_duration_ms.setValue(
            int(self._current.get("fw_file_vad_min_speech_duration_ms", 250))
        )
        self.spin_fw_file_vad_min_speech_duration_ms.setSuffix(" ms")
        self.spin_fw_file_vad_min_speech_duration_ms.setToolTip(
            "이 시간보다 짧은 소리는 말로 인식하지 않고 무시합니다. (기본 250ms)"
        )
        form.addRow(
            "--vad_min_speech_duration_ms:",
            self.spin_fw_file_vad_min_speech_duration_ms,
        )

        self.spin_fw_file_vad_min_silence_duration_ms = NoScrollSpinBox()
        self.spin_fw_file_vad_min_silence_duration_ms.setRange(0, 10000)
        self.spin_fw_file_vad_min_silence_duration_ms.setValue(
            int(self._current.get("fw_file_vad_min_silence_duration_ms", 3000))
        )
        self.spin_fw_file_vad_min_silence_duration_ms.setSuffix(" ms")
        self.spin_fw_file_vad_min_silence_duration_ms.setToolTip(
            "문장 분리 기준이 되는 최소 무음 시간입니다. (기본 3000ms)"
        )
        form.addRow(
            "--vad_min_silence_duration_ms:",
            self.spin_fw_file_vad_min_silence_duration_ms,
        )

        self.spin_fw_file_vad_window_size_samples = NoScrollSpinBox()
        self.spin_fw_file_vad_window_size_samples.setRange(0, 4096)
        self.spin_fw_file_vad_window_size_samples.setSingleStep(256)
        self.spin_fw_file_vad_window_size_samples.setValue(
            int(self._current.get("fw_file_vad_window_size_samples", 1536))
        )
        self.spin_fw_file_vad_window_size_samples.setToolTip(
            "VAD 윈도우 크기입니다. (기본 1536)"
        )
        form.addRow(
            "--vad_window_size_samples:", self.spin_fw_file_vad_window_size_samples
        )

        self.spin_fw_file_vad_threshold = NoScrollDoubleSpinBox()
        self.spin_fw_file_vad_threshold.setRange(0.0, 1.0)
        self.spin_fw_file_vad_threshold.setSingleStep(0.01)
        self.spin_fw_file_vad_threshold.setDecimals(2)
        self.spin_fw_file_vad_threshold.setValue(
            float(self._current.get("fw_file_vad_threshold", 0.45))
        )
        self.spin_fw_file_vad_threshold.setToolTip(
            "VAD 확률 임계값입니다. (기본 0.45)"
        )
        form.addRow("--vad_threshold:", self.spin_fw_file_vad_threshold)

        self.spin_fw_file_length_penalty = NoScrollDoubleSpinBox()
        self.spin_fw_file_length_penalty.setRange(0.0, 2.0)
        self.spin_fw_file_length_penalty.setSingleStep(0.01)
        self.spin_fw_file_length_penalty.setDecimals(2)
        self.spin_fw_file_length_penalty.setValue(
            float(self._current.get("fw_file_length_penalty", 0.9))
        )
        form.addRow("--length_penalty:", self.spin_fw_file_length_penalty)

        self.spin_fw_file_beam_size = NoScrollSpinBox()
        self.spin_fw_file_beam_size.setRange(1, 20)
        self.spin_fw_file_beam_size.setValue(
            int(self._current.get("fw_file_beam_size", 5))
        )
        form.addRow("--beam_size:", self.spin_fw_file_beam_size)

        self.spin_fw_file_best_of = NoScrollSpinBox()
        self.spin_fw_file_best_of.setRange(1, 20)
        self.spin_fw_file_best_of.setValue(int(self._current.get("fw_file_best_of", 1)))
        form.addRow("--best_of:", self.spin_fw_file_best_of)

        self.spin_fw_file_compression_ratio_threshold = NoScrollDoubleSpinBox()
        self.spin_fw_file_compression_ratio_threshold.setRange(0.0, 10.0)
        self.spin_fw_file_compression_ratio_threshold.setSingleStep(0.01)
        self.spin_fw_file_compression_ratio_threshold.setDecimals(2)
        self.spin_fw_file_compression_ratio_threshold.setValue(
            float(self._current.get("fw_file_compression_ratio_threshold", 2.0))
        )
        form.addRow(
            "--compression_ratio_threshold:",
            self.spin_fw_file_compression_ratio_threshold,
        )

        self.spin_fw_file_logprob_threshold = NoScrollDoubleSpinBox()
        self.spin_fw_file_logprob_threshold.setRange(-10.0, 0.0)
        self.spin_fw_file_logprob_threshold.setSingleStep(0.01)
        self.spin_fw_file_logprob_threshold.setDecimals(2)
        self.spin_fw_file_logprob_threshold.setValue(
            float(self._current.get("fw_file_logprob_threshold", -1.0))
        )
        form.addRow("--logprob_threshold:", self.spin_fw_file_logprob_threshold)

        self.spin_fw_file_no_speech_threshold = NoScrollDoubleSpinBox()
        self.spin_fw_file_no_speech_threshold.setRange(0.0, 1.0)
        self.spin_fw_file_no_speech_threshold.setSingleStep(0.01)
        self.spin_fw_file_no_speech_threshold.setDecimals(2)
        self.spin_fw_file_no_speech_threshold.setValue(
            float(self._current.get("fw_file_no_speech_threshold", 0.6))
        )
        self.spin_fw_file_no_speech_threshold.setToolTip(
            "무음(No Speech) 확률이 이 값보다 높으면 스킵합니다. (기본 0.6)"
        )
        form.addRow("--no_speech_threshold:", self.spin_fw_file_no_speech_threshold)

        note = QLabel(
            "주의: '추가 매개변수...' JSON에 같은 키가 있으면, 그 값이 우선 적용됩니다."
        )
        note.setStyleSheet("color: gray;")
        self.note_fw_file = note

        # FFmpeg 세그먼트 분리 설정 섹션
        self.grp_ffmpeg = QGroupBox("FFmpeg 세그먼트 분리")
        self.grp_ffmpeg.setCheckable(True)
        self.grp_ffmpeg.setChecked(
            bool(self._current.get("ffmpeg_segmentation_enabled", False))
        )
        self.grp_ffmpeg.toggled.connect(self._on_change)
        ffmpeg_form = QFormLayout(self.grp_ffmpeg)

        # FFmpeg 활성화 여부
        lbl_ffmpeg_info = QLabel("FFmpeg를 사용해 무음 구간을 기준으로 음성 세그먼트를 자동으로 분리합니다.\n"
                                 "각 세그먼트를 개별적으로 전사하여 더 정확한 결과를 얻을 수 있습니다.")
        lbl_ffmpeg_info.setWordWrap(True)
        lbl_ffmpeg_info.setStyleSheet("color: gray; font-size: 12px;")
        ffmpeg_form.addRow(lbl_ffmpeg_info)

        # 무음 감도
        self.spin_ffmpeg_silence_threshold = NoScrollDoubleSpinBox()
        self.spin_ffmpeg_silence_threshold.setRange(-60.0, -20.0)
        self.spin_ffmpeg_silence_threshold.setSingleStep(1.0)
        self.spin_ffmpeg_silence_threshold.setDecimals(1)
        self.spin_ffmpeg_silence_threshold.setValue(
            float(self._current.get("ffmpeg_silence_threshold", -30.0))
        )
        self.spin_ffmpeg_silence_threshold.setSuffix(" dB")
        self.spin_ffmpeg_silence_threshold.setToolTip(
            "무음으로 감지할 기준 레벨입니다. 값이 클수록 더 민감하게 감지합니다. (-60 ~ -20 dB)"
        )
        ffmpeg_form.addRow("무음 감도:", self.spin_ffmpeg_silence_threshold)

        # 최소 무음 시간
        self.spin_ffmpeg_min_silence_duration = NoScrollDoubleSpinBox()
        self.spin_ffmpeg_min_silence_duration.setRange(0.1, 5.0)
        self.spin_ffmpeg_min_silence_duration.setSingleStep(0.1)
        self.spin_ffmpeg_min_silence_duration.setDecimals(1)
        self.spin_ffmpeg_min_silence_duration.setValue(
            float(self._current.get("ffmpeg_min_silence_duration", 0.5))
        )
        self.spin_ffmpeg_min_silence_duration.setSuffix(" 초")
        self.spin_ffmpeg_min_silence_duration.setToolTip(
            "무음으로 간주할 최소 지속 시간입니다. 이 시간보다 짧은 무음은 무시됩니다."
        )
        ffmpeg_form.addRow("최소 무음 시간:", self.spin_ffmpeg_min_silence_duration)

        # 패딩 시간
        self.spin_ffmpeg_padding_ms = NoScrollSpinBox()
        self.spin_ffmpeg_padding_ms.setRange(0, 1000)
        self.spin_ffmpeg_padding_ms.setSingleStep(10)
        self.spin_ffmpeg_padding_ms.setValue(
            int(self._current.get("ffmpeg_padding_ms", 100))
        )
        self.spin_ffmpeg_padding_ms.setSuffix(" ms")
        self.spin_ffmpeg_padding_ms.setToolTip(
            "세그먼트 전후에 추가할 여백 시간입니다. 세그먼트 간 경계를 자연스럽게 만듭니다."
        )
        ffmpeg_form.addRow("패딩 시간:", self.spin_ffmpeg_padding_ms)

        # 30분 단위 분할 처리
        self.chk_ffmpeg_split_30min = QCheckBox("30분 단위로 분할 처리")
        self.chk_ffmpeg_split_30min.setChecked(
            bool(self._current.get("ffmpeg_split_30min", False))
        )
        self.chk_ffmpeg_split_30min.setToolTip(
            "대용량 파일의 메모리 사용량을 줄이기 위해 30분 단위로 나누어 처리합니다."
        )
        ffmpeg_form.addRow(self.chk_ffmpeg_split_30min)

        lbl_ffmpeg_warning = QLabel("경고: FFmpeg가 src/bin/ffmpeg.exe에 설치되어 있어야 합니다.")
        lbl_ffmpeg_warning.setStyleSheet("color: orange; font-size: 12px; font-weight: bold;")
        ffmpeg_form.addRow(lbl_ffmpeg_warning)

        layout.addWidget(self.grp_file_pp)
        layout.addWidget(group)
        layout.addWidget(self.grp_ffmpeg)
        layout.addWidget(note)
        layout.addStretch()

        scroll.setWidget(widget)
        tab_layout.addWidget(scroll)
        return tab

    def _open_extra_params_dialog(self):
        current_text = str(self._current.get("faster_whisper_params", "{}"))

        dialog = QDialog(self)
        dialog.setWindowTitle(i18n.tr("Faster-Whisper 추가 매개변수"))
        dialog.setMinimumSize(520, 360)

        v = QVBoxLayout(dialog)
        v.addWidget(
            QLabel(
                i18n.tr(
                    "WhisperModel.transcribe()에 전달할 추가 매개변수를 입력하세요.\n"
                    "형식: --key value, --key2 value (콤마 구분)\n"
                    "예: --beam_size 5, --temperature 0.0, --condition_on_previous_text False\n"
                    "(기존 JSON 포맷도 지원합니다)"
                )
            )
        )

        edit = QPlainTextEdit()
        edit.setPlainText(current_text)
        edit.setPlaceholderText("--beam_size 5, --temperature 0.0")
        v.addWidget(edit, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_clear = QPushButton(i18n.tr("비우기"))
        btn_ok = QPushButton(i18n.tr("확인"))
        btn_cancel = QPushButton(i18n.tr("취소"))
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        v.addLayout(btn_row)

        def on_clear():
            edit.setPlainText("")

        def on_ok():
            text = edit.toPlainText().strip()
            if not text:
                text = ""

            # Basic validation: check if it looks like JSON
            if text.startswith("{"):
                try:
                    obj = json.loads(text)
                    if not isinstance(obj, dict):
                        raise ValueError("JSON must be an object")
                    # Canonical JSON
                    text = json.dumps(obj, ensure_ascii=False, indent=2)
                except Exception as e:
                    QMessageBox.warning(
                        dialog,
                        i18n.tr("JSON 경고"),
                        i18n.tr(
                            "입력값이 JSON으로 보이지만 파싱할 수 없습니다.\n문자열 그대로 저장합니다.\n"
                        )
                        + str(e),
                    )

            self._current["faster_whisper_params"] = text
            self.settings_changed.emit(self._current)
            dialog.accept()

        btn_clear.clicked.connect(on_clear)
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok.clicked.connect(on_ok)

        dialog.exec()

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

    def _open_abbrev_whitelist_dialog(self, mode: str):
        key = "live_abbrev_whitelist" if mode == "live" else "stt_abbrev_whitelist"
        title = (
            i18n.tr("약어 화이트리스트 (Live)")
            if mode == "live"
            else i18n.tr("약어 화이트리스트 (STT)")
        )
        current = self._current.get(key, self.DEFAULT_ABBREV_WHITELIST)
        current = self._normalize_abbrev_list(current)

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(420, 320)

        v = QVBoxLayout(dialog)
        v.addWidget(
            QLabel(
                i18n.tr("약어를 한 줄에 하나씩 입력하거나 쉼표(,)로 구분해 입력하세요.")
            )
        )

        edit = QPlainTextEdit()
        edit.setPlainText("\n".join(current))
        v.addWidget(edit, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_reset = QPushButton(i18n.tr("기본값"))
        btn_cancel = QPushButton(i18n.tr("취소"))
        btn_ok = QPushButton(i18n.tr("확인"))
        btn_row.addWidget(btn_reset)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        v.addLayout(btn_row)

        def on_reset():
            edit.setPlainText("\n".join(self.DEFAULT_ABBREV_WHITELIST))

        def on_ok():
            raw = edit.toPlainText().strip()
            items = []
            for part in raw.replace("\n", ",").split(","):
                text = part.strip().lower()
                if text:
                    items.append(text)
            self._current[key] = self._normalize_abbrev_list(items)
            self.settings_changed.emit(self._current)
            dialog.accept()

        btn_reset.clicked.connect(on_reset)
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok.clicked.connect(on_ok)

        dialog.exec()

    def _populate_audio_devices(self):
        """Populate audio devices."""
        import sounddevice as sd

        try:
            devices = sd.query_devices()
            host_apis = sd.query_hostapis()

            default_input = sd.default.device[0]

            self.combo_mic.clear()
            self._audio_devices = []

            def _set_item_state(item_index: int, enabled: bool, reason: str = ""):
                model = self.combo_mic.model()
                if hasattr(model, "item"):
                    it = model.item(item_index)
                    if it is not None:
                        it.setEnabled(enabled)

                if not enabled:
                    # Hide unusable devices
                    try:
                        self.combo_mic.removeItem(item_index)
                        if 0 <= item_index < len(self._audio_devices):
                            self._audio_devices.pop(item_index)
                    except Exception:
                        pass

            def _check_device(device_index: int, *, loopback: bool) -> tuple[bool, str]:
                try:
                    extra = None
                    dev = devices[device_index]
                    # Match AudioRecorder behavior: open at device default samplerate
                    samplerate = float(dev.get("default_samplerate", 16000))
                    in_ch = int(dev.get("max_input_channels", 1))
                    channels = 2 if in_ch >= 2 else 1
                    if loopback:
                        # User request: loopback entries removed from UI
                        return (False, "loopback disabled")

                    sd.check_input_settings(
                        device=device_index,
                        samplerate=samplerate,
                        channels=channels,
                        dtype="float32",
                        extra_settings=extra,
                    )
                    return (True, "")
                except Exception as e:
                    return (False, str(e))

            def _is_bluetooth(name: str) -> bool:
                n = (name or "").lower()
                return (
                    "bluetooth" in n
                    or "hands-free" in n
                    or "handsfree" in n
                    or " bt" in n
                    or n.endswith(" bt")
                    or n.endswith("bt")
                )

            def _is_desktop_capture(name: str) -> bool:
                n = (name or "").lower()
                return (
                    "stereo mix" in n
                    or "stereo input" in n
                    or "what u hear" in n
                    or "\uc2a4\ud14c\ub808\uc624" in n
                    or "\ubbf9\uc2a4" in n
                )

            def _is_stereo_mix(name: str) -> bool:
                n = (name or "").lower()
                return "stereo mix" in n or "\uc2a4\ud14c\ub808\uc624 \ubbf9\uc2a4" in n

            # Input devices
            for i, dev in enumerate(devices):
                try:
                    in_ch = int(dev.get("max_input_channels", 0))
                except Exception:
                    in_ch = 0
                if in_ch <= 0:
                    continue

                # Exclude Bluetooth devices
                if _is_bluetooth(str(dev.get("name", ""))):
                    continue

                name = str(dev.get("name", ""))

                # Allow Stereo Mix, but keep other desktop-capture variants hidden
                if _is_desktop_capture(name) and not _is_stereo_mix(name):
                    continue

                api_name = host_apis[int(dev["hostapi"])]["name"]
                api_upper = str(api_name).upper()
                # User request: keep only MME and Windows DirectSound
                if ("MME" not in api_upper) and ("DIRECTSOUND" not in api_upper):
                    continue

                prefix = (
                    "\uc2a4\ud14c\ub808\uc624 \ubbf9\uc2a4 - "
                    if _is_stereo_mix(name)
                    else ""
                )
                display = f"{prefix}{name} [{api_name}]"
                ok, reason = _check_device(i, loopback=False)

                self.combo_mic.addItem(display)
                self._audio_devices.append({"index": i, "loopback": False})
                _set_item_state(self.combo_mic.count() - 1, ok, reason)

                if i == default_input and ok:
                    self.combo_mic.setCurrentIndex(self.combo_mic.count() - 1)

            # User request: remove desktop audio(loopback) entries entirely

            # Prefer saved mic_index if present
            try:
                saved = int(self._current.get("mic_index", -1))
            except Exception:
                saved = -1

            saved_loopback = (
                str(self._current.get("mic_loopback", "false")).lower() == "true"
            )
            for idx, entry in enumerate(self._audio_devices):
                if (
                    int(entry.get("index", -1)) == saved
                    and bool(entry.get("loopback", False)) == saved_loopback
                ):
                    model = self.combo_mic.model()
                    enabled = True
                    if hasattr(model, "item"):
                        it = model.item(idx)
                        if it is not None:
                            enabled = it.isEnabled()
                    if enabled:
                        self.combo_mic.setCurrentIndex(idx)
                    break

        except Exception as e:
            self.combo_mic.addItem(f"Error: {e}")

    def update_mic_indicator(self, rms_value: float):
        """Update the indicator color based on RMS."""
        # 1. Calibration Collection
        if getattr(self, "_calib_phase", "IDLE") in ["SILENCE", "VOICE"]:
            getattr(self, "_calib_samples", []).append(rms_value)

        # 2. Visual Feedback
        threshold = self.spin_vad_threshold.value()
        is_active = rms_value > threshold

        # "Mic Active(Red) when Input, Silent(Green) when no Input"
        color = "#ef4444" if is_active else "#22c55e"  # Red : Green
        border = "#dc2626" if is_active else "#16a34a"

        if self.btn_mic_test.isEnabled():
            self.btn_mic_test.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    border-radius: 15px;
                    border: 2px solid {border};
                    font-weight: bold;
                    color: white;
                }}
            """)

    def _run_calibration(self):
        """Run microphone calibration sequence."""
        import winsound

        self.btn_mic_test.setEnabled(False)
        self.btn_mic_test.setText("...")

        self._calib_samples = []
        self._calib_phase = "INIT"

        # 1. Beep
        winsound.Beep(1000, 200)  # 1000Hz, 200ms

        # Start Silence Phase
        self._calib_phase = "SILENCE"
        self.btn_mic_test.setText("🤫")
        self.setWindowTitle("환경 소음 측정 중 (조용히)... 3초")

        QTimer.singleShot(3000, self._start_voice_phase)

    def _start_voice_phase(self):
        import winsound

        self._calib_silence_max = (
            max(self._calib_samples) if self._calib_samples else 0.0
        )
        self._calib_samples = []  # Reset

        winsound.Beep(1500, 200)  # Alert
        self._calib_phase = "VOICE"
        self.btn_mic_test.setText("🗣️")
        self.setWindowTitle("목소리 측정 중 (말씀하세요)... 4초")

        QTimer.singleShot(4000, self._finish_calibration)

    def _finish_calibration(self):
        import winsound

        self._calib_voice_avg = (
            sum(self._calib_samples) / len(self._calib_samples)
            if self._calib_samples
            else 0.0
        )

        noise = self._calib_silence_max
        speech = self._calib_voice_avg

        # Ideal threshold calculation
        if speech > noise:
            # 20% interpolation from noise to speech
            new_threshold = noise + (speech - noise) * 0.2
            new_threshold = max(new_threshold, 0.005)
        else:
            new_threshold = noise * 2.0
            new_threshold = max(new_threshold, 0.01)

        self.spin_vad_threshold.setValue(new_threshold)

        self.setWindowTitle("ThinkSub2 - 설정")
        self.btn_mic_test.setText("⚪")
        self.btn_mic_test.setEnabled(True)
        self._calib_phase = "IDLE"
        winsound.Beep(2000, 200)

    def _create_post_processing_tab(self) -> QWidget:
        """Create the Post-Processing settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("출력 필터")
        form = QFormLayout(group)

        # Main Toggle
        self.chk_enable_pp = QCheckBox("후처리 필터 사용 (Enable Filters)")
        self.chk_enable_pp.setChecked(self._current.get("enable_post_processing", True))
        self.chk_enable_pp.setToolTip(
            "체크 해제 시 모든 필터를 무시하고 모든 자막을 표시합니다."
        )
        form.addRow(self.chk_enable_pp)

        # Min Text Length
        self.spin_min_length = NoScrollSpinBox()
        self.spin_min_length.setRange(0, 100)
        self.spin_min_length.setValue(self._current.get("min_text_length", 0))
        self.spin_min_length.setSuffix(" 자 (글자수)")
        self.spin_min_length.setToolTip(
            "지정된 글자 수보다 짧은 자막은 무시합니다. (0 = 끄기)"
        )
        form.addRow("최소 길이 제한:", self.spin_min_length)

        # RMS Threshold
        self.spin_rms_filter = NoScrollDoubleSpinBox()
        self.spin_rms_filter.setRange(0.0, 1.0)
        self.spin_rms_filter.setSingleStep(0.001)
        self.spin_rms_filter.setDecimals(4)
        self.spin_rms_filter.setValue(self._current.get("rms_threshold", 0.002))
        self.spin_rms_filter.setToolTip(
            "이 값보다 평균 볼륨(RMS)이 낮은 구간은 무시합니다."
        )
        form.addRow("최소 볼륨 (RMS Cutoff):", self.spin_rms_filter)

        # Min Duration
        self.spin_min_duration = NoScrollDoubleSpinBox()
        self.spin_min_duration.setRange(0.0, 5.0)
        self.spin_min_duration.setSingleStep(0.1)
        self.spin_min_duration.setDecimals(1)
        self.spin_min_duration.setValue(self._current.get("min_duration", 0.0))
        self.spin_min_duration.setSuffix(" 초 (s)")
        self.spin_min_duration.setToolTip(
            "지정된 시간보다 짧은 음성 구간은 무시합니다. (0 = 끄기)"
        )
        form.addRow("최소 음성 길이:", self.spin_min_duration)

        # Max Duration
        self.spin_max_duration = NoScrollDoubleSpinBox()
        self.spin_max_duration.setRange(0.0, 120.0)
        self.spin_max_duration.setSingleStep(0.1)
        self.spin_max_duration.setDecimals(1)
        self.spin_max_duration.setValue(self._current.get("max_duration", 29.9))
        self.spin_max_duration.setSuffix(" 초 (s)")
        self.spin_max_duration.setToolTip(
            "지정된 시간 이상인 음성 구간은 무시합니다. (0 = 끄기)"
        )
        form.addRow("최대 음성 길이:", self.spin_max_duration)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _create_ui_tab(self) -> QWidget:
        """Create the UI settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        ui_group = QGroupBox(i18n.tr("인터페이스"))
        ui_layout = QFormLayout(ui_group)

        self.combo_ui_language = QComboBox()
        self.combo_ui_language.addItems([i18n.tr("한국어"), i18n.tr("English")])
        current_ui_lang = self._current.get("ui_language", "ko")
        self.combo_ui_language.setCurrentIndex(0 if current_ui_lang == "ko" else 1)
        ui_layout.addRow(i18n.tr("UI 언어:"), self.combo_ui_language)

        self.combo_ui_theme = QComboBox()
        self.combo_ui_theme.addItems(
            [i18n.tr("다크 모드"), i18n.tr("라이트 모드"), i18n.tr("남색 모드")]
        )
        current_theme = self._current.get("ui_theme", "dark")
        idx = 0
        if current_theme == "light":
            idx = 1
        elif current_theme == "navy":
            idx = 2
        self.combo_ui_theme.setCurrentIndex(idx)
        ui_layout.addRow(i18n.tr("UI 테마:"), self.combo_ui_theme)

        layout.addWidget(ui_group)
        layout.addStretch()
        return widget

    def _create_shortcuts_tab(self) -> QWidget:
        """Create the shortcuts settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        shortcuts_group = QGroupBox(i18n.tr("단축키 설정"))
        shortcuts_layout = QFormLayout(shortcuts_group)

        self.edit_undo = QLineEdit("Ctrl+Z")
        self.edit_undo.setReadOnly(True)
        shortcuts_layout.addRow(i18n.tr("실행취소:"), self.edit_undo)

        self.edit_redo = QLineEdit("Ctrl+Y")
        self.edit_redo.setReadOnly(True)
        shortcuts_layout.addRow(i18n.tr("다시실행:"), self.edit_redo)

        self.edit_merge = QLineEdit("Ctrl+M")
        self.edit_merge.setReadOnly(True)
        shortcuts_layout.addRow(i18n.tr("병합:"), self.edit_merge)

        self.edit_split = QLineEdit("Ctrl+S")
        self.edit_split.setReadOnly(True)
        shortcuts_layout.addRow(i18n.tr("분할:"), self.edit_split)

        self.edit_delete = QLineEdit("Delete")
        self.edit_delete.setReadOnly(True)
        shortcuts_layout.addRow(i18n.tr("삭제:"), self.edit_delete)

        layout.addWidget(shortcuts_group)

        note = QLabel(i18n.tr("(단축키 변경 기능은 추후 업데이트 예정)"))
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        layout.addStretch()
        return widget

    def _reset_to_defaults(self):
        """Reset all settings to defaults."""
        self._current = self.DEFAULT_SETTINGS.copy()

        # Ensure params are reset
        if "faster_whisper_params" not in self._current:
            self._current["faster_whisper_params"] = "{}"
        if "mic_index" not in self._current:
            self._current["mic_index"] = -1

        self.combo_model.setCurrentText(self._current["model"])
        self.combo_language.setCurrentText(self._current["language"])
        self.combo_device.setCurrentText(self._current["device"])
        self.spin_vad_threshold.setValue(self._current["vad_threshold"])
        self.spin_silence_duration.setValue(self._current["vad_silence_duration"])

        self.spin_font_size.setValue(self._current["subtitle_font_size"])
        self.spin_max_chars.setValue(self._current["subtitle_max_chars"])
        self.spin_max_lines.setValue(self._current["subtitle_max_lines"])
        self.spin_opacity.setValue(self._current["subtitle_opacity"])

        self.spin_min_length.setValue(self._current["min_text_length"])
        self.spin_rms_filter.setValue(self._current["rms_threshold"])
        self.spin_min_duration.setValue(self._current.get("min_duration", 0.0))
        self.spin_max_duration.setValue(self._current.get("max_duration", 29.9))
        self.chk_enable_pp.setChecked(self._current["enable_post_processing"])
        self.spin_live_wordtimestamp_offset.setValue(
            float(self._current.get("live_wordtimestamp_offset", 0.2))
        )
        self.spin_live_pad_before.setValue(
            float(self._current.get("live_pad_before", 0.1))
        )
        self.spin_live_pad_after.setValue(
            float(self._current.get("live_pad_after", 0.1))
        )

        self.spin_stt_seg_endmin.setValue(self._current.get("stt_seg_endmin", 0.05))
        self.chk_stt_extend_on_touch.setChecked(
            bool(self._current.get("stt_extend_on_touch", False))
        )

        self.chk_fw_live_sentence.setChecked(
            bool(self._current.get("fw_live_sentence", True))
        )
        self.spin_fw_live_max_gap.setValue(
            float(self._current.get("fw_live_max_gap", 0.8))
        )
        self.spin_fw_live_max_line_width.setValue(
            int(self._current.get("fw_live_max_line_width", 55))
        )
        self.spin_fw_live_max_line_count.setValue(
            int(self._current.get("fw_live_max_line_count", 2))
        )
        self.spin_fw_live_max_comma_cent.setValue(
            int(self._current.get("fw_live_max_comma_cent", 70))
        )
        self.spin_fw_live_one_word.setValue(
            int(self._current.get("fw_live_one_word", 0))
        )
        self.spin_fw_live_vad_max_speech_duration_s.setValue(
            float(self._current.get("fw_live_vad_max_speech_duration_s", 7.0))
        )
        self.spin_fw_live_length_penalty.setValue(
            float(self._current.get("fw_live_length_penalty", 0.9))
        )
        self.spin_fw_live_beam_size.setValue(
            int(self._current.get("fw_live_beam_size", 5))
        )
        self.spin_fw_live_best_of.setValue(int(self._current.get("fw_live_best_of", 1)))
        self.spin_fw_live_compression_ratio_threshold.setValue(
            float(self._current.get("fw_live_compression_ratio_threshold", 2.0))
        )
        self.spin_fw_live_logprob_threshold.setValue(
            float(self._current.get("fw_live_logprob_threshold", -1.0))
        )

        # File
        self.chk_fw_file_sentence.setChecked(
            bool(self._current.get("fw_file_sentence", True))
        )
        self.spin_fw_file_max_gap.setValue(
            float(self._current.get("fw_file_max_gap", 0.8))
        )
        self.spin_fw_file_max_line_width.setValue(
            int(self._current.get("fw_file_max_line_width", 55))
        )
        self.spin_fw_file_max_line_count.setValue(
            int(self._current.get("fw_file_max_line_count", 2))
        )
        self.spin_fw_file_max_comma_cent.setValue(
            int(self._current.get("fw_file_max_comma_cent", 70))
        )
        self.spin_fw_file_one_word.setValue(
            int(self._current.get("fw_file_one_word", 0))
        )
        self.spin_fw_file_vad_max_speech_duration_s.setValue(
            float(self._current.get("fw_file_vad_max_speech_duration_s", 7.0))
        )
        self.spin_fw_file_vad_threshold.setValue(
            float(self._current.get("fw_file_vad_threshold", 0.45))
        )
        self.spin_fw_file_length_penalty.setValue(
            float(self._current.get("fw_file_length_penalty", 0.9))
        )
        self.spin_fw_file_beam_size.setValue(
            int(self._current.get("fw_file_beam_size", 5))
        )
        self.spin_fw_file_best_of.setValue(int(self._current.get("fw_file_best_of", 1)))
        self.spin_fw_file_compression_ratio_threshold.setValue(
            float(self._current.get("fw_file_compression_ratio_threshold", 2.0))
        )
        self.spin_fw_file_logprob_threshold.setValue(
            float(self._current.get("fw_file_logprob_threshold", -1.0))
        )

        self.combo_ui_language.setCurrentIndex(0)
        self.combo_ui_theme.setCurrentIndex(0)

    def _create_subtitle_tab(self):
        widget = QWidget()
        layout = QVBoxLayout()

        # Font Size
        layout.addWidget(QLabel("폰트 크기 (Default: 25):"))
        self.spin_font_size = NoScrollSpinBox()
        self.spin_font_size.setRange(10, 100)
        self.spin_font_size.setValue(
            self._current.get(
                "subtitle_font_size", self.DEFAULT_SETTINGS["subtitle_font_size"]
            )
        )
        layout.addWidget(self.spin_font_size)

        # Max Characters
        layout.addWidget(QLabel("최대 표시 글자수 (Default: 40):"))
        self.spin_max_chars = NoScrollSpinBox()
        self.spin_max_chars.setRange(10, 200)
        self.spin_max_chars.setValue(
            self._current.get(
                "subtitle_max_chars", self.DEFAULT_SETTINGS["subtitle_max_chars"]
            )
        )
        layout.addWidget(self.spin_max_chars)

        # Max Lines
        layout.addWidget(QLabel("최대 줄 수 (Default: 2):"))
        self.spin_max_lines = NoScrollSpinBox()
        self.spin_max_lines.setRange(1, 10)
        self.spin_max_lines.setValue(
            self._current.get(
                "subtitle_max_lines", self.DEFAULT_SETTINGS["subtitle_max_lines"]
            )
        )
        layout.addWidget(self.spin_max_lines)

        # Opacity
        layout.addWidget(QLabel("불투명도 (%) (Default: 80):"))
        self.spin_opacity = NoScrollSpinBox()
        self.spin_opacity.setRange(10, 100)
        self.spin_opacity.setValue(
            self._current.get(
                "subtitle_opacity", self.DEFAULT_SETTINGS["subtitle_opacity"]
            )
        )
        layout.addWidget(self.spin_opacity)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def _apply_settings(self):
        """Apply and save settings (OK button)."""
        self._update_current_from_ui()  # Ensure latest state
        self._save_settings()  # Persist to disk
        self._saved = True  # Mark as saved
        self.close()

    def closeEvent(self, a0):
        """Handle close event. Revert if not saved."""
        if not self._saved:
            # Revert to original settings (Runtime only)
            self.settings_changed.emit(self._original)
        super().closeEvent(a0)

    def get_settings(self) -> dict:
        """Get current settings."""
        return self._current.copy()
