# AI 아침 브리핑 비서

매일 아침 **날씨 · 오늘 일정 · 새 뉴스**를 Gemini로 요약해서 **카카오톡 '나와의 채팅'**으로 보냅니다.
GitHub Actions에서 무료로 돌아가고, 서버는 필요 없습니다.

```
📋 오늘의 브리핑
☀️ 오늘 알아야 할 것
• 오후 비 소식(강수확률 70%) — 우산 챙기세요
• 14:00 팀 회의 전에 자료 확인
─────────────
📅 일정
• 14:00–15:00 팀 회의
─────────────
📰 뉴스
• 한국은행 기준금리 동결 (연합뉴스)
...                                  [뉴스 더보기]
```

## 동작 방식

| 기능 | 방법 |
|---|---|
| 일정 | 구글 캘린더 비공개 iCal 주소 (반복 일정 포함) |
| 날씨 | Open-Meteo (API 키 불필요) |
| 뉴스 | 구글 뉴스 RSS (`config.yaml`에서 피드 추가/변경) |
| 요약 | Gemini API (`gemini-3.8-flash` 기본) |
| 알림 | 카카오톡 메시지 API — 나에게 보내기 |

> 카카오톡 텍스트 메시지 하나는 **최대 200자**라서, 브리핑을 섹션별(오늘 알아야 할 것 / 일정 / 뉴스)로
> 나눠 보통 3~5개 메시지로 보냅니다. 200자를 넘는 섹션은 줄 단위로 더 나눕니다.

### 요청 거절 대비 (재시도)
- **모든 외부 요청**(카카오 토큰·메시지, 뉴스, 날씨, 캘린더, Gemini)은 429/5xx/네트워크 오류가 나면
  2초 → 4초 → 8초 … 간격으로 최대 6번 다시 시도하고, **성공하는 순간 멈춥니다.**
  메시지는 하나씩 따로 재시도하므로, 이미 간 메시지가 다시 오지는 않습니다.
- 잘못된 키나 만료된 리프레시 토큰처럼 다시 해도 안 되는 오류는 바로 멈추고 로그에 해결 방법을 남깁니다.
- **GitHub 예약 실행 자체가 밀리거나 빠지는 경우**에 대비해 아침에 5번
  (07:03, 07:23, 07:43, 08:13, 08:47 KST) 실행됩니다. 그날 전송에 성공하면
  기록이 남아서 나머지 실행은 바로 종료되므로 브리핑은 하루 한 번만 옵니다.
- 기록 push와 시크릿 교체도 실패하면 최대 5번 다시 시도합니다.
- Gemini가 끝내 실패하면 AI 없이 뉴스 제목을 모아 보내서, 알림 자체는 빠지지 않습니다.

### 중복 소식 제외
- 보낸 뉴스는 `state/sent_news.json`에 기억하고, 다음 날에는 **새 소식만** 보냅니다.
- 같은 기사가 다른 주소나 다른 언론사 이름으로 와도 걸러냅니다
  (추적 파라미터를 뗀 주소, `[속보]`·`- 언론사` 꼬리표를 뗀 제목으로 비교).
- 알림이 **실제로 전송된 뒤에만** 기억합니다. 전송이 실패하면 그 소식은 다음 실행에서 다시 나갑니다.
- 기록은 14일 뒤 자동 정리되고, 공개 저장소여도 괜찮도록 제목 대신 해시만 저장합니다.

### 카카오 토큰 자동 관리
- 액세스 토큰(약 12시간)은 실행할 때마다 리프레시 토큰으로 새로 받습니다.
- 리프레시 토큰은 **약 60일** 유효하고, 남은 기간이 1개월 미만이 되면 카카오가 새 토큰을 줍니다.
  `GH_PAT` 시크릿이 있으면 워크플로가 그 새 토큰으로 `KAKAO_REFRESH_TOKEN`을 **자동 교체**하므로
  다시 로그인할 필요가 없습니다. `GH_PAT`이 없으면 Actions 로그에 경고가 뜨고, 60일 안에 직접 바꿔야 합니다.

## 설치

### 1. 카카오 개발자 앱 만들기
[카카오 개발자 콘솔](https://developers.kakao.com/console/app)에 로그인합니다.
1. **애플리케이션 추가하기**: 이름은 아무거나 (예: `내 AI 비서`)
2. **앱 → 플랫폼 키**: `REST API 키` 값 복사
3. 같은 화면의 **REST API 키 카드 ⋮ → 수정** (JavaScript 키 아님!)
   - **카카오 로그인 리다이렉트 URI**에 `https://localhost` 추가
   - **클라이언트 시크릿** 코드 복사
4. **카카오 로그인 → 일반**: 사용 설정 **ON**
5. **카카오 로그인 → 동의항목**: `카카오톡 메시지 전송(talk_message)`을 **선택 동의**로 설정
6. **앱 → 제품 링크 관리 → 웹 도메인**에 `https://news.google.com` 등록
   (메시지의 **뉴스 더보기** 버튼이 여는 주소, `config.yaml`의 `kakao.link_url`)

> 메뉴 이름은 콘솔 개편에 따라 조금 다를 수 있습니다.

### 2. 리프레시 토큰 받기 (한 번만)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # KAKAO_REST_API_KEY, KAKAO_CLIENT_SECRET 채우기
.venv/bin/python -m assistant.kakao_auth --test
```

1. 출력된 주소를 브라우저에서 열고 카카오 로그인 후 메시지 전송에 동의합니다.
2. `https://localhost/?code=...` 로 이동하면서 "사이트에 연결할 수 없음"이 떠도 정상입니다.
   **주소창의 주소 전체**를 복사해 터미널에 붙여 넣습니다.
3. 출력된 리프레시 토큰을 복사합니다. 카카오톡 '나와의 채팅'에 테스트 메시지가 오면 성공입니다.

### 3. Gemini API 키
[Google AI Studio](https://aistudio.google.com/apikey)에서 발급합니다.

### 4. 구글 캘린더 주소 (선택)
구글 캘린더 → 설정 → 일정을 가져올 캘린더 선택 → **캘린더 통합** →
**iCal 형식의 비공개 주소** 복사. 여러 캘린더는 쉼표로 이어 붙입니다.

### 5. GitHub에 올리고 시크릿 등록

```bash
git init && git add . && git commit -m "init" && git branch -M main
git remote add origin https://github.com/<아이디>/<저장소>.git && git push -u origin main
```

저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| 이름 | 값 | 필수 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini API 키 | ✅ |
| `KAKAO_REST_API_KEY` | 카카오 REST API 키 | ✅ |
| `KAKAO_CLIENT_SECRET` | 카카오 Client Secret | 사용 중이면 ✅ |
| `KAKAO_REFRESH_TOKEN` | 2단계에서 받은 리프레시 토큰 | ✅ |
| `GH_PAT` | 토큰 자동 교체용 GitHub 토큰 (아래 참고) | 추천 |
| `CALENDAR_ICS_URLS` | 캘린더 비공개 iCal 주소 | 선택 |

**`GH_PAT` 만들기**: GitHub → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** → 이 저장소만 선택 → Repository permissions의 **Secrets: Read and write**.

(선택) **Variables** 탭에 `GEMINI_MODEL`을 넣으면 모델을 바꿀 수 있습니다.

### 6. 테스트
**Actions → Daily Brief → Run workflow**
- `dry_run` 체크: 전송 없이, 카카오톡으로 나갈 메시지를 로그로 확인
- 아무것도 체크 안 함: 실제 전송 (오늘 이미 보냈다면 `force` 체크)

## 설정 바꾸기
- 알림 시간: `.github/workflows/daily-brief.yml`의 `cron` (UTC 기준, 한국시간 −9시간)
- 날씨 위치 · 뉴스 피드 · 뉴스 개수 · 버튼 주소 · 재시도 횟수: `config.yaml`
- 브리핑 형식/말투: `assistant/summarizer.py`의 프롬프트

## 로컬에서 실행

```bash
.venv/bin/python -m assistant.main --dry-run   # 전송 없이 카카오 메시지 미리보기
.venv/bin/python -m assistant.main --force     # 실제 전송
.venv/bin/pip install pytest && .venv/bin/python -m pytest
```

## 참고
- 저장소에 60일 동안 활동이 없으면 GitHub가 예약 워크플로를 자동으로 끌 수 있습니다.
  알림이 안 오면 Actions 탭에서 다시 켜 주세요.
- GitHub 예약 실행은 수 분~수십 분 늦게 시작될 수 있습니다.
- '나에게 보내기'는 로그인한 본인 카카오톡으로만 보낼 수 있습니다.
