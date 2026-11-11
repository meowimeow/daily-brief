"""재시도 유틸리티.

GitHub Actions 러너는 공용 IP를 쓰기 때문에 외부 API(알림, 뉴스, Gemini)가
트래픽 제한(429)이나 일시적 서버 오류(5xx)로 요청을 거절하는 일이 잦다.
여기서는 요청이 성공할 때까지 지수 백오프로 여러 번 다시 시도하고,
성공하는 즉시 멈춘다. 잘못된 토큰처럼 다시 해봐야 소용없는 오류는 바로 포기한다.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

import requests

log = logging.getLogger(__name__)
T = TypeVar("T")

# 다시 시도하면 통과될 가능성이 있는 HTTP 상태 코드
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RetryableError(Exception):
    """일시적인 실패. 잠시 후 다시 시도한다."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(Exception):
    """다시 시도해도 소용없는 실패 (잘못된 키, 잘못된 주소 등)."""


class RetryExhausted(Exception):
    """정해진 횟수만큼 시도했지만 모두 실패."""


DEFAULT_RETRY_ON: tuple[type[BaseException], ...] = (
    RetryableError,
    requests.ConnectionError,
    requests.Timeout,
)


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 6
    base_delay: float = 2.0
    max_delay: float = 60.0

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """attempt번째 실패 후 기다릴 시간(초). 지수 백오프 + 지터."""
        delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        delay *= random.uniform(0.5, 1.0)
        if retry_after:
            # 서버가 기다리라고 알려준 시간은 존중하되 상한은 지킨다.
            delay = max(delay, min(retry_after, self.max_delay))
        return delay

    def run(
        self,
        fn: Callable[[], T],
        desc: str,
        retry_on: tuple[type[BaseException], ...] = DEFAULT_RETRY_ON,
        sleep: Callable[[float], None] = time.sleep,
    ) -> T:
        last: BaseException | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                result = fn()
            except PermanentError:
                log.error("%s: 재시도해도 해결되지 않는 오류라 중단합니다.", desc)
                raise
            except retry_on as exc:
                last = exc
                if attempt == self.attempts:
                    break
                wait = self.delay_for(attempt, getattr(exc, "retry_after", None))
                log.warning(
                    "%s 실패 (%d/%d): %s → %.1f초 후 다시 시도",
                    desc, attempt, self.attempts, exc, wait,
                )
                sleep(wait)
            else:
                if attempt > 1:
                    log.info("%s: %d번째 시도에서 성공, 재시도를 멈춥니다.", desc, attempt)
                return result
        raise RetryExhausted(f"{desc}: {self.attempts}번 모두 실패 ({last})") from last


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def check_response(resp: requests.Response, desc: str) -> requests.Response:
    """HTTP 응답을 검사해 재시도 가능/불가능 오류로 분류한다."""
    if resp.status_code in RETRYABLE_STATUS:
        raise RetryableError(
            f"HTTP {resp.status_code}",
            retry_after=_parse_retry_after(resp.headers.get("Retry-After")),
        )
    if resp.status_code >= 400:
        raise PermanentError(f"{desc}: HTTP {resp.status_code} {resp.text[:200]}")
    return resp


USER_AGENT = "daily-brief-bot/1.0 (+https://github.com)"


def http_get(url: str, policy: RetryPolicy, desc: str, timeout: float = 20) -> requests.Response:
    return policy.run(
        lambda: check_response(
            requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}), desc
        ),
        desc=desc,
    )


def http_post_json(
    url: str,
    payload: dict,
    policy: RetryPolicy,
    desc: str,
    headers: dict | None = None,
    timeout: float = 20,
) -> requests.Response:
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    return policy.run(
        lambda: check_response(
            requests.post(url, json=payload, headers=merged, timeout=timeout), desc
        ),
        desc=desc,
    )
