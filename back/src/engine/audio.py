"""
Audio Capture and VAD (Voice Activity Detection) for ThinkSub2.
RMS calculation is performed in this thread, NOT in the UI thread.
"""

import threading
import queue
import time
from dataclasses import dataclass
from typing import Optional, Callable, Any, Dict, cast

import numpy as np
import sounddevice as sd


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

    SAMPLE_RATE = 16000  # Whisper expects 16kHz
    CHANNELS = 1
    CHUNK_DURATION = 0.1  # 100ms chunks

    def __init__(self):
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

    def _resample_to_16k(self, audio_data: np.ndarray, src_rate: float) -> np.ndarray:
        """Resample audio_data to 16kHz using linear interpolation."""
        if src_rate == self.SAMPLE_RATE:
            return audio_data.astype(np.float32, copy=False)
        if audio_data.size == 0:
            return audio_data.astype(np.float32, copy=False)

        dst_len = int(round(audio_data.size * (self.SAMPLE_RATE / float(src_rate))))
        if dst_len <= 1:
            return np.zeros((0,), dtype=np.float32)

        x_old = np.linspace(0.0, 1.0, num=audio_data.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=False)
        out = np.interp(x_new, x_old, audio_data).astype(np.float32)
        return out

    def _audio_callback(self, indata, frames, time_info, status):
        """Audio callback."""
        if status:
            print(f"[Audio] Status: {status}")

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

        # Resample to 16k for downstream (Whisper/VAD)
        audio_data = self._resample_to_16k(audio_data, float(self._stream_sample_rate))
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

        print("[Audio] Start called. Resetting total_frames to 0.")
        self._running = True
        self._session_start_time = time.time()
        self._total_frames = 0  # Frame counter for stable timestamps
        self._adc_start_time = None  # Legacy, unused but kept for structure

        # Determine stream samplerate/channels from device default, then resample to 16k
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
            samplerate = float(dev.get("default_samplerate", self.SAMPLE_RATE))
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
            print(
                f"[Audio] Stream Started. Requested=16000, Actual={self._stream.samplerate}, Channels={self._stream.channels}"
            )

            if self._stream.samplerate != self.SAMPLE_RATE:
                print(
                    f"[Audio] WARNING: Sample Rate Mismatch! Hardware running at {self._stream.samplerate}"
                )

        except Exception as e:
            print(f"[Audio] Failed to start stream: {e}")
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

    def __init__(self, threshold: float = 0.02, min_silence_duration: float = 0.5):
        """
        Args:
            threshold: RMS threshold for voice detection
            min_silence_duration: Minimum silence duration to end a phrase (seconds)
        """
        self.threshold = threshold
        self.min_silence_duration = min_silence_duration

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

                        print(
                            f"[VAD-Debug] Phrase Detected: Start={phrase_start:.3f}, End={phrase_end:.3f}, Duration={phrase_end - phrase_start:.3f}"
                        )

                        # Reset state
                        self._is_speaking = False
                        self._silence_start = None
                        self._phrase_buffer = []
                        self._phrase_start_time = None

                        # Add a small buffer to phrase_end (e.g., 0.2s) to prevent clipping
                        padding = 0.2
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
            duration = len(phrase_audio) / AudioRecorder.SAMPLE_RATE
            phrase_end = phrase_start + duration

            return (phrase_audio, phrase_start, phrase_end)
        return None

    def reset(self):
        """Reset VAD state."""
        self._is_speaking = False
        self._silence_start = None
        self._phrase_buffer = []
        self._phrase_start_time = None
