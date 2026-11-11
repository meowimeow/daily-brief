"""Open-Meteo(무료, API 키 불필요)로 오늘 날씨를 가져온다."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from .retry import RetryPolicy, http_get

WMO_KO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "짙은 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "많은 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 85: "약한 눈보라", 86: "강한 눈보라",
    95: "뇌우", 96: "우박 동반 뇌우", 99: "강한 우박 동반 뇌우",
}


@dataclass(frozen=True)
class Weather:
    location: str
    description: str
    t_min: float
    t_max: float
    precip_prob: int | None

    def format(self) -> str:
        rain = f", 강수확률 {self.precip_prob}%" if self.precip_prob is not None else ""
        return f"{self.location}: {self.description}, 최저 {self.t_min:.0f}° / 최고 {self.t_max:.0f}°{rain}"


def fetch_weather(location: str, lat: float, lon: float, tz_name: str, policy: RetryPolicy) -> Weather:
    query = urlencode({
        "latitude": lat,
        "longitude": lon,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": tz_name,
        "forecast_days": 1,
    })
    data = http_get(f"https://api.open-meteo.com/v1/forecast?{query}", policy, desc="날씨").json()
    daily = data["daily"]
    code = daily["weather_code"][0]
    prob = daily.get("precipitation_probability_max", [None])[0]
    return Weather(
        location=location,
        description=WMO_KO.get(code, f"날씨 코드 {code}"),
        t_min=daily["temperature_2m_min"][0],
        t_max=daily["temperature_2m_max"][0],
        precip_prob=int(prob) if prob is not None else None,
    )
