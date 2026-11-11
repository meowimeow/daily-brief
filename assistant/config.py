"""config.yaml(공개 설정)과 환경 변수(비밀 값)를 읽는다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .retry import RetryPolicy

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Feed:
    name: str
    url: str


@dataclass
class Config:
    timezone: str
    location_name: str
    latitude: float
    longitude: float
    feeds: list[Feed]
    news_max_age_hours: int
    news_per_feed_limit: int
    news_max_candidates: int
    news_in_brief: int
    retention_days: int
    gemini_model: str
    notify_title: str
    kakao_link_url: str
    kakao_button_title: str
    retry: RetryPolicy
    state_path: Path
    # 비밀 값 (GitHub Secrets / .env)
    gemini_api_key: str = ""
    kakao_rest_api_key: str = ""
    kakao_client_secret: str = ""
    kakao_refresh_token: str = ""
    kakao_new_token_file: str = ""  # 새 리프레시 토큰을 적어 둘 파일 (Actions가 시크릿 교체)
    calendar_ics_urls: list[str] = field(default_factory=list)


def _load_dotenv(path: Path) -> None:
    """로컬 실행용 .env 로더 (이미 설정된 환경 변수는 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def load_config(path: Path | None = None) -> Config:
    _load_dotenv(ROOT / ".env")
    raw = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))

    loc = raw.get("location", {})
    news = raw.get("news", {})
    retry = raw.get("retry", {})

    return Config(
        timezone=raw.get("timezone", "Asia/Seoul"),
        location_name=loc.get("name", "서울"),
        latitude=float(loc.get("latitude", 37.5665)),
        longitude=float(loc.get("longitude", 126.9780)),
        feeds=[Feed(f["name"], f["url"]) for f in news.get("feeds", [])],
        news_max_age_hours=int(news.get("max_age_hours", 36)),
        news_per_feed_limit=int(news.get("per_feed_limit", 15)),
        news_max_candidates=int(news.get("max_candidates", 30)),
        news_in_brief=int(news.get("in_brief", 8)),
        retention_days=int(raw.get("memory", {}).get("retention_days", 14)),
        gemini_model=_env("GEMINI_MODEL", raw.get("gemini", {}).get("model", "gemini-3.8-flash")),
        notify_title=raw.get("notify", {}).get("title", "오늘의 브리핑"),
        kakao_link_url=raw.get("kakao", {}).get("link_url", "https://news.google.com"),
        kakao_button_title=raw.get("kakao", {}).get("button_title", "뉴스 더보기"),
        retry=RetryPolicy(
            attempts=int(retry.get("attempts", 6)),
            base_delay=float(retry.get("base_delay_seconds", 2)),
            max_delay=float(retry.get("max_delay_seconds", 60)),
        ),
        state_path=ROOT / raw.get("memory", {}).get("state_file", "state/sent_news.json"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        kakao_rest_api_key=_env("KAKAO_REST_API_KEY"),
        kakao_client_secret=_env("KAKAO_CLIENT_SECRET"),
        kakao_refresh_token=_env("KAKAO_REFRESH_TOKEN"),
        kakao_new_token_file=_env("KAKAO_NEW_REFRESH_TOKEN_FILE"),
        calendar_ics_urls=[u.strip() for u in _env("CALENDAR_ICS_URLS").split(",") if u.strip()],
    )
