"""아침 브리핑 실행 진입점.

    python -m assistant.main            # 오늘 아직 안 보냈으면 전송
    python -m assistant.main --dry-run  # 전송/저장 없이 결과만 출력
    python -m assistant.main --force    # 오늘 이미 보냈어도 다시 전송

GitHub Actions의 예약 실행은 트래픽이 몰리면 지연되거나 빠지기도 해서
워크플로가 아침에 여러 번 실행된다. 그날 이미 전송에 성공했다면
이후 실행은 바로 종료된다(중복 알림 방지).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .calendar_events import fetch_events
from .config import load_config
from .kakao import split_for_kakao
from .news import fetch_news
from .notifier import build_notifiers, send_all
from .state import SentStore
from .summarizer import build_prompt, fallback_brief, summarize_with_gemini
from .weather import fetch_weather

log = logging.getLogger("assistant")


def run(dry_run: bool = False, force: bool = False) -> int:
    cfg = load_config()
    tz = ZoneInfo(cfg.timezone)
    today = datetime.now(tz).date()

    store = SentStore.load(cfg.state_path)
    if store.already_sent_on(today) and not force and not dry_run:
        log.info("%s 브리핑은 이미 전송됨 → 이번 실행은 건너뜁니다.", today)
        return 0

    notifiers = build_notifiers(cfg)
    if not notifiers and not dry_run:
        log.error("카카오톡 설정이 없습니다. KAKAO_REST_API_KEY와 KAKAO_REFRESH_TOKEN을 설정하세요.")
        return 2

    pruned = store.prune(today, cfg.retention_days)
    if pruned:
        log.info("%d일 지난 기록 %d개 정리", cfg.retention_days, pruned)

    weather = None
    try:
        weather = fetch_weather(cfg.location_name, cfg.latitude, cfg.longitude, cfg.timezone, cfg.retry)
    except Exception as exc:
        log.error("날씨 가져오기 실패: %s", exc)

    events = fetch_events(cfg.calendar_ics_urls, today, tz, cfg.retry) if cfg.calendar_ics_urls else []

    all_news = fetch_news(cfg.feeds, cfg.retry, cfg.news_max_age_hours, cfg.news_per_feed_limit)
    new_news = store.filter_new(all_news)[: cfg.news_max_candidates]
    log.info("뉴스 %d건 중 새 소식 %d건 (이미 보낸 소식 제외)", len(all_news), len(new_news))

    body = None
    if cfg.gemini_api_key:
        prompt = build_prompt(today, weather, events, new_news, cfg.news_in_brief)
        try:
            body = summarize_with_gemini(cfg.gemini_api_key, cfg.gemini_model, prompt, cfg.retry)
        except Exception as exc:
            log.error("Gemini 요약 실패, 기본 브리핑으로 대체: %s", exc)
    else:
        log.warning("GEMINI_API_KEY가 없어 기본 브리핑을 사용합니다.")
    if not body:
        body = fallback_brief(today, weather, events, new_news, cfg.news_in_brief)

    if dry_run:
        messages = split_for_kakao(cfg.notify_title, body)
        for i, text in enumerate(messages, 1):
            print(f"----- 카카오톡 메시지 {i}/{len(messages)} ({len(text)}자) -----\n{text}")
        return 0

    if not send_all(notifiers, cfg.notify_title, body):
        # 기록을 저장하지 않으므로 다음 예약 실행이 다시 시도한다.
        log.error("모든 알림 채널 전송 실패. 다음 실행에서 다시 시도합니다.")
        return 1

    # 전송에 성공한 뒤에만 '보낸 소식'으로 기억한다.
    store.mark_sent(new_news, today)
    store.save()
    log.info("전송 완료, 보낸 소식 %d건을 기록했습니다.", len(new_news))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 아침 브리핑")
    parser.add_argument("--dry-run", action="store_true", help="전송·저장 없이 결과만 출력")
    parser.add_argument("--force", action="store_true", help="오늘 이미 보냈어도 다시 전송")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run(dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
