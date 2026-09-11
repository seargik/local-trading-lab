from __future__ import annotations

import compileall
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from app_src.lifecycle_gate_v28 import run_lifecycle_gate_lab


def _feature_payload() -> dict[str, object]:
    return {
        "trend_regime_score": 62.0,
        "range_regime_score": 20.0,
        "squeeze_regime_score": 15.0,
        "panic_regime_score": 10.0,
        "adx_14": 25.0,
        "rsi_14": 55.0,
        "atr_pct": 0.01,
        "bb_width_pct": 0.02,
        "vwap_distance_pct": 0.005,
        "range_position_20": 0.5,
        "trend_distance_pct": 0.01,
        "breakout_above_n_bar_high": False,
        "breakout_below_n_bar_low": False,
        "liquidity_sweep_high": False,
        "liquidity_sweep_low": False,
        "volume_spike": False,
        "ma_stack_state": "bullish",
        "local_trend": "up",
        "global_trend": "up",
        "htf_alignment": "bullish",
        "slope_pct_10": 0.2,
        "ema_20_50_spread_pct": 0.01,
    }


def _trades() -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(40):
        side = "LONG" if i < 24 else "SHORT"
        pnl = 0.55 + (i % 3) * 0.05 if side == "LONG" else -0.45 - (i % 2) * 0.05
        rows.append(
            {
                "symbol": "BTCUSDT",
                "entry_time": start + timedelta(hours=i * 6),
                "exit_time": start + timedelta(hours=i * 6 + 2),
                "analysis_timeframe": "1h",
                "strategy_name": "HTF Pullback Continuation",
                "strategy_mode": "single",
                "trade_owner_key": "single:1",
                "side": side,
                "score": 78.0,
                "risk_pct": 1.0,
                "pnl_pct": pnl,
                "execution_cost_pct": 0.08,
                "trend_regime_score": 62.0,
                "range_regime_score": 20.0,
                "squeeze_regime_score": 15.0,
                "panic_regime_score": 10.0,
                "feature_snapshot_json": json.dumps(_feature_payload()),
                "htf_context_json": json.dumps({}),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    strategy = {
        "strategy_name": "HTF Pullback Continuation",
        "template_key": "rule_builder",
        "score_threshold": 70,
        "rule_params": {"score_threshold": 70},
    }
    report = run_lifecycle_gate_lab(
        _trades(),
        strategy_payload=strategy,
        fixed_stake_usd=100,
        confidence_floor=55,
        analysis_tf="1h",
    )
    assert len(report.enriched_trades) == 40
    assert set(report.enriched_trades["lifecycle_state"].unique()) == {"trend_pullback_entry"}
    long_rows = report.enriched_trades[report.enriched_trades["side"] == "LONG"]
    short_rows = report.enriched_trades[report.enriched_trades["side"] == "SHORT"]
    assert (long_rows["fit_status"] == "fit").all()
    assert (short_rows["fit_status"] == "direction_conflict").all()

    comparison = report.comparison.set_index("scenario")
    assert "Baseline" in comparison.index
    assert "Fit only" in comparison.index
    assert int(comparison.loc["Fit only", "trades"]) == 24
    assert float(comparison.loc["Fit only", "expectancy_r"]) > float(comparison.loc["Baseline", "expectancy_r"])
    assert float(comparison.loc["Fit only", "separation_ci_low_pct"]) > 0
    assert report.recommendation["status"] in {"promising_for_exact_replay", "promising_but_unproven"}

    assert compileall.compile_file("app_src/lifecycle_gate_v28.py", quiet=1)
    assert compileall.compile_file("app_src/lifecycle_gate_ui.py", quiet=1)
    assert compileall.compile_file("pages/02_Lifecycle_Gate_Lab.py", quiet=1)
    print("V28.8 smoke test passed: lifecycle gate counterfactual analysis is available.")


if __name__ == "__main__":
    main()
