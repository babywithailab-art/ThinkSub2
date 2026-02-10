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
import json

# Import JSON logger for structured logging
try:
    from src.utils.json_logger import (
        get_logger as _get_logger,
        generate_request_id,
        log_with_request_id_synced,
    )

    HAS_JSON_LOGGER = True
except ImportError:
    # Fallback if json_logger not available
    import logging

    HAS_JSON_LOGGER = False

    def _get_logger(*args, **kwargs):
        name = args[0] if args else "transcriber"
        return logging.getLogger(name)

    def generate_request_id():
        import uuid

        return f"req_{uuid.uuid4().hex[:8]}"

    def log_with_request_id_synced(*args, **kwargs):
        return kwargs.get("request_id")


def get_logger(*args, **kwargs):
    return _get_logger(*args, **kwargs)


class ControlCommand(Enum):
    """Commands for the transcriber process."""

    LOAD_MODEL = auto()
    TRANSCRIBE_LIVE = auto()  # word_timestamps=False
    TRANSCRIBE_FINAL = auto()  # word_timestamps=True
    TRANSCRIBE_FILE = auto()  # Transcribe a file
    TRANSCRIBE_FILE_WITH_SEGMENTS = auto()  # Transcribe a file with FFmpeg-based segmentation
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

        # Initialize logger (use json_logger if available)
        logger = get_logger("transcriber", log_level="DEBUG")

        def log(message: str, level="INFO", request_id=None, data=None):
            """Enhanced logging function with request ID support."""
            import logging

            logger.log(
                getattr(logging, level.upper(), logging.INFO),
                message,
                extra={"request_id": request_id, "data": data},
            )
            # Also send to legacy queue for backward compatibility
            log_queue.put(f"[Transcriber] {message}")

        # Generate session request ID for this transcriber process
        session_request_id = generate_request_id()
        log(
            "Transcriber process initialized",
            request_id=session_request_id,
            data={"process_id": os.getpid()},
        )

        log("Process started. Waiting for commands...")

        cancel_file = False
        active_file: Optional[str] = None
        shutdown_requested = False

        while True:
            # Check for control commands first
            try:
                cmd, data = control_queue.get_nowait()

                if cmd == ControlCommand.LOAD_MODEL:
                    # Generate request ID for this model load operation
                    model_load_request_id = generate_request_id()

                    log(
                        "Loading model started",
                        request_id=model_load_request_id,
                        data={
                            "model": model_size,
                            "device": device,
                            "compute_type": compute_type,
                        },
                    )
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

                        log(
                            "Initializing WhisperModel",
                            request_id=model_load_request_id,
                            data={"model_path": models_dir, "base_path": base_path},
                        )

                        # Custom Model Logic
                        if model_size == "Custom Model...":
                            custom_path = config.get("custom_model_path", "")
                            if not custom_path or not os.path.exists(custom_path):
                                raise ValueError("Custom model path is invalid or empty.")
                            model_path = custom_path
                            log(f"Using Custom Model from: {model_path}")
                        else:
                            # Standard Download Logic
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
                                    "Download retry with fallback",
                                    request_id=model_load_request_id,
                                    level="WARNING",
                                    data={"download_error": str(dl_error)},
                                )
                                model_path = model_size  # Fallback

                        model = WhisperModel(
                            model_path, device=device, compute_type=compute_type
                        )

                        if device == "cuda" and HAS_JSON_LOGGER:
                            try:
                                # Use ctranslate2 to check GPU count, but memory info is not directly available
                                # without nvml or torch. We will skip memory logging to avoid heavy dependencies.
                                # If needed, we could use pynvml, but keeping it simple is better.
                                log(
                                    "Model loaded successfully",
                                    request_id=model_load_request_id,
                                    data={
                                        "compute": compute_type,
                                        "gpu_available": True,
                                        "model_path": str(model_path),
                                    },
                                )
                            except Exception:
                                log(
                                    "Model loaded successfully",
                                    request_id=model_load_request_id,
                                    data={"compute": compute_type},
                                )
                        else:
                            log(
                                "Model loaded successfully",
                                request_id=model_load_request_id,
                                data={"compute": compute_type},
                            )

                        result_queue.put(("MODEL_READY", None, model_load_request_id))

                    except Exception as e:
                        log(
                            "Failed to load model",
                            request_id=model_load_request_id,
                            level="ERROR",
                            data={
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "model": model_size,
                                "device": device,
                            },
                        )
                        result_queue.put(("MODEL_ERROR", str(e), model_load_request_id))

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
                    if isinstance(data, dict):
                        if "model" in data:
                            model_size = data["model"]
                        if "device" in data:
                            device = data["device"]
                        if "language" in data:
                            language = data["language"]
                            log(f"Language updated to: {language}")
                        if "compute_type" in data:
                            compute_type = data["compute_type"]
                        if "custom_model_path" in data:
                            # Update the config dict directly as LOAD_MODEL uses it
                            config["custom_model_path"] = data["custom_model_path"]
                        
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

                        # Memory Cleanup before large task
                        gc.collect()
                        # Removed torch.cuda.empty_cache() to avoid torch dependency

                        cancel_file = False
                        active_file = file_path

                        # File transcription
                        # Use params from extra_params (default True)
                        vad_filter_val = True
                        word_timestamps_val = True

                        if isinstance(extra_params, dict):
                            if "vad_filter" in extra_params:
                                vad_filter_val = extra_params["vad_filter"]
                            if "word_timestamps" in extra_params:
                                word_timestamps_val = extra_params["word_timestamps"]

                        transcribe_kwargs = merged_transcribe_kwargs(
                            language=language if language != "auto" else None,
                            word_timestamps=word_timestamps_val,
                            vad_filter=vad_filter_val,
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
                            # Log RAW segment from Whisper (User Request for Debugging)
                            log(
                                f"[RAW] Seg {segment.start:.2f}-{segment.end:.2f}: {segment.text}",
                                level="DEBUG",
                                data={
                                    "start": segment.start,
                                    "end": segment.end,
                                    "text": segment.text,
                                    "words": [
                                        {
                                            "start": w.start,
                                            "end": w.end,
                                            "text": w.word,
                                            "prob": w.probability,
                                        }
                                        for w in (segment.words or [])
                                    ],
                                },
                            )

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

                elif cmd == ControlCommand.TRANSCRIBE_FILE_WITH_SEGMENTS:
                    # 세그먼트 기반 파일 전사
                    file_path = data.get("file_path")
                    segmentation_config = data.get("segmentation_config", {})

                    log(f"Transcribing file with segments: {file_path}")
                    log(f"Segmentation config: {segmentation_config}")

                    if model is None:
                        log("ERROR: Model not loaded. Cannot transcribe file.")
                        result_queue.put(("TRANSCRIPTION_ERROR", "Model not loaded"))
                        continue

                    try:
                        import gc

                        # Memory Cleanup before large task
                        gc.collect()
                        # Removed torch.cuda.empty_cache() to avoid torch dependency

                        cancel_file = False
                        active_file = file_path

                        # FFmpeg 기반 세그먼트 분리
                        try:
                            from src.engine.audio_segmenter import AudioSegmenter

                            log(f"AudioSegmenter 초기화 중...")
                            segmenter = AudioSegmenter()
                            log(f"AudioSegmenter 초기화 완료. FFmpeg 경로: {segmenter.ffmpeg_path}")

                            # 무음 구간 감지
                            segments = segmenter.detect_silence_segments(
                                audio_file_path=file_path,
                                noise_threshold=segmentation_config.get("noise_threshold", -30.0),
                                min_silence_duration=segmentation_config.get("min_silence_duration", 0.5),
                                padding_ms=segmentation_config.get("padding_ms", 100)
                            )

                            if not segments:
                                log("No segments detected, falling back to regular file transcription")
                                # 세그먼트가 없으면 기존 방식으로 전사
                                self.control_queue.put((ControlCommand.TRANSCRIBE_FILE, file_path))
                                continue

                            log(f"Created {len(segments)} audio segments")

                            # 세그먼트별 WAV 파일 생성
                            segmented_files = segmenter.create_audio_segments(
                                audio_file_path=file_path,
                                segments=segments,
                                sample_rate=16000,
                                channels=1
                            )

                            if not segmented_files:
                                log("ERROR: Failed to create any audio segments")
                                result_queue.put(("TRANSCRIPTION_ERROR", "Failed to create audio segments"))
                                segmenter.cleanup_temp_files()
                                continue

                            log(f"Created {len(segmented_files)} segment files")

                            # 각 세그먼트 전사
                            all_segments = []
                            last_logged_percent = -1

                            # 세그먼트별 전사 (병렬 처리 가능)
                            for idx, seg_file in enumerate(segmented_files):
                                # 취소 확인
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
                                                    and isinstance(cdata["faster_whisper_params"], dict)
                                                ):
                                                    extra_params = cdata["faster_whisper_params"]
                                        else:
                                            pass
                                except Exception:
                                    pass

                                if cancel_file:
                                    log(f"File transcription cancelled: {file_path}")
                                    result_queue.put(("FILE_CANCELLED", file_path))
                                    break

                                # 세그먼트 전사
                                log(f"Transcribing segment {idx+1}/{len(segmented_files)}: {seg_file.file_path}")

                                # VAD 필터 비활성화 (이미 세그먼트화했으므로)
                                vad_filter_val = False
                                word_timestamps_val = True

                                if isinstance(extra_params, dict):
                                    if "vad_filter" in extra_params:
                                        vad_filter_val = extra_params["vad_filter"]
                                    if "word_timestamps" in extra_params:
                                        word_timestamps_val = extra_params["word_timestamps"]

                                transcribe_kwargs = merged_transcribe_kwargs(
                                    language=language if language != "auto" else None,
                                    word_timestamps=word_timestamps_val,
                                    vad_filter=vad_filter_val,
                                )

                                segments_gen, info = model.transcribe(
                                    seg_file.file_path,
                                    **transcribe_kwargs,
                                )

                                # 세그먼트 결과 처리
                                for segment in segments_gen:
                                    # 절대 시간으로 오프셋 보정
                                    absolute_start = seg_file.start + segment.start
                                    absolute_end = seg_file.start + segment.end

                                    # 결과 수집
                                    words_data = []
                                    if segment.words:
                                        for word in segment.words:
                                            words_data.append((
                                                seg_file.start + word.start,
                                                seg_file.start + word.end,
                                                word.word,
                                                word.probability,
                                            ))

                                    res = TranscribeResult(
                                        segment_id="",
                                        text=segment.text.strip(),
                                        start=absolute_start,
                                        end=absolute_end,
                                        words=words_data,
                                        is_final=True,
                                        source="file_with_segments",
                                    )
                                    all_segments.append(res)

                                # 진행률 로그
                                if len(segmented_files) > 1:
                                    percent = int(((idx + 1) / len(segmented_files)) * 100)
                                    percent = min(100, max(0, percent))
                                    if percent > last_logged_percent:
                                        log(f"[진행률] {percent}% ({idx+1}/{len(segmented_files)} segments)")
                                        last_logged_percent = percent

                            if cancel_file:
                                active_file = None
                                if shutdown_requested:
                                    break
                                continue

                            # 결과 정렬 (시간 순)
                            all_segments.sort(key=lambda x: x.start)

                            log(f"Segment-based transcription completed: {file_path}")
                            result_queue.put(("FILE_ALL_SEGMENTS", all_segments))
                            result_queue.put(("FILE_COMPLETED", file_path))
                            active_file = None

                            # 임시 파일 정리
                            segmenter.cleanup_temp_files()

                            if shutdown_requested:
                                break

                        except ImportError as e:
                            log(f"ERROR: Failed to import AudioSegmenter: {e}")
                            result_queue.put(("TRANSCRIPTION_ERROR", f"AudioSegmenter import failed: {e}"))
                        except Exception as e:
                            log(f"Segment-based transcription failed: {e}")
                            result_queue.put(("TRANSCRIPTION_ERROR", str(e)))
                            # 오류 시 임시 파일 정리
                            try:
                                segmenter.cleanup_temp_files()
                            except:
                                pass

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

    def transcribe_file_with_segments(self, file_path: str, segmentation_config: Optional[Dict[str, Any]] = None):
        """Queue a file for segment-based transcription."""
        data = {
            "file_path": file_path,
            "segmentation_config": segmentation_config or {}
        }
        self.control_queue.put((ControlCommand.TRANSCRIBE_FILE_WITH_SEGMENTS, data))

    def cancel_file(self):
        """Request cancellation of in-progress file transcription."""
        self.control_queue.put((ControlCommand.CANCEL_FILE, None))

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()
