from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app_src.historical_backfill import backfill_symbol_history, summarize_store
from app_src.runtime_state import atomic_write_json
from app_src.settings import ANALYSIS_REQUEST_PATH, OHLCV_STORE_ROOT


def _split_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in str(raw or "").replace(";", ",").split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Binance USD-M futures OHLCV into the local parquet OHLCV store.")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--interval", default="1h", help="Kline interval, e.g. 5m, 15m, 1h, 4h, 1d")
    parser.add_argument("--lookback", default=None, help="Relative history window, e.g. 30d, 6mo, 1y, 5y")
    parser.add_argument("--start", default=None, help="UTC start date/time, e.g. 2021-01-01 or 2021-01-01T00:00:00Z")
    parser.add_argument("--end", default=None, help="UTC end date/time. Defaults to now.")
    parser.add_argument("--update-only", action="store_true", help="Start from the last stored candle + one interval when local history already exists.")
    parser.add_argument("--store-root", default=str(OHLCV_STORE_ROOT), help="Target OHLCV parquet store root")
    parser.add_argument("--sleep", type=float, default=0.15, help="Pause between Binance requests in seconds")
    parser.add_argument("--max-pages", type=int, default=None, help="Safety limit for requests per symbol")
    parser.add_argument("--request-analysis", action="store_true", help="Ask analyzer_worker.py to rerun after the backfill finishes")
    args = parser.parse_args()

    symbols = _split_symbols(args.symbols)
    if not symbols:
        raise SystemExit("No valid symbols supplied")
    if not args.lookback and not args.start:
        raise SystemExit("Provide either --lookback or --start")

    all_results = []
    for symbol in symbols:
        print(f"Backfilling {symbol} {args.interval} ...", flush=True)
        result = backfill_symbol_history(
            symbol,
            args.interval,
            start=args.start,
            end=args.end,
            lookback=args.lookback,
            update_only=args.update_only,
            store_root=args.store_root,
            sleep_seconds=args.sleep,
            max_pages=args.max_pages,
        )
        all_results.append(result.to_dict())
        print(json.dumps(result.to_dict(), indent=2, default=str), flush=True)
        print(json.dumps(summarize_store(symbol, args.interval, store_root=args.store_root), indent=2, default=str), flush=True)

    if args.request_analysis:
        atomic_write_json(
            Path(ANALYSIS_REQUEST_PATH),
            {
                "reason": "historical_backfill_completed",
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "symbols": symbols,
                "interval": args.interval,
            },
        )
        print("Analyzer rerun requested.", flush=True)

    print("Historical backfill completed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
