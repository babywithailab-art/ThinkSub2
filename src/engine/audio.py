"""
Audio Capture and VAD (Voice Activity Detection) for ThinkSub2.
RMS calculation is performed in this thread, NOT in the UI thread.
"""

import threading
import queue
import time
from dataclasses import dataclass
from typing import Optional, Callable, Any, Dict, cast
import logging

import numpy as np
import sounddevice as sd

# Import JSON logger for structured logging
try:
    from src.utils.json_logger import get_logger, generate_request_id

    HAS_JSON_LOGGER = True
except ImportError:
    # Fallback if json_logger not available
    HAS_JSON_LOGGER = False
    import uuid

    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

    def generate_request_id() -> str:
        return f"req_{uuid.uuid4().hex[:8]}"


@dataclass
class AudioChunk:
    """Represents an audio chunk with timing information."""

    data: np.ndarray
    start_time: float  # Absolute start time in session
    rms: float  # Pre-calculated RMS value


class AudioRecorder:
    """
    Records audio from the microphone using a callback-based stream.
    Uses hardware timestamps (inputBufferAdcTime) for precise synchronization.
    Calculates RMS in the callback thread.
    """

    SAMPLE_RATE = 44100  # Input stream target rate
    MODEL_SAMPLE_RATE = 16000  # Whisper/VAD target rate
    CHANNELS = 1
    CHUNK_DURATION = 0.1  # 100ms chunks

    def __init__(self):
        # Initialize logger for audio recorder
        self._logger = get_logger("audio")
        self._session_request_id = generate_request_id()

        self._logger.info(
            "AudioRecorder initialized",
            extra={
                "data": {
                    "request_id": self._session_request_id,
                    "sample_rate": self.SAMPLE_RATE,
                    "model_rate": self.MODEL_SAMPLE_RATE,
                    "channels": self.CHANNELS,
                }
            },
        )

        self._running = False
        self._stream: Optional[sd.InputStream] = None
        self._audio_queue: queue.Queue[AudioChunk] = queue.Queue()
        self._device_index: Optional[int] = None
        self._loopback: bool = False
        self._active_channels: int = self.CHANNELS
        self._stream_sample_rate: float = float(self.SAMPLE_RATE)

        # Timing
        self._session_start_time: float = 0.0
        self._adc_start_time: Optional[float] = None

        # Callbacks
        self._on_rms_update: Optional[Callable[[float], None]] = None
        self._on_audio_chunk: Optional[Callable[[AudioChunk], None]] = None

    @staticmethod
    def list_devices() -> list:
        """List available audio input devices."""
        devices = sd.query_devices()
        input_devices = []
        for i, dev in enumerate(devices):
            dev = cast(Dict[str, Any], dev)
            if dev["max_input_channels"] > 0:
                input_devices.append(
                    {
                        "index": i,
                        "name": dev["name"],
                        "channels": dev["max_input_channels"],
                        "sample_rate": dev["default_samplerate"],
                    }
                )
        return input_devices

    def set_device(self, device_index: Optional[int], loopback: bool = False):
        """Set the audio input device.

        When loopback=True on Windows WASAPI, device_index should be an OUTPUT device
        index (desktop audio capture).
        """
        self._device_index = device_index
        self._loopback = loopback

    def set_on_rms_update(self, callback: Callable[[float], None]):
        """Set callback for RMS updates (for UI meter)."""
        self._on_rms_update = callback

    def set_on_audio_chunk(self, callback: Callable[[AudioChunk], None]):
        """Set callback for audio chunks."""
        self._on_audio_chunk = callback

    @property
    def audio_queue(self) -> queue.Queue[AudioChunk]:
        """Queue containing recorded audio chunks."""
        return self._audio_queue

    def _calculate_rms(self, audio_data: np.ndarray) -> float:
        """Calculate RMS (Root Mean Square) of audio data."""
        return float(np.sqrt(np.mean(audio_data**2)))

    def _resample_for_model(
        self, audio_data: np.ndarray, src_rate: float
    ) -> np.ndarray:
        """Resample audio_data to model sample rate using linear interpolation."""
        if src_rate == self.MODEL_SAMPLE_RATE:
            return audio_data.astype(np.float32, copy=False)
        if audio_data.size == 0:
            return audio_data.astype(np.float32, copy=False)

        dst_len = int(
            round(audio_data.size * (self.MODEL_SAMPLE_RATE / float(src_rate)))
        )
        if dst_len <= 1:
            return np.zeros((0,), dtype=np.float32)

        x_old = np.linspace(0.0, 1.0, num=audio_data.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=False)
        out = np.interp(x_new, x_old, audio_data).astype(np.float32)
        return out

    def _audio_callback(self, indata, frames, time_info, status):
        """Audio callback."""
        if status:
            self._logger.debug(
                f"Audio status changed: {status}",
                extra={
                    "data": {
                        "request_id": self._session_request_id,
                        "status": str(status),
                        "frames": self._total_frames,
                    }
                },
            )

        # 1. Precise Frame Timing (ALWAYS track frames regardless of state)
        # Capture START frame of this chunk
        start_frame = self._total_frames

        # Increment TOTAL frames
        self._total_frames += frames

        # 2. State Check (Early Exit)
        if not self._running:
            return

        # 3. Calculate Time (0-based Frame Time)
        # Use actual stream sample rate (loopback is usually 48k)
        chunk_start_time = start_frame / float(self._stream_sample_rate)

        # 4. Process Audio
        # Downmix to mono if needed
        if hasattr(indata, "ndim") and indata.ndim == 2 and indata.shape[1] > 1:
            audio_data = indata.mean(axis=1).astype(np.float32, copy=True)
        else:
            audio_data = indata.flatten().copy()  # Copy is important for queue safety

        # Resample to model rate for downstream (Whisper/VAD)
        audio_data = self._resample_for_model(
            audio_data, float(self._stream_sample_rate)
        )
        rms = self._calculate_rms(audio_data)

        chunk = AudioChunk(data=audio_data, start_time=chunk_start_time, rms=rms)

        self._audio_queue.put(chunk)

        # Callbacks
        if self._on_rms_update:
            self._on_rms_update(rms)

        if self._on_audio_chunk:
            self._on_audio_chunk(chunk)

    def start(self):
        """Start recording."""
        if self._running:
            return

        self._logger.debug(
            "Audio start called",
            extra={"data": {"request_id": self._session_request_id}},
        )
        self._running = True
        self._session_start_time = time.time()
        self._total_frames = 0  # Frame counter for stable timestamps
        self._adc_start_time = None  # Legacy, unused but kept for structure

        # Determine stream samplerate/channels from device default, then resample to model rate
        samplerate = float(self.SAMPLE_RATE)
        channels = self.CHANNELS

        try:
            dev_idx = self._device_index
            if dev_idx is None:
                default_pair = sd.default.device
                default_in = None
                if default_pair:
                    default_in = default_pair[0]
                if default_in is None:
                    raise RuntimeError("No default input device")
                dev_idx = int(default_in)
                self._device_index = dev_idx

            dev = cast(Dict[str, Any], sd.query_devices(dev_idx))
            in_ch = int(dev.get("max_input_channels", 1))
            # Stereo Mix is often 2ch; downmix in callback.
            channels = 2 if in_ch >= 2 else 1
        except Exception:
            samplerate = float(self.SAMPLE_RATE)
            channels = self.CHANNELS

        # If loopback requested but device has no input channels, we cannot capture
        if self._loopback:
            try:
                if self._device_index is None:
                    self._running = False
                    return

                dev = cast(Dict[str, Any], sd.query_devices(self._device_index))
                if int(dev.get("max_input_channels", 0)) <= 0:
                    print(
                        "[Audio] Loopback requested but no loopback support in this sounddevice build. "
                        "Enable 'Stereo Mix' or use a loopback-capable backend."
                    )
                    self._running = False
                    return
            except Exception:
                self._running = False
                return

        self._stream_sample_rate = samplerate
        self._active_channels = channels

        # Calculate block size based on stream samplerate
        block_size = int(self._stream_sample_rate * self.CHUNK_DURATION)

        try:
            extra_settings = None

            self._stream = sd.InputStream(
                device=self._device_index,
                samplerate=self._stream_sample_rate,
                channels=channels,
                dtype=np.float32,
                blocksize=block_size,
                callback=self._audio_callback,
                extra_settings=extra_settings,
            )
            self._stream.start()
            self._logger.info(
                "Stream started",
                extra={
                    "data": {
                        "request_id": self._session_request_id,
                        "requested_rate": self.SAMPLE_RATE,
                        "actual_rate": self._stream.samplerate,
                        "channels": self._stream.channels,
                        "device_index": self._device_index,
                    }
                },
            )

            if self._stream.samplerate != self.SAMPLE_RATE:
                self._logger.warning(
                    "Sample rate mismatch",
                    extra={
                        "data": {
                            "request_id": self._session_request_id,
                            "requested": self.SAMPLE_RATE,
                            "actual": self._stream.samplerate,
                        }
                    },
                )

        except Exception as e:
            self._logger.error(
                "Failed to start audio stream",
                extra={
                    "data": {
                        "request_id": self._session_request_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "device_index": self._device_index,
                    }
                },
            )
            self._running = False

    def stop(self):
        """Stop recording."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def clear_queue(self):
        """Clear the audio queue."""
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    @property
    def is_running(self) -> bool:
        return self._running


class VADProcessor:
    """
    Voice Activity Detection processor.
    Detects speech segments and silence for phrase boundary detection.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration: float = 0.5,
        speech_pad_ms: int = 50,
    ):
        """
        Args:
            threshold: RMS threshold for voice detection
            min_silence_duration: Minimum silence duration to end a phrase (seconds)
            speech_pad_ms: Padding added to the end of a phrase (milliseconds)
        """
        self.threshold = threshold
        self.min_silence_duration = min_silence_duration
        self.speech_pad_seconds = float(speech_pad_ms) / 1000.0
        self._debug = False

        self._is_speaking = False
        self._silence_start: Optional[float] = None
        self._phrase_buffer: list = []
        self._phrase_start_time: Optional[float] = None

    def process_chunk(self, chunk: AudioChunk) -> Optional[tuple]:
        """
        Process an audio chunk through VAD.

        Returns:
            None if no phrase boundary detected.
            (audio_array, start_time, end_time) if a phrase ended.
        """
        is_voice = chunk.rms > self.threshold

        if self._debug:
            print(
                f"[VAD] RMS: {chunk.rms:.5f} (Threshold: {self.threshold:.5f}) - Voice: {is_voice}"
            )
        if is_voice:
            # Voice detected
            if not self._is_speaking:
                # Start of new phrase
                self._is_speaking = True
                self._phrase_start_time = chunk.start_time

            self._silence_start = None
            self._phrase_buffer.append(chunk.data)

        else:
            # Silence detected
            if self._is_speaking:
                # Add to buffer even during silence (for natural transitions)
                self._phrase_buffer.append(chunk.data)

                if self._silence_start is None:
                    self._silence_start = chunk.start_time

                silence_duration = chunk.start_time - self._silence_start

                if silence_duration >= self.min_silence_duration:
                    # Phrase ended - return buffered audio
                    if self._phrase_buffer and self._phrase_start_time is not None:
                        phrase_audio = np.concatenate(self._phrase_buffer)
                        phrase_start = self._phrase_start_time
                        phrase_end = chunk.start_time

                        if self._debug:
                            print(
                                f"[VAD-Debug] Phrase Detected: Start={phrase_start:.3f}, End={phrase_end:.3f}, Duration={phrase_end - phrase_start:.3f}"
                            )

                        # Reset state
                        self._is_speaking = False
                        self._silence_start = None
                        self._phrase_buffer = []
                        self._phrase_start_time = None

                        # Add padding to phrase_end to prevent clipping
                        padding = self.speech_pad_seconds
                        phrase_end_padded = phrase_end + padding

                        return (phrase_audio, phrase_start, phrase_end_padded)

        return None

    def get_current_phrase(self) -> Optional[tuple]:
        """
        Get the currently accumulated phrase buffer without resetting.
        Used for intermediate 'Live' transcription updates.

        Returns:
            (audio_array, start_time, end_time) or None if no active phrase.
        """
        if (
            self._is_speaking
            and self._phrase_buffer
            and self._phrase_start_time is not None
        ):
            phrase_audio = np.concatenate(self._phrase_buffer)
            phrase_start = self._phrase_start_time
            # For live updates, end time is effectively 'now' relative to start
            # But strictly it's start + duration of captured audio
            duration = len(phrase_audio) / AudioRecorder.MODEL_SAMPLE_RATE
            phrase_end = phrase_start + duration

            return (phrase_audio, phrase_start, phrase_end)
        return None

    def set_params(
        self, threshold: float, min_silence_duration: float, speech_pad_ms: int
    ):
        """Update VAD parameters."""
        self.threshold = threshold
        self.min_silence_duration = min_silence_duration
        self.speech_pad_seconds = float(speech_pad_ms) / 1000.0

    def reset(self):
        """Reset VAD state."""
        self._is_speaking = False
        self._silence_start = None
        self._phrase_buffer = []
        self._phrase_start_time = None
