from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .ohlcv_store import append_candles, load_recent_candles, load_range, prune_unclosed_candles
from .settings import BINANCE_FUTURES_REST, OHLCV_STORE_ROOT

# Binance USD-M futures connector documents /fapi/v1/klines with default 500, max 1000.
BINANCE_KLINE_LIMIT = 1000
DEFAULT_UPDATE_OVERLAP_BARS = 2
DEFAULT_MAX_RETRIES = 4
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_MAX_GAP_REPAIRS = 50
RETRYABLE_HTTP_STATUS = {418, 429, 500, 502, 503, 504}


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
    overlap_bars: int = 0
    retries_used: int = 0
    discarded_unclosed_rows: int = 0
    pruned_unclosed_rows: int = 0
    gaps_before: int = 0
    missing_rows_before: int = 0
    repair_attempts: int = 0
    gaps_repaired: int = 0
    gaps_remaining: int = 0
    missing_rows_remaining: int = 0
    integrity_status: str = "unknown"

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


def _retry_delay(response: Any, attempt: int, base_backoff_seconds: float) -> float:
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    return max(0.0, float(base_backoff_seconds)) * (2 ** max(0, attempt))


def _fetch_klines_page_with_stats(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    limit: int = BINANCE_KLINE_LIMIT,
    session: requests.Session | None = None,
    timeout_seconds: int = 30,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    closed_before_ms: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sess = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(max(0, int(max_retries)) + 1):
        response = None
        try:
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
            status = int(getattr(response, "status_code", 200) or 200)
            if status in RETRYABLE_HTTP_STATUS:
                raise requests.HTTPError(f"Retryable Binance HTTP {status}", response=response)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                raise RuntimeError(f"Binance kline error: {payload}")
            raw_rows = list(payload or [])
            cutoff = int(closed_before_ms if closed_before_ms is not None else min(end_ms, int(datetime.now(timezone.utc).timestamp() * 1000)))
            closed_rows = [row for row in raw_rows if len(row) > 6 and int(row[6]) <= cutoff]
            normalized = [_normalize_kline(symbol, interval, row) for row in closed_rows]
            return normalized, {
                "raw_rows": len(raw_rows),
                "discarded_unclosed_rows": max(0, len(raw_rows) - len(closed_rows)),
                "retries_used": attempt,
            }
        except Exception as exc:
            last_error = exc
            status = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
            retryable = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status in RETRYABLE_HTTP_STATUS
            if not retryable or attempt >= max(0, int(max_retries)):
                raise
            delay = _retry_delay(response, attempt, retry_backoff_seconds)
            if delay > 0:
                time.sleep(delay)
    if last_error is not None:
        raise last_error
    return [], {"raw_rows": 0, "discarded_unclosed_rows": 0, "retries_used": 0}


def fetch_klines_page(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    limit: int = BINANCE_KLINE_LIMIT,
    session: requests.Session | None = None,
    timeout_seconds: int = 30,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    closed_before_ms: int | None = None,
) -> list[dict[str, Any]]:
    rows, _ = _fetch_klines_page_with_stats(
        symbol,
        interval,
        start_ms,
        end_ms,
        limit=limit,
        session=session,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        closed_before_ms=closed_before_ms,
    )
    return rows


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


def audit_store_integrity(
    symbol: str,
    interval: str,
    *,
    start: Any | None = None,
    end: Any | None = None,
    store_root: str | Path | None = None,
    max_gap_details: int = 200,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(store_root or OHLCV_STORE_ROOT)
    interval_ms = interval_to_milliseconds(interval)
    frame = load_range(symbol, interval, start=start, end=end, store_root=root)
    if frame.empty:
        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "rows": 0,
            "gap_count": 0,
            "missing_rows": 0,
            "unclosed_rows": 0,
            "continuity_ok": True,
            "gaps": [],
        }
    work = frame.copy()
    work["open_time"] = pd.to_datetime(work["open_time"], utc=True, errors="coerce")
    work["close_time"] = pd.to_datetime(work.get("close_time"), utc=True, errors="coerce")
    work = work.dropna(subset=["open_time"]).sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
    cutoff = pd.to_datetime(now or datetime.now(timezone.utc), utc=True, errors="coerce")
    if pd.isna(cutoff):
        cutoff = pd.Timestamp.now(tz="UTC")
    unclosed_mask = work["close_time"].notna() & (work["close_time"] > cutoff)
    if "is_closed" in work.columns:
        unclosed_mask = unclosed_mask | (~work["is_closed"].fillna(False).astype(bool))
    work["next_open_time"] = work["open_time"].shift(-1)
    delta_ms = (work["next_open_time"] - work["open_time"]).dt.total_seconds() * 1000
    gap_mask = delta_ms > interval_ms * 1.5
    gap_rows = work.loc[gap_mask, ["open_time", "next_open_time"]].copy()
    details: list[dict[str, Any]] = []
    missing_total = 0
    for _, gap in gap_rows.head(max_gap_details).iterrows():
        gap_after = pd.to_datetime(gap["open_time"], utc=True)
        next_open = pd.to_datetime(gap["next_open_time"], utc=True)
        missing = max(0, int(round((next_open - gap_after).total_seconds() * 1000 / interval_ms)) - 1)
        missing_total += missing
        details.append({
            "gap_after": gap_after.isoformat(),
            "next_open_time": next_open.isoformat(),
            "missing_rows": missing,
        })
    # Include missing rows from gaps beyond the detail cap in the total.
    if len(gap_rows) > len(details):
        for _, gap in gap_rows.iloc[len(details):].iterrows():
            gap_after = pd.to_datetime(gap["open_time"], utc=True)
            next_open = pd.to_datetime(gap["next_open_time"], utc=True)
            missing_total += max(0, int(round((next_open - gap_after).total_seconds() * 1000 / interval_ms)) - 1)
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "rows": int(len(work)),
        "gap_count": int(len(gap_rows)),
        "missing_rows": int(missing_total),
        "unclosed_rows": int(unclosed_mask.sum()),
        "continuity_ok": bool(len(gap_rows) == 0 and int(unclosed_mask.sum()) == 0),
        "first_open_time": work.iloc[0]["open_time"].isoformat() if len(work) else None,
        "last_open_time": work.iloc[-1]["open_time"].isoformat() if len(work) else None,
        "gaps": details,
    }


def _download_window(
    symbol: str,
    interval: str,
    *,
    start_dt: datetime,
    end_dt: datetime,
    store_root: Path,
    limit: int,
    sleep_seconds: float,
    max_pages: int | None,
    session: requests.Session,
    max_retries: int,
    retry_backoff_seconds: float,
) -> dict[str, Any]:
    interval_ms = interval_to_milliseconds(interval)
    current_ms = _to_milliseconds(start_dt)
    end_ms = _to_milliseconds(end_dt)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    closed_before_ms = min(end_ms, now_ms)
    pages = 0
    fetched_rows = 0
    written_partitions = 0
    discarded_unclosed_rows = 0
    retries_used = 0
    first_open: str | None = None
    last_open: str | None = None
    stopped_reason = "completed"

    while current_ms <= end_ms:
        if max_pages is not None and pages >= max_pages:
            stopped_reason = "max_pages_reached"
            break
        rows, meta = _fetch_klines_page_with_stats(
            symbol,
            interval,
            current_ms,
            end_ms,
            limit=limit,
            session=session,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            closed_before_ms=closed_before_ms,
        )
        retries_used += int(meta.get("retries_used") or 0)
        discarded_unclosed_rows += int(meta.get("discarded_unclosed_rows") or 0)
        if not rows:
            stopped_reason = "current_candle_not_closed" if int(meta.get("raw_rows") or 0) > 0 and int(meta.get("discarded_unclosed_rows") or 0) > 0 else "no_more_rows"
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

    return {
        "pages": pages,
        "fetched_rows": fetched_rows,
        "written_partitions": written_partitions,
        "discarded_unclosed_rows": discarded_unclosed_rows,
        "retries_used": retries_used,
        "first_open_time": first_open,
        "last_open_time": last_open,
        "stopped_reason": stopped_reason,
    }


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
    update_overlap_bars: int = DEFAULT_UPDATE_OVERLAP_BARS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    repair_gaps: bool = True,
    max_gap_repairs: int = DEFAULT_MAX_GAP_REPAIRS,
) -> HistoricalBackfillResult:
    store_root = Path(store_root or OHLCV_STORE_ROOT)
    symbol = symbol.upper().strip()
    interval = str(interval).strip()
    if not symbol:
        raise ValueError("Symbol is required")
    if not interval:
        raise ValueError("Interval is required")

    requested_start_dt, end_dt = resolve_backfill_window(start=start, end=end, lookback=lookback)
    interval_ms = interval_to_milliseconds(interval)
    now_dt = datetime.now(timezone.utc)
    # Prune only candles that are unfinished *now*.  An historical end date must
    # never delete newer valid data already stored outside the requested window.
    pruned_unclosed = prune_unclosed_candles(symbol, interval, cutoff=now_dt, store_root=store_root)

    start_dt = requested_start_dt
    overlap_bars = max(0, int(update_overlap_bars)) if update_only else 0
    update_start_from_store = latest_stored_open_time(symbol, interval, store_root=store_root) if update_only else None
    if update_start_from_store is not None:
        if overlap_bars > 0:
            candidate = update_start_from_store.to_pydatetime().astimezone(timezone.utc) - timedelta(milliseconds=interval_ms * (overlap_bars - 1))
        else:
            candidate = update_start_from_store.to_pydatetime().astimezone(timezone.utc) + timedelta(milliseconds=interval_ms)
        if candidate > start_dt:
            start_dt = candidate

    current_ms = _to_milliseconds(start_dt)
    end_ms = _to_milliseconds(end_dt)
    if current_ms > end_ms:
        return HistoricalBackfillResult(
            symbol, interval, start_dt.isoformat(), end_dt.isoformat(), 0, 0, 0, None, None,
            str(store_root), update_only, "already_current", overlap_bars=overlap_bars,
            pruned_unclosed_rows=pruned_unclosed, integrity_status="current",
        )

    sess = session or requests.Session()
    primary = _download_window(
        symbol,
        interval,
        start_dt=start_dt,
        end_dt=end_dt,
        store_root=store_root,
        limit=limit,
        sleep_seconds=sleep_seconds,
        max_pages=max_pages,
        session=sess,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    before = audit_store_integrity(symbol, interval, start=requested_start_dt, end=end_dt, store_root=store_root, now=now_dt)
    repair_attempts = 0
    repair_pages = 0
    repair_rows = 0
    repair_partitions = 0
    repair_retries = 0
    repair_discarded = 0
    repair_first: str | None = None
    repair_last: str | None = None

    if repair_gaps and before["gap_count"]:
        for gap in list(before.get("gaps") or [])[: max(0, int(max_gap_repairs))]:
            gap_after = pd.to_datetime(gap["gap_after"], utc=True, errors="coerce")
            next_open = pd.to_datetime(gap["next_open_time"], utc=True, errors="coerce")
            if pd.isna(gap_after) or pd.isna(next_open):
                continue
            repair_start = gap_after.to_pydatetime() + timedelta(milliseconds=interval_ms)
            repair_end = next_open.to_pydatetime() - timedelta(milliseconds=1)
            if repair_start >= next_open.to_pydatetime():
                continue
            repair_attempts += 1
            stats = _download_window(
                symbol,
                interval,
                start_dt=repair_start,
                end_dt=repair_end,
                store_root=store_root,
                limit=limit,
                sleep_seconds=sleep_seconds,
                max_pages=max_pages,
                session=sess,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            repair_pages += int(stats["pages"])
            repair_rows += int(stats["fetched_rows"])
            repair_partitions += int(stats["written_partitions"])
            repair_retries += int(stats["retries_used"])
            repair_discarded += int(stats["discarded_unclosed_rows"])
            repair_first = repair_first or stats.get("first_open_time")
            repair_last = stats.get("last_open_time") or repair_last

    after = audit_store_integrity(symbol, interval, start=requested_start_dt, end=end_dt, store_root=store_root, now=now_dt)
    gaps_repaired = max(0, int(before["gap_count"]) - int(after["gap_count"]))
    integrity_status = "ready" if after["continuity_ok"] else ("gaps_remaining" if after["gap_count"] else "unclosed_rows_remaining")
    first_open = primary.get("first_open_time") or repair_first
    last_open = repair_last or primary.get("last_open_time")

    return HistoricalBackfillResult(
        symbol=symbol,
        interval=interval,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        pages=int(primary["pages"]) + repair_pages,
        fetched_rows=int(primary["fetched_rows"]) + repair_rows,
        written_partitions=int(primary["written_partitions"]) + repair_partitions,
        first_open_time=first_open,
        last_open_time=last_open,
        store_root=str(store_root),
        update_only=update_only,
        stopped_reason=str(primary["stopped_reason"]),
        overlap_bars=overlap_bars,
        retries_used=int(primary["retries_used"]) + repair_retries,
        discarded_unclosed_rows=int(primary["discarded_unclosed_rows"]) + repair_discarded,
        pruned_unclosed_rows=int(pruned_unclosed),
        gaps_before=int(before["gap_count"]),
        missing_rows_before=int(before["missing_rows"]),
        repair_attempts=repair_attempts,
        gaps_repaired=gaps_repaired,
        gaps_remaining=int(after["gap_count"]),
        missing_rows_remaining=int(after["missing_rows"]),
        integrity_status=integrity_status,
    )


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
