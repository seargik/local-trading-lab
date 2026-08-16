from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .ohlcv_store import append_candles, load_recent_candles, load_range
from .settings import BINANCE_FUTURES_REST, OHLCV_STORE_ROOT

# Binance USD-M futures connector documents /fapi/v1/klines with default 500, max 1000.
BINANCE_KLINE_LIMIT = 1000


@dataclass
class HistoricalBackfillResult:
    symbol: str
    interval: str
    start: str
    end: str
    pages: int
    fetched_rows: int
    written_partitions: int
    first_open_time: str | None
    last_open_time: str | None
    store_root: str
    update_only: bool = False
    stopped_reason: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def interval_to_milliseconds(interval: str) -> int:
    tf = str(interval or "").strip()
    if not tf:
        raise ValueError("Interval is required")
    unit = tf[-1]
    amount = int(tf[:-1] or "1")
    multipliers = {
        "m": 60_000,
        "h": 60 * 60_000,
        "d": 24 * 60 * 60_000,
        "w": 7 * 24 * 60 * 60_000,
    }
    if unit not in multipliers:
        raise ValueError(f"Unsupported fixed-length interval for backfill: {interval}")
    return amount * multipliers[unit]


def parse_utc_datetime(value: Any | None, *, default: datetime | None = None) -> datetime:
    if value is None or value == "":
        if default is None:
            raise ValueError("Datetime value is required")
        return default.astimezone(timezone.utc)
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Could not parse datetime: {value}")
    return ts.to_pydatetime().astimezone(timezone.utc)


def parse_lookback(value: str) -> timedelta:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("Lookback is required, e.g. 30d, 6mo, 1y, 5y")
    aliases = {
        "1month": "1mo",
        "1 month": "1mo",
        "1mth": "1mo",
        "1year": "1y",
        "1 year": "1y",
        "5years": "5y",
        "5 years": "5y",
    }
    raw = aliases.get(raw, raw)
    if raw.endswith("mo"):
        return timedelta(days=int(raw[:-2]) * 30)
    if raw.endswith("d"):
        return timedelta(days=int(raw[:-1]))
    if raw.endswith("w"):
        return timedelta(weeks=int(raw[:-1]))
    if raw.endswith("y"):
        return timedelta(days=int(raw[:-1]) * 365)
    if raw.endswith("m"):
        # In this CLI, `m` means months for lookback values; use intervals for minutes.
        return timedelta(days=int(raw[:-1]) * 30)
    raise ValueError(f"Unsupported lookback: {value}. Use 30d, 6mo, 1y, 5y")


def _to_milliseconds(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _normalize_kline(symbol: str, interval: str, row: list[Any]) -> dict[str, Any]:
    return {
        "exchange": "binance_futures",
        "symbol": symbol.upper(),
        "interval": interval,
        "open_time": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "close_time": datetime.fromtimestamp(int(row[6]) / 1000, tz=timezone.utc).isoformat(),
        "is_closed": True,
        "source": "historical_backfill",
    }


def fetch_klines_page(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    limit: int = BINANCE_KLINE_LIMIT,
    session: requests.Session | None = None,
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    sess = session or requests.Session()
    response = sess.get(
        f"{BINANCE_FUTURES_REST}/fapi/v1/klines",
        params={
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
            "limit": min(int(limit), BINANCE_KLINE_LIMIT),
        },
        timeout=timeout_seconds,
    )
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        raise RuntimeError(f"Binance kline error: {payload}")
    return [_normalize_kline(symbol, interval, row) for row in payload]


def resolve_backfill_window(
    *,
    start: Any | None = None,
    end: Any | None = None,
    lookback: str | None = None,
) -> tuple[datetime, datetime]:
    end_dt = parse_utc_datetime(end, default=datetime.now(timezone.utc))
    if start is not None:
        start_dt = parse_utc_datetime(start)
    elif lookback:
        start_dt = end_dt - parse_lookback(lookback)
    else:
        raise ValueError("Provide either start or lookback")
    if start_dt >= end_dt:
        raise ValueError(f"Backfill start must be before end: {start_dt} >= {end_dt}")
    return start_dt, end_dt


def latest_stored_open_time(symbol: str, interval: str, store_root: str | Path | None = None) -> pd.Timestamp | None:
    recent = load_recent_candles(symbol, interval, limit=2, store_root=store_root)
    if recent.empty or "open_time" not in recent.columns:
        return None
    ts = pd.to_datetime(recent.iloc[-1]["open_time"], utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def backfill_symbol_history(
    symbol: str,
    interval: str,
    *,
    start: Any | None = None,
    end: Any | None = None,
    lookback: str | None = None,
    update_only: bool = False,
    store_root: str | Path | None = None,
    limit: int = BINANCE_KLINE_LIMIT,
    sleep_seconds: float = 0.15,
    max_pages: int | None = None,
    session: requests.Session | None = None,
) -> HistoricalBackfillResult:
    store_root = Path(store_root or OHLCV_STORE_ROOT)
    symbol = symbol.upper().strip()
    interval = str(interval).strip()
    if not symbol:
        raise ValueError("Symbol is required")
    if not interval:
        raise ValueError("Interval is required")

    start_dt, end_dt = resolve_backfill_window(start=start, end=end, lookback=lookback)
    interval_ms = interval_to_milliseconds(interval)
    update_start_from_store = latest_stored_open_time(symbol, interval, store_root=store_root) if update_only else None
    if update_start_from_store is not None:
        candidate = update_start_from_store.to_pydatetime().astimezone(timezone.utc) + timedelta(milliseconds=interval_ms)
        if candidate > start_dt:
            start_dt = candidate

    current_ms = _to_milliseconds(start_dt)
    end_ms = _to_milliseconds(end_dt)
    pages = 0
    fetched_rows = 0
    written_partitions = 0
    first_open: str | None = None
    last_open: str | None = None
    stopped_reason = "completed"

    if current_ms > end_ms:
        return HistoricalBackfillResult(symbol, interval, start_dt.isoformat(), end_dt.isoformat(), 0, 0, 0, None, None, str(store_root), update_only, "already_current")

    sess = session or requests.Session()
    while current_ms <= end_ms:
        if max_pages is not None and pages >= max_pages:
            stopped_reason = "max_pages_reached"
            break
        rows = fetch_klines_page(symbol, interval, current_ms, end_ms, limit=limit, session=sess)
        if not rows:
            stopped_reason = "no_more_rows"
            break
        written = append_candles(rows, store_root=store_root)
        pages += 1
        fetched_rows += len(rows)
        written_partitions += len(written)
        first_open = first_open or rows[0]["open_time"]
        last_open = rows[-1]["open_time"]
        last_ts = pd.to_datetime(last_open, utc=True, errors="coerce")
        if pd.isna(last_ts):
            stopped_reason = "bad_last_timestamp"
            break
        next_ms = _to_milliseconds(last_ts.to_pydatetime()) + interval_ms
        if next_ms <= current_ms:
            next_ms = current_ms + interval_ms
        current_ms = next_ms
        if sleep_seconds > 0:
            time.sleep(float(sleep_seconds))

    return HistoricalBackfillResult(symbol, interval, start_dt.isoformat(), end_dt.isoformat(), pages, fetched_rows, written_partitions, first_open, last_open, str(store_root), update_only, stopped_reason)


def summarize_store(symbol: str, interval: str, store_root: str | Path | None = None) -> dict[str, Any]:
    df = load_range(symbol, interval, store_root=store_root)
    if df.empty:
        return {"symbol": symbol.upper(), "interval": interval, "rows": 0, "first_open_time": None, "last_open_time": None}
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "rows": int(len(df)),
        "first_open_time": df.iloc[0]["open_time"].isoformat(),
        "last_open_time": df.iloc[-1]["open_time"].isoformat(),
    }
