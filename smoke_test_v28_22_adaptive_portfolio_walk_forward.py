from __future__ import annotations

from copy import deepcopy

import pandas as pd

from app_src.adaptive_evidence_v2820 import AdaptiveEvidenceResult
from app_src.adaptive_portfolio_walk_forward_v2822 import (
    build_freeze_record_from_components,
    evaluate_frozen_portfolio_walk_forward,
    implementation_fingerprints,
    load_portfolio_walk_forward_policy,
    policy_snapshots,
    verify_portfolio_freeze,
)

families = ["trend_pullback", "compression_breakout", "range_reversion"]
symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
start = pd.Timestamp("2025-01-01T00:00:00Z")
rows = []

# One year of deterministic synthetic opportunities. The adaptive router chooses
# one profitable family each cycle while the static baseline also takes two small
# losing trades. Signals are staggered so this test isolates policy selection
# rather than capacity collisions (already covered by V28.21).
for i in range(183):
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
    job_id="synthetic-v2822",
    family_comparison=pd.DataFrame(),
    pooled_comparison=pd.DataFrame(),
    wait_analysis=pd.DataFrame(),
    friction_stress=pd.DataFrame(),
    stability_by_symbol=pd.DataFrame(),
    stability_by_month=pd.DataFrame(),
    annotated_trades=annotated,
    concurrency={},
    integrity={"ok": True, "timing_integrity_ok": True, "data_integrity_ok": True},
    verdict={"verdict": "adaptive_edge_candidate"},
    critique=[],
)

strategies = [
    {
        "research_family": family,
        "strategy_name": f"Synthetic {family}",
        "benchmark_only": False,
        "strategy_payload": {"strategy_name": f"Synthetic {family}", "research_family": family, "version": 1},
        "strategy_sha256": f"synthetic-{family}",
        "source_run_dir": f"synthetic/{family}",
        "timing_integrity_version": "28.18",
    }
    for family in families
]
source = {
    "job_id": "synthetic-v2822",
    "created_at": "2026-09-13T00:00:00+00:00",
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
    "symbols": symbols,
    "entry_timeframe": "1h",
    "analysis_timeframe": "4h",
    "source_root": "synthetic",
}
source_signature = {
    "verdict": {"verdict": "shared_account_edge_candidate"},
    "adaptive_summary": {"return_pct": 10.0},
    "static_summary": {"return_pct": 2.0},
}
freeze = build_freeze_record_from_components(
    source=source,
    strategy_snapshots=strategies,
    policies=policy_snapshots(),
    implementations=implementation_fingerprints(),
    source_shared_account_signature=source_signature,
    created_at="2026-09-13T00:00:00+00:00",
)

verified = verify_portfolio_freeze(freeze, check_current=True)
if not verified["ok"]:
    raise AssertionError(f"Expected intact freeze, got {verified}")

tampered = deepcopy(freeze)
tampered["policies"]["shared_account"]["payload"]["base_risk_per_trade_pct"] = 9.9
tampered_check = verify_portfolio_freeze(tampered, check_current=False)
if tampered_check["ok"]:
    raise AssertionError("Tampering with the frozen capital policy must invalidate the freeze record")

policy = load_portfolio_walk_forward_policy()
result = evaluate_frozen_portfolio_walk_forward(
    evidence,
    freeze,
    walk_forward_policy=policy,
    freeze_verification=verified,
)
if len(result.folds) != 3:
    raise AssertionError(f"Expected three chronological folds, got {len(result.folds)}")
if result.folds["framework_sha256"].nunique() != 1 or result.folds.iloc[0]["framework_sha256"] != freeze["framework_sha256"]:
    raise AssertionError("Every fold must use the exact same frozen framework hash")
if result.verdict["verdict"] != "pass_for_future_freeze":
    raise AssertionError(f"Expected pass_for_future_freeze, got {result.verdict}")
if result.verdict["fold_pass_share"] < 0.99:
    raise AssertionError("Synthetic adaptive edge should pass every chronological fold")
if result.verdict["aggregate_adaptive_return_pct"] <= result.verdict["aggregate_static_return_pct"]:
    raise AssertionError("Adaptive OOS account should outperform the static shared-account baseline")
if result.verdict["aggregate_oos_trades"] < int(policy["min_aggregate_oos_trades"]):
    raise AssertionError("Synthetic OOS sample should exceed the minimum sample gate")

# Benchmark-only participation in an OOS accepted trade must block the pass.
benchmark_rows = annotated.copy()
benchmark_rows.loc[
    (benchmark_rows["research_family"] == "compression_breakout") & benchmark_rows["router_match"],
    "benchmark_only",
] = True
benchmark_evidence = AdaptiveEvidenceResult(
    **{**evidence.__dict__, "annotated_trades": benchmark_rows}
)
benchmark_result = evaluate_frozen_portfolio_walk_forward(
    benchmark_evidence,
    freeze,
    walk_forward_policy=policy,
    freeze_verification=verified,
)
if benchmark_result.verdict["verdict"] == "pass_for_future_freeze" or benchmark_result.verdict["checks"]["benchmark_clear"]:
    raise AssertionError("Accepted benchmark-only OOS trades must block future-freeze promotion")

# A simpler static account that is clearly stronger must be called out explicitly,
# even when the adaptive account itself remains profitable.
static_better_rows = annotated.copy()
static_better_rows["pnl_pct"] = static_better_rows["router_match"].map({True: 0.2, False: 0.8}).astype(float)
static_better_evidence = AdaptiveEvidenceResult(
    **{**evidence.__dict__, "annotated_trades": static_better_rows}
)
static_better_result = evaluate_frozen_portfolio_walk_forward(
    static_better_evidence,
    freeze,
    walk_forward_policy=policy,
    freeze_verification=verified,
)
if static_better_result.verdict["verdict"] != "static_baseline_better_oos":
    raise AssertionError(f"Expected static_baseline_better_oos, got {static_better_result.verdict}")

print("V28.22 smoke test passed: full-framework freeze integrity, chronological portfolio folds, aggregate shared-capital OOS replay, friction gates and static-baseline rejection are available.")
