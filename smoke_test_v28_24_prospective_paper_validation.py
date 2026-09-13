from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pandas as pd

from app_src.adaptive_evidence_v2820 import CORE_FAMILIES
from app_src.adaptive_portfolio_walk_forward_v2822 import build_freeze_record_from_components
from app_src.future_adaptive_portfolio_holdout_v2823 import (
    _canonical_hash,
    build_future_freeze_record_from_components,
    current_implementation_fingerprints,
    current_policy_snapshots,
    load_future_holdout_policy,
    verify_future_freeze,
)
import app_src.prospective_paper_validation_v2824 as pp
from app_src.prospective_paper_signals_v2824 import executable_entry_price, materialize_candidate
from app_src.runtime_state import atomic_write_json


# Build a synthetic but internally valid V28.22 -> V28.23 chain. No historical
# PnL is consulted by the prospective V28.24 mechanics below.
future_policy = load_future_holdout_policy()
current_policies = current_policy_snapshots(future_policy)
source_policies = {k: v for k, v in current_policies.items() if k != "future_holdout"}
strategy_snapshots = []
execution_configs = []
for family in CORE_FAMILIES:
    payload = {
        "strategy_name": f"Synthetic {family}",
        "research_family": family,
        "benchmark_only": False,
        "score_threshold": 70,
        "expected_rr": "1:2",
        "rule_params": {"score_threshold": 70, "stop_multiplier": 1.0},
    }
    strategy_snapshots.append({
        "research_family": family,
        "strategy_name": payload["strategy_name"],
        "benchmark_only": False,
        "strategy_payload": payload,
        "strategy_sha256": _canonical_hash(payload),
        "source_run_dir": f"synthetic/{family}",
        "timing_integrity_version": "28.18",
    })
    cfg = {
        "fixed_stake_usd": 100.0,
        "fee_bps_per_side": 4.0,
        "spread_bps_round_trip": 2.0,
        "slippage_bps_per_side": 2.0,
        "funding_bps_per_8h": 0.0,
        "timing_integrity_version": "28.18",
        "entry_mode": "next_open",
        "max_hold_bars": 72,
        "cooldown_bars": 2,
    }
    execution_configs.append({"research_family": family, "config": cfg, "config_sha256": _canonical_hash(cfg)})

source_signature = {
    "verdict": {"verdict": "shared_account_edge_candidate"},
    "adaptive_summary": {"return_pct": 12.0, "profit_factor": 1.4},
    "static_summary": {"return_pct": 8.0, "profit_factor": 1.2},
}
source_freeze = build_freeze_record_from_components(
    source={
        "job_id": "synthetic-source-v2824",
        "created_at": "2026-01-01T00:00:00+00:00",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "entry_timeframe": "1h",
        "analysis_timeframe": "4h",
        "source_root": "data/ohlcv_store",
    },
    strategy_snapshots=strategy_snapshots,
    policies=source_policies,
    implementations={"synthetic_source_impl": {"path": "synthetic", "sha256": "abc123"}},
    source_shared_account_signature=source_signature,
    created_at="2026-01-05T00:00:00+00:00",
)
walk_signature = {
    "freeze_id": source_freeze["freeze_id"],
    "verdict": {
        "verdict": "pass_for_future_freeze",
        "framework_sha256": source_freeze["framework_sha256"],
        "aggregate_adaptive_return_pct": 9.0,
        "aggregate_static_return_pct": 5.0,
    },
    "folds": [{"fold_id": "wf_1", "fold_pass": True}, {"fold_id": "wf_2", "fold_pass": True}, {"fold_id": "wf_3", "fold_pass": True}],
    "aggregate_comparison": [],
    "friction_stress": [],
    "integrity": {"freeze": {"ok": True}},
}
future_freeze = build_future_freeze_record_from_components(
    source_portfolio_freeze=source_freeze,
    source_walk_forward_signature=walk_signature,
    execution_configs=execution_configs,
    policies=current_policies,
    implementations=current_implementation_fingerprints(),
    future_policy=future_policy,
    now=datetime(2026, 1, 10, 12, 30, tzinfo=timezone.utc),
)
if not verify_future_freeze(future_freeze, check_current=True)["ok"]:
    raise AssertionError("Synthetic source V28.23 freeze must be valid under current code/policies")

future_snapshot = {
    "schema_version": "28.23-future-holdout-snapshot-v1",
    "freeze_id": future_freeze["freeze_id"],
    "freeze_record_sha256": future_freeze["record_sha256"],
    "future_framework_sha256": future_freeze["future_framework_sha256"],
    "verdict": {
        "verdict": "pass_for_frozen_paper",
        "adaptive_return_pct": 8.5,
        "static_return_pct": 5.0,
        "adaptive_profit_factor": 1.35,
    },
    "integrity": {"freeze": {"ok": True}, "job": {"ok": True}},
}

paper_policy = pp.load_paper_policy()
paper_freeze = pp.build_paper_session_freeze(
    future_freeze,
    future_snapshot,
    now=datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc),
    policy=paper_policy,
)
if paper_freeze["first_eligible_decision_utc"] != "2026-04-01T14:00:00+00:00":
    raise AssertionError(f"First paper decision must follow one completely unseen 1h bar: {paper_freeze['first_eligible_decision_utc']}")
if paper_freeze["target_end_utc"] != "2026-05-01T13:00:00+00:00":
    raise AssertionError(f"Expected fixed 30-day paper endpoint, got {paper_freeze['target_end_utc']}")
if paper_freeze.get("live_execution_enabled") is not False:
    raise AssertionError("V28.24 must never enable live execution")
verification = pp.verify_paper_session_freeze(paper_freeze, check_current=True)
if not verification["ok"]:
    raise AssertionError(f"Fresh V28.24 paper freeze should verify: {verification}")

# Freeze tampering must be visible.
tampered = dict(paper_freeze)
tampered["target_end_utc"] = "2026-04-20T13:00:00+00:00"
if pp.verify_paper_session_freeze(tampered, check_current=False)["ok"]:
    raise AssertionError("Changing the precommitted paper endpoint must invalidate the freeze")

# Same passed future result cannot quietly restart the paper clock later.
with tempfile.TemporaryDirectory() as tmp:
    pp.save_paper_session_freeze(paper_freeze, output_dir=tmp)
    restarted = pp.build_paper_session_freeze(
        future_freeze,
        future_snapshot,
        now=datetime(2026, 4, 5, 9, 15, tzinfo=timezone.utc),
        policy=paper_policy,
    )
    try:
        pp.save_paper_session_freeze(restarted, output_dir=tmp)
        raise AssertionError("Restarting the same passed V28.23 paper clock should be refused")
    except ValueError as exc:
        if "already has" not in str(exc).lower() and "restart" not in str(exc).lower():
            raise

# Top-of-book entry is deliberately worse than the closed-candle reference for
# the taker side and is recorded as an adverse slippage diagnostic.
quote = {
    "best_bid": 99.9,
    "best_ask": 100.1,
    "mark_price": 100.0,
    "spread_bps": 20.0,
    "executable_top_of_book": True,
    "source": "synthetic_top_of_book",
}
entry, meta = executable_entry_price("LONG", quote, 100.0, {"slippage_bps_per_side": 2.0})
if not (entry > 100.1 and meta["entry_quote_observed"]):
    raise AssertionError("Observed LONG paper entry should pay ask plus configured adverse slippage")

candidate = materialize_candidate({
    "symbol": "BTCUSDT",
    "decision_time": "2026-04-01T14:00:00+00:00",
    "research_family": "trend_pullback",
    "strategy_name": "Synthetic trend",
    "strategy_sha256": "abc",
    "benchmark_only": False,
    "side": "LONG",
    "score": 82.0,
    "signal_allowed": True,
    "router_match": True,
    "router_confidence": 80.0,
    "router_risk_multiplier": 0.75,
    "reference_price": 100.0,
    "execution_config": {"slippage_bps_per_side": 2.0, "spread_bps_round_trip": 2.0},
    "strategy_payload": {"strategy_name": "Synthetic trend", "expected_rr": "1:2", "rule_params": {"stop_multiplier": 1.0}},
    "features": {"close": 100.0, "range_position_20": 0.5, "atr_14": 1.0, "atr_pct": 0.01},
}, quote)
if not candidate or candidate["risk_distance_pct"] <= 0 or candidate["paper_entry_price"] <= 100.0:
    raise AssertionError("Prospective candidate should have executable paper price and stop-distance risk")

# Event log is a hash chain; modifying an old event must be detected.
with tempfile.TemporaryDirectory() as tmp:
    state = pp.initialize_paper_session(paper_freeze, session_root=tmp)
    chain = pp.verify_event_chain(paper_freeze, session_root=tmp)
    if not chain["ok"] or chain["events"] != 1:
        raise AssertionError(f"Fresh event chain should contain one valid SESSION_STARTED event: {chain}")
    event_path = pp._events_path(paper_freeze, tmp)
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    event["payload"]["live_execution_enabled"] = True
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    if pp.verify_event_chain(paper_freeze, session_root=tmp)["ok"]:
        raise AssertionError("Historical paper-event tampering must invalidate the chain")

# A late cycle must record the slot as MISSED and must never manufacture the
# signal after seeing more market data.
with tempfile.TemporaryDirectory() as tmp:
    pp.initialize_paper_session(paper_freeze, session_root=tmp)
    original_due = pp._due_decisions
    try:
        due_time = pd.Timestamp("2026-04-01T14:00:00Z")
        pp._due_decisions = lambda _record, _state, _symbol, _now_ts, store_root: [{
            "decision_time": due_time,
            "candle": {"open_time": "2026-04-01T13:00:00Z", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "volume": 1.0},
        }]
        cycle = pp.run_prospective_paper_cycle(
            paper_freeze,
            now=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
            session_root=tmp,
            store_root=tmp,
        )
    finally:
        pp._due_decisions = original_due
    if not cycle.get("ok") or cycle.get("candidates") != 0 or cycle.get("missed_decision_slots") != 3:
        raise AssertionError(f"Late decisions must be missed, not backfilled: {cycle}")
    events = pp._read_events(pp._events_path(paper_freeze, tmp))
    missed_events = [e for e in events if e.get("event_type") == "MISSED_DECISION"]
    if len(missed_events) != 3 or any((e.get("payload") or {}).get("retroactive_signal_created") is not False for e in missed_events):
        raise AssertionError("Missed-decision evidence must explicitly record that no retroactive signal was created")


def synthetic_trades(*, strong: bool) -> list[dict]:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    families = list(CORE_FAMILIES)
    rows = []
    for i in range(20):
        loser = i in {3, 7, 11, 15, 19}
        pnl = (-10.0 if loser else (30.0 if strong else 20.0))
        rows.append({
            "candidate_id": f"t{i}",
            "symbol": symbols[i % len(symbols)],
            "research_family": families[i % len(families)],
            "notional_usd": 250.0,
            "net_pnl_usd": pnl,
            "benchmark_only": False,
            "entry_adverse_slippage_vs_reference_bps": 5.0,
        })
    return rows


def write_synthetic_state(tmp: str, *, adaptive_equity: float, static_equity: float, static_strong: bool) -> None:
    state = pp.load_paper_state(paper_freeze, session_root=tmp)
    adaptive_trades = synthetic_trades(strong=False)
    static_trades = synthetic_trades(strong=static_strong)
    state["accounts"]["adaptive"].update({
        "realized_equity_usd": adaptive_equity,
        "mtm_equity_usd": adaptive_equity,
        "peak_mtm_equity_usd": max(10000.0, adaptive_equity + 150.0),
        "max_mtm_drawdown_pct": 4.0,
        "open_positions": [],
        "closed_trades": adaptive_trades,
        "accepted_trades": 20,
        "rejected_candidates": 3,
    })
    state["accounts"]["static"].update({
        "realized_equity_usd": static_equity,
        "mtm_equity_usd": static_equity,
        "peak_mtm_equity_usd": max(10000.0, static_equity + 150.0),
        "max_mtm_drawdown_pct": 5.0,
        "open_positions": [],
        "closed_trades": static_trades,
        "accepted_trades": 20,
        "rejected_candidates": 2,
    })
    state["counters"].update({
        "recorded_decision_slots": 120,
        "missed_decision_slots": 0,
        "data_gap_slots": 0,
        "executable_quote_slots": 120,
        "proxy_quote_slots": 0,
    })
    atomic_write_json(pp._state_path(paper_freeze, tmp), state)

# A strong prospective adaptive account can only reach MICRO-LIVE REVIEW; it
# still does not flip the live-execution bit.
with tempfile.TemporaryDirectory() as tmp:
    pp.initialize_paper_session(paper_freeze, session_root=tmp)
    write_synthetic_state(tmp, adaptive_equity=10250.0, static_equity=10120.0, static_strong=False)
    result = pp.evaluate_paper_session(
        paper_freeze,
        now=datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
        session_root=tmp,
    )
    if result.verdict["verdict"] != "pass_for_micro_live_review":
        raise AssertionError(f"Expected strong synthetic prospective evidence to pass for manual review only: {result.verdict}")
    if result.integrity.get("live_execution_enabled") is not False:
        raise AssertionError("Passing paper evidence must not enable live execution")

# Sophisticated adaptation gets no credit if the simpler static shadow account
# is better on both return and PF.
with tempfile.TemporaryDirectory() as tmp:
    pp.initialize_paper_session(paper_freeze, session_root=tmp)
    write_synthetic_state(tmp, adaptive_equity=10100.0, static_equity=10300.0, static_strong=True)
    result = pp.evaluate_paper_session(
        paper_freeze,
        now=datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
        session_root=tmp,
    )
    if result.verdict["verdict"] != "static_baseline_better_paper":
        raise AssertionError(f"Static outperformance must be a first-class prospective rejection: {result.verdict}")

print("V28.24 smoke test passed: prospective freeze, anti-restart, tamper-evident events, no retroactive signals, paper MTM gates and static-baseline rejection are available.")
