"""RSS 피드에서 최근 뉴스를 가져온다."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser

from .config import Feed
from .retry import RetryPolicy, http_get

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime | None
    summary: str = ""


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def parse_feed(content: bytes, feed: Feed, now: datetime, max_age_hours: int, limit: int) -> list[NewsItem]:
    parsed = feedparser.parse(content)
    items: list[NewsItem] = []
    for entry in parsed.entries[:limit]:
        title = _clean(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        published = _published(entry)
        if published and now - published > timedelta(hours=max_age_hours):
            continue
        source = _clean((entry.get("source") or {}).get("title")) or feed.name
        if title.endswith(f" - {source}"):  # 구글 뉴스 "제목 - 언론사" 꼬리표 제거
            title = title[: -len(source) - 3].strip()
        summary = _clean(entry.get("summary"))
        if summary.startswith(title):  # 구글 뉴스는 요약이 제목 반복인 경우가 많다
            summary = ""
        items.append(NewsItem(title, link, source, published, summary[:300]))
    return items


def fetch_news(feeds: list[Feed], policy: RetryPolicy, max_age_hours: int, per_feed_limit: int) -> list[NewsItem]:
    now = datetime.now(timezone.utc)
    items: list[NewsItem] = []
    for feed in feeds:
        try:
            resp = http_get(feed.url, policy, desc=f"뉴스 피드 '{feed.name}'")
            got = parse_feed(resp.content, feed, now, max_age_hours, per_feed_limit)
            log.info("뉴스 피드 '%s': %d건", feed.name, len(got))
            items.extend(got)
        except Exception as exc:  # 피드 하나가 죽어도 나머지는 계속
            log.error("뉴스 피드 '%s' 가져오기 실패: %s", feed.name, exc)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: i.published or epoch, reverse=True)
    return items
