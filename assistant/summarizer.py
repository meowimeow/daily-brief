"""Gemini API로 브리핑을 요약한다. 실패하면 AI 없이 만든 기본 브리핑으로 대체한다."""

from __future__ import annotations

import logging
from datetime import date

from .calendar_events import Event
from .news import NewsItem
from .retry import RETRYABLE_STATUS, PermanentError, RetryableError, RetryPolicy
from .weather import Weather

log = logging.getLogger(__name__)

WEEKDAYS = "월화수목금토일"

SYSTEM_INSTRUCTION = """너는 사용자의 아침 브리핑을 써 주는 한국어 개인 비서다.
휴대폰 알림으로 읽히므로 짧고 훑어보기 쉽게 쓴다.
주어진 자료에 없는 사실은 절대 지어내지 않는다.
마크다운 문법(#, **, 표, 링크)은 쓰지 않고, 이모지 제목과 '• ' 글머리표만 쓴다."""


def _date_label(day: date) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일 ({WEEKDAYS[day.weekday()]})"


def build_prompt(day: date, weather: Weather | None, events: list[Event], news: list[NewsItem], news_in_brief: int) -> str:
    lines = [f"오늘 날짜: {_date_label(day)}", ""]
    lines.append("[날씨]")
    lines.append(weather.format() if weather else "정보 없음")
    lines.append("")
    lines.append("[오늘 일정]")
    lines.extend([f"- {e.format()}" for e in events] or ["없음"])
    lines.append("")
    lines.append("[새 뉴스 후보] (어제까지 이미 알려준 소식은 이미 빠져 있음)")
    if news:
        for i, n in enumerate(news, 1):
            extra = f" — {n.summary}" if n.summary else ""
            lines.append(f"{i}. [{n.source}] {n.title}{extra}")
    else:
        lines.append("없음")
    lines += [
        "",
        "위 자료로 카카오톡 메시지용 아침 브리핑을 써 줘.",
        "섹션마다 카카오톡 메시지 한 개로 따로 보내므로, 섹션 사이는 빈 줄 하나로 구분하고",
        "섹션 안에는 빈 줄을 넣지 마. 각 섹션은 공백 포함 190자를 넘지 않게 해.",
        "",
        "☀️ 오늘 알아야 할 것",
        "• 날씨에 따른 준비물(우산, 겉옷 등), 놓치면 안 되는 일정, 가장 중요한 뉴스를 합쳐 2~3줄",
        "",
        "📅 일정",
        "• 시간순으로. 일정이 없으면 '오늘 등록된 일정 없음' 한 줄",
        "",
        "📰 뉴스",
        f"• 중요도 순으로 최대 {news_in_brief}개, 각 35자 안팎의 한 줄 요약 (끝에 출처를 괄호로)",
        "• 같은 사건을 다룬 기사들은 하나로 합칠 것",
        "• 새 뉴스가 없으면 '어제 이후 새 소식 없음' 한 줄",
    ]
    return "\n".join(lines)


def summarize_with_gemini(api_key: str, model: str, prompt: str, policy: RetryPolicy) -> str:
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, temperature=0.4)

    def call() -> str:
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code is None or code in RETRYABLE_STATUS:
                raise RetryableError(f"Gemini API {code}: {exc}") from exc
            raise PermanentError(f"Gemini API {code}: {exc}") from exc
        except Exception as exc:  # 네트워크 오류 등
            raise RetryableError(f"Gemini 연결 오류: {exc}") from exc
        text = (resp.text or "").strip()
        if not text:
            raise RetryableError("Gemini가 빈 응답을 돌려줌")
        return text

    return policy.run(call, desc="Gemini 요약")


def fallback_brief(day: date, weather: Weather | None, events: list[Event], news: list[NewsItem], news_in_brief: int) -> str:
    """Gemini를 못 쓸 때도 알림은 가도록 만드는 기본 브리핑."""
    out = [f"☀️ {_date_label(day)}"]
    if weather:
        out.append(f"• {weather.format()}")
    out += ["", "📅 일정"]
    out += [f"• {e.format()}" for e in events] or ["• 오늘 등록된 일정 없음"]
    out += ["", "📰 뉴스"]
    out += [f"• {n.title} ({n.source})" for n in news[:news_in_brief]] or ["• 어제 이후 새 소식 없음"]
    out.append("(AI 요약 실패로 원문 제목을 보냅니다)")
    return "\n".join(out)
