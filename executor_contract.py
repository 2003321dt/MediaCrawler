from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_published_at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(timezone.utc)


def filter_recent_items(
    items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    window_hours: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(hours=window_hours)
    future_limit = current + timedelta(minutes=5)
    kept: list[dict[str, Any]] = []
    stats = {"input": len(items), "kept": 0, "dropped_old": 0, "dropped_invalid_time": 0}
    for item in items:
        published_at = parse_published_at(item.get("published_at"))
        if published_at is None or published_at > future_limit:
            stats["dropped_invalid_time"] += 1
            continue
        if published_at < cutoff:
            stats["dropped_old"] += 1
            continue
        normalized = dict(item)
        normalized["published_at"] = published_at.isoformat()
        kept.append(normalized)
    stats["kept"] = len(kept)
    return kept, stats
