"""iCal(ICS) 주소에서 오늘 일정을 가져온다.

구글 캘린더: 설정 → 내 캘린더 설정 → '비공개 주소(iCal 형식)'를 복사해
CALENDAR_ICS_URLS 시크릿에 넣는다. 여러 개면 쉼표로 구분.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from .retry import RetryPolicy, http_get

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime | None  # 종일 일정이면 None
    end: datetime | None
    location: str = ""

    @property
    def all_day(self) -> bool:
        return self.start is None

    def format(self) -> str:
        loc = f" @{self.location}" if self.location else ""
        if self.all_day:
            return f"종일 · {self.title}{loc}"
        span = self.start.strftime("%H:%M")
        if self.end:
            span += f"–{self.end.strftime('%H:%M')}"
        return f"{span} {self.title}{loc}"


def _to_local(value, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(tz)


def events_for_day(ics: bytes, day: date, tz: ZoneInfo) -> list[Event]:
    cal = icalendar.Calendar.from_ical(ics)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    events: list[Event] = []
    for comp in recurring_ical_events.of(cal).between(start, end):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        title = str(comp.get("SUMMARY", "")).strip() or "(제목 없음)"
        location = str(comp.get("LOCATION", "")).strip()
        dtstart = comp.get("DTSTART").dt
        dtend = comp.get("DTEND").dt if comp.get("DTEND") else None
        if isinstance(dtstart, datetime):
            events.append(Event(
                title,
                _to_local(dtstart, tz),
                _to_local(dtend, tz) if isinstance(dtend, datetime) else None,
                location,
            ))
        else:
            events.append(Event(title, None, None, location))
    events.sort(key=lambda e: (not e.all_day, e.start or start))
    return events


def fetch_events(urls: list[str], day: date, tz: ZoneInfo, policy: RetryPolicy) -> list[Event]:
    events: list[Event] = []
    for i, url in enumerate(urls, 1):
        try:
            # URL 자체가 비밀이므로 로그에는 번호만 남긴다.
            resp = http_get(url, policy, desc=f"캘린더 #{i}")
            events.extend(events_for_day(resp.content, day, tz))
        except Exception as exc:
            log.error("캘린더 #%d 가져오기 실패: %s", i, type(exc).__name__)
    events.sort(key=lambda e: (not e.all_day, e.start or datetime.min.replace(tzinfo=tz)))
    return events
