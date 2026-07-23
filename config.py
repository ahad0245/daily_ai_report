from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Karachi"
TIMEZONE_FALLBACKS = {
    "Asia/Karachi": datetime_timezone(timedelta(hours=5), name="Asia/Karachi"),
    "UTC": datetime_timezone.utc,
}


@dataclass(frozen=True)
class ReportConfig:
    timezone_name: str
    page_size: int
    selected_status: str
    rejected_status: str

    @property
    def timezone(self):
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            return TIMEZONE_FALLBACKS.get(self.timezone_name, datetime_timezone.utc)


def get_report_config() -> ReportConfig:
    timezone_name = (
        os.getenv("REPORT_APP_TIMEZONE")
        or os.getenv("APP_TIMEZONE")
        or os.getenv("TIMEZONE")
        or os.getenv("TZ")
        or DEFAULT_TIMEZONE
    ).strip()
    page_size = int(os.getenv("REPORT_API_PAGE_SIZE", "200"))
    return ReportConfig(
        timezone_name=timezone_name,
        page_size=page_size,
        selected_status=os.getenv("REPORT_SELECTED_STATUS", "Ai score above 7").strip(),
        rejected_status=os.getenv("REPORT_REJECTED_STATUS", "Rejected by AI").strip(),
    )


def get_default_report_date(config: ReportConfig) -> date:
    now = datetime.now(config.timezone)
    return (now - timedelta(days=1)).date()


def get_day_bounds(target_date: date, config: ReportConfig) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time(0, 0, 0), tzinfo=config.timezone)
    end = datetime.combine(target_date, time(23, 59, 59), tzinfo=config.timezone)
    return start, end
