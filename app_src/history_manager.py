from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .historical_backfill import audit_store_integrity, interval_to_milliseconds, parse_lookback, summarize_store
from .ohlcv_store import load_range
from .settings import OHLCV_STORE_ROOT

DEFAULT_HISTORY_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "LTCUSDT",
    "BNBUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "XRPUSDT",
    "TRXUSDT",
]

DEFAULT_HISTORY_INTERVALS = ["1h", "4h"]
DEFAULT_HISTORY_LOOKBACK = "12mo"


@dataclass(frozen=True)
class CoverageTarget:
    symbol: str
    interval: str
    lookback: str = DEFAULT_HISTORY_LOOKBACK


def normalize_symbols(symbols: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if symbols is None:
        return list(DEFAULT_HISTORY_SYMBOLS)
    if isinstance(symbols, str):
        raw_items = symbols.replace(";", ",").split(",")
    else:
        raw_items = list(symbols)
    out: list[str] = []
    for item in raw_items:
        symbol = str(item or "").upper().strip()
        if not symbol:
            continue
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        if symbol not in out:
            out.append(symbol)
    return out


def normalize_intervals(intervals: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if intervals is None:
        return list(DEFAULT_HISTORY_INTERVALS)
    if isinstance(intervals, str):
        raw_items = intervals.replace(";", ",").split(",")
    else:
        raw_items = list(intervals)
    out: list[str] = []
    for item in raw_items:
        interval = str(item or "").strip().lower()
        if not interval:
            continue
        # Validate early so the UI/test fails with a clear message.
        interval_to_milliseconds(interval)
        if interval not in out:
            out.append(interval)
    return out


def load_history_targets(path: str | Path = "config/history_backfill_targets.json") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {
            "symbols": list(DEFAULT_HISTORY_SYMBOLS),
            "intervals": list(DEFAULT_HISTORY_INTERVALS),
            "lookback": DEFAULT_HISTORY_LOOKBACK,
        }
    with p.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return {
        "symbols": normalize_symbols(payload.get("symbols")),
        "intervals": normalize_intervals(payload.get("intervals")),
        "lookback": str(payload.get("lookback") or DEFAULT_HISTORY_LOOKBACK),
        "notes": payload.get("notes", ""),
    }


def target_start_for_lookback(lookback: str, *, now: datetime | None = None) -> datetime:
    now_dt = now or datetime.now(timezone.utc)
    return now_dt.astimezone(timezone.utc) - parse_lookback(lookback)


def _safe_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def summarize_history_coverage(
    symbols: list[str] | str | None = None,
    intervals: list[str] | str | None = None,
    *,
    lookback: str = DEFAULT_HISTORY_LOOKBACK,
    store_root: str | Path | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return one coverage/integrity row per symbol/interval from the local OHLCV store."""
    root = Path(store_root or OHLCV_STORE_ROOT)
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    desired_start = target_start_for_lookback(lookback, now=now_dt)
    rows: list[dict[str, Any]] = []
    for symbol in normalize_symbols(symbols):
        for interval in normalize_intervals(intervals):
            summary = summarize_store(symbol, interval, store_root=root)
            first_ts = _safe_timestamp(summary.get("first_open_time"))
            last_ts = _safe_timestamp(summary.get("last_open_time"))
            interval_ms = interval_to_milliseconds(interval)
            expected_rows = max(1, int(((now_dt - desired_start).total_seconds() * 1000) // interval_ms))
            rows_in_window = 0
            if last_ts is not None and first_ts is not None:
                try:
                    window_df = load_range(symbol, interval, start=desired_start, end=now_dt, store_root=root)
                    rows_in_window = int(len(window_df))
                except Exception:
                    rows_in_window = 0
            missing_approx = max(0, expected_rows - rows_in_window)
            coverage_pct = round(min(100.0, rows_in_window / expected_rows * 100.0), 2) if expected_rows else 0.0
            freshness_minutes = None
            if last_ts is not None:
                freshness_minutes = round((now_dt - last_ts.to_pydatetime()).total_seconds() / 60.0, 1)
            try:
                integrity = audit_store_integrity(
                    symbol,
                    interval,
                    start=desired_start,
                    end=now_dt,
                    store_root=root,
                    now=now_dt,
                )
            except Exception:
                integrity = {"gap_count": 0, "missing_rows": 0, "unclosed_rows": 0, "continuity_ok": False}
            coverage_ready = coverage_pct >= 95 and (freshness_minutes is not None and freshness_minutes <= max(180, interval_ms / 60_000 * 3))
            continuity_ready = bool(integrity.get("continuity_ok", False))
            status = "ready" if coverage_ready and continuity_ready else ("missing" if rows_in_window == 0 else "partial")
            rows.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "lookback": lookback,
                    "store_rows_total": int(summary.get("rows") or 0),
                    "rows_in_lookback": rows_in_window,
                    "expected_rows_approx": expected_rows,
                    "missing_rows_approx": missing_approx,
                    "coverage_pct": coverage_pct,
                    "first_open_time": first_ts.isoformat() if first_ts is not None else None,
                    "last_open_time": last_ts.isoformat() if last_ts is not None else None,
                    "freshness_minutes": freshness_minutes,
                    "internal_gap_count": int(integrity.get("gap_count") or 0),
                    "internal_missing_rows": int(integrity.get("missing_rows") or 0),
                    "unclosed_rows": int(integrity.get("unclosed_rows") or 0),
                    "continuity_ok": continuity_ready,
                    "store_root": str(root),
                    "status": status,
                }
            )
    return pd.DataFrame(rows)


def audit_history_gaps(
    symbol: str,
    interval: str,
    *,
    lookback: str = DEFAULT_HISTORY_LOOKBACK,
    store_root: str | Path | None = None,
    max_gaps: int = 200,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Find open_time gaps larger than the expected interval in the requested lookback window."""
    root = Path(store_root or OHLCV_STORE_ROOT)
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_dt = target_start_for_lookback(lookback, now=now_dt)
    interval_ms = interval_to_milliseconds(interval)
    df = load_range(symbol, interval, start=start_dt, end=now_dt, store_root=root)
    if df.empty or "open_time" not in df.columns:
        return pd.DataFrame(columns=["symbol", "interval", "gap_after", "next_open_time", "missing_rows_approx", "gap_minutes"])
    frame = df.copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    frame["next_open_time"] = frame["open_time"].shift(-1)
    frame["delta_ms"] = (frame["next_open_time"] - frame["open_time"]).dt.total_seconds() * 1000
    gaps = frame[frame["delta_ms"] > interval_ms * 1.5].copy()
    if gaps.empty:
        return pd.DataFrame(columns=["symbol", "interval", "gap_after", "next_open_time", "missing_rows_approx", "gap_minutes"])
    gaps["missing_rows_approx"] = (gaps["delta_ms"] // interval_ms - 1).astype(int)
    gaps["gap_minutes"] = (gaps["delta_ms"] / 60_000).round(1)
    out = gaps[["open_time", "next_open_time", "missing_rows_approx", "gap_minutes"]].rename(columns={"open_time": "gap_after"})
    out.insert(0, "interval", interval)
    out.insert(0, "symbol", symbol.upper())
    return out.head(max_gaps).reset_index(drop=True)


def build_backfill_command(
    symbols: list[str] | str | None = None,
    intervals: list[str] | str | None = None,
    *,
    lookback: str = DEFAULT_HISTORY_LOOKBACK,
    update_only: bool = True,
    request_analysis: bool = False,
    windows: bool = True,
) -> str:
    py = r".\.venv\Scripts\python.exe" if windows else "python"
    symbol_text = ",".join(normalize_symbols(symbols))
    interval_text = ",".join(normalize_intervals(intervals))
    cmd = f"{py} backfill_default_history.py --symbols {symbol_text} --intervals {interval_text} --lookback {lookback}"
    if update_only:
        cmd += " --update-only --overlap-bars 2"
    if request_analysis:
        cmd += " --request-analysis"
    return cmd
