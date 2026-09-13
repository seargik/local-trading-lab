from __future__ import annotations

from types import SimpleNamespace
import json

import numpy as np
import pandas as pd

import app_src.backtest_core as core


def _frame(rows: int = 220) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=rows, freq="1h", tz="UTC")
    close = pd.Series(np.linspace(100.0, 130.0, rows))
    return pd.DataFrame({
        "open_time": ts,
        "open": close - 0.1,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.linspace(1000.0, 1500.0, rows),
        "close_time": ts + pd.Timedelta(hours=1),
        "is_closed": True,
    })


def main() -> None:
    assert core.TIMING_INTEGRITY_VERSION == "28.18"
    assert core.run_backtest.__module__ == "app_src.backtest_core"

    # Feature availability moves from candle open to candle close while the
    # source timestamp remains available for audit/explanation.
    original_bfill = pd.DataFrame.bfill
    raw = _frame()
    pack = core.historical_enrich_features(raw)
    assert pd.DataFrame.bfill is original_bfill
    assert "source_open_time" in pack.frame.columns
    assert pack.frame.iloc[0]["open_time"] == raw.iloc[0]["open_time"] + pd.Timedelta(hours=1)
    assert pack.frame.iloc[0]["source_open_time"] == raw.iloc[0]["open_time"]

    # No backward fill: MA200 cannot exist before 200 observations.
    assert pd.isna(pack.frame.iloc[100]["ma_200"])
    assert pd.notna(pack.frame.iloc[205]["ma_200"])

    # Five-bar pivot at index 2 is only confirmed when index 4 is known.
    pivot_df = pd.DataFrame({
        "high": [1.0, 2.0, 10.0, 2.0, 1.0],
        "low": [0.0, 0.0, -1.0, 0.0, 0.0],
    })
    _, piv_hi, piv_lo = core._causal_market_structure(pivot_df)
    assert pd.isna(piv_hi.iloc[2])
    assert float(piv_hi.iloc[4]) == 10.0
    assert pd.isna(piv_lo.iloc[2])
    assert float(piv_lo.iloc[4]) == -1.0

    good = pd.DataFrame([{
        "signal_time": "2026-01-02T04:00:00Z",
        "entry_time": "2026-01-02T04:00:00Z",
        "htf_context_json": json.dumps({
            "4h": {"open_time": "2026-01-02T04:00:00Z"},
            "1d": {"open_time": "2026-01-02T00:00:00Z"},
        }),
    }])
    good_audit = core.audit_backtest_timing(SimpleNamespace(trades=good))
    assert good_audit["ok"] is True
    assert good_audit["signal_after_entry_violations"] == 0
    assert good_audit["htf_context"]["violations"] == 0
    assert good_audit["causal_pivots"] is True
    assert good_audit["backward_fill_disabled"] is True

    bad = pd.DataFrame([{
        "signal_time": "2026-01-02T04:00:00Z",
        "entry_time": "2026-01-02T03:00:00Z",
        "htf_context_json": json.dumps({"4h": {"open_time": "2026-01-02T08:00:00Z"}}),
    }])
    bad_audit = core.audit_backtest_timing(SimpleNamespace(trades=bad))
    assert bad_audit["ok"] is False
    assert bad_audit["signal_after_entry_violations"] == 1
    assert bad_audit["htf_context"]["violations"] == 1

    print("V28.18 smoke test passed: backtests use closed-bar availability, causal pivots, and no backward fill.")


if __name__ == "__main__":
    main()
