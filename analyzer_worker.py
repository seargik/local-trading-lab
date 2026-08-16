from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from app_src.analysis_core import current_trade_signal_state, evaluate_open_trades, run_scanner, slot_fingerprint
from app_src.app_state import load_user_config
from app_src.engine import normalize_slot_rows
from app_src.runtime_state import atomic_write_json, read_json
from app_src.settings import (
    ANALYSIS_REQUEST_PATH,
    ANALYZER_PID_PATH,
    ANALYZER_POLL_SECONDS,
    ANALYZER_STATE_PATH,
    DEFAULT_ANALYSIS_TIMEFRAME,
    DEFAULT_CHART_TIMEFRAME,
    DEFAULT_SELECTED_SYMBOLS,
    LAB_DB_PATH,
)
from app_src.storage import Storage

STOP = False


def _handle_stop(*_args):
    global STOP
    STOP = True


for sig_name in ("SIGINT", "SIGTERM"):
    if hasattr(signal, sig_name):
        signal.signal(getattr(signal, sig_name), _handle_stop)


def project_root() -> Path:
    return Path(__file__).resolve().parent


def pid_path() -> Path:
    return (project_root() / ANALYZER_PID_PATH).resolve()


def state_path() -> Path:
    return (project_root() / ANALYZER_STATE_PATH).resolve()


def request_path() -> Path:
    return (project_root() / ANALYSIS_REQUEST_PATH).resolve()


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


def resolve_runtime() -> tuple[list[str], str, str, bool, bool]:
    cfg = load_user_config()
    symbols = [s.upper() for s in cfg.get("selected_symbols", DEFAULT_SELECTED_SYMBOLS)]
    analysis_timeframe = str(cfg.get("analysis_timeframe") or cfg.get("timeframe") or DEFAULT_ANALYSIS_TIMEFRAME)
    chart_timeframe = str(cfg.get("chart_timeframe") or DEFAULT_CHART_TIMEFRAME)
    auto_paper = bool(cfg.get("auto_paper_mode", False))
    live_bundle_mode = bool(cfg.get("live_bundle_mode", False))
    return symbols, analysis_timeframe, chart_timeframe, auto_paper, live_bundle_mode


def consume_request() -> dict | None:
    rp = request_path()
    if not rp.exists():
        return None
    payload = read_json(rp, {})
    try:
        rp.unlink()
    except Exception:
        pass
    return payload or {"reason": "manual"}


async def run() -> None:
    storage = Storage(LAB_DB_PATH)
    started_at = datetime.now(timezone.utc).isoformat()
    analysis_timeframe = DEFAULT_ANALYSIS_TIMEFRAME
    chart_timeframe = DEFAULT_CHART_TIMEFRAME
    last_analyzed_signature: dict[str, str | None] = {}
    last_slot_fp = ""
    last_run_at = None
    last_analyzed_candle_time = None
    last_error = None

    def write_state(**overrides):
        atomic_write_json(
            state_path(),
            {
                "running": overrides.get("running", True),
                "pid": os.getpid(),
                "symbols": overrides.get("symbols"),
                "timeframe": overrides.get("analysis_timeframe"),
                "analysis_timeframe": overrides.get("analysis_timeframe"),
                "chart_timeframe": overrides.get("chart_timeframe"),
                "started_at": started_at,
                "last_run_at": overrides.get("last_run_at", last_run_at),
                "last_analyzed_candle_time": overrides.get("last_analyzed_candle_time", last_analyzed_candle_time),
                "last_error": overrides.get("last_error", last_error),
                "analysis_stale": overrides.get("analysis_stale", False),
                "live_bundle_mode": overrides.get("live_bundle_mode", False),
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    write_pid()
    write_state(running=True, analysis_stale=True, analysis_timeframe=DEFAULT_ANALYSIS_TIMEFRAME, chart_timeframe=DEFAULT_CHART_TIMEFRAME)

    try:
        while not STOP:
            symbols, analysis_timeframe, chart_timeframe, auto_paper, live_bundle_mode = resolve_runtime()
            slot_rows = normalize_slot_rows(storage.get_active_slots())
            slot_fp = slot_fingerprint(slot_rows) + f"|live_bundle_mode={int(live_bundle_mode)}"
            signature = storage.get_latest_closed_candle_signature(symbols, analysis_timeframe)
            latest_candle = max([v for v in signature.values() if v], default=None)
            request_payload = consume_request()
            should_run = False
            reason = None
            if request_payload:
                should_run = True
                reason = request_payload.get("reason", "manual")
            elif signature and signature != last_analyzed_signature:
                should_run = True
                reason = "new_closed_candle"
            elif slot_fp != last_slot_fp:
                should_run = True
                reason = "strategy_change"

            if should_run:
                try:
                    evaluate_open_trades(storage)
                    scanner_rows, analysis_map = run_scanner(storage, analysis_timeframe, symbols, slot_rows, auto_paper, live_bundle_mode=live_bundle_mode)
                    score_map = current_trade_signal_state(analysis_map)
                    storage.update_open_trade_live_scores(score_map)
                    meta = {
                        "last_run_at": datetime.now(timezone.utc).isoformat(),
                        "timeframe": analysis_timeframe,
                        "analysis_timeframe": analysis_timeframe,
                        "chart_timeframe": chart_timeframe,
                        "symbols": symbols,
                        "slot_fingerprint": slot_fp,
                        "latest_closed_signature": signature,
                        "latest_closed_candle_time": latest_candle,
                        "reason": reason,
                        "live_bundle_mode": live_bundle_mode,
                    }
                    storage.write_analysis_cache(scanner_rows, analysis_map, meta)
                    last_analyzed_signature = signature
                    last_slot_fp = slot_fp
                    last_run_at = meta["last_run_at"]
                    last_analyzed_candle_time = latest_candle
                    last_error = None
                    write_state(running=True, symbols=symbols, analysis_timeframe=analysis_timeframe, chart_timeframe=chart_timeframe, last_run_at=last_run_at, last_analyzed_candle_time=last_analyzed_candle_time, last_error=None, analysis_stale=False, live_bundle_mode=live_bundle_mode)
                except Exception as exc:
                    last_error = str(exc)
                    write_state(running=True, symbols=symbols, analysis_timeframe=analysis_timeframe, chart_timeframe=chart_timeframe, last_error=last_error, analysis_stale=True, live_bundle_mode=live_bundle_mode)
            else:
                analysis_stale = bool(signature and signature != last_analyzed_signature)
                write_state(running=True, symbols=symbols, analysis_timeframe=analysis_timeframe, chart_timeframe=chart_timeframe, analysis_stale=analysis_stale, live_bundle_mode=live_bundle_mode)
            await asyncio.sleep(ANALYZER_POLL_SECONDS)
    finally:
        write_state(running=False, analysis_stale=True, last_error="Stopped" if STOP else last_error, analysis_timeframe=analysis_timeframe, chart_timeframe=chart_timeframe)
        clear_pid()


if __name__ == "__main__":
    asyncio.run(run())
