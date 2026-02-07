"""
FFmpeg 기반 음성 구간 자동 분리 모듈
무음 구간을 기준으로 음성 세그먼트를 분리하고 WAV 파일로 저장
"""

import os
import re
import subprocess
import tempfile
import shutil
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
import logging

# JSON Logger 임포트
try:
    from src.utils.json_logger import get_logger
    HAS_JSON_LOGGER = True
except ImportError:
    import logging
    HAS_JSON_LOGGER = False

    def get_logger(name, log_level="INFO"):
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        return logger


@dataclass
class AudioSegment:
    """음성 세그먼트 정보"""
    segment_id: int
    start: float
    end: float
    file_path: str  # 세그먼트 WAV 파일 경로


class AudioSegmenter:
    """FFmpeg를 사용해 무음 구간 기반 음성 세그먼트 분리"""

    def __init__(self):
        self.logger = get_logger("audio_segmenter", log_level="DEBUG")
        self.temp_dir = None
        self.ffmpeg_path = None
        self._find_ffmpeg()

    def _find_ffmpeg(self):
        """FFmpeg 실행 파일 경로 찾기"""
        # src/bin 폴더에서 ffmpeg.exe 찾기
        current_dir = os.path.dirname(os.path.abspath(__file__))  # src/engine
        base_dir = os.path.dirname(current_dir)  # src
        bin_dir = os.path.join(base_dir, "bin")  # src/bin

        self.logger.debug(f"Base directory: {base_dir}")
        self.logger.debug(f"Bin directory: {bin_dir}")

        # Windows와 Linux/Mac 경로 모두 지원
        ffmpeg_candidates = [
            os.path.join(bin_dir, "ffmpeg.exe"),  # Windows
            os.path.join(bin_dir, "ffmpeg"),      # Linux/Mac
            "ffmpeg",  # PATH에서 찾기
        ]

        for candidate in ffmpeg_candidates:
            self.logger.debug(f"Checking FFmpeg: {candidate}")
            if self._check_ffmpeg(candidate):
                self.ffmpeg_path = candidate
                self.logger.info(f"FFmpeg 찾음: {self.ffmpeg_path}")
                return

        # ffmpeg를 찾지 못한 경우
        self.ffmpeg_path = None
        self.logger.error("FFmpeg 실행 파일을 찾을 수 없습니다. src/bin/ffmpeg.exe가 있는지 확인하세요.")

    def _check_ffmpeg(self, path: str) -> bool:
        """FFmpeg 실행 파일이 유효한지 확인"""
        try:
            cmd = [path, "-version"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0 and "ffmpeg" in result.stdout.lower()
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    def detect_silence_segments(
        self,
        audio_file_path: str,
        noise_threshold: float = -30.0,
        min_silence_duration: float = 0.5,
        padding_ms: int = 100
    ) -> List[AudioSegment]:
        """
        무음 구간을 감지하고 음성 세그먼트 목록 생성

        Args:
            audio_file_path: 입력 오디오 파일 경로
            noise_threshold: 무음 감도 (dB, -60 ~ -20)
            min_silence_duration: 최소 무음 지속 시간 (초)
            padding_ms: 세그먼트 전후 패딩 (ms)

        Returns:
            AudioSegment 리스트
        """
        if not self.ffmpeg_path:
            raise RuntimeError("FFmpeg가 설치되지 않았습니다.")

        self.logger.info(f"무음 구간 감지 시작: {audio_file_path}")
        self.logger.info(f"파라미터: noise_threshold={noise_threshold}dB, min_silence={min_silence_duration}s, padding={padding_ms}ms")

        # FFmpeg silencedetect 명령 실행
        cmd = [
            self.ffmpeg_path,
            "-i", audio_file_path,
            "-af", f"silencedetect=noise={noise_threshold}dB:duration={min_silence_duration}",
            "-f", "null",
            "-"
        ]

        self.logger.debug(f"FFmpeg 명령: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',  # UTF-8 인코딩 지정
                errors='ignore',   # 디코딩 오류 무시
                timeout=300  # 5분 타임아웃
            )

            stderr = result.stderr or ""
            self.logger.debug(f"FFmpeg 출력:\n{stderr}")

            # 무음 구간 파싱
            silence_intervals = self._parse_silence_output(stderr)

            if not silence_intervals:
                self.logger.info("무음 구간이 감지되지 않음. 전체 파일을 하나의 세그먼트로 처리")
                # 무음 구간이 없으면 전체 파일을 하나의 세그먼트로
                segments = [AudioSegment(
                    segment_id=0,
                    start=0.0,
                    end=float('inf'),  # 실제 길이는 later에서 설정
                    file_path=""
                )]
            else:
                # 세그먼트 생성 (무음 구간 사이의 구간들)
                segments = self._create_segments_from_silence(
                    audio_file_path,
                    silence_intervals,
                    padding_ms
                )

            self.logger.info(f"총 {len(segments)}개 세그먼트 감지됨")
            return segments

        except subprocess.TimeoutExpired:
            self.logger.error("FFmpeg 실행 타임아웃")
            raise RuntimeError("FFmpeg 실행 중 타임아웃이 발생했습니다.")
        except subprocess.SubprocessError as e:
            self.logger.error(f"FFmpeg 실행 오류: {e}")
            raise RuntimeError(f"FFmpeg 실행 오류: {e}")

    def _parse_silence_output(self, stderr: str) -> List[Tuple[float, float]]:
        """
        FFmpeg silencedetect 출력에서 무음 구간 파싱

        Returns:
            [(시작시간, 종료시간), ...]
        """
        if not stderr:
            self.logger.warning("FFmpeg 출력 비어있음")
            return []

        silence_pattern = re.compile(
            r"silence_(?:start|end): ([\d.]+)(?:\s+silence_duration: ([\d.]+))?",
            re.IGNORECASE
        )

        silence_starts = []
        silence_ends = []

        for match in silence_pattern.finditer(stderr):
            if "start" in match.group(0).lower():
                time = float(match.group(1))
                silence_starts.append(time)
            elif "end" in match.group(0).lower():
                time = float(match.group(1))
                silence_ends.append(time)

        # 시작과 끝을 쌍으로 만들기
        silence_intervals = []
        for start in silence_starts:
            # 가장 가까운 끝 시간 찾기
            ends_after = [t for t in silence_ends if t > start]
            if ends_after:
                end = min(ends_after)
                silence_intervals.append((start, end))

        self.logger.debug(f"파싱된 무음 구간: {silence_intervals}")
        return silence_intervals

    def _create_segments_from_silence(
        self,
        audio_file_path: str,
        silence_intervals: List[Tuple[float, float]],
        padding_ms: int
    ) -> List[AudioSegment]:
        """무음 구간을 기준으로 세그먼트 생성"""
        segments = []
        segment_id = 0
        current_start = 0.0

        # 세그먼트 생성
        for silence_start, silence_end in silence_intervals:
            # 패딩 적용
            actual_start = max(0.0, current_start - (padding_ms / 1000.0))
            actual_end = silence_start + (padding_ms / 1000.0)

            if actual_end - actual_start >= 0.5:  # 최소 0.5초 이상만
                segments.append(AudioSegment(
                    segment_id=segment_id,
                    start=actual_start,
                    end=actual_end,
                    file_path=""
                ))
                segment_id += 1

            current_start = silence_end

        # 마지막 세그먼트
        if current_start > 0:
            actual_start = max(0.0, current_start - (padding_ms / 1000.0))
            # 실제 오디오 길이는 later에서 설정
            segments.append(AudioSegment(
                segment_id=segment_id,
                start=actual_start,
                end=float('inf'),
                file_path=""
            ))

        return segments

    def create_audio_segments(
        self,
        audio_file_path: str,
        segments: List[AudioSegment],
        sample_rate: int = 16000,
        channels: int = 1
    ) -> List[AudioSegment]:
        """
        세그먼트별 WAV 파일 생성

        Args:
            audio_file_path: 원본 오디오 파일 경로
            segments: AudioSegment 리스트
            sample_rate: 샘플레이트 (Hz)
            channels: 채널 수 (1=mono, 2=stereo)

        Returns:
            업데이트된 AudioSegment 리스트 (file_path 포함)
        """
        if not self.temp_dir:
            self.temp_dir = tempfile.mkdtemp(prefix="thinksub2_")
            self.logger.info(f"임시 디렉토리 생성: {self.temp_dir}")

        # 오디오 길이 확인
        duration = self._get_audio_duration(audio_file_path)
        self.logger.info(f"오디오 길이: {duration:.2f}초")

        created_segments = []

        for segment in segments:
            # 무한 끝 시간을 실제 길이로 교체
            actual_end = min(segment.end, duration) if segment.end == float('inf') else segment.end

            # 너무 짧은 세그먼트 제외
            if actual_end - segment.start < 0.5:
                self.logger.debug(f"세그먼트 {segment.segment_id} 너무 짧음 ({actual_end - segment.start:.2f}s), 건너뜀")
                continue

            # 출력 파일 경로
            output_path = os.path.join(
                self.temp_dir,
                f"thinksub2_seg_{segment.segment_id:04d}_{segment.start:.2f}_{actual_end:.2f}.wav"
            )

            self.logger.debug(f"세그먼트 {segment.segment_id} 생성 중: {segment.start:.2f}s ~ {actual_end:.2f}s")

            # FFmpeg로 세그먼트 추출
            cmd = [
                self.ffmpeg_path,
                "-i", audio_file_path,
                "-ss", str(segment.start),
                "-t", str(actual_end - segment.start),
                "-ar", str(sample_rate),
                "-ac", str(channels),
                "-acodec", "pcm_s16le",
                "-y",  # 기존 파일 덮어쓰기
                output_path
            ]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=60  # 각 세그먼트당 1분
                )

                if result.returncode == 0:
                    self.logger.debug(f"세그먼트 생성 완료: {output_path}")
                    created_segments.append(AudioSegment(
                        segment_id=segment.segment_id,
                        start=segment.start,
                        end=actual_end,
                        file_path=output_path
                    ))
                else:
                    self.logger.error(f"세그먼트 생성 실패: {result.stderr}")

            except subprocess.TimeoutExpired:
                self.logger.error(f"세그먼트 {segment.segment_id} 생성 타임아웃")
            except subprocess.SubprocessError as e:
                self.logger.error(f"세그먼트 {segment.segment_id} 생성 오류: {e}")

        self.logger.info(f"총 {len(created_segments)}개 세그먼트 파일 생성됨")
        return created_segments

    def _get_audio_duration(self, audio_file_path: str) -> float:
        """FFprobe를 사용해 오디오 길이 확인"""
        if not self.ffmpeg_path:
            return 0.0

        cmd = [
            self.ffmpeg_path,
            "-i", audio_file_path,
            "-f", "null",
            "-"
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=30
            )

            # 시간 파싱
            duration_pattern = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)", re.IGNORECASE)
            match = duration_pattern.search(result.stderr)

            if match:
                hours = int(match.group(1))
                minutes = int(match.group(2))
                seconds = float(match.group(3))
                return hours * 3600 + minutes * 60 + seconds

        except Exception as e:
            self.logger.error(f"오디오 길이 확인 실패: {e}")

        return 0.0

    def cleanup_temp_files(self):
        """임시 파일 및 디렉토리 정리"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                self.logger.info(f"임시 디렉토리 삭제됨: {self.temp_dir}")
                self.temp_dir = None
            except Exception as e:
                self.logger.error(f"임시 디렉토리 삭제 실패: {e}")

    def get_temp_dir(self) -> Optional[str]:
        """임시 디렉토리 경로 반환"""
        return self.temp_dir

    def __del__(self):
        """소멸자에서 정리"""
        self.cleanup_temp_files()
