"""알림 전송 공통 부분.

각 요청은 RetryPolicy로 성공할 때까지 여러 번 재시도하고, 성공하면 즉시 멈춘다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Protocol

from .config import Config
from .kakao import KakaoNotifier

log = logging.getLogger(__name__)


def split_message(text: str, limit: int, measure: Callable[[str], int] = len) -> list[str]:
    """줄 단위로 limit을 넘지 않게 나눈다. 한 줄이 너무 길면 그 줄을 잘라 넣는다."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        while measure(line) > limit:  # 아주 긴 한 줄
            cut = len(line)
            while measure(line[:cut]) > limit:
                cut = int(cut * 0.9)
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:cut])
            line = line[cut:]
        candidate = f"{current}\n{line}" if current else line
        if measure(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks or [""]


class Notifier(Protocol):
    name: str

    def send(self, title: str, body: str) -> None: ...


def build_notifiers(cfg: Config) -> list[Notifier]:
    if cfg.kakao_rest_api_key and cfg.kakao_refresh_token:
        return [KakaoNotifier(
            rest_api_key=cfg.kakao_rest_api_key,
            client_secret=cfg.kakao_client_secret,
            refresh_token=cfg.kakao_refresh_token,
            link_url=cfg.kakao_link_url,
            button_title=cfg.kakao_button_title,
            policy=cfg.retry,
            new_token_file=Path(cfg.kakao_new_token_file) if cfg.kakao_new_token_file else None,
        )]
    return []


def send_all(notifiers: list[Notifier], title: str, body: str) -> bool:
    """하나 이상의 채널로 전달되면 성공으로 본다."""
    delivered = False
    for n in notifiers:
        try:
            n.send(title, body)
            log.info("%s 알림 전송 성공", n.name)
            delivered = True
        except Exception as exc:
            log.error("%s 알림 전송 최종 실패: %s", n.name, exc)
    return delivered
