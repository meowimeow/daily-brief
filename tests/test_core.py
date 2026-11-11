from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from assistant.calendar_events import events_for_day
from assistant.news import NewsItem
from assistant.notifier import split_message
from assistant.retry import PermanentError, RetryableError, RetryExhausted, RetryPolicy
from assistant.state import SentStore, normalize_title, normalize_url

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def item(title, link):
    return NewsItem(title=title, link=link, source="test", published=NOW)


# ---------- 재시도 ----------

def test_retry_stops_as_soon_as_request_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RetryableError("429")
        return "ok"

    sleeps = []
    assert RetryPolicy(attempts=6).run(flaky, "t", sleep=sleeps.append) == "ok"
    assert len(calls) == 3          # 3번째에 성공하고 멈춤
    assert len(sleeps) == 2


def test_retry_gives_up_after_max_attempts():
    calls = []

    def always_fail():
        calls.append(1)
        raise RetryableError("503")

    with pytest.raises(RetryExhausted):
        RetryPolicy(attempts=4).run(always_fail, "t", sleep=lambda _: None)
    assert len(calls) == 4


def test_permanent_error_is_not_retried():
    calls = []

    def bad_token():
        calls.append(1)
        raise PermanentError("401")

    with pytest.raises(PermanentError):
        RetryPolicy(attempts=5).run(bad_token, "t", sleep=lambda _: None)
    assert len(calls) == 1


def test_retry_after_header_is_respected():
    assert RetryPolicy(base_delay=1, max_delay=60).delay_for(1, retry_after=30) >= 30


# ---------- 중복 제거 ----------

def test_title_normalization_strips_publisher_and_tags():
    a = normalize_title("[속보] 한국은행, 기준금리 동결 결정 - 연합뉴스")
    b = normalize_title("한국은행,  기준금리 동결 결정 - 한겨레")
    assert a == b


def test_url_normalization_drops_tracking():
    assert normalize_url("http://www.ex.com/a/?utm_source=x&id=1#top") == normalize_url("https://ex.com/a?id=1")


def test_store_skips_yesterdays_news_and_keeps_new(tmp_path):
    path = tmp_path / "state.json"
    yesterday, today = date(2026, 11, 10), date(2026, 11, 11)

    store = SentStore.load(path)
    sent = [item("반도체 수출 3개월 연속 증가세", "https://a.com/1")]
    store.mark_sent(sent, yesterday)
    store.save()

    store = SentStore.load(path)  # 다음 날: 파일에서 다시 읽음
    fresh = store.filter_new([
        item("반도체 수출 3개월 연속 증가세 - 다른신문", "https://b.com/xyz"),  # 같은 소식, 다른 언론사
        item("반도체 수출 3개월 연속 증가세", "https://a.com/1?utm_source=rss"),  # 같은 주소
        item("서울 첫눈 예보, 출근길 주의", "https://a.com/2"),               # 새 소식
        item("서울 첫눈 예보, 출근길 주의", "https://c.com/9"),               # 같은 묶음 내 중복
    ])
    assert [f.link for f in fresh] == ["https://a.com/2"]
    assert not store.already_sent_on(today)
    assert store.already_sent_on(yesterday)


def test_store_prunes_old_entries(tmp_path):
    store = SentStore(tmp_path / "s.json")
    store.mark_sent([item("오래된 소식 제목입니다", "https://a.com/old")], date(2026, 10, 1))
    store.mark_sent([item("최근 소식 제목입니다", "https://a.com/new")], date(2026, 11, 10))
    store.prune(date(2026, 11, 11), retention_days=14)
    assert store.filter_new([item("오래된 소식 제목입니다", "https://a.com/old")])
    assert not store.filter_new([item("최근 소식 제목입니다", "https://a.com/new")])


# ---------- 알림 분할 ----------

def test_split_message_respects_byte_limit():
    text = "\n".join(f"• 뉴스 항목 {i} " + "가" * 50 for i in range(100))
    chunks = split_message(text, 1000, lambda s: len(s.encode("utf-8")))
    assert len(chunks) > 1
    assert all(len(c.encode("utf-8")) <= 1000 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_message_handles_single_long_line():
    chunks = split_message("가" * 5000, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == "가" * 5000


# ---------- 캘린더 ----------

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART;TZID=Asia/Seoul:20261111T140000
DTEND;TZID=Asia/Seoul:20261111T150000
SUMMARY:\xed\x8c\x80 \xed\x9a\x8c\xec\x9d\x98
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20261111
DTEND;VALUE=DATE:20261112
SUMMARY:Birthday
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART:20261101T000000Z
DTEND:20261101T010000Z
RRULE:FREQ=WEEKLY;BYDAY=WE
SUMMARY:Weekly
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=Asia/Seoul:20261112T090000
DTEND;TZID=Asia/Seoul:20261112T100000
SUMMARY:Tomorrow
END:VEVENT
END:VCALENDAR
"""


def test_events_for_day_includes_recurring_and_all_day():
    events = events_for_day(ICS, date(2026, 11, 11), ZoneInfo("Asia/Seoul"))
    assert [e.format() for e in events] == [
        "종일 · Birthday",
        "09:00–10:00 Weekly",       # 매주 수요일 00:00 UTC = 09:00 KST
        "14:00–15:00 팀 회의",
    ]
