from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from app_src.historical_backfill import (
    _fetch_klines_page_with_stats,
    audit_store_integrity,
    backfill_symbol_history,
)
from app_src.ohlcv_store import append_candles, load_range, prune_unclosed_candles

HOUR_MS = 60 * 60 * 1000
BASE_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def kline(open_ms: int, close: float = 100.0):
    return [
        open_ms,
        str(close - 0.5),
        str(close + 1.0),
        str(close - 1.0),
        str(close),
        "123.45",
        open_ms + HOUR_MS - 1,
        "0",
        "0",
        "0",
        "0",
        "0",
    ]


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


class RetryThenMixedSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            return FakeResponse([], status_code=429, headers={"Retry-After": "0"})
        return FakeResponse([
            kline(BASE_MS, 100.0),
            kline(BASE_MS + HOUR_MS, 101.0),
        ])


session = RetryThenMixedSession()
rows, meta = _fetch_klines_page_with_stats(
    "BTCUSDT",
    "1h",
    BASE_MS,
    BASE_MS + 2 * HOUR_MS,
    session=session,
    max_retries=2,
    retry_backoff_seconds=0,
    closed_before_ms=BASE_MS + HOUR_MS + 30 * 60 * 1000,
)
if session.calls != 2:
    raise AssertionError(f"Expected one retry, calls={session.calls}")
if meta["retries_used"] != 1:
    raise AssertionError(f"Retry accounting failed: {meta}")
if len(rows) != 1 or meta["discarded_unclosed_rows"] != 1:
    raise AssertionError(f"Unfinished-candle filtering failed: rows={len(rows)} meta={meta}")


# Verify an unfinished row already left by an older collector is physically removed.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    append_candles(
        [
            {
                "symbol": "BTCUSDT",
                "interval": "1h",
                "open_time": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 10,
                "close_time": "2026-01-01T00:59:59.999Z",
                "is_closed": True,
            },
            {
                "symbol": "BTCUSDT",
                "interval": "1h",
                "open_time": "2099-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 10,
                "close_time": "2099-01-01T00:59:59.999Z",
                "is_closed": True,
            },
        ],
        store_root=root,
    )
    removed = prune_unclosed_candles("BTCUSDT", "1h", cutoff="2026-01-02T00:00:00Z", store_root=root)
    remaining = load_range("BTCUSDT", "1h", store_root=root)
    if removed != 1 or len(remaining) != 1:
        raise AssertionError(f"Stored unfinished candle was not pruned: removed={removed}, rows={len(remaining)}")


class GapRepairSession:
    def __init__(self):
        self.starts: list[int] = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        start = int(params.get("startTime") or 0)
        self.starts.append(start)
        hour = int((start - BASE_MS) // HOUR_MS)
        if hour == 2:
            return FakeResponse([kline(BASE_MS + h * HOUR_MS, 100.0 + h) for h in [2, 3, 4, 5]])
        if hour == 1:
            return FakeResponse([kline(BASE_MS + HOUR_MS, 101.0)])
        return FakeResponse([])


# Seed a store with one internal hole: 00:00, 02:00, 03:00.  Update-only should
# overlap the two latest candles, then the integrity pass should repair 01:00.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    seed = []
    for hour in [0, 2, 3]:
        row = kline(BASE_MS + hour * HOUR_MS, 100.0 + hour)
        seed.append(
            {
                "symbol": "ETHUSDT",
                "interval": "1h",
                "open_time": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc).isoformat(),
                "is_closed": True,
            }
        )
    append_candles(seed, store_root=root)

    before = audit_store_integrity(
        "ETHUSDT",
        "1h",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T05:00:00Z",
        store_root=root,
    )
    if before["gap_count"] != 1 or before["missing_rows"] != 1:
        raise AssertionError(f"Expected seeded one-row gap, got {before}")

    fake = GapRepairSession()
    result = backfill_symbol_history(
        "ETHUSDT",
        "1h",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T05:00:00Z",
        update_only=True,
        update_overlap_bars=2,
        repair_gaps=True,
        store_root=root,
        sleep_seconds=0,
        retry_backoff_seconds=0,
        session=fake,
    )
    if not result.start.startswith("2026-01-01T02:00:00"):
        raise AssertionError(f"Two-bar update overlap should restart at 02:00, got {result.start}")
    if result.gaps_before != 1 or result.gaps_repaired != 1 or result.gaps_remaining != 0:
        raise AssertionError(f"Gap repair accounting failed: {result.to_dict()}")
    if result.integrity_status != "ready":
        raise AssertionError(f"Expected ready integrity after repair: {result.to_dict()}")
    after = audit_store_integrity(
        "ETHUSDT",
        "1h",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T05:00:00Z",
        store_root=root,
    )
    if not after["continuity_ok"] or after["rows"] != 6:
        raise AssertionError(f"Repaired store is not continuous: {after}")

print("V28.19 smoke test passed: retries, closed-candle filtering, overlap refresh, pruning and gap repair are available.")
