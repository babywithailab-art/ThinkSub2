"""
Real-time Waveform Visualization for ThinkSub2.
Uses PyQtGraph for high-performance rendering.
"""

import numpy as np
from typing import Optional, List, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMenu, QScrollBar
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QColor

import pyqtgraph as pg

from src.gui import i18n


class TimeAxisItem(pg.AxisItem):
    """AxisItem that displays time in MM:SS.ms format."""

    def tickStrings(self, values, scale, spacing):
        strings = []
        for v in values:
            if v < 0:
                strings.append("")
                continue

            # Show milliseconds if zoomed in (spacing < 1.0)
            show_millis = spacing < 1.0

            total_seconds = int(v)
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            millis = int((v % 1) * 100)

            hours = minutes // 60

            s_str = f"{seconds:02d}"
            if show_millis:
                s_str += f".{millis:02d}"

            if hours > 0:
                minutes = minutes % 60
                strings.append(f"{hours:02d}:{minutes:02d}:{s_str}")
            else:
                strings.append(f"{minutes:02d}:{s_str}")
        return strings


class CustomPlotWidget(pg.PlotWidget):
    """Custom PlotWidget to handle Ctrl+Wheel zooming."""

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Allow Zoom
            super().wheelEvent(event)
        else:
            if hasattr(self, "wheel_pan_callback") and self.wheel_pan_callback:
                self.wheel_pan_callback(event)
                return
            event.ignore()


class WaveformWidget(QWidget):
    """
    Real-time waveform visualization with subtitle overlays.
    X-axis represents "Audio Time" (seconds elapsed).

    Performance Optimizations:
    - Item Reuse: Do NOT create new GraphicsItems every frame.
    - Update existing items with setData/setPos.
    """

    # Signals
    segment_clicked = pyqtSignal(str)  # Emitted when a segment is clicked
    split_requested = pyqtSignal(str, float)  # segment_id, time
    playback_started = pyqtSignal()
    playback_finished = pyqtSignal()
    region_changed = pyqtSignal(str, float, float)  # segment_id, new_start, new_end
    cursor_time_changed = pyqtSignal(float)
    scroll_cursor_time_changed = pyqtSignal(float)

    def __init__(self, parent=None, scrollbar_position: str = "bottom"):
        super().__init__(parent)

        # Data Storage (Simple List)
        self._sample_rate = 16000
        self._audio_start_time = 0.0

        # Store all raw audio chunks
        self._audio_store: List[np.ndarray] = []

        # Settings
        self._visible_seconds = 10.0  # Show last 10 seconds on refresh
        self._max_visible_seconds = 300.0  # Max 5 minutes visible
        self._visual_gain = 3.0  # Visual boost factor (default 3x)
        self._live_render = False  # Deferred rendering by default
        self._follow_head = True

        # Throttling (Low Refresh Rate)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(100)  # Faster refresh for smooth GoldWave look
        self._refresh_timer.timeout.connect(self._refresh_plot)
        self._refresh_timer.start()

        self._full_audio_cache = None
        self._full_render_mode = False
        self._view_render_timer = QTimer(self)
        self._view_render_timer.setSingleShot(True)
        self._view_render_timer.timeout.connect(self._render_view_range)

        # Subtitle overlays (reused items)
        self._segment_items: Dict[str, pg.LinearRegionItem] = {}
        self._word_lines: Dict[str, List[pg.InfiniteLine]] = {}
        self._segment_times: Dict[str, tuple[float, float]] = {}
        self._segment_bounds: Dict[str, tuple[float, float | None]] = {}

        self._show_word_timestamps = True
        self._show_one_second_lines = True
        self._snap_enabled = True  # Snap to grid
        self._edit_enabled = True
        self._scrollbar_position = scrollbar_position

        self._setup_ui()

    def set_edit_enabled(self, enabled: bool):
        """Enable/disable waveform edit interactions (split/resize)."""
        self._edit_enabled = bool(enabled)

        # Update region interactivity
        for item in self._segment_items.values():
            try:
                item.setMovable(self._edit_enabled)
            except Exception:
                pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # PyQtGraph plot widget
        # Use Custom Axis for Time (MM:SS)
        self.plot_widget = CustomPlotWidget(
            axisItems={"bottom": TimeAxisItem(orientation="bottom")}
        )
        self.plot_widget.wheel_pan_callback = self._on_wheel_pan
        self.plot_widget.setBackground("#000030")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.12)
        self.plot_widget.getAxis("bottom").setPen(pg.mkPen(color="#9fb3ff"))
        self.plot_widget.getAxis("left").setPen(pg.mkPen(color="#9fb3ff"))

        self.plot_widget.setLabel(
            "bottom",
            i18n.tr("시간"),
            units=i18n.tr("초"),
            color="#FFFFFF",
        )
        self.plot_widget.setMouseEnabled(x=True, y=False)
        self.plot_widget.setClipToView(True)
        self.plot_widget.setYRange(-1.0, 1.0, padding=0)
        # We handle downsampling manually now

        self.plot_widget.plotItem.vb.sigXRangeChanged.connect(
            lambda *_: self._on_view_range_changed()
        )

        # Waveform curve (GoldWave Style)
        self.waveform_curve = self.plot_widget.plot(
            pen=pg.mkPen(color="#00FF00", width=1),
            antialias=False,
        )
        self.waveform_curve.setDownsampling(auto=False, ds=1)
        self.waveform_curve.setClipToView(False)
        self.waveform_curve.opts["connect"] = "pairs"

        self._scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self._scrollbar.setVisible(False)
        self._scrollbar.valueChanged.connect(self._on_scrollbar_changed)
        self._scrollbar.sliderPressed.connect(self._on_scrollbar_pressed)
        self._scrollbar.sliderReleased.connect(self._on_scrollbar_released)

        if self._scrollbar_position == "top":
            layout.addWidget(self._scrollbar)
            layout.addWidget(self.plot_widget)
        else:
            layout.addWidget(self.plot_widget)
            layout.addWidget(self._scrollbar)

        # Enable wheel zoom handled by CustomPlotWidget
        self.plot_widget.setMouseEnabled(x=True, y=False)

        # Custom Menu only
        self.plot_widget.getPlotItem().setMenuEnabled(False)

        # Cursor Line (Red, tracks mouse)
        self.cursor_line = pg.InfiniteLine(
            pos=0,
            angle=90,
            pen=pg.mkPen(color="#ff3b30", width=3, style=Qt.PenStyle.SolidLine),
            movable=False,
        )
        self.cursor_line.setZValue(150)
        self.cursor_line.setVisible(False)
        self.plot_widget.addItem(self.cursor_line)

        # Scroll Cursor Line (Blue, scroll sync)
        self.scroll_cursor_line = pg.InfiniteLine(
            pos=0,
            angle=90,
            pen=pg.mkPen(color="#3a8dff", width=2, style=Qt.PenStyle.SolidLine),
            movable=False,
        )
        self.scroll_cursor_line.setZValue(140)
        self.scroll_cursor_line.setVisible(True)
        self.plot_widget.addItem(self.scroll_cursor_line)

        # Center Line (Y=0)
        self.center_line = pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen(color="#333333", width=1), movable=False
        )
        self.plot_widget.addItem(self.center_line)

        # Context menu & Mouse
        self.plot_widget.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # Cursor Lock State
        self._cursor_locked = False

        # Playback uses cursor_line

    def retranslate_ui(self):
        self.plot_widget.setLabel(
            "bottom",
            i18n.tr("시간"),
            units=i18n.tr("초"),
            color="#FFFFFF",
        )

    def keyPressEvent(self, event):
        """Handle keyboard events."""
        if event.key() == Qt.Key.Key_Space:
            self.toggle_playback()
        else:
            super().keyPressEvent(event)

    def toggle_playback(self):
        """Toggle playback based on state."""
        # If playing, stop
        if hasattr(self, "_anim_timer") and self._anim_timer.isActive():
            self.stop_playback()
            return

        # If not playing, start from cursor (if locked/visible)
        start_time = 0.0
        if self._cursor_locked:
            # Always trust cursor if locked, even if momentarily hidden by scroll
            start_time = self.cursor_line.value()

            # If start is near end, restart from 0
            if start_time >= self._current_head_time - 0.1:
                start_time = 0.0

        self.play_segment(start_time, self._current_head_time)

    def play_segment(self, start: float, end: float):
        """
        Reconstruct audio from store and play.
        Complexity: O(Total_Chunks) search.
        """
        import sounddevice as sd
        import time
        from PyQt6.QtCore import QTimer

        sd.stop()

        try:
            # We need to reconstruct full array relative to session start?
            # Or assume continuous?
            # Our _audio_store is list of continuous chunks from session start.
            # Assuming no gaps for now as per AudioRecorder logic.

            # 1. Flatten only needed range?
            # Optimization: Don't flatten everything.
            # Convert Query Time to Sample Indices
            # Assuming store starts at session_start (0.0 relative)

            start_idx = int(start * self._sample_rate)
            end_idx = int(end * self._sample_rate)

            # Find which chunks cover this range
            # Since chunks are strictly ordered and continuous:
            current_ptr = 0
            audio_parts = []

            for chunk in self._audio_store:
                chunk_len = len(chunk)
                chunk_start = current_ptr
                chunk_end = current_ptr + chunk_len

                # Check intersection
                if chunk_end > start_idx and chunk_start < end_idx:
                    # Overlap
                    # Intersect [start_idx, end_idx] with [chunk_start, chunk_end]
                    req_start = max(start_idx, chunk_start)
                    req_end = min(end_idx, chunk_end)

                    # Local indices
                    loc_start = req_start - chunk_start
                    loc_end = req_end - chunk_start

                    audio_parts.append(chunk[loc_start:loc_end])

                current_ptr += chunk_len

                if current_ptr > end_idx:
                    break

            if not audio_parts:
                print("No audio found.")
                # Force cleanup of cursor if no audio
                self.cursor_line.setVisible(False)
                return

            full_audio = np.concatenate(audio_parts)

            sd.play(full_audio, self._sample_rate)

            self.cursor_line.setVisible(True)
            self.cursor_line.setValue(start)

            self._playback_start_time = time.time()
            self._playback_duration = end - start
            self._playback_audio_start = start

            if hasattr(self, "_anim_timer"):
                self._anim_timer.stop()
            self._anim_timer = QTimer(self)
            self._anim_timer.timeout.connect(self._update_playback_cursor)
            self._anim_timer.start(16)  # 60 FPS for smoother cursor

            QTimer.singleShot(
                int(self._playback_duration * 1000) + 200, self._stop_playback_cursor
            )

            self.playback_started.emit()

        except Exception as e:
            print(f"Playback error: {e}")
            import traceback

            traceback.print_exc()

    def extract_audio(self, start: float, end: float) -> Optional[np.ndarray]:
        """Extract audio in [start, end] as float32 at 16kHz."""
        try:
            if not self._audio_store:
                return None

            start_idx = int(start * self._sample_rate)
            end_idx = int(end * self._sample_rate)
            if end_idx <= start_idx:
                return None

            current_ptr = 0
            audio_parts = []
            for chunk in self._audio_store:
                chunk_len = len(chunk)
                chunk_start = current_ptr
                chunk_end = current_ptr + chunk_len

                if chunk_end > start_idx and chunk_start < end_idx:
                    req_start = max(start_idx, chunk_start)
                    req_end = min(end_idx, chunk_end)
                    loc_start = req_start - chunk_start
                    loc_end = req_end - chunk_start
                    audio_parts.append(chunk[loc_start:loc_end])

                current_ptr += chunk_len
                if current_ptr > end_idx:
                    break

            if not audio_parts:
                return None
            return np.concatenate(audio_parts)

        except Exception as e:
            print(f"[Waveform] extract_audio error: {e}")
            return None

    def stop_playback(self):
        """Stop current playback."""
        import sounddevice as sd

        sd.stop()
        # Sync cursor to the current position before stopping
        try:
            current_pos = self._get_playback_time()
            self.cursor_line.setValue(current_pos)
        except Exception:
            pass
        self._stop_playback_cursor()

    def is_playing(self) -> bool:
        return hasattr(self, "_anim_timer") and self._anim_timer.isActive()

    # ... (cursor helpers unchanged) ...
    def _update_playback_cursor(self):
        import time

        elapsed = time.time() - self._playback_start_time
        if elapsed > self._playback_duration:
            self._stop_playback_cursor()
            return
        current_pos = self._playback_audio_start + elapsed
        self.cursor_line.setValue(current_pos)
        self.cursor_line.setVisible(True)  # Force Visible
        self.cursor_time_changed.emit(float(current_pos))

    def _stop_playback_cursor(self):
        if hasattr(self, "_anim_timer"):
            self._anim_timer.stop()
        # Keep cursor locked at playback position
        self._cursor_locked = True
        self.cursor_line.setVisible(True)
        self.playback_finished.emit()

    def _get_playback_time(self) -> float:
        """Get current playback time in seconds (waveform playback)."""
        import time

        if hasattr(self, "_playback_start_time") and hasattr(
            self, "_playback_audio_start"
        ):
            elapsed = time.time() - self._playback_start_time
            if hasattr(self, "_playback_duration"):
                elapsed = min(elapsed, self._playback_duration)
            return self._playback_audio_start + elapsed

        if self.cursor_line.isVisible():
            return float(self.cursor_line.value())
        return 0.0

    def _on_mouse_moved(self, pos):
        """Update cursor line position on hover."""
        # If locked (clicked), do not follow mouse
        if self._cursor_locked:
            return

        if self.plot_widget.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            self.cursor_line.setPos(mouse_point.x())
            self.cursor_line.setVisible(True)
        else:
            self.cursor_line.setVisible(False)

    def update_audio(self, data: np.ndarray, start_time: float):
        """
        Appends data to store. O(1).
        Checks for gaps and fills with silence if needed.
        """
        try:
            # Check for GAP
            # Allow gap check on first chunk too (if start_time > 0)
            if (
                hasattr(self, "_next_expected_time")
                and start_time > self._next_expected_time
            ):
                diff = start_time - self._next_expected_time

                # Tolerance: e.g., > 50ms gap implies missing data (or drift/jitter)
                if diff > 0.05:
                    print(
                        f"[Waveform] Gap detected: {diff:.3f}s. Filling with silence."
                    )

                    # Create silence
                    num_zeros = int(diff * self._sample_rate)
                    if num_zeros > 0:
                        zeros = np.zeros(num_zeros, dtype=np.float32)
                        self._audio_store.append(zeros)

                    # Update expected time (it effectively jumps)
                    self._next_expected_time += diff

            # Just store
            self._audio_store.append(data)

            # Update head
            duration = len(data) / self._sample_rate
            self._current_head_time = start_time + duration

            # Track next expected
            self._next_expected_time = self._current_head_time

            # [Fix for File Load]
            # If a single chunk is huge (e.g. > 30s), it's likely a full file load.
            # In this case, disable the live refresh timer to stop it from
            # forcefully scrolling to the end.
            if duration > 30.0:
                self._refresh_timer.stop()

        except Exception as e:
            print(f"[Waveform] Update Error: {e}")
            import traceback

            traceback.print_exc()

    def clear(self):
        """Clear waveform data and overlays."""
        self._audio_store = []
        self._audio_start_time = 0.0
        self._current_head_time = 0.0
        self._next_expected_time = 0.0
        self._full_audio_cache = None
        self._full_render_mode = False
        self.waveform_curve.setData([], [], connect="pairs")
        self.cursor_line.setVisible(False)

    def _refresh_plot(self):
        """
        Timer callback: Redraw the last N seconds.
        This runs at low FPS (e.g. 1Hz).
        """
        try:
            if not self._audio_store:
                return

            # 1. Determine how much data to fetch
            visible_seconds = min(self._visible_seconds, self._max_visible_seconds)
            target_samples = int(visible_seconds * self._sample_rate)

            # 2. Collect chunks from the end
            collected_samples = 0
            chunks_to_plot = []

            # Iterate backwards
            for i in range(len(self._audio_store) - 1, -1, -1):
                chunk = self._audio_store[i]
                chunks_to_plot.append(chunk)
                collected_samples += len(chunk)
                if collected_samples >= target_samples:
                    break

            if not chunks_to_plot:
                return

            # Reverse back to normal order
            chunks_to_plot.reverse()

            # 3. Concatenate and slice
            full_data = np.concatenate(chunks_to_plot)

            if len(full_data) > target_samples:
                # Keep last N samples
                full_data = full_data[-target_samples:]

            end_time = self._current_head_time
            duration = len(full_data) / self._sample_rate
            start_time = end_time - duration

            self.waveform_curve.setPen(pg.mkPen(color="#00FF00", width=1))
            x_data, y_data = self._build_smoothed_waveform(
                full_data, start_time, end_time, target_bins=2000, window=9
            )
            self.waveform_curve.opts["connect"] = "pairs"
            self.waveform_curve.setData(
                x=x_data, y=y_data, connect="pairs", skipFiniteCheck=True
            )

            # Set View Range
            if self._follow_head:
                self._set_view_start(end_time - visible_seconds, end_time)
            else:
                self._set_view_start(self._get_scrollbar_start(), None)

            self._sync_scrollbar(end_time, visible_seconds)

        except Exception as e:
            print(f"[Waveform] Refresh Error: {e}")

    def set_live_render(self, enabled: bool):
        """Enable or disable live rendering."""
        if enabled:
            self._full_render_mode = False
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()
        else:
            self._refresh_timer.stop()
            # Optional: Clear plot or show "Recording..." status
            # For now, just stop updating. The last frame will persist or we can clear.
            # Let's clear to avoid confusion? Or keep static?
            # User asked: "unable status live view", so maybe clear or dim.
            self.waveform_curve.setData([], [], connect="pairs")

    def render_full_session(self):
        """Render the entire session waveform (pro-grade)."""
        if not self._audio_store:
            return

        try:
            # 1. Concatenate ALL data
            full_data = np.concatenate(self._audio_store)
            self._full_audio_cache = full_data
            self._full_render_mode = True

            duration = len(full_data) / self._sample_rate
            start_time = self._audio_start_time  # Relative to session start (0.0)
            end_time = start_time + duration

            self.waveform_curve.setPen(pg.mkPen(color="#00FF00", width=2))
            pixel_bins = 2000
            x_data, y_data = self._build_pixel_waveform(
                full_data, start_time, end_time, pixel_bins
            )
            self.waveform_curve.opts["connect"] = "pairs"
            self.waveform_curve.setData(
                x=x_data, y=y_data, connect="pairs", skipFiniteCheck=True
            )

            visible_seconds = min(self._max_visible_seconds, end_time)
            self._set_view_start(0.0, visible_seconds)
            self._sync_scrollbar(end_time, visible_seconds)

        except Exception as e:
            print(f"[Waveform] Full Render Error: {e}")
            import traceback

            traceback.print_exc()

    def _on_view_range_changed(self) -> None:
        if not self._full_render_mode:
            return
        if self._full_audio_cache is None:
            return
        if not self._view_render_timer.isActive():
            self._view_render_timer.start(30)

    def _on_scrollbar_pressed(self):
        self._follow_head = False

    def _on_scrollbar_released(self):
        self._follow_head = False

    def _on_scrollbar_changed(self, value: int):
        if not self._scrollbar.isVisible():
            return
        visible_seconds = min(self._visible_seconds, self._max_visible_seconds)
        start = float(value) / 1000.0
        self._set_view_start(start, start + visible_seconds)

    def _on_wheel_pan(self, event):
        if not self._scrollbar.isVisible():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._follow_head = False
        steps = int(delta / 120)
        step_seconds = 5.0
        view_range = self.plot_widget.viewRange()[0]
        current_start = float(view_range[0])
        new_start = current_start - (steps * step_seconds)
        self._set_view_start(new_start, None)

    def _set_view_start(self, start: float, end: Optional[float]) -> None:
        if start < 0:
            start = 0.0
        if end is None:
            visible_seconds = min(self._visible_seconds, self._max_visible_seconds)
            end = start + visible_seconds

        self.plot_widget.setXRange(start, end, padding=0)

        if self._scrollbar.isVisible():
            max_start = max(0.0, float(self._scrollbar.maximum()) / 1000.0)
            if start > max_start:
                start = max_start
            self._scrollbar.blockSignals(True)
            self._scrollbar.setValue(int(start * 1000))
            self._scrollbar.blockSignals(False)

    def _get_scrollbar_start(self) -> float:
        if not self._scrollbar.isVisible():
            return 0.0
        return float(self._scrollbar.value()) / 1000.0

    def _sync_scrollbar(self, end_time: float, visible_seconds: float) -> None:
        max_start = max(0.0, end_time - visible_seconds)
        if max_start <= 0.0:
            self._scrollbar.setVisible(False)
            return

        self._scrollbar.setVisible(True)
        self._scrollbar.blockSignals(True)
        self._scrollbar.setRange(0, int(max_start * 1000))
        self._scrollbar.setPageStep(int(visible_seconds * 1000))
        if self._follow_head:
            self._scrollbar.setValue(int(max_start * 1000))
        self._scrollbar.blockSignals(False)

    def _render_view_range(self) -> None:
        if not self._full_render_mode:
            return
        if self._full_audio_cache is None:
            return

        view_range = self.plot_widget.viewRange()[0]
        view_start = max(self._audio_start_time, float(view_range[0]))
        view_end = max(view_start, float(view_range[1]))
        full_duration = len(self._full_audio_cache) / self._sample_rate
        full_end = self._audio_start_time + full_duration
        if view_start >= full_end:
            return
        if view_end > full_end:
            view_end = full_end

        start_idx = int((view_start - self._audio_start_time) * self._sample_rate)
        end_idx = int((view_end - self._audio_start_time) * self._sample_rate)
        if end_idx <= start_idx:
            return

        segment = self._full_audio_cache[start_idx:end_idx]
        pixel_bins = 2000
        x_data, y_data = self._build_pixel_waveform(
            segment, view_start, view_end, pixel_bins
        )
        self.waveform_curve.setPen(pg.mkPen(color="#00FF00", width=2))
        self.waveform_curve.opts["connect"] = "pairs"
        self.waveform_curve.setData(
            x=x_data, y=y_data, connect="pairs", skipFiniteCheck=True
        )

    def _smooth_waveform(self, y: np.ndarray, window: int = 5) -> np.ndarray:
        if y is None:
            return y
        if len(y) < window:
            return y
        kernel = np.ones(window, dtype=np.float32) / float(window)
        return np.convolve(y, kernel, mode="same")

    def _build_smoothed_waveform(
        self,
        data: np.ndarray,
        start_time: float,
        end_time: float,
        target_bins: int,
        window: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        boosted = np.clip(data * self._visual_gain, -1.0, 1.0)
        if len(boosted) == 0:
            return np.array([], dtype=np.float32), np.array([], dtype=np.float32)

        bins = min(target_bins, len(boosted))
        bin_size = max(1, len(boosted) // bins)
        trimmed_len = bin_size * bins
        trimmed = boosted[:trimmed_len].reshape(bins, bin_size)
        mins = np.min(trimmed, axis=1)
        maxs = np.max(trimmed, axis=1)
        x_bins = np.linspace(start_time, end_time, len(mins))
        x_data = np.repeat(x_bins, 2)
        y_data = np.column_stack([mins, maxs]).reshape(-1)
        return x_data, y_data

    def _build_pixel_waveform(
        self, data: np.ndarray, start_time: float, end_time: float, bins: int
    ) -> tuple[np.ndarray, np.ndarray]:
        boosted = np.clip(data * self._visual_gain, -1.0, 1.0)
        if len(boosted) == 0:
            return np.array([], dtype=np.float32), np.array([], dtype=np.float32)

        bins = max(1, min(bins, len(boosted)))
        edges = np.linspace(0, len(boosted), bins + 1, dtype=int)
        mins = np.empty(bins, dtype=np.float32)
        maxs = np.empty(bins, dtype=np.float32)
        for i in range(bins):
            segment = boosted[edges[i] : edges[i + 1]]
            if segment.size == 0:
                mins[i] = 0.0
                maxs[i] = 0.0
            else:
                mins[i] = float(np.min(segment))
                maxs[i] = float(np.max(segment))

        x_bins = np.linspace(start_time, end_time, len(mins))
        x_data = np.repeat(x_bins, 2)
        y_data = np.column_stack([mins, maxs]).reshape(-1)
        return x_data, y_data

    def get_cursor_time(self) -> float:
        """Get current cursor time if locked, else -1."""
        if self.cursor_line.isVisible():
            value = self.cursor_line.value()
            if isinstance(value, (list, tuple)):
                if not value:
                    return -1.0
                return float(value[0])
            if value is None:
                return -1.0
            return float(value)
        return -1.0

    def get_scroll_cursor_time(self) -> float:
        if self.scroll_cursor_line.isVisible():
            value = self.scroll_cursor_line.value()
            if isinstance(value, (list, tuple)):
                if not value:
                    return -1.0
                return float(value[0])
            if value is None:
                return -1.0
            return float(value)
        return -1.0

    def get_playback_time(self) -> float:
        """Get playback time if playing, otherwise cursor time."""
        if hasattr(self, "_anim_timer") and self._anim_timer.isActive():
            return float(self._get_playback_time())
        return self.get_cursor_time()

    def set_cursor_pos(self, time: float, emit: bool = True):
        """Set cursor position programmatically (e.g. from editor)."""
        if time < 0:
            return

        self._cursor_locked = True
        self.cursor_line.setPos(time)
        self.cursor_line.setVisible(True)
        if emit:
            self.cursor_time_changed.emit(float(time))

    def set_scroll_cursor_pos(self, time: float, emit: bool = True):
        if time < 0:
            return
        self.scroll_cursor_line.setPos(time)
        self.scroll_cursor_line.setVisible(True)
        if emit:
            self.scroll_cursor_time_changed.emit(float(time))

    def _on_region_changed(self, segment_id: str, item):
        """Handle region resize/move."""
        start, end = item.getRegion()

        start, end = self._clamp_region_bounds(segment_id, start, end)

        if self._snap_enabled:
            # Snap to 0.05s grid
            step = 0.05
            start = round(start / step) * step
            end = round(end / step) * step

            start, end = self._clamp_region_bounds(segment_id, start, end)

            # Apply snapped values back to item (visual feedback)
            item.blockSignals(True)
            item.setRegion([start, end])
            item.blockSignals(False)

        self.region_changed.emit(segment_id, start, end)

    def _on_region_changing(self, segment_id: str, item):
        start, end = item.getRegion()
        start, end = self._clamp_region_bounds(segment_id, start, end)
        item.blockSignals(True)
        item.setRegion([start, end])
        item.blockSignals(False)

    def _clamp_region_bounds(
        self, segment_id: str, start: float, end: float
    ) -> tuple[float, float]:
        bounds = self._segment_bounds.get(segment_id)
        if bounds:
            min_bound, max_bound = bounds
            if start < min_bound:
                start = min_bound
            if max_bound is not None and end > max_bound:
                end = max_bound
        if end < start:
            end = start
        return start, end

    def _recompute_segment_bounds(self):
        ordered = sorted(self._segment_times.items(), key=lambda item: item[1][0])
        bounds: Dict[str, tuple[float, float | None]] = {}
        for idx, (sid, (start, end)) in enumerate(ordered):
            min_bound = 0.0
            max_bound = None
            if idx > 0:
                prev_end = ordered[idx - 1][1][1]
                min_bound = prev_end
            if idx < len(ordered) - 1:
                next_start = ordered[idx + 1][1][0]
                max_bound = next_start
            bounds[sid] = (min_bound, max_bound)
        self._segment_bounds = bounds

    def add_segment_overlay(
        self, segment_id: str, start: float, end: float, is_final: bool = False
    ):
        """
        Add or update a subtitle segment overlay.
        Uses Item Reuse pattern for performance.
        """
        # [Request] Hide real-time draft layers (Only show FINAL)
        if not is_final:
            # Remove if it exists (e.g. status change)
            if segment_id in self._segment_items:
                self.plot_widget.removeItem(self._segment_items[segment_id])
                del self._segment_items[segment_id]
            return

        color = QColor(74, 222, 128, 80) if is_final else QColor(250, 204, 21, 60)

        if segment_id in self._segment_items:
            # REUSE: Update existing item
            item = self._segment_items[segment_id]
            # Avoid triggering signal if region hasn't changed significantly?
            # Or temporarily block signals?
            # But setRegion is usually programmatic here.
            # If user is dragging, this might conflict with update loop.
            # Assuming updates come from backend (e.g. initial load or merge),
            # we should block signals to prevent loop.
            item.blockSignals(True)
            item.setRegion([start, end])
            item.setBrush(pg.mkBrush(color))
            try:
                item.setMovable(self._edit_enabled)
            except Exception:
                pass
            item.blockSignals(False)
        else:
            # Create new item (only when segment is new)
            item = pg.LinearRegionItem(
                values=[start, end], brush=pg.mkBrush(color), movable=self._edit_enabled
            )
            item.setZValue(10)  # Bring to front (above waveform) for dragging

            # Hover Effect
            hover_color = QColor(color)
            hover_color.setAlpha(160)  # Brighter/Opaque on hover
            item.setHoverBrush(pg.mkBrush(hover_color))

            # Custom Cursor for resize handles (<->)
            for line in item.lines:
                line.setCursor(Qt.CursorShape.SizeHorCursor)

            # Connect resize/move signal
            item.sigRegionChangeFinished.connect(
                lambda item=item, sid=segment_id: self._on_region_changed(sid, item)
            )
            item.sigRegionChanged.connect(
                lambda item=item, sid=segment_id: self._on_region_changing(sid, item)
            )

            self.plot_widget.addItem(item)
            self._segment_items[segment_id] = item

    def add_word_timestamps(self, segment_id: str, words: List[tuple]):
        """
        Add word timestamp lines for a segment.
        words: List of (start, end, text, probability)
        """
        if not self._show_word_timestamps:
            return

        # Remove old lines if any (REUSE pool)
        if segment_id in self._word_lines:
            for line in self._word_lines[segment_id]:
                self.plot_widget.removeItem(line)

        lines = []
        # Cyan/Aqua color for high visibility
        ts_color = QColor(0, 255, 255, 200)

        for word in words:
            start_time = word[0]
            word_text = word[2]

            # Line
            line = pg.InfiniteLine(
                pos=start_time,
                angle=90,
                pen=pg.mkPen(color=ts_color, width=1.5, style=Qt.PenStyle.DashLine),
            )
            line.setZValue(-5)
            self.plot_widget.addItem(line)
            lines.append(line)

            # Text
            text_item = pg.TextItem(word_text, anchor=(0, 1))
            # HTML for small font
            text_item.setHtml(
                f'<div style="font-size: 8pt; color: #00FFFF;">{word_text}</div>'
            )
            text_item.setPos(start_time, 0.95)  # Top
            text_item.setZValue(-4)
            self.plot_widget.addItem(text_item)
            lines.append(text_item)

        self._word_lines[segment_id] = lines

    def refresh_segments(self, segments):
        """Re-draw all segment overlays and word lines (Smart Diff)."""
        # Import SegmentStatus to check if final
        from src.engine.subtitle import SegmentStatus

        self._segment_times = {
            s.id: (s.start, s.end)
            for s in segments
            if not getattr(s, "is_hidden", False) and s.status == SegmentStatus.FINAL
        }
        self._recompute_segment_bounds()

        new_ids = {s.id for s in segments}
        current_ids = set(self._segment_items.keys())
        self.plot_widget.setUpdatesEnabled(False)

        # 1. Remove deleted
        to_remove = current_ids - new_ids
        for sid in to_remove:
            # Segments
            if sid in self._segment_items:
                self.plot_widget.removeItem(self._segment_items[sid])
                del self._segment_items[sid]
            # Words
            if sid in self._word_lines:
                for line in self._word_lines[sid]:
                    self.plot_widget.removeItem(line)
                del self._word_lines[sid]

        # 2. Add/Update existing
        for seg in segments:
            # Skip hidden segments if any
            if getattr(seg, "is_hidden", False):
                continue

            is_final = seg.status == SegmentStatus.FINAL
            self.add_segment_overlay(seg.id, seg.start, seg.end, is_final)

            # Word timestamps (Only update if needed? add_word_timestamps clears old ones for that ID)
            # Optimization: Check if word count changed? Or just re-add for modified segments.
            # But here we iterate ALL.
            # Ideally we only update if changed. But logic is complex.
            # Let's rely on add_word_timestamps being reasonably fast for single items.
            if seg.words:
                word_tuples = [
                    (w.start, w.end, w.text, w.probability) for w in seg.words
                ]
                self.add_word_timestamps(seg.id, word_tuples)
        self.plot_widget.setUpdatesEnabled(True)

    def update_segment_visual(self, segment):
        """Update visualization for a single segment (Optimized)."""
        sid = segment.id
        from src.engine.subtitle import SegmentStatus

        is_final = segment.status == SegmentStatus.FINAL

        if is_final and not getattr(segment, "is_hidden", False):
            self._segment_times[sid] = (segment.start, segment.end)
        else:
            if sid in self._segment_times:
                del self._segment_times[sid]
        self._recompute_segment_bounds()

        # 1. Update/Add Segment Region
        if sid in self._segment_items:
            # Existing: Update inplace
            item = self._segment_items[sid]
            item.blockSignals(True)
            item.setRegion([segment.start, segment.end])
            item.blockSignals(False)
        else:
            # New: Add
            self.add_segment_overlay(sid, segment.start, segment.end, is_final)

        # 2. Update Word Timestamps
        # Just reuse add_word_timestamps which clears old lines for this ID
        if segment.words:
            word_tuples = [
                (w.start, w.end, w.text, w.probability) for w in segment.words
            ]
            self.add_word_timestamps(sid, word_tuples)
        else:
            # If no words now but had before, clear them
            if sid in self._word_lines:
                for line in self._word_lines[sid]:
                    self.plot_widget.removeItem(line)
                del self._word_lines[sid]

    def remove_segment_visual(self, segment_id: str):
        if segment_id in self._segment_items:
            self.plot_widget.removeItem(self._segment_items[segment_id])
            del self._segment_items[segment_id]
        if segment_id in self._word_lines:
            for line in self._word_lines[segment_id]:
                self.plot_widget.removeItem(line)
            del self._word_lines[segment_id]
        if segment_id in self._segment_times:
            del self._segment_times[segment_id]
            self._recompute_segment_bounds()

    def apply_diff(self, added: List, removed_ids: List[str], updated: List):
        """Apply diff results: add new, update existing, remove deleted."""
        self.plot_widget.setUpdatesEnabled(False)
        try:
            for rid in removed_ids:
                self.remove_segment_visual(rid)

            for seg in added:
                self.update_segment_visual(seg)

            for seg in updated:
                self.update_segment_visual(seg)
        finally:
            self.plot_widget.setUpdatesEnabled(True)

    def add_segment_visual(self, segment):
        """Add visualization for a new segment."""
        self.update_segment_visual(segment)

    def zoom_to_range(self, start: float, end: float):
        """Zoom view to specific range with padding."""
        duration = end - start
        if duration <= 0:
            return

        padding = max(0.5, duration * 0.2)  # Min 0.5s or 20% padding
        new_min = start - padding
        new_max = end + padding

        # Clamp to 0
        if new_min < 0:
            new_min = 0
            new_max = max(new_max, duration + padding * 2)

        self.plot_widget.setXRange(new_min, new_max, padding=0)

    def highlight_segment(self, segment_id: str):
        """Highlight a segment (visual only)."""
        target_item = None
        for sid, item in self._segment_items.items():
            if sid == segment_id:
                item.setBrush(pg.mkBrush(QColor(59, 130, 246, 120)))  # Blue highlight
                target_item = item
            else:
                item.setBrush(pg.mkBrush(QColor(74, 222, 128, 80)))

        if target_item:
            # Center View
            region = target_item.getRegion()
            center = (region[0] + region[1]) / 2
            curr_range = self.plot_widget.viewRange()[0]
            width = curr_range[1] - curr_range[0]

            # Keep zoom level, just pan
            new_min = center - (width / 2)
            new_max = center + (width / 2)

            if new_min < 0:
                new_min = 0
                new_max = width

            self.plot_widget.setXRange(new_min, new_max, padding=0)

    def _on_mouse_clicked(self, event):
        """Handle mouse clicks."""
        pos = event.scenePos()
        view_pos = self.plot_widget.plotItem.vb.mapSceneToView(pos)
        click_time = view_pos.x()

        # Lock/Unlock cursor on click
        self.setFocus()  # Ensure widget gets keyboard focus
        self.set_cursor_pos(click_time, emit=True)

        if event.button() == Qt.MouseButton.RightButton:
            # Find segment
            clicked_segment_id = None
            for sid, item in self._segment_items.items():
                region = item.getRegion()
                if region[0] <= click_time <= region[1]:
                    clicked_segment_id = sid
                    break

            # Show simplified context menu
            # Use the locked cursor position for splitting if available
            split_time = click_time
            self._show_context_menu(event.screenPos(), clicked_segment_id, split_time)

        elif event.button() == Qt.MouseButton.LeftButton:
            # Existing Selection Logic
            for sid, item in self._segment_items.items():
                region = item.getRegion()
                if region[0] <= click_time <= region[1]:
                    self.segment_clicked.emit(sid)
                    break

    def _show_context_menu(
        self, screen_pos, segment_id: Optional[str], click_time: float
    ):
        """Show custom context menu."""
        menu = QMenu(self)

        # 1. Split (only if clicked on a segment)
        if self._edit_enabled:
            if segment_id:
                split_action = menu.addAction("여기서 분할")
                split_action.triggered.connect(
                    lambda: self.split_requested.emit(segment_id, click_time)
                )
            else:
                act = menu.addAction("여기서 분할")
                act.setEnabled(False)
        else:
            act = menu.addAction("(웨이브폼 편집: 끔)")
            act.setEnabled(False)

        menu.addSeparator()

        # 2. Toggle Timestamps
        toggle_words = menu.addAction(
            "단어 타임스탬프 표시"
            if not self._show_word_timestamps
            else "단어 타임스탬프 숨기기"
        )
        toggle_words.triggered.connect(self._toggle_word_timestamps)

        # 3. Toggle Grid
        toggle_seconds = menu.addAction(
            "1초 눈금 표시" if not self._show_one_second_lines else "1초 눈금 숨기기"
        )
        toggle_seconds.triggered.connect(self._toggle_second_lines)

        # 4. Toggle Snap
        toggle_snap = menu.addAction(
            "자석 효과 (Snap) 끄기" if self._snap_enabled else "자석 효과 (Snap) 켜기"
        )
        toggle_snap.triggered.connect(self._toggle_snap)

        menu.exec(screen_pos.toPoint())

    def _toggle_snap(self):
        self._snap_enabled = not self._snap_enabled

    def _toggle_word_timestamps(self):
        self._show_word_timestamps = not self._show_word_timestamps
        # Hide/show all word lines
        for lines in self._word_lines.values():
            for line in lines:
                line.setVisible(self._show_word_timestamps)

    def _toggle_second_lines(self):
        self._show_one_second_lines = not self._show_one_second_lines
        # TODO: Implement 1-second grid lines

    def clear(self):
        """Clear all."""
        import sounddevice as sd

        sd.stop()
        self._stop_playback_cursor()

        # Reset storage
        self._audio_store = []

        self._current_head_time = 0.0
        self._audio_start_time = 0.0
        self._next_expected_time = 0.0

        self.waveform_curve.setData([], [])

        for item in self._segment_items.values():
            self.plot_widget.removeItem(item)
        self._segment_items.clear()

        for lines in self._word_lines.values():
            for line in lines:
                self.plot_widget.removeItem(line)
        self._word_lines.clear()

    def seek_to(self, time: float):
        """
        Center the view on the specified time.
        Called when a subtitle segment is selected in Editor.
        """
        # Current view range width
        curr_range = self.plot_widget.viewRange()[0]
        width = curr_range[1] - curr_range[0]

        # New range centered on time
        new_min = time - (width / 2)
        new_max = time + (width / 2)

        if new_min < 0:
            new_min = 0
            new_max = width

        self.plot_widget.setXRange(new_min, new_max, padding=0)

    def set_total_duration(self, duration: float):
        """Set the visible range to cover the entire duration (0 to duration)."""
        # Stop live refreshing
        self._refresh_timer.stop()

        if duration <= 0:
            return
        self.plot_widget.setXRange(0, duration, padding=0)

    def start_monitoring(self):
        """Restart monitoring/refreshing."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def save_to_wav(self, filename: str):
        """
        Save the current audio session to a WAV file.
        Format: 16-bit PCM, Mono, 16kHz
        """
        if not self._audio_store:
            raise ValueError("저장할 오디오 데이터가 없습니다.")

        import wave

        try:
            # 1. Concatenate all data
            full_data = np.concatenate(self._audio_store)

            # 2. Convert to 16-bit PCM
            # float32 (-1.0 to 1.0) -> int16 (-32768 to 32767)
            pcm_data = (full_data * 32767).astype(np.int16)

            # 3. Write to WAV
            with wave.open(filename, "wb") as wf:
                wf.setnchannels(1)  # Mono
                wf.setsampwidth(2)  # 2 bytes = 16 bit
                wf.setframerate(self._sample_rate)
                wf.writeframes(pcm_data.tobytes())

            print(f"[Waveform] Saved WAV to {filename}")

        except Exception as e:
            print(f"[Waveform] WAV Export Error: {e}")
            raise e
