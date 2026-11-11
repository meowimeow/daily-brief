"""카카오톡 '나에게 보내기' API.

- 액세스 토큰은 약 12시간이면 만료되므로 실행할 때마다 리프레시 토큰으로 새로 받는다.
- 리프레시 토큰은 약 60일 유효하고, 남은 기간이 1개월 미만이면 갱신 응답에 새 토큰이 온다.
  새 토큰은 KAKAO_NEW_REFRESH_TOKEN_FILE 경로에 저장해 두고,
  GitHub Actions가 그 값으로 KAKAO_REFRESH_TOKEN 시크릿을 교체한다.
- 텍스트 메시지 한 개는 최대 200자라서 브리핑을 섹션 단위로 나눠 여러 개로 보낸다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

from .retry import USER_AGENT, PermanentError, RetryPolicy, check_response

log = logging.getLogger(__name__)

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
TEXT_LIMIT = 200  # 텍스트 템플릿 text 최대 길이


def _in_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def mask_secret(value: str) -> None:
    """GitHub Actions 로그에 값이 찍히지 않도록 가린다."""
    if value and _in_github_actions():
        print(f"::add-mask::{value}", flush=True)


def _form_post(url: str, data: dict, policy: RetryPolicy, desc: str, headers: dict | None = None) -> dict:
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    resp = policy.run(
        lambda: check_response(requests.post(url, data=data, headers=merged, timeout=20), desc),
        desc=desc,
    )
    return resp.json()


def exchange_code(rest_api_key: str, client_secret: str, redirect_uri: str, code: str, policy: RetryPolicy) -> dict:
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return _form_post(TOKEN_URL, data, policy, desc="카카오 토큰 발급")


class KakaoNotifier:
    name = "kakao"

    def __init__(
        self,
        rest_api_key: str,
        client_secret: str,
        refresh_token: str,
        link_url: str,
        button_title: str,
        policy: RetryPolicy,
        new_token_file: Path | None = None,
    ):
        self.rest_api_key = rest_api_key
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.link_url = link_url
        self.button_title = button_title
        self.policy = policy
        self.new_token_file = new_token_file
        self._access_token: str | None = None

    # ---------- 토큰 ----------

    def refresh_access_token(self) -> str:
        data = {
            "grant_type": "refresh_token",
            "client_id": self.rest_api_key,
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            tokens = _form_post(TOKEN_URL, data, self.policy, desc="카카오 토큰 갱신")
        except PermanentError:
            log.error(
                "카카오 리프레시 토큰이 만료됐거나 잘못됐습니다. "
                "`python -m assistant.kakao_auth`로 다시 로그인해 KAKAO_REFRESH_TOKEN을 바꿔 주세요."
            )
            raise
        self._access_token = tokens["access_token"]
        mask_secret(self._access_token)

        new_refresh = tokens.get("refresh_token")
        if new_refresh and new_refresh != self.refresh_token:
            mask_secret(new_refresh)
            self.refresh_token = new_refresh
            days = int(tokens.get("refresh_token_expires_in", 0)) // 86400
            log.info("카카오 리프레시 토큰이 새로 발급됐습니다 (유효 %d일).", days)
            if self.new_token_file:
                self.new_token_file.parent.mkdir(parents=True, exist_ok=True)
                self.new_token_file.write_text(new_refresh, encoding="utf-8")
            else:
                log.warning("새 리프레시 토큰을 저장할 곳이 없습니다. 시크릿을 직접 바꿔 주세요.")
        return self._access_token

    # ---------- 전송 ----------

    def _template(self, text: str) -> str:
        return json.dumps({
            "object_type": "text",
            "text": text,
            "link": {"web_url": self.link_url, "mobile_web_url": self.link_url},
            "button_title": self.button_title,
        }, ensure_ascii=False)

    def _send_one(self, text: str, desc: str) -> None:
        refreshed = False
        while True:
            token = self._access_token or self.refresh_access_token()
            try:
                resp = self.policy.run(
                    lambda: check_response(
                        requests.post(
                            MEMO_URL,
                            data={"template_object": self._template(text)},
                            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
                            timeout=20,
                        ),
                        desc,
                    ),
                    desc=desc,
                )
            except PermanentError as exc:
                # 401: 액세스 토큰 만료 → 한 번만 새로 받아서 다시 시도
                if "HTTP 401" in str(exc) and not refreshed:
                    self._access_token = None
                    refreshed = True
                    continue
                raise
            body = resp.json()
            if body.get("result_code") != 0:
                raise PermanentError(f"{desc}: 예상치 못한 응답 {body}")
            return

    def send(self, title: str, body: str) -> None:
        messages = split_for_kakao(title, body)
        for i, text in enumerate(messages, 1):
            self._send_one(text, desc=f"카카오톡 메시지 ({i}/{len(messages)})")


def split_for_kakao(title: str, body: str, limit: int = TEXT_LIMIT) -> list[str]:
    """빈 줄로 나뉜 섹션마다 메시지 하나. 200자를 넘는 섹션은 줄 단위로 더 나눈다."""
    from .notifier import split_message

    sections = [s.strip() for s in body.strip().split("\n\n") if s.strip()]
    if title and sections:
        sections[0] = f"{title}\n\n{sections[0]}"
    messages: list[str] = []
    for section in sections:
        messages.extend(c for c in split_message(section, limit) if c.strip())
    return messages
