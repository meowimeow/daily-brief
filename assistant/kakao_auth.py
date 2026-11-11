"""카카오 로그인을 한 번 해서 리프레시 토큰을 받는 도우미.

    python -m assistant.kakao_auth          # 리프레시 토큰 발급
    python -m assistant.kakao_auth --test   # 발급 후 '나와의 채팅'으로 테스트 메시지 전송

.env에 KAKAO_REST_API_KEY(필수), KAKAO_CLIENT_SECRET, KAKAO_REDIRECT_URI가 있어야 한다.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

from .config import ROOT, load_config
from .kakao import AUTHORIZE_URL, KakaoNotifier, exchange_code

# 카카오 오류 코드 → 원인과 고칠 곳
HINTS = {
    "KOE004": "카카오 로그인이 꺼져 있습니다 → [카카오 로그인 > 일반] 사용 설정 ON",
    "KOE006": "Redirect URI가 등록되지 않았습니다 → [앱 > 플랫폼 키 > REST API 키 ⋮ > 수정]에 "
              "https://localhost 추가 (.env의 KAKAO_REDIRECT_URI와 똑같이, 끝에 / 없이)",
    "KOE101": "REST API 키가 틀렸습니다 → [앱 > 플랫폼 키]에서 REST API 키를 다시 복사 (JavaScript 키 아님)",
    "KOE010": "Client Secret이 틀렸거나 비어 있습니다 → REST API 키 수정 화면의 클라이언트 시크릿을 다시 복사",
    "invalid_client": "REST API 키 또는 Client Secret이 틀렸습니다 → 둘 다 다시 복사",
    "KOE205": "메시지 전송 동의항목이 설정되지 않았습니다 → [카카오 로그인 > 동의항목] 카카오톡 메시지 전송을 선택 동의로",
    "KOE303": "로그인할 때와 토큰 받을 때의 Redirect URI가 다릅니다 → .env의 KAKAO_REDIRECT_URI 확인",
    "KOE320": "로그인 코드가 만료됐거나 이미 사용됐습니다 → 명령을 다시 실행해 새 주소로 로그인 (코드는 몇 분 안에, 한 번만 사용 가능)",
    "invalid_grant": "로그인 코드가 만료됐거나 이미 사용됐습니다 → 명령을 다시 실행해 새 주소로 로그인",
    "access_denied": "동의 화면에서 취소했습니다 → 다시 실행해서 '동의하고 계속하기'를 누르세요",
    "-402": "메시지 전송 권한에 동의하지 않았습니다 → 동의항목 설정 확인 후 다시 로그인하면서 메시지 전송에 체크",
    "-401": "토큰이 유효하지 않습니다 → 명령을 다시 실행",
    "-3": "앱에서 해당 기능이 꺼져 있습니다 → 카카오 로그인 ON, 동의항목 설정 확인",
}


def explain(error_text: str) -> str:
    found = [
        hint for code, hint in HINTS.items()
        if re.search(rf"(?<![\w-]){re.escape(code)}(?![\w])", error_text)
    ]
    if not found:
        return "알려진 오류 코드가 아닙니다. 위 오류 메시지를 그대로 복사해서 물어봐 주세요."
    return "\n".join(f"  👉 {h}" for h in found)


def _fail(stage: str, error_text: str) -> None:
    print(f"\n❌ {stage} 실패")
    print(f"   카카오 응답: {error_text}")
    print(explain(error_text))
    raise SystemExit(1)


def _extract_code(pasted: str, expected_state: str) -> str:
    """붙여 넣은 값에서 로그인 코드를 꺼낸다. 코드가 없거나 잘렸으면 빈 문자열."""
    pasted = pasted.strip()
    if "KOE" in pasted.upper() and "code=" not in pasted:  # 브라우저 오류 화면의 코드를 붙여 넣은 경우
        _fail("카카오 로그인", pasted.upper())
    if "code=" not in pasted and "error=" not in pasted:
        # 코드만 붙여 넣은 경우. 주소 일부만 들어온 경우는 빈 값 처리
        return "" if pasted.startswith("http") else pasted
    query = parse_qs(urlsplit(pasted).query)
    if "error" in query:
        _fail("카카오 로그인", f"{query['error'][0]} {query.get('error_description', [''])[0]}")
    state = query.get("state", [""])[0]
    if state and state != expected_state:
        raise SystemExit("state 값이 일치하지 않습니다. 처음부터 다시 시도하세요.")
    return query.get("code", [""])[0].strip()


def _ask_code(expected_state: str, tries: int = 3) -> str:
    for attempt in range(1, tries + 1):
        code = _extract_code(input("주소 또는 code: "), expected_state)
        if len(code) >= 20:
            return code
        print(
            "\n⚠️  붙여 넣은 주소에 코드가 없거나 잘렸습니다. (code= 뒤가 비어 있음)\n"
            "   브라우저 주소창을 한 번 클릭한 뒤 Cmd+A → Cmd+C로 주소 전체를 복사해서 다시 붙여 넣으세요.\n"
            "   주소는 https://localhost/?code=긴글자...&state=... 모양이어야 합니다.\n"
        )
    raise SystemExit("코드를 받지 못했습니다. 명령을 다시 실행해 주세요.")


def _save_to_env(key: str, value: str) -> bool:
    env = ROOT / ".env"
    if not env.exists():
        return False
    lines = env.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="카카오 리프레시 토큰 발급")
    parser.add_argument("--test", action="store_true", help="발급 후 테스트 메시지 전송")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_config()
    redirect_uri = os.environ.get("KAKAO_REDIRECT_URI", "").strip() or "https://localhost"
    if not cfg.kakao_rest_api_key:
        raise SystemExit(".env에 KAKAO_REST_API_KEY를 먼저 넣어 주세요.")

    state = secrets.token_urlsafe(16)
    url = AUTHORIZE_URL + "?" + urlencode({
        "client_id": cfg.kakao_rest_api_key,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "talk_message",
        "state": state,
    })
    print("\n1) 아래 주소를 브라우저에서 열고 카카오 로그인 + '카카오톡 메시지 전송'에 동의하세요.\n")
    print(url)
    print("   브라우저에 'KOE006'처럼 KOE로 시작하는 오류가 뜨면, 그 코드를 아래에 입력하세요. 원인을 알려 드립니다.")
    print(f"\n2) 로그인 후 {redirect_uri}?code=... 로 이동합니다.")
    print("   페이지가 열리지 않아도 괜찮으니, 주소창의 주소 전체를 복사해 붙여 넣으세요.\n")
    code = _ask_code(state)

    try:
        tokens = exchange_code(cfg.kakao_rest_api_key, cfg.kakao_client_secret, redirect_uri, code, cfg.retry)
    except Exception as exc:
        _fail("토큰 발급", str(exc))
    refresh = tokens["refresh_token"]
    days = int(tokens.get("refresh_token_expires_in", 0)) // 86400
    saved = _save_to_env("KAKAO_REFRESH_TOKEN", refresh)
    print(f"\n3) 발급 완료! (유효기간 약 {days}일, 이 값은 비밀번호처럼 다루세요)")
    if saved:
        print("   .env의 KAKAO_REFRESH_TOKEN에 자동으로 저장했습니다.")
    print("   GitHub 시크릿 KAKAO_REFRESH_TOKEN에는 아래 값을 넣으세요.\n")
    print(refresh)

    if args.test:
        notifier = KakaoNotifier(
            cfg.kakao_rest_api_key, cfg.kakao_client_secret, refresh,
            cfg.kakao_link_url, cfg.kakao_button_title, cfg.retry,
        )
        notifier._access_token = tokens["access_token"]
        try:
            notifier.send("", "✅ AI 비서 연결 테스트\n카카오톡 알림이 정상적으로 설정됐습니다.")
        except Exception as exc:
            _fail("테스트 메시지 전송", str(exc))
        print("\n테스트 메시지를 '나와의 채팅'으로 보냈습니다. 카카오톡을 확인하세요!")


if __name__ == "__main__":
    main()
