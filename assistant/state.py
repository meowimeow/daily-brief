"""이미 보낸 뉴스를 기억해서 중복을 걸러내는 저장소.

state/sent_news.json에 저장되고, GitHub Actions가 실행 후 저장소에 커밋한다.
공개 저장소여도 안전하도록 제목/주소 원문이 아니라 해시만 저장한다.

같은 기사를 두 가지 키로 기억한다.
  - 주소 키: 추적 파라미터(utm_ 등)를 뗀 정규화 URL
  - 제목 키: 언론사 꼬리표, [속보] 같은 말머리, 공백·기호를 뗀 정규화 제목
둘 중 하나라도 겹치면 이미 보낸 소식으로 본다.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .news import NewsItem

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref", "cmpid", "oc")
_PREFIX_TAG_RE = re.compile(r"^\s*[\[\(【<〈][^\]\)】>〉]{1,12}[\]\)】>〉]\s*")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), ""))


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).strip()
    # "기사 제목 - 언론사" 형태의 꼬리표 제거
    if " - " in t:
        head, tail = t.rsplit(" - ", 1)
        if head and len(tail) <= 25:
            t = head
    # [속보], (종합), 【단독】 같은 말머리 반복 제거
    while True:
        stripped = _PREFIX_TAG_RE.sub("", t, count=1)
        if stripped == t:
            break
        t = stripped
    return _NON_WORD_RE.sub("", t.lower())


def _h(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def keys_for(item: NewsItem) -> list[str]:
    keys = [_h("u:", normalize_url(item.link))]
    title = normalize_title(item.title)
    if len(title) >= 8:  # 너무 짧은 제목은 오탐 위험이 커서 주소로만 판단
        keys.append(_h("t:", title))
    return keys


class SentStore:
    def __init__(self, path: Path, seen: dict[str, str] | None = None, last_sent_date: str | None = None):
        self.path = path
        self.seen: dict[str, str] = seen or {}
        self.last_sent_date = last_sent_date

    @classmethod
    def load(cls, path: Path) -> "SentStore":
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return cls(path, data.get("seen", {}), data.get("last_sent_date"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "last_sent_date": self.last_sent_date,
            "seen": dict(sorted(self.seen.items())),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def already_sent_on(self, day: date) -> bool:
        return self.last_sent_date == day.isoformat()

    def prune(self, today: date, retention_days: int) -> int:
        cutoff = (today - timedelta(days=retention_days)).isoformat()
        old = [k for k, d in self.seen.items() if d < cutoff]
        for k in old:
            del self.seen[k]
        return len(old)

    def filter_new(self, items: Iterable[NewsItem]) -> list[NewsItem]:
        """이미 보낸 소식과, 이번 묶음 안에서 겹치는 소식을 뺀다."""
        batch: set[str] = set()
        fresh: list[NewsItem] = []
        for item in items:
            keys = keys_for(item)
            if any(k in self.seen or k in batch for k in keys):
                continue
            batch.update(keys)
            fresh.append(item)
        return fresh

    def mark_sent(self, items: Iterable[NewsItem], day: date) -> None:
        stamp = day.isoformat()
        for item in items:
            for k in keys_for(item):
                self.seen[k] = stamp
        self.last_sent_date = stamp
