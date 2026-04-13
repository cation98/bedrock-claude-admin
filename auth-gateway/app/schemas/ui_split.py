"""UI Split 집계 API 스키마 — T23."""
from datetime import date

from pydantic import BaseModel


class UiSplitBucket(BaseModel):
    """단일 주간/월간 버킷 집계."""

    period_start: date
    period_end: date       # exclusive end (다음 월요일 / 다음 달 1일)
    webchat_users: int     # 해당 기간 webchat 사용 distinct 사용자 수
    console_users: int     # 해당 기간 console 사용 distinct 사용자 수
    total_events: int      # 해당 기간 전체 이벤트 수


class UiSplitSummary(BaseModel):
    """전체 윈도우 기간 UI Split 집계 응답."""

    period: str            # "weekly" | "monthly"
    window: int            # 반환된 버킷 개수
    webchat_total_users: int   # 윈도우 전체 기간 webchat 사용 distinct 사용자
    console_total_users: int   # 윈도우 전체 기간 console 사용 distinct 사용자
    both_users: int            # webchat + console 모두 사용한 사용자
    webchat_only_users: int    # webchat만 사용한 사용자
    console_only_users: int    # console만 사용한 사용자
    buckets: list[UiSplitBucket]  # 오래된 것 → 최신 순
