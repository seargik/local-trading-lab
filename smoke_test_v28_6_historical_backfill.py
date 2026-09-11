from __future__ import annotations

import tempfile
from pathlib import Path

from app_src.historical_backfill import (
    backfill_symbol_history,
    interval_to_milliseconds,
    parse_lookback,
    resolve_backfill_window,
    summarize_store,
)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        start = int((params or {}).get("startTime") or 0)
        interval = 60 * 60 * 1000
        if self.calls == 1:
            return FakeResponse([
                self._row(start, interval, 100.0),
                self._row(start + interval, interval, 101.0),
            ])
        if self.calls == 2:
            return FakeResponse([
                self._row(start, interval, 102.0),
            ])
        return FakeResponse([])

    @staticmethod
    def _row(open_ms: int, interval_ms: int, close: float):
        return [
            open_ms,
            str(close - 0.5),
            str(close + 1.0),
            str(close - 1.0),
            str(close),
            "123.45",
            open_ms + interval_ms - 1,
            "0",
            "0",
            "0",
            "0",
            "0",
        ]


if interval_to_milliseconds("1h") != 3_600_000:
    raise AssertionError("1h interval conversion failed")
if parse_lookback("1mo").days != 30:
    raise AssertionError("1mo lookback conversion failed")
start, end = resolve_backfill_window(start="2026-01-01", end="2026-01-02")
if start >= end:
    raise AssertionError("Window resolution failed")

with tempfile.TemporaryDirectory() as tmp:
    result = backfill_symbol_history(
        "ETHUSDT",
        "1h",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T05:00:00Z",
        store_root=Path(tmp),
        sleep_seconds=0,
        session=FakeSession(),
    )
    if result.fetched_rows != 3:
        raise AssertionError(f"Expected 3 fetched rows, got {result.fetched_rows}")
    summary = summarize_store("ETHUSDT", "1h", store_root=Path(tmp))
    if summary["rows"] != 3:
        raise AssertionError(f"Expected 3 stored rows, got {summary}")
    result2 = backfill_symbol_history(
        "ETHUSDT",
        "1h",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T05:00:00Z",
        update_only=True,
        store_root=Path(tmp),
        sleep_seconds=0,
        session=FakeSession(),
    )
    if result2.start <= result.start:
        raise AssertionError("Update-only mode did not advance start from existing store")

print("V28.6 smoke test passed: historical backfill stores paged OHLCV and supports update-only refresh.")
