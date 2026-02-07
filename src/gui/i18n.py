"""Lightweight QTranslator-backed i18n for ThinkSub2."""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QCoreApplication, QTranslator, QSettings
from PySide6.QtWidgets import QLabel, QPushButton, QCheckBox, QGroupBox


EN_MAP: Dict[str, str] = {
    "ThinkSub2 - 설정": "ThinkSub2 - Settings",
    "ThinkSub2 - 로그": "ThinkSub2 - Log",
    "Faster-Whisper": "Faster-Whisper",
    "Live 자막": "Live Subtitles",
    "STT 실행": "Run STT",
    "자막": "Subtitles",
    "단축키": "Shortcuts",
    "기본값 복원": "Restore Defaults",
    "로그창": "Log Window",
    "자동 스크롤": "Auto Scroll",
    "복사": "Copy",
    "지우기": "Clear",
    "취소": "Cancel",
    "확인": "OK",
    "인터페이스": "Interface",
    "UI 언어:": "UI Language:",
    "단축키 설정": "Shortcut Settings",
    "실행취소:": "Undo:",
    "다시실행:": "Redo:",
    "병합:": "Merge:",
    "분할:": "Split:",
    "삭제:": "Delete:",
    "(단축키 변경 기능은 추후 업데이트 예정)": "(Shortcut editing will be available later)",
    "마이크 설정": "Microphone",
    "모델 설정": "Model",
    "VAD 설정": "VAD",
    "🧾 STT일괄": "🧾 STT Batch",
    "STT 시작": "Start STT",
    "STT 중단": "Stop STT",
    "파일 추가": "Add Files",
    "파일명": "Filename",
    "진행률": "Progress",
    "작업중": "Running",
    "대기": "Queued",
    "완료": "Done",
    "중단": "Stopped",
    "오류": "Error",
    "▶ Live 자막": "▶ Live",
    "📐 화면전환": "📐 Layout",
    "📐 Editor 왼쪽": "📐 Editor Left",
    "📐 Editor 오른쪽": "📐 Editor Right",
    "📐 Editor 분할": "📐 Editor Split",
    "📊 웨이브폼": "📊 Waveform",
    "↕ 웨이브폼 상단": "↕ Waveform Top",
    "↕ 웨이브폼 하단": "↕ Waveform Bottom",
    "↕ 웨이브폼 분할": "↕ Waveform Split",
    "🔗 스크롤": "🔗 Sync",
    "CC: 전체": "CC: Both",
    "CC: 상단": "CC: Top",
    "CC: 하단": "CC: Bottom",
    "CC: 끔": "CC: Off",
    "💾 내보내기": "💾 Export",
    "🎙 STT실행": "🎙 Run STT",
    "🎙 STT중지": "🎙 Stop STT",
    "준비": "Ready",
    "모델 로딩 중...": "Loading model...",
    "녹음 중": "Recording",
    "Live 자막 진행 중...": "Live subtitles running...",
    "⏹ 취소": "⏹ Cancel",
    "⏹ 정지": "⏹ Stop",
    "미리보기 영상 준비 중...": "Preparing preview video...",
    "모델:": "Model:",
    "언어:": "Language:",
    "장치:": "Device:",
    "정밀도:": "Precision:",
    "마이크:": "Microphone:",
    "파일 변환 완료: ": "File completed: ",
    "파일 변환 오류: ": "File error: ",
    "파일 변환 취소됨: ": "File cancelled: ",
    "🎬 미디어뷰": "🎬 Media View",
    "📂 파일열기": "📂 Open File",
    "⚙ 설정": "⚙ Settings",
    "✂ 분할": "✂ Split",
    "🔗 병합": "🔗 Merge",
    "↩ 실행취소": "↩ Undo",
    "🗑 삭제": "🗑 Delete",
    "SRT 내보내기": "Export SRT",
    "좌측 SRT": "Left SRT",
    "우측 SRT": "Right SRT",
    "메타데이터 내보내기 (JSON)": "Export Metadata (JSON)",
    "좌측 메타데이터": "Left Metadata",
    "우측 메타데이터": "Right Metadata",
    "LoRA 데이터 내보내기": "Export LoRA Data",
    "좌측 LoRA": "Left LoRA",
    "우측 LoRA": "Right LoRA",
    "자막 파일 열기": "Open Subtitle File",
    "오디오 로딩": "Loading Audio",
    "파형 렌더링 중...": "Rendering waveform...",
    "시작": "Start",
    "종료": "End",
    "길이": "Length",
    "재생": "Play",
    "텍스트": "Text",
    "시간": "Time",
    "초": "s",
    "좌측 자막": "Left Subtitles",
    "우측 자막": "Right Subtitles",
    "덮어쓰기 경고": "Overwrite Warning",
    "우측 에디터의 기존 내용이 삭제됩니다.\n계속하시겠습니까?": "Existing content in the right editor will be deleted.\nDo you want to continue?",
    "오디오 내보내기 (WAV)": "Export Audio (WAV)",
    "STT 일괄 작업": "Batch STT",
    "STT": "STT",
    "Live 실행 중에는 STT를 실행할 수 없습니다.": "Cannot run STT while Live is running.",
    "자막 덮어쓰기": "Overwrite Subtitles",
    "현재 우측 자막을 지우고 STT 결과를 새로 생성합니다. 계속하시겠습니까?": "This will delete the current right subtitles and create new STT results. Continue?",
    "일괄 작업할 파일을 추가하세요.": "Add files to process in batch.",
    "UI": "UI",
    "한국어": "Korean",
    "English": "English",
    "전처리": "Pre-proc",
    "약어 화이트리스트 (Live)": "Abbreviation Whitelist (Live)",
    "약어 화이트리스트 (STT)": "Abbreviation Whitelist (STT)",
    "약어를 한 줄에 하나씩 입력하거나 쉼표(,)로 구분해 입력하세요.": "Enter abbreviations one per line or separated by commas (,).",
    "기본값": "Reset",
    "폰트 크기 (Default: 25):": "Font Size (Default: 25):",
    "최대 표시 글자수 (Default: 40):": "Max Characters (Default: 40):",
    "최대 줄 수 (Default: 2):": "Max Lines (Default: 2):",
    "불투명도 (%) (Default: 80):": "Opacity (%) (Default: 80):",
    "모델 설정": "Model",
    "추가 매개변수...": "Extra Params...",
    "추가 매개변수:": "Extra Params:",
    "VAD 설정": "VAD",
    "음성 감지 임계값 (VAD):": "VAD Threshold:",
    "무음 시간 (초):": "Silence Duration (s):",
    "Live 후처리": "Live Post-Processing",
    "후처리 필터 사용 (Enable Filters)": "Enable Post-Processing Filters",
    "최소 길이 제한:": "Min Text Length:",
    "최소 볼륨 (RMS Cutoff):": "Min Volume (RMS Cutoff):",
    "최소 음성 길이:": "Min Speech Length:",
    "최대 음성 길이:": "Max Speech Length:",
    "Wordtimestamp 보정:": "Word Timestamp Offset:",
    "-padding:": "-padding:",
    "+padding:": "+padding:",
    "약어 화이트리스트...": "Abbreviation Whitelist...",
    "약어 화이트리스트:": "Abbreviation Whitelist:",
    "Live 자막 매개변수": "Live Subtitle Parameters",
    "STT 후처리": "STT Post-Processing",
    "Seg.Endmin:": "Seg.Endmin:",
    "Extend on touch": "Extend on touch",
    "STT 실행 (파일) 매개변수": "STT (File) Parameters",
    "마이크 설정": "Microphone",
    "인터페이스": "Interface",
    "단축키 설정": "Shortcut Settings",
    "미디어 파일 열기": "Open Media File",
    "faster-whisper WhisperModel.transcribe()에 전달할 추가 매개변수를 JSON으로 설정합니다.": "Set extra parameters for faster-whisper WhisperModel.transcribe() in JSON.",
    "체크 해제 시 모든 필터를 무시하고 모든 자막을 표시합니다.": "If unchecked, all filters are ignored and all subtitles are shown.",
    "지정된 글자 수보다 짧은 자막은 무시합니다. (0 = 끄기)": "Ignore subtitles shorter than this length. (0 = off)",
    "이 값보다 평균 볼륨(RMS)이 낮은 구간은 무시합니다.": "Ignore segments with average RMS below this value.",
    "지정된 시간보다 짧은 음성 구간은 무시합니다. (0 = 끄기)": "Ignore speech segments shorter than this duration. (0 = off)",
    "지정된 시간 이상인 음성 구간은 무시합니다. (0 = 끄기)": "Ignore speech segments longer than this duration. (0 = off)",
    "Live 자막의 타임스탬프를 지정한 시간만큼 이동합니다.": "Shift live subtitle timestamps by this amount.",
    "Live 자막 시작 시간을 앞당겨 구간을 확장합니다.": "Extend live subtitle start time earlier.",
    "Live 자막 종료 시간을 늦춰 구간을 확장합니다.": "Extend live subtitle end time later.",
    "문장 단위로 끊어서 자막을 만들도록 시도합니다. (프로젝트에서 실제 적용 여부는 내보내기/후처리 구현에 따릅니다)": "Try to split subtitles by sentences. (Actual behavior depends on export/post-processing implementation.)",
    "자막 분할 시, 두 단어/세그먼트 사이의 최대 허용 간격(초)입니다.": "Maximum allowed gap between words/segments when splitting subtitles (seconds).",
    "자막 한 줄의 최대 문자 폭(대략적인 글자수)입니다. (SRT 줄바꿈에 사용)": "Max characters per line (approx.). Used for SRT line breaks.",
    "자막 한 항목에서 허용하는 최대 줄 수입니다.": "Maximum number of lines per subtitle entry.",
    "쉼표(,) 기준으로 분할할 때의 기준 퍼센트 값입니다.": "Percent threshold for splitting at commas.",
    "1이면 한 단어씩 자막으로 만들도록 강제합니다. 0이면 비활성화입니다.": "If 1, force one word per subtitle. 0 to disable.",
    "(파일 STT에서) VAD가 한 번에 잡을 수 있는 최대 발화 길이(초)입니다.": "(File STT) Max speech duration VAD can capture at once (seconds).",
    "디코딩에서 길이 패널티입니다. 값이 클수록 짧은 결과를 선호합니다.": "Length penalty during decoding; higher favors shorter outputs.",
    "Beam search 빔 크기입니다. 클수록 정확도가 올라갈 수 있지만 느려집니다.": "Beam search size; larger can improve accuracy but is slower.",
    "샘플링 시 후보 중 best_of 개 중 최적을 선택합니다. (temperature>0에서 의미가 큼)": "Select best among best_of candidates during sampling (meaningful when temperature>0).",
    "압축 비율이 이 값보다 크면 (반복/이상 출력으로 판단) 해당 결과를 거를 수 있습니다.": "If compression ratio exceeds this, the result may be filtered as repetitive/abnormal.",
    "평균 로그확률이 이 값보다 낮으면 결과를 거를 수 있습니다. (-1.0은 보통 관대한 값)": "Filter results with avg logprob below this value (-1.0 is lenient).",
    "세그먼트 최소 길이(초)입니다. 이 값보다 짧으면 끝 시간을 늘립니다.": "Minimum segment length (seconds). Shorter segments will be extended.",
    "자막 구간을 편집할 때 인접 구간과 맞닿도록 확장합니다.": "Extend to touch adjacent segments when editing.",
    "문장 단위로 끊어서 자막을 만들도록 시도합니다.": "Try to split subtitles by sentences.",
    " 자 (글자수)": " chars",
    " 초 (s)": " s",
    "Faster-Whisper 추가 매개변수": "Faster-Whisper Extra Params",
    'WhisperModel.transcribe()에 전달할 추가 매개변수를 JSON 오브젝트로 입력하세요.\n예: {"beam_size": 5, "temperature": 0.0}': 'Enter extra parameters for WhisperModel.transcribe() as JSON.\nExample: {"beam_size": 5, "temperature": 0.0}',
    "비우기": "Clear",
    "JSON 오류": "JSON Error",
    "JSON 파싱 실패:\n": "JSON parsing failed:\n",
    "형식 오류": "Format Error",
    "JSON은 오브젝트({}) 형태여야 합니다.": "JSON must be an object ({})",
    "전처리": "Pre-proc",
    "음성 감지 임계값 (VAD):": "VAD Threshold:",
    "무음 시간 (초):": "Silence Duration (s):",
    "추가 매개변수...": "Extra Params...",
    "추가 매개변수:": "Extra Params:",
    "Live 후처리": "Live Post-Processing",
    "후처리 필터 사용 (Enable Filters)": "Enable Post-Processing Filters",
    "최소 길이 제한:": "Min Text Length:",
    "최소 볼륨 (RMS Cutoff):": "Min Volume (RMS Cutoff):",
    "최소 음성 길이:": "Min Speech Length:",
    "최대 음성 길이:": "Max Speech Length:",
    "약어 화이트리스트...": "Abbreviation Whitelist...",
    "약어 화이트리스트:": "Abbreviation Whitelist:",
    "Live 자막 매개변수": "Live Subtitle Parameters",
    "STT 후처리": "STT Post-Processing",
    "Seg.Endmin:": "Seg.Endmin:",
    "Extend on touch": "Extend on touch",
    "STT 실행 (파일) 매개변수": "STT (File) Parameters",
    "주의: '추가 매개변수...' JSON에 같은 키가 있으면, 그 값이 우선 적용됩니다.": "Note: If the same key exists in 'Extra Params...', that value takes precedence.",
    "폰트 크기 (Default: 25):": "Font Size (Default: 25):",
    "최대 표시 글자수 (Default: 40):": "Max Characters (Default: 40):",
    "최대 줄 수 (Default: 2):": "Max Lines (Default: 2):",
    "불투명도 (%) (Default: 80):": "Opacity (%) (Default: 80):",
    "최소 음성 길이:": "Min Speech Length:",
    "최대 음성 길이:": "Max Speech Length:",
    "주의: '추가 매개변수...' JSON에 같은 키가 있으면, 그 값이 우선 적용됩니다.": "Note: If the same key exists in 'Extra Params...', that value takes precedence.",
    "VAD 설정": "Pre-proc",
    "마이크 설정": "Microphone",
    "모델 설정": "Model",
    "UI 테마:": "UI Theme:",
    "다크 모드": "Dark Mode",
    "라이트 모드": "Light Mode",
    "남색 모드": "Navy Mode",
    "💾 작업저장": "💾 Save Work",
    "📂 작업불러오기": "📂 Load Work",
    "작업 저장": "Save Work",
    "작업 불러오기": "Load Work",
    "JSON 파일 (*.json)": "JSON Files (*.json)",
    "저장할 파일을 선택하세요": "Select a file to save",
    "열 파일을 선택하세요": "Select a file to open",
    # Save Menu
    "동일 이름으로 저장": "Overwrite Save",
    "새로 저장...": "Save As...",
    "저장되지 않음": "Unsaved Changes",
    "작업이 저장되지 않았습니다. 저장하지 않고 종료하시겠습니까?": "You have unsaved changes. Do you want to exit without saving?",
    "작업 저장 중...": "Saving...",
    "저장 실패": "Save Failed",
    "자동 저장이 실패했습니다:\n": "Auto-save failed:\n",
    "\n\n그래도 종료하시겠습니까?": "\n\nExit anyway?",
    "짧은 구간 병합 (길이):": "Merge Short (Length):",
    "짧은 구간 병합 (간격):": "Merge Short (Gap):",
    "자석 모드 (Snapping)": "Magnet Mode (Snapping)",
    "자석 모드: 켜짐": "Magnet Mode: ON",
    "자석 모드: 꺼짐": "Magnet Mode: OFF",
}


class DictTranslator(QTranslator):
    def __init__(self, lang: str):
        super().__init__()
        self._lang = lang

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if self._lang == "en":
            return EN_MAP.get(sourceText, sourceText)
        return sourceText


_current_translator: DictTranslator | None = None


def install_translator(lang: str) -> None:
    global _current_translator
    app = QCoreApplication.instance()
    if not app:
        return
    if _current_translator:
        app.removeTranslator(_current_translator)
    _current_translator = DictTranslator(lang)
    app.installTranslator(_current_translator)


def get_lang() -> str:
    settings = QSettings("ThinkSub", "ThinkSub2")
    return str(settings.value("ui_language", "ko"))


def tr(text: str) -> str:
    return QCoreApplication.translate("ui", text)


def _translate_widget(widget) -> None:
    """Helper to translate a single widget."""
    try:
        source = widget.property("i18n_source")
        if not source:
            source = widget.text()
            widget.setProperty("i18n_source", source)
        widget.setText(tr(source))
    except Exception:
        pass


def apply_widget_translations(root) -> None:
    # PySide6: findChildren doesn't accept tuple, call separately for each type
    for widget in root.findChildren(QLabel):
        _translate_widget(widget)
    for widget in root.findChildren(QPushButton):
        _translate_widget(widget)
    for widget in root.findChildren(QCheckBox):
        _translate_widget(widget)
    for group in root.findChildren(QGroupBox):
        try:
            source = group.property("i18n_source")
            if not source:
                source = group.title()
                group.setProperty("i18n_source", source)
            group.setTitle(tr(source))
        except Exception:
            pass
