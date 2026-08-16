from __future__ import annotations

import argparse
import asyncio
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_src.app_state import load_user_config
from app_src.market_data import BinanceFuturesClient
from app_src.runtime_state import atomic_write_json
from app_src.settings import (
    COLLECTOR_PID_PATH,
    COLLECTOR_STATE_PATH,
    DEFAULT_ANALYSIS_TIMEFRAME,
    DEFAULT_CHART_TIMEFRAME,
    DEFAULT_LOOKBACK,
    DEFAULT_POLL_SECONDS,
    DEFAULT_SELECTED_SYMBOLS,
    HTF_MAP,
    LAB_DB_PATH,
)
from app_src.storage import Storage
from app_src.ohlcv_store import append_candles

STOP = False


def _handle_stop(*_args):
    global STOP
    STOP = True


for sig_name in ("SIGINT", "SIGTERM"):
    if hasattr(signal, sig_name):
        signal.signal(getattr(signal, sig_name), _handle_stop)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Lab background collector worker")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols")
    parser.add_argument("--timeframe", default="", help="Backward-compatible alias for analysis timeframe")
    parser.add_argument("--analysis-timeframe", default="", help="Analysis timeframe, e.g. 1h")
    parser.add_argument("--chart-timeframe", default="", help="Chart display timeframe, e.g. 5m")
    parser.add_argument("--lookback", type=int, default=0, help="Bootstrap lookback candles")
    parser.add_argument("--poll-seconds", type=int, default=0, help="Polling interval in seconds")
    return parser.parse_args()


def resolve_runtime() -> tuple[list[str], str, str, int, int]:
    args = parse_args()
    cfg = load_user_config()
    symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()] or [s.upper() for s in cfg.get("selected_symbols", DEFAULT_SELECTED_SYMBOLS)]
    analysis_timeframe = (args.analysis_timeframe or args.timeframe or cfg.get("analysis_timeframe") or cfg.get("timeframe") or DEFAULT_ANALYSIS_TIMEFRAME).strip()
    chart_timeframe = (args.chart_timeframe or cfg.get("chart_timeframe") or DEFAULT_CHART_TIMEFRAME).strip()
    lookback = int(args.lookback or cfg.get("lookback") or DEFAULT_LOOKBACK)
    poll_seconds = int(args.poll_seconds or cfg.get("poll_seconds") or DEFAULT_POLL_SECONDS)
    poll_seconds = max(30, min(600, poll_seconds))
    return symbols, analysis_timeframe, chart_timeframe, lookback, poll_seconds


def project_root() -> Path:
    return Path(__file__).resolve().parent


def pid_path() -> Path:
    return (project_root() / COLLECTOR_PID_PATH).resolve()


def state_path() -> Path:
    return (project_root() / COLLECTOR_STATE_PATH).resolve()


def write_pid() -> None:
    path = pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def clear_pid() -> None:
    path = pid_path()
    if not path.exists():
        return
    try:
        existing = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        existing = None
    if existing == os.getpid():
        try:
            path.unlink()
        except Exception:
            pass


def latest_closed_time_from_rows(rows: list[dict[str, Any]]) -> str | None:
    for row in reversed(rows):
        if row.get("is_closed"):
            return row.get("open_time")
    return rows[-1].get("open_time") if rows else None


def build_fetch_plan(analysis_timeframe: str, chart_timeframe: str) -> list[str]:
    ordered: list[str] = []
    for tf in [analysis_timeframe, chart_timeframe, *HTF_MAP.get(analysis_timeframe, [])]:
        if tf and tf not in ordered:
            ordered.append(tf)
    return ordered


async def run() -> None:
    symbols, analysis_timeframe, chart_timeframe, lookback, poll_seconds = resolve_runtime()
    storage = Storage(LAB_DB_PATH)
    client = BinanceFuturesClient()
    started_at = datetime.now(timezone.utc).isoformat()
    cycle_count = 0
    last_event_time = None
    fetch_plan = build_fetch_plan(analysis_timeframe, chart_timeframe)

    def write_state(**overrides):
        atomic_write_json(
            state_path(),
            {
                "running": overrides.get("running", True),
                "pid": os.getpid(),
                "symbols": symbols,
                "timeframe": analysis_timeframe,
                "analysis_timeframe": analysis_timeframe,
                "chart_timeframe": chart_timeframe,
                "lookback": lookback,
                "poll_seconds": poll_seconds,
                "fetched_intervals": fetch_plan,
                "history_bootstrapped": overrides.get("history_bootstrapped", False),
                "last_error": overrides.get("last_error"),
                "last_event_time": overrides.get("last_event_time", last_event_time),
                "reconnects": overrides.get("reconnects", 0),
                "started_at": started_at,
                "cycle_count": overrides.get("cycle_count", cycle_count),
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    write_pid()
    write_state(running=True, history_bootstrapped=False, last_error=None)

    try:
        while not STOP:
            cycle_payload: dict[str, Any] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "timeframe": analysis_timeframe,
                "analysis_timeframe": analysis_timeframe,
                "chart_timeframe": chart_timeframe,
                "lookback": lookback,
                "poll_seconds": poll_seconds,
                "fetched_intervals": fetch_plan,
                "symbols": {},
            }
            try:
                for symbol in symbols:
                    frames: dict[str, list[dict[str, Any]]] = {}
                    analysis_rows: list[dict[str, Any]] = []
                    archive_rows: list[dict[str, Any]] = []
                    for tf in fetch_plan:
                        limit = lookback if tf in {analysis_timeframe, chart_timeframe} else 300
                        try:
                            rows = client.fetch_history(symbol, tf, limit=limit)
                        except Exception:
                            rows = []
                        frames[tf] = rows
                        archive_rows.extend(rows)
                        if tf == analysis_timeframe:
                            analysis_rows = rows
                    if archive_rows:
                        try:
                            append_candles(archive_rows)
                        except Exception:
                            pass
                    extras = client.fetch_market_snapshot(symbol, oi_period=analysis_timeframe if analysis_timeframe in {"5m", "15m", "1h"} else "5m")
                    rule = client.get_symbol_rule(symbol)
                    cycle_payload["symbols"][symbol] = {
                        "frames": frames,
                        "market_snapshot": extras,
                        "symbol_rule": rule,
                        "latest_closed_candle": latest_closed_time_from_rows(analysis_rows),
                    }
                storage.write_market_snapshot(cycle_payload)
                cycle_count += 1
                latest_times = [v.get("latest_closed_candle") for v in cycle_payload["symbols"].values() if v.get("latest_closed_candle")]
                last_event_time = max(latest_times) if latest_times else None
                write_state(running=True, history_bootstrapped=True, last_error=None, last_event_time=last_event_time, cycle_count=cycle_count)
            except Exception as exc:
                write_state(running=True, history_bootstrapped=cycle_count > 0, last_error=str(exc), last_event_time=last_event_time, cycle_count=cycle_count)
            slept = 0
            while not STOP and slept < poll_seconds:
                await asyncio.sleep(1)
                slept += 1
    finally:
        write_state(running=False, history_bootstrapped=cycle_count > 0, last_error="Stopped" if STOP else None, last_event_time=last_event_time, cycle_count=cycle_count)
        clear_pid()


if __name__ == "__main__":
    asyncio.run(run())
