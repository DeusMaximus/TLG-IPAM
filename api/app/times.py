"""Datetimes as the server stores and hands them out (brief §4.3).

Every datetime the server writes is UTC, and every column holding one is
`DateTime(timezone=True)`, but SQLite keeps only the wall-clock time and reads it back
without a zone. `utc` restores the zone: services use it to compare a stored value with
one in memory, and `schemas.UtcDatetime` uses it so every datetime in a response
carries its offset, whether the row was just written or read back.
"""

from datetime import datetime, timezone


def utc(value: datetime | None) -> datetime | None:
    """The value as an aware UTC datetime: an offset-less one is taken as UTC (as SQLite
    returns every stored value), one with another offset is converted."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
