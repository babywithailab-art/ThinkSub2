# ThinkSub2 QA Workflow Guide

## Overview

Zero Script QA 기반 ThinkSub2 테스트 워크플로우입니다. Docker 환경에서 실시간 로그 모니터링으로 버그를 감지합니다.

---

## Prerequisites

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|-----------|-------------|
| OS | Linux / WSL2 | Ubuntu 22.04+ |
| Docker | 20.10+ | 24.0+ |
| RAM | 4GB | 8GB+ |
| GPU | - | NVIDIA (CUDA 12+) |
| Storage | 10GB | 20GB+ |

### Host Setup (Linux/WSL2)

#### 1. X11 Forwarding (GUI 지원)

```bash
# X11 액세스 허용
xhost +local:docker

# 확인
echo $DISPLAY  # :0
```

#### 2. PulseAudio (오디오 공유)

```bash
# PulseAudio 소켓 공유
pulseaudio --load=module-native-protocol-unix \
    socket=/run/user/1000/pulse/native \
    auth-anonymous=1
```

#### 3. Docker 설치 확인

```bash
docker --version
docker compose version
```

---

## Quick Start

### 1. 빌드 및 시작

```bash
# Docker 이미지 빌드
docker compose build

# 서비스 시작 (GUI 모드)
docker compose up -d thinksub2

# 로그 스트리밍 시작 (새 터미널)
./scripts/qa-logs.sh
```

### 2. GUI 접근

Docker 컨테이너 내부에서 실행되므로, 호스트의 X11 디스플레이를 통해 GUI가 표시됩니다.

```bash
# 컨테이너 상태 확인
docker ps | grep thinksub2

# GUI 창이 자동으로 뜹니다
```

### 3. 로그 모니터링

```bash
# 전체 로그 스트리밍 (컬러 하이라이팅)
./scripts/qa-logs.sh

# 에러만 필터링
./scripts/qa-logs.sh ERROR

# 특정 요청 ID 추적 (JSON 로거 적용 시)
./scripts/qa-logs.sh req_abc123

# 모델 이벤트만 보기
./scripts/qa-logs.sh "MODEL_READY|MODEL_ERROR"
```

---

## QA Test Cycle

### Cycle Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                   QA Test Cycle                        │
├─────────────────────────────────────────────────────────────┤
│                                                        │
│  Cycle N:                                               │
│  1. docker compose up -d (서비스 시작)            │
│  2. ./scripts/qa-logs.sh (로그 스트리밍 시작) │
│  3. GUI에서 기능 테스트 (Live, STT, Editor 등)    │
│  4. 로그에서 오류 패턴 감지                        │
│  5. 버그 문서화                                     │
│  6. 코드 수정                                            │
│  7. docker compose up -d --build (리빌드 & 재시작) │
│                                                        │
│  반복하여 합격률 >85% 도달                      │
│                                                        │
└─────────────────────────────────────────────────────────────┘
```

### Test Categories

#### 1. Smoke Tests (초기 부팅)

| Test | Command | Expected Result |
|-------|----------|----------------|
| 컨테이너 시작 | `docker compose up -d` | Container running, GUI appears |
| 로그 스트리밍 | `./scripts/qa-logs.sh` | Live logs visible, no errors |
| 모델 로드 | GUI: Live 버튼 클릭 | `MODEL_READY` log appears |
| 오디오 장치 | GUI: Live 버튼 클릭 | No audio errors |

#### 2. Functional Tests (주요 기능)

| Feature | Test Steps | Expected Logs |
|---------|-------------|----------------|
| Live 녹음 시작 | 1. Live 버튼 클릭<br>2. 말하기<br>3. 녹음 중지 | `[Audio] Recording started`<br>`[Transcriber] Processing`<br>`[Audio] Recording stopped` |
| File STT | 1. 파일 선택<br>2. STT 실행 버튼 클릭 | `Transcribe started`<br>`Transcribe completed` |
| 자막 편집 | 1. 자막 클릭<br>2. 텍스트 수정<br>3. 저장 | `[EDITOR] ...` |
| SRT 내보내기 | 1. Export → SRT 선택 | `[DEBUG_SAVE] Writing to: ...` |

#### 3. Integration Tests (종합 흐름)

| Test Scenario | Steps | Success Criteria |
|--------------|-------|----------------|
| 녹음 → STT → 편집 → 내보내기 | 전체 워크플로우 실행 | SRT 파일 생성됨 |
| Live STT → 실시간 자막 표시 | Live 모드 시작 | Overlay 자막 표시됨 |
| 배치 STT | 다수 파일 선택 | 모든 파일 처리됨 |

---

## Log Pattern Detection

### Critical Issues (즉시 보고)

| Pattern | Color | Action |
|---------|--------|--------|
| `ERROR` | 🔴 RED | Immediate investigation |
| `MODEL_ERROR` | 🔴 RED | Model load failure |
| `CUDA out of memory` | 🔴 RED | GPU memory issue |
| `Failed to start stream` | 🔴 RED | Audio device issue |

### Warnings (주시 필요)

| Pattern | Color | Action |
|---------|--------|--------|
| `WARNING` | 🟡 YELLOW | Monitor closely |
| `Cannot connect to PulseAudio` | 🟡 YELLOW | Check audio setup |
| `Failed to restore view state` | 🟡 YELLOW | Non-critical |

### Info Events (정상 동작)

| Pattern | Color | Meaning |
|---------|--------|---------|
| `MODEL_READY` | 🟢 GREEN | Model loaded successfully |
| `[Audio] Status:` | 🔵 BLUE | Audio state changed |
| `[Transcriber]` | 🩵 CYAN | STT processing |

---

## Issue Documentation Template

### Issue Report Format

```markdown
# Issue Report: ISSUE-XXX

## Summary
- **Date**: YYYY-MM-DD HH:MM
- **Severity**: Critical / High / Medium / Low
- **Component**: Audio / Transcriber / GUI / Other
- **Log Snippet**:
  ```
  [Transcriber] ERROR: Failed to load model: CUDA out of memory
  ```

## Reproduction Path
1. Open ThinkSub2 GUI
2. Click Live button
3. Select model: `large-v3-turbo`
4. System: GPU with 8GB VRAM
5. Error occurs during model load

## Root Cause
- **Analysis**: GPU memory insufficient for large model
- **Evidence**: CUDA out of memory error

## Fix Applied
- **File**: `src/engine/transcriber.py:184`
- **Change**: Added memory check before model load
- **Code**:
  ```python
  if torch.cuda.memory_allocated() > MODEL_MEMORY_THRESHOLD:
      log("ERROR: Insufficient GPU memory", extra={'data': {'available_mb': ...}})
      return False
  ```

## Verification
- **Test**: Re-run Live STT with same model
- **Result**: ✅ Pass / ❌ Fail
- **Notes**: Model loads successfully now
```

---

## Docker Commands Reference

### Container Management

```bash
# 빌드
docker compose build

# 시작 (백그라운드)
docker compose up -d

# 정지
docker compose stop thinksub2

# 재시작
docker compose restart thinksub2

# 완전 제거
docker compose down -v
```

### Log Access

```bash
# 실시간 스트리밍 (컬러)
./scripts/qa-logs.sh

# 최근 100줄
docker compose logs --tail=100 thinksub2

# JSON 파싱 (jq 필요)
docker compose logs thinksub2 | jq '. | select(.level=="ERROR")'

# 파일로 저장
docker compose logs thinksub2 > logs/latest.log
```

### Debug Mode

```bash
# 쉘 접속 (디버깅)
docker compose exec thinksub2 bash

# Python REPL 접속
docker compose exec thinksub2 python

# 환경 변수 확인
docker compose exec thinksub2 env | sort

# 디스크 사용량 확인
docker compose exec thinksub2 du -sh /app/logs
```

---

## Troubleshooting

### GUI Not Showing

**Symptom**: 컨테이너 실행 중이나 GUI 창이 보이지 않음

**Solution**:
```bash
# X11 액세스 확인
xhost

# 재설정
xhost +local:docker

# DISPLAY 변수 확인
echo $DISPLAY

# 컨테이너 재시작
docker compose restart thinksub2
```

### Audio Not Working

**Symptom**: Live 버튼 클릭 시 오디오 에러

**Solution**:
```bash
# PulseAudio 소켓 확인
ls -la /run/user/1000/pulse/

# PulseAudio 재시작
pulseaudio --kill
pulseaudio --start

# 권한 확인
chmod 666 /run/user/1000/pulse/native

# 컨테이너 재시작
docker compose restart thinksub2
```

### Model Load Failure

**Symptom**: `MODEL_ERROR` 로그 발생

**Solution**:
```bash
# 로그 확인
./scripts/qa-logs.sh MODEL_ERROR

# GPU 메모리 확인
docker compose exec thinksub2 nvidia-smi

# 모델 파일 확인
ls -lh /app/models/

# 작은 모델로 테스트
```

---

## Advanced Monitoring

### Log Aggregation (Dozzle)

```bash
# Log viewer 서비스 시작
docker compose --profile monitoring up -d

# 웹 브라우저로 접속
open http://localhost:8080
```

### Metrics Collection

```bash
# CPU 사용량
docker stats thinksub2

# GPU 사용량 (NVIDIA)
watch -n 1 nvidia-smi

# 메모리 사용량
docker stats thinksub2 --no-stream
```

---

## Continuous Integration

### CI/CD Pipeline

```yaml
# .github/workflows/qa.yml (예시)
name: QA Tests
on: [push, pull_request]

jobs:
  qa:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Build Docker
        run: docker compose build

      - name: Start Services
        run: docker compose up -d

      - name: Run Smoke Tests
        run: ./tests/smoke-test.sh

      - name: Collect Logs
        run: docker compose logs thinksub2 > qa-output.log

      - name: Analyze Logs
        run: python tests/analyze-logs.py qa-output.log
```

---

## Checklist

### Pre-QA
- [ ] Docker 최신 버전 설치
- [ ] X11 forwarding 설정 (`xhost +local:docker`)
- [ ] PulseAudio 설정
- [ ] GPU 드라이버 설치 (NVIDIA 사용 시)
- [ ] 네트워크 연결 확인

### QA Session
- [ ] 컨테이너 성공적 시작
- [ ] GUI 정상 표시
- [ ] 오디오 장치 인식
- [ ] 모델 로드 성공
- [ ] 로그 스트리밍 작동
- [ ] Smoke tests 통과
- [ ] Functional tests 통과

### Post-QA
- [ ] 이슈 보고서 작성
- [ ] 버그 수정 완료
- [ ] 리그레션 테스트 통과
- [ ] 문서 업데이트
- [ ] 커밋 및 PR 생성

---

## Best Practices

1. **작은 주기로 테스트**: 1개 기능 → 테스트 → 수정
2. **로그 중심 디버깅**: `print()` 대신 로그 확인
3. **이슈 즉시 문서화**: 발견 즉시 보고서 작성
4. **재현 단계 상세**: 버그 재현 스텝 최대 구체화
5. **수정 후 검증**: 수정 직후 재테스트 수행

---

## Additional Resources

- [Docker Compose Reference](https://docs.docker.com/compose/)
- [X11 Forwarding Guide](https://www.x.org/archive/X11R7.6/doc/xsec/X SECURITY/security3.html)
- [PulseAudio Documentation](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation)
- [Zero Script QA Skill](https://github.com/bkit-dev/zero-script-qa)
