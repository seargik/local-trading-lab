from __future__ import annotations

from datetime import datetime, timezone
import tempfile

import pandas as pd

# Reuse the already adversarial V28.24 fixture. Importing this module executes
# its full freeze/event/no-retroactive/static-baseline test suite first.
import smoke_test_v28_24_prospective_paper_validation as base
from app_src import prospective_paper_validation_v2824 as core
from app_src import prospective_paper_runtime_v2824 as runtime
from app_src.ohlcv_store import append_candles


paper_freeze = runtime.build_paper_session_freeze(
    base.future_freeze,
    base.future_snapshot,
    now=datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc),
    policy=base.paper_policy,
)
if not paper_freeze.get("runtime_hardening"):
    raise AssertionError("Hardened paper freeze must bind the runtime wrapper hash")
verification = runtime.verify_paper_session_freeze(paper_freeze, check_current=True)
if not verification.get("ok") or not verification.get("runtime_hardening_current_match"):
    raise AssertionError(f"Fresh hardened runtime should verify: {verification}")

# Tampering with the frozen runtime hash is a hard integrity failure even though
# the base V28.24 engine itself has not changed.
tampered = dict(paper_freeze)
tampered["runtime_hardening"] = dict(paper_freeze["runtime_hardening"])
tampered["runtime_hardening"]["sha256"] = "0" * 64
tampered["record_sha256"] = core.paper_freeze_record_hash(tampered)
if runtime.verify_paper_session_freeze(tampered, check_current=True).get("ok"):
    raise AssertionError("Runtime-wrapper drift must invalidate a prospective paper session")

# At the fixed endpoint a quote outage must use the last locally closed candle,
# not silently reset MTM back to entry price.
with tempfile.TemporaryDirectory() as tmp:
    append_candles([
        {
            "exchange": "binance_futures",
            "symbol": "BTCUSDT",
            "interval": "1h",
            "open_time": "2026-05-01T12:00:00+00:00",
            "open": 82.0,
            "high": 83.0,
            "low": 79.0,
            "close": 80.0,
            "volume": 100.0,
            "close_time": "2026-05-01T12:59:59+00:00",
            "is_closed": True,
            "source": "v2824_runtime_smoke",
        }
    ], store_root=tmp)
    state = {
        "counters": {"data_gap_slots": 0},
        "accounts": {
            "static": {"realized_equity_usd": 10000.0, "mtm_equity_usd": 10000.0, "peak_mtm_equity_usd": 10000.0, "max_mtm_drawdown_pct": 0.0, "open_positions": []},
            "adaptive": {
                "realized_equity_usd": 10000.0,
                "mtm_equity_usd": 10000.0,
                "peak_mtm_equity_usd": 10000.0,
                "max_mtm_drawdown_pct": 0.0,
                "open_positions": [{
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "paper_entry_price": 100.0,
                    "quantity": 10.0,
                    "notional_usd": 1000.0,
                    "risk_usd": 20.0,
                    "entry_time": "2026-04-30T12:00:00+00:00",
                    "execution_config": {"fee_bps_per_side": 0.0, "funding_bps_per_8h": 0.0},
                }],
            },
        },
        "last_event_sha256": None,
    }
    quotes = {}
    runtime._ensure_position_quotes(
        paper_freeze,
        state,
        quotes,
        snapshot_time=pd.Timestamp("2026-05-01T13:00:00Z"),
        now_dt=datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
        store_root=tmp,
        quote_provider=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quote unavailable")),
        session_root=tmp,
    )
    q = quotes.get("BTCUSDT") or {}
    if q.get("source") != "fixed_endpoint_closed_bar" or abs(float(q.get("mark_price") or 0) - 80.0) > 1e-9:
        raise AssertionError(f"Endpoint MTM should use local close 80, not entry price: {q}")
    mark = core._mark_account(state["accounts"]["adaptive"], quotes, at_time=pd.Timestamp("2026-05-01T13:00:00Z"))
    if float(mark.get("mtm_equity_usd") or 0) >= 9900.0:
        raise AssertionError(f"A 20% adverse move must be visible in MTM rather than hidden by entry-price fallback: {mark}")

# The hardened runtime path inherits the same anti-hindsight rule: a 30-minute
# late hourly decision is missed, not reconstructed.
with tempfile.TemporaryDirectory() as tmp:
    core.initialize_paper_session(paper_freeze, session_root=tmp)
    original_due = core._due_decisions
    try:
        due_time = pd.Timestamp("2026-04-01T14:00:00Z")
        core._due_decisions = lambda _record, _state, _symbol, _now_ts, store_root: [{
            "decision_time": due_time,
            "candle": {"open_time": "2026-04-01T13:00:00Z", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "volume": 1.0},
        }]
        cycle = runtime.run_prospective_paper_cycle(
            paper_freeze,
            now=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
            session_root=tmp,
            store_root=tmp,
        )
    finally:
        core._due_decisions = original_due
    if not cycle.get("ok") or cycle.get("missed_decision_slots") != 3 or cycle.get("candidates") != 0:
        raise AssertionError(f"Hardened runtime must not reconstruct late signals: {cycle}")
    if cycle.get("mtm_runtime_hardening") != runtime.RUNTIME_HARDENING_VERSION:
        raise AssertionError("Cycle should identify the hardened MTM runtime version")

print("V28.24b smoke test passed: runtime hash freeze, local-close MTM fallback, preallocation runtime and anti-hindsight path are hardened.")
