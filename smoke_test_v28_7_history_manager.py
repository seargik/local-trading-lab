from __future__ import annotations

import compileall
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app_src.history_manager import (
    DEFAULT_HISTORY_SYMBOLS,
    audit_history_gaps,
    build_backfill_command,
    load_history_targets,
    normalize_intervals,
    normalize_symbols,
    summarize_history_coverage,
)
from app_src.ohlcv_store import append_candles


def _make_rows(symbol: str, interval: str, start: datetime, count: int, step: timedelta) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    price = 100.0
    for i in range(count):
        ts = start + step * i
        price += 1.0
        rows.append(
            {
                "symbol": symbol,
                "interval": interval,
                "open_time": ts.isoformat(),
                "open": price,
                "high": price + 2,
                "low": price - 2,
                "close": price + 1,
                "volume": 10 + i,
                "close_time": (ts + step).isoformat(),
                "is_closed": True,
            }
        )
    return rows


def main() -> None:
    assert normalize_symbols(["btc", "ETHUSDT", "sol"]) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert normalize_intervals("1h,4h") == ["1h", "4h"]
    assert "AAVEUSDT" in DEFAULT_HISTORY_SYMBOLS

    cfg = load_history_targets("missing_config_for_smoke.json")
    assert cfg["lookback"] == "12mo"
    assert "TRXUSDT" in cfg["symbols"]

    cmd = build_backfill_command(["BTC", "ETH"], ["1h"], lookback="12mo", update_only=True, request_analysis=True)
    assert "backfill_default_history.py" in cmd
    assert "BTCUSDT,ETHUSDT" in cmd
    assert "--update-only" in cmd
    assert "--request-analysis" in cmd

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ohlcv_store"
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        start = now - timedelta(hours=25)
        rows = _make_rows("BTCUSDT", "1h", start, 26, timedelta(hours=1))
        append_candles(rows, store_root=root)

        coverage = summarize_history_coverage(["BTCUSDT", "ETHUSDT"], ["1h"], lookback="1d", store_root=root, now=now)
        assert set(coverage["symbol"].tolist()) == {"BTCUSDT", "ETHUSDT"}
        btc = coverage[coverage["symbol"] == "BTCUSDT"].iloc[0]
        eth = coverage[coverage["symbol"] == "ETHUSDT"].iloc[0]
        assert btc["rows_in_lookback"] >= 24
        assert btc["coverage_pct"] >= 95
        assert eth["status"] == "missing"

        gaps = audit_history_gaps("BTCUSDT", "1h", lookback="1d", store_root=root, now=now)
        assert gaps.empty

    assert compileall.compile_file("pages/01_Data_History.py", quiet=1)
    assert compileall.compile_file("backfill_default_history.py", quiet=1)
    print("V28.7 smoke test passed: history manager helpers, page, and default target runner are available.")


if __name__ == "__main__":
    main()
