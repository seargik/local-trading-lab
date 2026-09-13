from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import prospective_paper_validation_v2824 as core
from .ohlcv_store import load_range
from .prospective_paper_signals_v2824 import fetch_or_proxy_quote, normalize_quote
from .settings import OHLCV_STORE_ROOT


def _local_reference_price(
    record: dict[str, Any],
    symbol: str,
    at_time: pd.Timestamp,
    *,
    store_root: str | Path,
    fallback: float,
) -> tuple[float, bool]:
    """Return the latest locally closed price available at or before ``at_time``.

    This prevents MTM from quietly reverting to the position entry price when
    the live quote endpoint is unavailable.  The boolean says whether a local
    close was actually found.
    """
    delta = core._interval_delta(record)
    end_open = at_time - delta
    frame = load_range(
        symbol,
        str(record.get("entry_timeframe") or "1h"),
        end=end_open,
        limit=1,
        store_root=store_root,
    )
    if frame is None or frame.empty:
        return float(fallback), False
    price = core._num(frame.iloc[-1].get("close"), fallback)
    return (float(price) if price > 0 else float(fallback)), bool(price > 0)


def _ensure_position_quotes(
    record: dict[str, Any],
    state: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    *,
    snapshot_time: pd.Timestamp,
    now_dt: datetime,
    store_root: str | Path,
    quote_provider: Callable[..., dict[str, Any]] | None,
    session_root: str | Path,
) -> None:
    symbols: dict[str, float] = {}
    for mode in ["static", "adaptive"]:
        for position in state["accounts"][mode].get("open_positions") or []:
            symbol = str(position.get("symbol") or "")
            if not symbol or symbol in quotes:
                continue
            symbols.setdefault(symbol, core._num(position.get("paper_entry_price"), 0.0))

    target_end = core._utc(record.get("target_end_utc"))
    after_window = bool(not pd.isna(target_end) and pd.Timestamp(now_dt) > target_end)
    for symbol, entry_fallback in symbols.items():
        reference, local_ok = _local_reference_price(
            record,
            symbol,
            snapshot_time,
            store_root=store_root,
            fallback=entry_fallback,
        )
        if after_window:
            # Never use a quote observed after the fixed endpoint to value the
            # endpoint.  Use the final locally closed bar instead.
            quote = normalize_quote(symbol, None, reference_price=reference, now=target_end.to_pydatetime())
            quote["source"] = "fixed_endpoint_closed_bar"
        else:
            quote = fetch_or_proxy_quote(
                symbol,
                reference_price=reference,
                now=now_dt,
                quote_provider=quote_provider,
            )
            if not quote.get("executable_top_of_book") and local_ok:
                quote["source"] = "latest_closed_bar_proxy"
        quotes[symbol] = quote
        if not local_ok:
            state["counters"]["data_gap_slots"] = int(state["counters"].get("data_gap_slots") or 0) + 1
            core._append_event(
                record,
                state,
                "MTM_LOCAL_PRICE_GAP",
                {
                    "symbol": symbol,
                    "snapshot_time": snapshot_time.isoformat(),
                    "fallback_entry_price": entry_fallback,
                    "quote_source": quote.get("source"),
                },
                recorded_at=now_dt,
                session_root=session_root,
            )


def run_prospective_paper_cycle(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    quote_provider: Callable[..., dict[str, Any]] | None = None,
    store_root: str | Path | None = None,
    session_root: str | Path = core.SESSION_ROOT,
) -> dict[str, Any]:
    """Run the evidence-grade V28.24 cycle with current MTM before allocation.

    The base engine owns the evidence model and append-only ledger.  This runtime
    wrapper hardens two economic details:

    * existing positions are marked before new risk is sized, so allocation does
      not use stale prior-cycle equity;
    * quote outages and post-window evaluation use the latest locally closed
      candle rather than silently marking positions back at entry.
    """
    policy = core._paper_policy(record)
    verification = core.verify_paper_session_freeze(
        record,
        check_current=bool(policy.get("require_current_framework_match", True)),
    )
    if not verification.get("ok", False):
        return {"ok": False, "reason": "paper_freeze_integrity_failed", "verification": verification}
    state = core.load_paper_state(record, session_root=session_root)
    chain = core.verify_event_chain(record, session_root=session_root)
    if bool(policy.get("require_event_chain_integrity", True)) and not chain.get("ok", False):
        return {"ok": False, "reason": "event_chain_integrity_failed", "event_chain": chain}

    now_dt = core._now_utc(now)
    now_ts = pd.Timestamp(now_dt)
    target_end = core._utc(record.get("target_end_utc"))
    root = str(store_root or record.get("source_root") or OHLCV_STORE_ROOT)
    lag_limit = pd.Timedelta(minutes=max(1, int(policy.get("max_decision_lag_minutes", 10))))
    candidates: list[dict[str, Any]] = []
    quotes: dict[str, dict[str, Any]] = {}
    processed_now = 0
    missed_now = 0
    gaps_now = 0

    through = min(
        now_ts.floor(f"{max(1, core.timeframe_minutes(str(record.get('entry_timeframe') or '1h')))}min"),
        target_end,
    )
    first = core._utc(record.get("first_eligible_decision_utc"))
    if not pd.isna(first) and through >= first:
        core._update_virtual_positions(record, state, through, store_root=root, session_root=session_root)
        core._update_account_positions(record, state, "static", through, store_root=root, session_root=session_root)
        core._update_account_positions(record, state, "adaptive", through, store_root=root, session_root=session_root)

    for symbol in record.get("symbols") or []:
        symbol = str(symbol).upper()
        due_rows = core._due_decisions(record, state, symbol, now_ts, store_root=root)
        for due in due_rows:
            decision_time = pd.Timestamp(due["decision_time"])
            candle = due.get("candle")
            lag = now_ts - decision_time
            state.setdefault("processed_decisions", {})[symbol] = decision_time.isoformat()
            if candle is None:
                if lag <= lag_limit:
                    state["processed_decisions"].pop(symbol, None)
                    break
                state["counters"]["data_gap_slots"] = int(state["counters"].get("data_gap_slots") or 0) + 1
                state["counters"]["missed_decision_slots"] = int(state["counters"].get("missed_decision_slots") or 0) + 1
                gaps_now += 1
                missed_now += 1
                core._append_event(
                    record,
                    state,
                    "DATA_GAP_MISSED_DECISION",
                    {"symbol": symbol, "decision_time": decision_time.isoformat(), "lag_minutes": round(lag.total_seconds() / 60.0, 2)},
                    recorded_at=now_ts,
                    session_root=session_root,
                )
                continue
            if lag > lag_limit:
                state["counters"]["missed_decision_slots"] = int(state["counters"].get("missed_decision_slots") or 0) + 1
                missed_now += 1
                core._append_event(
                    record,
                    state,
                    "MISSED_DECISION",
                    {
                        "symbol": symbol,
                        "decision_time": decision_time.isoformat(),
                        "lag_minutes": round(lag.total_seconds() / 60.0, 2),
                        "retroactive_signal_created": False,
                    },
                    recorded_at=now_ts,
                    session_root=session_root,
                )
                continue

            reference = core._num(candle.get("close"), 0.0)
            quote = fetch_or_proxy_quote(
                symbol,
                reference_price=reference,
                now=now_dt,
                quote_provider=quote_provider,
            )
            quotes[symbol] = quote
            state["counters"]["recorded_decision_slots"] = int(state["counters"].get("recorded_decision_slots") or 0) + 1
            if quote.get("executable_top_of_book"):
                state["counters"]["executable_quote_slots"] = int(state["counters"].get("executable_quote_slots") or 0) + 1
            else:
                state["counters"]["proxy_quote_slots"] = int(state["counters"].get("proxy_quote_slots") or 0) + 1
            processed_now += 1
            core._append_event(
                record,
                state,
                "DECISION_SLOT_RECORDED",
                {
                    "symbol": symbol,
                    "decision_time": decision_time.isoformat(),
                    "lag_minutes": round(lag.total_seconds() / 60.0, 2),
                    "quote": quote,
                },
                recorded_at=now_ts,
                session_root=session_root,
            )
            candidates.extend(
                core._candidate_stream_for_decision(
                    record,
                    state,
                    symbol,
                    decision_time,
                    quote,
                    store_root=root,
                    session_root=session_root,
                )
            )

    snapshot_time = min(now_ts, target_end)

    # Price every already-open position before sizing new entries.  This makes
    # the risk budget respond to current paper losses/gains rather than to the
    # previous cycle's equity.
    _ensure_position_quotes(
        record,
        state,
        quotes,
        snapshot_time=snapshot_time,
        now_dt=now_dt,
        store_root=root,
        quote_provider=quote_provider,
        session_root=session_root,
    )
    for mode in ["static", "adaptive"]:
        core._mark_account(state["accounts"][mode], quotes, at_time=snapshot_time)

    if candidates:
        entry_times = [core._utc(c.get("entry_time")) for c in candidates]
        decision_for_alloc = max([x for x in entry_times if not pd.isna(x)], default=through)
        core._allocate_candidates(record, state, "static", candidates, decision_for_alloc, session_root=session_root)
        core._allocate_candidates(record, state, "adaptive", candidates, decision_for_alloc, session_root=session_root)

    # New positions normally have a quote from their decision slot.  Re-run the
    # coverage helper for completeness before the final MTM snapshot.
    _ensure_position_quotes(
        record,
        state,
        quotes,
        snapshot_time=snapshot_time,
        now_dt=now_dt,
        store_root=root,
        quote_provider=quote_provider,
        session_root=session_root,
    )

    marks: dict[str, Any] = {}
    for mode in ["static", "adaptive"]:
        marks[mode] = core._mark_account(state["accounts"][mode], quotes, at_time=snapshot_time)
        core._append_event(
            record,
            state,
            "MTM_SNAPSHOT",
            {"mode": mode, "snapshot_time": snapshot_time.isoformat(), **marks[mode]},
            recorded_at=now_ts,
            session_root=session_root,
        )

    state["counters"]["cycles"] = int(state["counters"].get("cycles") or 0) + 1
    state["last_cycle_at"] = now_ts.isoformat()
    if now_ts > target_end:
        state["status"] = "paper_window_complete"
    core.atomic_write_json(core._state_path(record, session_root), state)
    return {
        "ok": True,
        "session_id": record.get("session_id"),
        "processed_decision_slots": processed_now,
        "missed_decision_slots": missed_now,
        "data_gap_slots": gaps_now,
        "candidates": len(candidates),
        "adaptive": marks.get("adaptive"),
        "static": marks.get("static"),
        "target_end_utc": record.get("target_end_utc"),
        "status": state.get("status"),
        "mtm_runtime_hardening": "28.24-local-close-fallback-preallocation-mtm",
    }
