from __future__ import annotations

import pandas as pd

from app_src.adaptive_evidence_v2820 import AdaptiveEvidenceResult
from app_src.shared_account_replay_v2821 import (
    evaluate_shared_account_evidence,
    load_shared_account_policy,
    prepare_portfolio_candidates,
    simulate_shared_account,
)

policy = load_shared_account_policy()
families = ["trend_pullback", "compression_breakout", "range_reversion"]
symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
start = pd.Timestamp("2025-01-01T00:00:00Z")
rows = []

# Ninety independent cycles across roughly six months. In each cycle the router
# selects one profitable family and rejects two losing family candidates. Trades
# do not overlap here, so the synthetic control isolates adaptive selection value.
for i in range(90):
    cycle = start + pd.Timedelta(days=i * 2)
    preferred = families[i % 3]
    symbol = symbols[i % 3]
    for j, family in enumerate(families):
        signal = cycle + pd.Timedelta(hours=j * 3)
        selected = family == preferred
        rows.append(
            {
                "symbol": symbol,
                "research_family": family,
                "strategy_name": f"Synthetic {family}",
                "signal_time": signal,
                "entry_time": signal + pd.Timedelta(hours=1),
                "exit_time": signal + pd.Timedelta(hours=2),
                "side": "LONG",
                "score": 80.0,
                "confidence": 82.0,
                "risk_pct": 1.0,
                "pnl_pct": 1.0 if selected else -0.4,
                "mae_pct": -0.25 if selected else -0.7,
                "state_available": True,
                "router_match": selected,
                "router_risk_multiplier": 1.0 if selected else 0.0,
                "benchmark_only": False,
            }
        )

annotated = pd.DataFrame(rows)
evidence = AdaptiveEvidenceResult(
    job_id="synthetic-v2821",
    family_comparison=pd.DataFrame(),
    pooled_comparison=pd.DataFrame(),
    wait_analysis=pd.DataFrame(),
    friction_stress=pd.DataFrame(),
    stability_by_symbol=pd.DataFrame(),
    stability_by_month=pd.DataFrame(),
    annotated_trades=annotated,
    concurrency={},
    integrity={"ok": True, "timing_integrity_ok": True, "data_integrity_ok": True},
    verdict={"verdict": "promising_research_only"},
    critique=[],
)

result = evaluate_shared_account_evidence(evidence, policy=policy)
if result.verdict["verdict"] != "shared_account_edge_candidate":
    raise AssertionError(f"Expected shared_account_edge_candidate, got {result.verdict}")
if result.adaptive_summary["accepted_trades"] != 90:
    raise AssertionError(f"Expected 90 adaptive trades, got {result.adaptive_summary['accepted_trades']}")
if result.adaptive_summary["return_pct"] <= result.static_summary["return_pct"]:
    raise AssertionError("Adaptive shared account should outperform the synthetic static control")
if result.verdict["return_uplift_pct_points"] <= 0:
    raise AssertionError("Expected positive account-level return uplift")
if result.adaptive_summary["max_gross_exposure_pct"] > float(policy["max_gross_exposure_pct"]) + 1e-6:
    raise AssertionError("Gross exposure exceeded policy")
if result.adaptive_summary["max_open_risk_pct"] > float(policy["max_total_open_risk_pct"]) + 1e-6:
    raise AssertionError("Open risk exceeded policy")

# Benchmark participation must block the production-oriented candidate verdict.
blocked_rows = annotated.copy()
blocked_rows.loc[blocked_rows["research_family"] == "compression_breakout", "benchmark_only"] = True
blocked_evidence = AdaptiveEvidenceResult(
    job_id="synthetic-v2821-benchmark",
    family_comparison=pd.DataFrame(),
    pooled_comparison=pd.DataFrame(),
    wait_analysis=pd.DataFrame(),
    friction_stress=pd.DataFrame(),
    stability_by_symbol=pd.DataFrame(),
    stability_by_month=pd.DataFrame(),
    annotated_trades=blocked_rows,
    concurrency={},
    integrity={"ok": True, "timing_integrity_ok": True, "data_integrity_ok": True},
    verdict={"verdict": "promising_research_only"},
    critique=[],
)
blocked = evaluate_shared_account_evidence(blocked_evidence, policy=policy)
if blocked.verdict["verdict"] != "promising_research_only" or not blocked.verdict["promotion_blocked"]:
    raise AssertionError(f"Benchmark-only accepted trades should block promotion: {blocked.verdict}")

# Simultaneous long candidates must respect one finite same-direction exposure budget.
same_time = pd.DataFrame(
    [
        {
            "symbol": symbol,
            "research_family": families[idx % 3],
            "strategy_name": f"Capacity {idx}",
            "signal_time": start,
            "entry_time": start + pd.Timedelta(hours=1),
            "exit_time": start + pd.Timedelta(hours=5),
            "side": "LONG",
            "score": 90.0 - idx,
            "confidence": 90.0 - idx,
            "risk_pct": 1.0,
            "pnl_pct": 0.5,
            "mae_pct": -0.2,
            "state_available": True,
            "router_match": True,
            "router_risk_multiplier": 1.0,
            "benchmark_only": False,
        }
        for idx, symbol in enumerate(["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    ]
)
capacity = simulate_shared_account(
    prepare_portfolio_candidates(same_time, "adaptive"),
    mode="adaptive",
    policy=policy,
)
if capacity["summary"]["max_same_direction_exposure_pct"] > float(policy["max_same_direction_exposure_pct"]) + 1e-6:
    raise AssertionError("Same-direction exposure exceeded policy")
if capacity["summary"]["accepted_trades"] >= len(same_time):
    raise AssertionError("Finite capital should reject at least one simultaneous candidate")

# Same-time arbitration must use pre-entry score, never future PnL.
priority_policy = dict(policy)
priority_policy["max_concurrent_positions"] = 1
priority_policy["max_same_direction_exposure_pct"] = 100.0
priority_test = pd.DataFrame(
    [
        {
            "symbol": "BTCUSDT",
            "research_family": "trend_pullback",
            "strategy_name": "High score future loser",
            "signal_time": start,
            "entry_time": start + pd.Timedelta(hours=1),
            "exit_time": start + pd.Timedelta(hours=4),
            "side": "LONG",
            "score": 95.0,
            "confidence": 50.0,
            "risk_pct": 1.0,
            "pnl_pct": -1.0,
            "state_available": True,
            "router_match": True,
            "router_risk_multiplier": 1.0,
            "benchmark_only": False,
        },
        {
            "symbol": "ETHUSDT",
            "research_family": "range_reversion",
            "strategy_name": "Low score future winner",
            "signal_time": start,
            "entry_time": start + pd.Timedelta(hours=1),
            "exit_time": start + pd.Timedelta(hours=4),
            "side": "LONG",
            "score": 40.0,
            "confidence": 50.0,
            "risk_pct": 1.0,
            "pnl_pct": 10.0,
            "state_available": True,
            "router_match": True,
            "router_risk_multiplier": 1.0,
            "benchmark_only": False,
        },
    ]
)
priority_run = simulate_shared_account(
    prepare_portfolio_candidates(priority_test, "static"),
    mode="static",
    policy=priority_policy,
)
accepted_name = str(priority_run["ledger"].iloc[0]["strategy_name"])
if accepted_name != "High score future loser":
    raise AssertionError("Same-time arbitration appears to have used future outcome rather than pre-entry score")

print("V28.21 smoke test passed: one-account chronology, risk/exposure caps, static comparison, friction gates and no-outcome-peeking arbitration are available.")
