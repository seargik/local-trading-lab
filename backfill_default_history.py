from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app_src.historical_backfill import (
    DEFAULT_MAX_GAP_REPAIRS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_UPDATE_OVERLAP_BARS,
    backfill_symbol_history,
)
from app_src.history_manager import (
    DEFAULT_HISTORY_INTERVALS,
    DEFAULT_HISTORY_LOOKBACK,
    DEFAULT_HISTORY_SYMBOLS,
    load_history_targets,
    normalize_intervals,
    normalize_symbols,
)
from app_src.runtime_state import atomic_write_json
from app_src.settings import ANALYSIS_REQUEST_PATH


def _write_analysis_request(symbols: list[str], intervals: list[str], reason: str) -> None:
    payload = {
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "symbols": symbols,
        "intervals": intervals,
    }
    atomic_write_json(ANALYSIS_REQUEST_PATH, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill and integrity-check the default historical OHLCV target set.")
    parser.add_argument("--config", default="config/history_backfill_targets.json", help="JSON config path with symbols, intervals and lookback.")
    parser.add_argument("--symbols", default=None, help="Override comma-separated symbols, e.g. BTCUSDT,ETHUSDT")
    parser.add_argument("--intervals", default=None, help="Override comma-separated intervals, e.g. 1h,4h")
    parser.add_argument("--lookback", default=None, help="Override lookback, e.g. 30d, 12mo, 1y, 5y")
    parser.add_argument("--update-only", action="store_true", help="Refresh only the recent tail, overlapping the latest stored candles for safety.")
    parser.add_argument("--overlap-bars", type=int, default=DEFAULT_UPDATE_OVERLAP_BARS, help="Stored candles to refetch in update-only mode (default: 2).")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Retry count for transient Binance/API failures.")
    parser.add_argument("--retry-backoff-seconds", type=float, default=DEFAULT_RETRY_BACKOFF_SECONDS, help="Base exponential retry delay.")
    parser.add_argument("--no-repair-gaps", action="store_true", help="Audit gaps but do not attempt automatic repairs.")
    parser.add_argument("--max-gap-repairs", type=int, default=DEFAULT_MAX_GAP_REPAIRS, help="Maximum internal gaps repaired per symbol/interval.")
    parser.add_argument("--request-analysis", action="store_true", help="Write an analyzer request file after successful completion.")
    parser.add_argument("--max-pages", type=int, default=None, help="Safety cap per download window; omit for full requested window.")
    parser.add_argument("--sleep-seconds", type=float, default=0.15, help="Delay between Binance kline requests.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned work without fetching candles.")
    args = parser.parse_args()

    cfg = load_history_targets(args.config)
    symbols = normalize_symbols(args.symbols) if args.symbols else normalize_symbols(cfg.get("symbols", DEFAULT_HISTORY_SYMBOLS))
    intervals = normalize_intervals(args.intervals) if args.intervals else normalize_intervals(cfg.get("intervals", DEFAULT_HISTORY_INTERVALS))
    lookback = str(args.lookback or cfg.get("lookback") or DEFAULT_HISTORY_LOOKBACK)

    plan = {
        "symbols": symbols,
        "intervals": intervals,
        "lookback": lookback,
        "update_only": bool(args.update_only),
        "overlap_bars": max(0, int(args.overlap_bars)),
        "max_retries": max(0, int(args.max_retries)),
        "retry_backoff_seconds": max(0.0, float(args.retry_backoff_seconds)),
        "repair_gaps": not bool(args.no_repair_gaps),
        "max_gap_repairs": max(0, int(args.max_gap_repairs)),
        "max_pages": args.max_pages,
        "sleep_seconds": args.sleep_seconds,
    }
    print(json.dumps({"plan": plan}, indent=2))
    if args.dry_run:
        return 0

    results: list[dict[str, Any]] = []
    for symbol in symbols:
        for interval in intervals:
            print(f"Backfilling {symbol} {interval} lookback={lookback} update_only={args.update_only}...")
            result = backfill_symbol_history(
                symbol,
                interval,
                lookback=lookback,
                update_only=bool(args.update_only),
                update_overlap_bars=max(0, int(args.overlap_bars)),
                max_retries=max(0, int(args.max_retries)),
                retry_backoff_seconds=max(0.0, float(args.retry_backoff_seconds)),
                repair_gaps=not bool(args.no_repair_gaps),
                max_gap_repairs=max(0, int(args.max_gap_repairs)),
                sleep_seconds=float(args.sleep_seconds),
                max_pages=args.max_pages,
            )
            payload = result.to_dict()
            results.append(payload)
            print(json.dumps(payload, indent=2, default=str))

    out_dir = Path("data/backfill_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"history_backfill_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"Wrote report: {report_path}")

    if args.request_analysis:
        _write_analysis_request(symbols, intervals, "v28_19_history_integrity_backfill")
        print(f"Requested analyzer refresh via {ANALYSIS_REQUEST_PATH}")

    summary = pd.DataFrame(results)
    if not summary.empty:
        columns = [
            "symbol",
            "interval",
            "fetched_rows",
            "discarded_unclosed_rows",
            "pruned_unclosed_rows",
            "retries_used",
            "gaps_before",
            "gaps_repaired",
            "gaps_remaining",
            "missing_rows_remaining",
            "integrity_status",
            "stopped_reason",
        ]
        print(summary[[c for c in columns if c in summary.columns]].to_string(index=False))
        not_ready = summary[summary.get("integrity_status", "") != "ready"] if "integrity_status" in summary.columns else pd.DataFrame()
        if not not_ready.empty:
            print("WARNING: Some history targets still have integrity issues; review the JSON report before research runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
