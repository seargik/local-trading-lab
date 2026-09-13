from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from app_src.adaptive_evidence_report_v2820 import policy_fingerprints, save_adaptive_evidence_snapshot
from app_src.adaptive_evidence_v2820 import (
    annotate_trades_with_router,
    evaluate_adaptive_trade_frames,
    load_adaptive_evidence_policy,
)

policy = load_adaptive_evidence_policy()
families = ["trend_pullback", "compression_breakout", "range_reversion"]
start = pd.Timestamp("2025-01-01T00:00:00Z")

replay_rows = []
family_rows = {family: [] for family in families}
for i in range(90):
    ts = start + pd.Timedelta(days=i * 2)
    symbol = "BTCUSDT" if i % 2 == 0 else "ETHUSDT"
    preferred = families[i % len(families)]
    replay_rows.append(
        {
            "symbol": symbol,
            "decision_time": ts,
            "market_state": "trend_pullback_entry" if preferred == "trend_pullback" else ("breakout_attempt" if preferred == "compression_breakout" else "range_chop"),
            "state_label": preferred,
            "preferred_strategy_family": preferred,
            "router_action": "TRADE_CANDIDATE",
            "router_direction": "LONG",
            "risk_multiplier": 1.0,
            "confidence": 82.0,
            "lookahead_ok": True,
        }
    )
    for family in families:
        selected = family == preferred
        pnl_usd = 1.0 if selected else -0.5
        family_rows[family].append(
            {
                "symbol": symbol,
                "signal_time": ts,
                "entry_time": ts + pd.Timedelta(minutes=5),
                "exit_time": ts + pd.Timedelta(hours=1),
                "side": "LONG",
                "static_stake_usd": 100.0,
                "static_pnl_usd": pnl_usd,
                "pre_friction_pnl_usd": pnl_usd + 0.05,
                "execution_cost_usd": 0.05,
                "r_multiple": pnl_usd,
                "research_family": family,
                "strategy_name": f"Synthetic {family}",
                "benchmark_only": False,
            }
        )

replay = pd.DataFrame(replay_rows)
frames = {family: pd.DataFrame(rows) for family, rows in family_rows.items()}
meta = {
    family: {"strategy_name": f"Synthetic {family}", "benchmark_only": False}
    for family in families
}
integrity = {"timing_integrity_ok": True, "data_integrity_ok": True, "ok": True}

result = evaluate_adaptive_trade_frames(
    frames,
    replay,
    family_meta=meta,
    policy=policy,
    integrity=integrity,
)
result.job_id = "synthetic-v28-20"

if result.verdict["verdict"] != "adaptive_edge_candidate":
    raise AssertionError(f"Expected adaptive edge candidate, got {result.verdict}")
if result.verdict["adaptive_trades"] != 90:
    raise AssertionError(f"Expected 90 selected trades, got {result.verdict['adaptive_trades']}")
if result.verdict["profit_factor_uplift_vs_static_pool"] <= 0:
    raise AssertionError("Adaptive profit factor should improve over static pool in synthetic control")
if result.verdict["expectancy_uplift_bps_vs_static_pool"] <= 0:
    raise AssertionError("Adaptive exposure-normalized expectancy should improve in synthetic control")
if float(result.wait_analysis["wait_value_usd"].sum()) <= 0:
    raise AssertionError("WAIT should add value by excluding synthetic losing trades")
if float(result.friction_stress.iloc[-1]["total_pnl_usd"]) <= 0:
    raise AssertionError("Synthetic adaptive evidence should remain positive under the configured friction stress")

# A future state must never be joined backward into an earlier signal.
one_trade = frames["trend_pullback"].iloc[[0]].copy()
future_replay = replay.iloc[[0]].copy()
future_replay["decision_time"] = one_trade.iloc[0]["signal_time"] + pd.Timedelta(hours=1)
annotated = annotate_trades_with_router(one_trade, future_replay, family="trend_pullback", policy=policy)
if bool(annotated.iloc[0]["state_available"]) or bool(annotated.iloc[0]["router_match"]):
    raise AssertionError("Future router state leaked into an earlier trade")

# Benchmark-only family participation must block promotion even when metrics are strong.
blocked_frames = {key: value.copy() for key, value in frames.items()}
blocked_frames["compression_breakout"]["benchmark_only"] = True
blocked_meta = dict(meta)
blocked_meta["compression_breakout"] = {"strategy_name": "Synthetic compression", "benchmark_only": True}
blocked = evaluate_adaptive_trade_frames(
    blocked_frames,
    replay,
    family_meta=blocked_meta,
    policy=policy,
    integrity=integrity,
)
if blocked.verdict["verdict"] != "promising_research_only" or not blocked.verdict["promotion_blocked"]:
    raise AssertionError(f"Benchmark-only compression should block promotion: {blocked.verdict}")

# Reproducibility: every economic snapshot must carry fingerprints for all adaptive policies.
fingerprints = policy_fingerprints()
for key in ["adaptive_evidence", "market_state_router", "market_state_replay"]:
    if not fingerprints.get(key, {}).get("sha256"):
        raise AssertionError(f"Missing policy fingerprint for {key}: {fingerprints}")

with tempfile.TemporaryDirectory() as tmp:
    report = save_adaptive_evidence_snapshot(
        result,
        source_job={
            "job_id": result.job_id,
            "created_at": "2026-09-13T00:00:00Z",
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "entry_timeframe": "1h",
            "analysis_timeframe": "4h",
            "start_date": "2025-01-01",
            "end_date": "2025-07-01",
            "source_root": "synthetic",
        },
        policy=policy,
        output_dir=Path(tmp),
    )
    manifest_path = report / "manifest.json"
    trades_path = report / "annotated_trades.parquet"
    if not manifest_path.exists() or not trades_path.exists():
        raise AssertionError(f"Evidence snapshot is incomplete: {report}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("combined_policy_sha256"):
        raise AssertionError("Evidence snapshot is missing combined policy hash")
    if manifest.get("source_job_id") != result.job_id:
        raise AssertionError(f"Evidence snapshot lost source job identity: {manifest}")

print("V28.20 smoke test passed: causal router joins, WAIT value, economic uplift, friction stress, promotion blocks and reproducible evidence snapshots are available.")
