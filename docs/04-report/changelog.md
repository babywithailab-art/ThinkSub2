# Changelog

## [2026-02-02] - FFmpeg 기반 음성 구간 자동 분리 기능 구현

### Added
- **FFmpeg 기반 무음 구간 감지 기능**: FFmpeg의 silencedetect 필터를 활용해 무음 구간을 자동으로 검출하는 기능
- **세그먼트별 병렬 전사**: 감지된 음성 구간을 개별 세그먼트로 분리하여 faster-whisper로 병렬 처리하는 기능
- **자동 시간 오프셋 보정**: 세그먼트별 절대 시간으로 오프셋을 적용하여 정확한 전사 결과 제공
- **FFmpeg 설정 UI**: ThinkSub2 설정에서 FFmpeg 세그먼트 분리 사용 여부, 무음 감도, 최소 무음 시간, 패딩 시간 등을 조정할 수 있는 UI
- **임시 파일 자동 관리**: tempfile.gettempdir() 하위 ThinkSub2 폴더에 임시 파일을 생성하고 처리 완료 후 자동 삭제
- **대용량 파일 분할 처리**: 30분 단위 분할 옵션을 통한 안정적인 대용량 파일 처리 지원

### Technical Details
- **audio_segmenter.py** (375줄): FFmpeg 기반 무음 구간 감지 및 세그먼트 생성 모듈 신규 개발
- **transcriber.py** (+157줄): 세그먼트 처리 로직 확장 및 TRANSCRIBE_FILE_WITH_SEGMENTS 명령 추가
- **settings.py** (+80줄): FFmpeg 관련 설정 UI 요소 추가
- **main_window.py** (+40줄): 설정 기반 모드 분기 로직 통합

### Improved
- **정확도 향상**: 무음 기반 지능적 세그먼트화로 더 정확한 전사 결과 제공
- **성능 개선**: 세그먼트별 병렬 처리로 처리 속도 향상
- **사용자 제어**: VAD 파라미터 직접 조정 가능으로 사용자 맞춤 전사 환경 구축
- **워크플로우 통합**: 설정 한 번으로 모드 전환 가능한 직관적인 UX
- **메모리 효율**: 대용량 파일 분할 처리로 메모리 사용량 최적화

### Backwards Compatible
- 기존 STT 기능에 전혀 영향 없음
- FFmpeg 비활성화 시 기존 transcribe_file() 모드 자동 사용