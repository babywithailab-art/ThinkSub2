"""
Faster-Whisper Transcription Engine for ThinkSub2.
Runs in a separate multiprocessing.Process to avoid GIL blocking.
"""

import multiprocessing as mp
from multiprocessing import Process, Queue
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Dict, Any
import time
import os
import sys


class ControlCommand(Enum):
    """Commands for the transcriber process."""

    LOAD_MODEL = auto()
    TRANSCRIBE_LIVE = auto()  # word_timestamps=False
    TRANSCRIBE_FINAL = auto()  # word_timestamps=True
    TRANSCRIBE_FILE = auto()  # Transcribe a file
    CANCEL_FILE = auto()  # Cancel in-progress file transcription
    SHUTDOWN = auto()
    RELOAD_SETTINGS = auto()


@dataclass
class TranscribeRequest:
    """Request to transcribe audio."""

    audio_data: bytes  # numpy array serialized
    start_time: float
    end_time: float
    is_final: bool  # True = VAD End (word_timestamps=True)
    source: str = "live"  # "live" or "file"


@dataclass
class TranscribeResult:
    """Result from transcription."""

    segment_id: str
    text: str
    start: float
    end: float
    words: list  # List of (start, end, text, probability) tuples
    is_final: bool
    source: str = "live"  # "live" or "file"
    avg_logprob: float = 0.0
    avg_rms: float = 0.0


class WhisperTranscriberProcess:
    """
    Wrapper for the transcriber process.
    Manages 4 queues: audio, result, control, log.
    """

    def __init__(self):
        self._process: Optional[Process] = None

        # 4-Queue Architecture
        self.audio_queue: Queue = Queue()  # Raw audio data
        self.result_queue: Queue = Queue()  # Transcription results
        self.control_queue: Queue = Queue()  # Commands (load_model, shutdown, etc.)
        self.log_queue: Queue = Queue()  # Logs & Download progress

        self._is_ready = False

    @staticmethod
    def _run_transcriber(
        audio_queue: Queue,
        result_queue: Queue,
        control_queue: Queue,
        log_queue: Queue,
        config: Dict[str, Any],
    ):
        """
        Main loop for the transcriber process.
        This runs in a completely separate process.
        """
        import numpy as np

        model = None
        model_size = config.get("model", "large-v3-turbo")
        device = config.get("device", "cuda")
        language = config.get("language", "ko")
        # Default to float16 on CUDA if not specified, int8 on CPU if not specified.
        # But if user specified, honor it.
        default_compute = "float16" if device == "cuda" else "int8"
        compute_type = config.get("compute_type", default_compute)

        # Additional transcribe kwargs (must be JSON-serializable)
        extra_params = config.get("faster_whisper_params") or {}
        if not isinstance(extra_params, dict):
            extra_params = {}

        RESERVED_TRANSCRIBE_KWARGS = {
            "language",
            "word_timestamps",
            "vad_filter",
            "without_timestamps",
        }

        def merged_transcribe_kwargs(**base_kwargs):
            # Extra params cannot override reserved keys
            merged = dict(extra_params)
            for k in list(merged.keys()):
                if k in RESERVED_TRANSCRIBE_KWARGS:
                    merged.pop(k, None)
            merged.update(base_kwargs)
            return merged

        def log(message: str):
            log_queue.put(f"[Transcriber] {message}")

        log("Process started. Waiting for commands...")

        cancel_file = False
        active_file: Optional[str] = None
        shutdown_requested = False

        while True:
            # Check for control commands first
            try:
                cmd, data = control_queue.get_nowait()

                if cmd == ControlCommand.LOAD_MODEL:
                    log(f"Loading model: {model_size} on {device}...")
                    try:
                        import warnings

                        # Suppress 'local_dir_use_symlinks' warning from huggingface_hub
                        warnings.filterwarnings(
                            "ignore", message=".*local_dir_use_symlinks.*"
                        )

                        from faster_whisper import WhisperModel, download_model

                        # Fix for WinError 1314 & Support for EXE distribution
                        # We determine the base path depending on whether we are frozen (EXE) or running as script.
                        if getattr(sys, "frozen", False):
                            # Distributed EXE mode
                            base_path = os.path.dirname(sys.executable)
                        else:
                            # Dev script mode (src/engine/transcriber.py -> ... -> project root)
                            base_path = os.path.dirname(
                                os.path.dirname(
                                    os.path.dirname(os.path.abspath(__file__))
                                )
                            )

                        models_dir = os.path.join(base_path, "models")
                        os.makedirs(models_dir, exist_ok=True)

                        log(f"Initializing WhisperModel (Model path: {models_dir})...")

                        # Explicitly download first to ensure we have files without symlinks issues
                        try:
                            # Try downloading to local dir which avoids symlinks usually
                            # faster_whisper's download_model uses hf_hub_download.
                            # We pass output_dir to force local download.
                            model_path = download_model(
                                model_size,
                                output_dir=os.path.join(
                                    models_dir, f"faster-whisper-{model_size}"
                                ),
                                local_files_only=False,
                            )
                        except Exception as dl_error:
                            log(
                                f"Download warning: {dl_error}. Retrying with default..."
                            )
                            model_path = model_size  # Fallback

                        model = WhisperModel(
                            model_path, device=device, compute_type=compute_type
                        )
                        log(f"Model loaded successfully! (Compute: {compute_type})")
                        result_queue.put(("MODEL_READY", None))

                    except Exception as e:
                        log(f"ERROR: Failed to load model: {e}")
                        result_queue.put(("MODEL_ERROR", str(e)))

                elif cmd == ControlCommand.SHUTDOWN:
                    log("Shutdown command received. Exiting...")
                    shutdown_requested = True
                    break

                elif cmd == ControlCommand.CANCEL_FILE:
                    cancel_file = True
                    if active_file:
                        log(f"Cancel requested for file: {active_file}")

                elif cmd == ControlCommand.RELOAD_SETTINGS:
                    # Update settings without reloading model
                    if "language" in data:
                        language = data["language"]
                        log(f"Language updated to: {language}")

                    if "faster_whisper_params" in data:
                        try:
                            new_params = data["faster_whisper_params"]
                            if isinstance(new_params, dict):
                                extra_params = new_params
                                log("Faster-Whisper extra params updated.")
                        except Exception as e:
                            log(f"Failed to update extra params: {e}")

                elif cmd == ControlCommand.TRANSCRIBE_FILE:
                    file_path = data
                    log(f"Transcribing file: {file_path}")
                    if model is None:
                        log("ERROR: Model not loaded. Cannot transcribe file.")
                        result_queue.put(("TRANSCRIPTION_ERROR", "Model not loaded"))
                        continue

                    try:
                        import gc
                        import torch

                        # Memory Cleanup before large task
                        gc.collect()
                        if device == "cuda":
                            torch.cuda.empty_cache()

                        cancel_file = False
                        active_file = file_path

                        # File transcription
                        transcribe_kwargs = merged_transcribe_kwargs(
                            language=language if language != "auto" else None,
                            word_timestamps=True,
                            vad_filter=True,  # Use VAD for files
                        )
                        log(f"Transcribe Params: {transcribe_kwargs}")

                        segments_gen, info = model.transcribe(
                            file_path,
                            **transcribe_kwargs,
                        )

                        all_segments = []
                        total_duration = info.duration
                        last_logged_percent = -1

                        for segment in segments_gen:
                            # Allow cancellation during long file runs
                            try:
                                while True:
                                    ccmd, cdata = control_queue.get_nowait()
                                    if ccmd == ControlCommand.CANCEL_FILE:
                                        cancel_file = True
                                    elif ccmd == ControlCommand.SHUTDOWN:
                                        cancel_file = True
                                        shutdown_requested = True
                                    elif ccmd == ControlCommand.RELOAD_SETTINGS:
                                        if isinstance(cdata, dict):
                                            if "language" in cdata:
                                                language = cdata["language"]
                                            if (
                                                "faster_whisper_params" in cdata
                                                and isinstance(
                                                    cdata["faster_whisper_params"], dict
                                                )
                                            ):
                                                extra_params = cdata[
                                                    "faster_whisper_params"
                                                ]
                                    else:
                                        # Ignore other commands during file transcription
                                        pass
                            except Exception:
                                pass

                            if cancel_file:
                                log(f"File transcription cancelled: {file_path}")
                                result_queue.put(("FILE_CANCELLED", file_path))
                                break

                            # Collect segment immediately (don't send yet)
                            words_data = []
                            if segment.words:
                                for word in segment.words:
                                    words_data.append(
                                        (
                                            word.start,
                                            word.end,
                                            word.word,
                                            word.probability,
                                        )
                                    )

                            res = TranscribeResult(
                                segment_id="",
                                text=segment.text.strip(),
                                start=segment.start,
                                end=segment.end,
                                words=words_data,
                                is_final=True,
                                source="file",
                            )
                            all_segments.append(res)

                            # Log Progress
                            if total_duration > 0:
                                percent = int((segment.end / total_duration) * 100)
                                percent = min(100, max(0, percent))
                                if percent > last_logged_percent:
                                    # Log every 1% or if gap is large
                                    log(
                                        f"[진행률] {percent}% ({segment.end:.1f}s / {total_duration:.1f}s)"
                                    )
                                    last_logged_percent = percent

                        if cancel_file:
                            active_file = None
                            if shutdown_requested:
                                break
                            continue

                        log(f"File transcription completed: {file_path}")
                        result_queue.put(("FILE_ALL_SEGMENTS", all_segments))
                        result_queue.put(("FILE_COMPLETED", file_path))
                        active_file = None

                        if shutdown_requested:
                            break

                    except Exception as e:
                        log(f"File transcription failed: {e}")
                        result_queue.put(("TRANSCRIPTION_ERROR", str(e)))

            except:
                pass  # No control command

            if shutdown_requested:
                break

            # Check for audio to transcribe
            try:
                request: TranscribeRequest = audio_queue.get(timeout=0.1)

                if model is None:
                    log("WARNING: Model not loaded, skipping transcription")
                    continue

                # Deserialize audio
                audio_array = np.frombuffer(request.audio_data, dtype=np.float32)

                duration_sec = len(audio_array) / 16000.0
                log(
                    f"[Transcriber-Debug] Job Received: Offset={request.start_time:.2f}s, Duration={duration_sec:.2f}s, Final={request.is_final}"
                )

                # Calculate RMS for this chunk
                rms = float(np.sqrt(np.mean(audio_array**2)))

                # Transcribe with appropriate settings
                word_timestamps = request.is_final  # True for VAD End, False for Live

                try:
                    segments, info = model.transcribe(
                        audio_array,
                        **merged_transcribe_kwargs(
                            language=language if language != "auto" else None,
                            word_timestamps=word_timestamps,
                            vad_filter=False,  # We handle VAD ourselves
                            without_timestamps=False,
                        ),
                    )

                    batch_results = []

                    segment_count = 0
                    for segment in segments:
                        segment_count += 1
                        # Fix: Transcriber is the Single Source of Truth for Absolute Time.
                        # segment.start is relative to the audio chunk provided.
                        # request.start_time is the absolute start time of that chunk.

                        # [ThinkSub2 Patch] Apply 0.05s offset correction for sync
                        # User reported timestamps are ~0.05s too early.
                        # ONLY for Live Mode. File STT usually doesn't need this.
                        time_correction = 0.05 if request.source == "live" else 0.0

                        absolute_start = (
                            request.start_time + segment.start + time_correction
                        )
                        absolute_end = (
                            request.start_time + segment.end + time_correction
                        )

                        log(
                            f"[Sync] Segment: Whisper={segment.start:.2f}-{segment.end:.2f} | Offset={request.start_time:.2f} | Abs={absolute_start:.2f}-{absolute_end:.2f}"
                        )

                        words_data = []
                        if word_timestamps and segment.words:
                            for word in segment.words:
                                words_data.append(
                                    (
                                        request.start_time
                                        + word.start
                                        + time_correction,
                                        request.start_time + word.end + time_correction,
                                        word.word,
                                        word.probability,
                                    )
                                )

                        result = TranscribeResult(
                            segment_id="",  # Will be assigned by manager
                            text=segment.text.strip(),
                            start=absolute_start,
                            end=absolute_end,
                            words=words_data,
                            is_final=request.is_final,
                            source="live",
                            avg_logprob=segment.avg_logprob,
                            avg_rms=rms,
                        )
                        batch_results.append(result)

                    # [ThinkSub2 Patch] Extend the last segment to match VAD end time
                    # This prevents subtitles from disappearing too quickly (before silence ends)
                    if batch_results and request.is_final:
                        last_res = batch_results[-1]
                        # If Whisper's timestamp is earlier than VAD's cutoff (plus padding), extend it.
                        if last_res.end < request.end_time:
                            # print(f"[Transcriber] Extending last segment end: {last_res.end:.2f} -> {request.end_time:.2f}")
                            last_res.end = request.end_time

                    if segment_count == 0:
                        log(
                            f"Whisper returned 0 segments for this audio chunk (RMS: {rms:.4f})"
                        )
                    else:
                        log(
                            f"Generated {segment_count} segments. Batch size: {len(batch_results)}"
                        )

                    if batch_results:
                        result_queue.put(("TRANSCRIPTION_BATCH", batch_results))
                        log("Batch sent to Result Queue.")

                except Exception as e:
                    log(f"Transcription error: {e}")
                    import traceback

                    log(f"Traceback: {traceback.format_exc()}")

            except:
                pass  # No audio in queue, continue loop

            time.sleep(0.01)  # Small sleep to prevent CPU spinning

        log("Process terminated.")

    def start(self, config: Optional[Dict[str, Any]] = None):
        """Start the transcriber process."""
        if self._process and self._process.is_alive():
            return

        final_config: Dict[str, Any] = config or {
            "model": "large-v3-turbo",
            "device": "cuda",
            "language": "ko",
        }

        self._process = Process(
            target=self._run_transcriber,
            args=(
                self.audio_queue,
                self.result_queue,
                self.control_queue,
                self.log_queue,
                final_config,
            ),
            daemon=True,
        )
        self._process.start()

    def load_model(self):
        """Send command to load the model."""
        self.control_queue.put((ControlCommand.LOAD_MODEL, None))

    def transcribe_live(self, audio_data, start_time: float, end_time: float):
        """Queue audio for Live transcription (word_timestamps=False)."""
        request = TranscribeRequest(
            audio_data=audio_data.tobytes(),
            start_time=start_time,
            end_time=end_time,
            is_final=False,
        )
        self.audio_queue.put(request)

    def transcribe_final(self, audio_data, start_time: float, end_time: float):
        """Queue audio for Final transcription (word_timestamps=True)."""
        request = TranscribeRequest(
            audio_data=audio_data.tobytes(),
            start_time=start_time,
            end_time=end_time,
            is_final=True,
        )
        self.audio_queue.put(request)

    def shutdown(self):
        """Shutdown the transcriber process."""
        if self._process and self._process.is_alive():
            self.control_queue.put((ControlCommand.SHUTDOWN, None))
            self._process.join(timeout=5.0)
            if self._process.is_alive():
                self._process.terminate()
        self._process = None

    def update_settings(self, settings: Dict[str, Any]):
        """Update transcriber settings."""
        self.control_queue.put((ControlCommand.RELOAD_SETTINGS, settings))

    def transcribe_file(self, file_path: str):
        """Queue a file for transcription."""
        self.control_queue.put((ControlCommand.TRANSCRIBE_FILE, file_path))

    def cancel_file(self):
        """Request cancellation of in-progress file transcription."""
        self.control_queue.put((ControlCommand.CANCEL_FILE, None))

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()
