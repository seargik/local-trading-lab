from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from app_src.market_state_replay_v2817 import load_replay_policy, replay_symbol_history, run_market_state_replay, save_market_state_replay


policy = load_replay_policy()
assert str(policy["version"]).startswith("28.17")
assert policy["analysis_timeframe"] == "4h"
assert policy["require_closed_higher_timeframes"] is True

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    periods = 280
    times = pd.date_range("2026-01-01", periods=periods, freq="4h", tz="UTC")
    base = 100 + np.linspace(0, 18, periods) + np.sin(np.arange(periods) / 8.0) * 2.0
    frame = pd.DataFrame({
        "open_time": times,
        "open": base,
        "high": base + 1.2,
        "low": base - 1.2,
        "close": base + np.sin(np.arange(periods) / 5.0) * 0.5,
        "volume": 1000 + (np.arange(periods) % 24) * 25,
    })
    frame.to_csv(root / "BTCUSDT_4h.csv", index=False)

    start = times[215].isoformat()
    end = (times[-1] + pd.Timedelta(hours=4)).isoformat()
    history = replay_symbol_history(
        "BTCUSDT",
        source_root=root,
        start_date=start,
        end_date=end,
        policy={**policy, "warmup_days": 120},
    )
    assert not history.empty
    assert history["lookahead_ok"].all()
    assert (pd.to_datetime(history["decision_time"], utc=True) > pd.to_datetime(history["bar_open_time"], utc=True)).all()
    assert {"market_state", "confidence", "preferred_strategy_family", "router_action"}.issubset(history.columns)
    assert "forward_return_6bar_pct" in history.columns
    assert "router_directional_return_6bar_pct" in history.columns

    result = run_market_state_replay(
        source_root=root,
        symbols=["BTCUSDT"],
        start_date=start,
        end_date=end,
        policy={**policy, "warmup_days": 120},
    )
    assert result.no_lookahead_audit["passed"] is True
    assert result.no_lookahead_audit["lookahead_violations"] == 0
    assert not result.state_summary.empty
    assert not result.router_summary.empty
    assert not result.dwell_summary.empty
    assert "state_change_rate" in result.no_lookahead_audit

    snapshot_root = root / "snapshots"
    paths = save_market_state_replay(result, snapshot_root)
    assert Path(paths["summary"]).exists()
    assert Path(paths["history"]).exists()

for path in [
    "app_src/market_state_replay_v2817.py",
    "app_src/market_state_replay_ui.py",
    "pages/08_Market_State_Replay.py",
    "config/market_state_replay_policy.json",
    "docs/MARKET_STATE_REPLAY_V28_17.md",
]:
    assert Path(path).exists(), path

print("V28.17 smoke test passed: closed-bar historical market-state replay and adaptation diagnostics are available.")
