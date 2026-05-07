from datetime import datetime, timezone, timedelta

TW = timezone(timedelta(hours=8))


def to_tw(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TW)


def tw_iso(dt, fallback=""):
    if dt is None:
        return fallback
    return to_tw(dt).isoformat()


def tw_str(dt, fmt="%Y/%m/%d %H:%M", fallback=""):
    if dt is None:
        return fallback
    return to_tw(dt).strftime(fmt)
