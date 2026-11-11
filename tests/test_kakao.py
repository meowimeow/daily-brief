import json

import pytest

from assistant import kakao
from assistant.kakao import KakaoNotifier, split_for_kakao
from assistant.retry import PermanentError, RetryPolicy

FAST = RetryPolicy(attempts=4, base_delay=0, max_delay=0)


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeKakao:
    """카카오 토큰/메시지 서버 흉내. responses에 (url 일부, 상태, 본문)을 순서대로 넣는다."""

    def __init__(self, monkeypatch, responses):
        self.responses = list(responses)
        self.calls = []
        monkeypatch.setattr(kakao.requests, "post", self.post)

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data, headers))
        expect, status, body = self.responses.pop(0)
        assert expect in url, f"{url} 호출 예상 밖 (기대: {expect})"
        return FakeResp(status, body)


def make(tmp_path, refresh="old-refresh"):
    return KakaoNotifier("rest-key", "secret", refresh, "https://news.google.com", "뉴스 더보기",
                         FAST, new_token_file=tmp_path / "new_token")


def test_split_for_kakao_keeps_every_message_under_200():
    body = "☀️ 오늘 알아야 할 것\n• 우산 챙기세요\n\n📅 일정\n• 14:00 회의\n\n📰 뉴스\n" + "\n".join(
        f"• 아주 중요한 뉴스 한 줄 요약입니다 번호 {i} (연합뉴스)" for i in range(10)
    )
    msgs = split_for_kakao("📋 오늘의 브리핑", body)
    assert msgs[0].startswith("📋 오늘의 브리핑\n\n☀️")
    assert msgs[1].startswith("📅 일정")
    assert len(msgs) >= 4               # 뉴스 섹션은 200자를 넘어서 둘 이상으로 나뉨
    assert all(0 < len(m) <= 200 for m in msgs)


def test_send_refreshes_token_then_sends_each_section(monkeypatch, tmp_path):
    fake = FakeKakao(monkeypatch, [
        ("oauth/token", 200, {"access_token": "AT1", "expires_in": 43199}),
        ("memo/default/send", 200, {"result_code": 0}),
        ("memo/default/send", 200, {"result_code": 0}),
    ])
    make(tmp_path).send("제목", "섹션 하나\n\n섹션 둘")

    token_call, first, second = fake.calls
    assert token_call[1]["grant_type"] == "refresh_token"
    assert token_call[1]["client_secret"] == "secret"
    assert first[2]["Authorization"] == "Bearer AT1"
    template = json.loads(first[1]["template_object"])
    assert template["object_type"] == "text"
    assert template["text"] == "제목\n\n섹션 하나"
    assert template["link"]["mobile_web_url"] == "https://news.google.com"
    assert json.loads(second[1]["template_object"])["text"] == "섹션 둘"
    assert not (tmp_path / "new_token").exists()  # 새 리프레시 토큰이 안 왔으니 교체 없음


def test_new_refresh_token_is_saved_for_rotation(monkeypatch, tmp_path):
    FakeKakao(monkeypatch, [
        ("oauth/token", 200, {"access_token": "AT", "refresh_token": "new-refresh",
                              "refresh_token_expires_in": 5184000}),
        ("memo/default/send", 200, {"result_code": 0}),
    ])
    make(tmp_path).send("", "안녕")
    assert (tmp_path / "new_token").read_text() == "new-refresh"


def test_rate_limited_message_is_retried_until_it_passes(monkeypatch, tmp_path):
    fake = FakeKakao(monkeypatch, [
        ("oauth/token", 503, {}),                       # 토큰 서버도 거절 → 재시도
        ("oauth/token", 200, {"access_token": "AT"}),
        ("memo/default/send", 429, {"code": -10}),      # 트래픽 거절 → 재시도
        ("memo/default/send", 429, {"code": -10}),
        ("memo/default/send", 200, {"result_code": 0}), # 통과 → 멈춤
    ])
    make(tmp_path).send("", "안녕")
    assert len(fake.calls) == 5
    assert fake.responses == []


def test_expired_access_token_is_refreshed_once(monkeypatch, tmp_path):
    fake = FakeKakao(monkeypatch, [
        ("oauth/token", 200, {"access_token": "OLD"}),
        ("memo/default/send", 401, {"code": -401}),
        ("oauth/token", 200, {"access_token": "NEW"}),
        ("memo/default/send", 200, {"result_code": 0}),
    ])
    make(tmp_path).send("", "안녕")
    assert fake.calls[-1][2]["Authorization"] == "Bearer NEW"


def test_invalid_refresh_token_fails_fast(monkeypatch, tmp_path):
    fake = FakeKakao(monkeypatch, [
        ("oauth/token", 401, {"error": "invalid_grant"}),
    ])
    with pytest.raises(PermanentError):
        make(tmp_path).send("", "안녕")
    assert len(fake.calls) == 1  # 다시 해도 안 되는 오류는 재시도하지 않음
