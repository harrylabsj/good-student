"""时间工具：全部使用 UTC ISO-8601（秒精度）字符串存储，保证字典序即时间序。"""

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_iso(value: str) -> str:
    """规范化任意合法 ISO 时间为统一 UTC 格式；非法输入抛 ValueError。"""
    return iso(parse_iso(value))


def add_days(value_iso: str, days: int) -> str:
    return iso(parse_iso(value_iso) + timedelta(days=days))
