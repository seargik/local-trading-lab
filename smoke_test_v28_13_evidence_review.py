from __future__ import annotations

import compileall
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from app_src.evidence_review_v2813 import (
    build_job_scorecard,
    evaluate_evidence_run,
    load_evidence_policy,
    save_scorecard_snapshot,
    scorecard_family_coverage,
)


def _frame(symbols=True, positive=True):
    if symbols:
        values = [120.0, 80.0, 60.0] if positive else [-80.0, 10.0, -20.0]
        return pd.DataFrame({"symbol": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "trades": [50, 50, 50], "total_pnl_usd": values})
    values = [40.0, 25.0, 30.0, -10.0, 35.0, 20.0, 25.0, 15.0] if positive else [-30.0, -20.0, 10.0, -15.0]
    return pd.DataFrame({"period": [f"2026-{i+1:02d}" for i in range(len(values))], "trades": [18] * len(values), "total_pnl_usd": values})


def _sides(total=150):
    return pd.DataFrame({
        "side": ["LONG", "SHORT"],
        "trades": [total // 2, total - total // 2],
        "total_pnl_usd": [150.0, 110.0],
    })


def _run(name: str, family: str, *, benchmark=False, good=True, trades=150):
    summary = {
        "total_trades": trades,
        "win_rate": 48.0 if good else 36.0,
        "expectancy_r": 0.18 if good else -0.08,
        "profit_factor": 1.45 if good else 0.82,
        "total_pnl_usd": 260.0 if good else -90.0,
        "pre_friction_pnl_usd": 340.0 if good else -10.0,
        "total_execution_cost_usd": 80.0,
        "max_drawdown_usd": 180.0 if good else 250.0,
    }
    return {
        "manifest": {
            "summary": summary,
            "strategy_payload": {
                "strategy_name": name,
                "research_family": family,
                "research_source": "bundled_research_benchmark" if benchmark else "saved_strategy",
                "benchmark_only": benchmark,
            },
        },
        "performance_by_symbol": _frame(symbols=True, positive=good),
        "performance_by_month": _frame(symbols=False, positive=good),
        "performance_by_side": _sides(trades),
    }


def main() -> int:
    policy = load_evidence_policy()

    strong = evaluate_evidence_run(_run("Trend", "trend_pullback"), policy=policy)
    assert strong["verdict"] == "cross_validation_candidate", strong
    assert strong["checks_passed"] == strong["checks_total"]

    benchmark = evaluate_evidence_run(_run("Compression benchmark", "compression_breakout", benchmark=True), policy=policy)
    assert benchmark["verdict"] == "promising", benchmark
    assert benchmark["promotion_blocked"] is True

    bad = evaluate_evidence_run(_run("Range", "range_reversion", good=False, trades=80), policy=policy)
    assert bad["verdict"] == "reject", bad

    small = evaluate_evidence_run(_run("Small", "trend_pullback", good=True, trades=20), policy=policy)
    assert small["verdict"] == "insufficient_evidence", small

    runs = {
        "trend": _run("Trend", "trend_pullback"),
        "compression": _run("Compression benchmark", "compression_breakout", benchmark=True),
        "range": _run("Range", "range_reversion", good=False, trades=80),
    }
    job = {
        "job_id": "v2813-test",
        "run_kind": "v28_12_core_research",
        "research_protocol": {"version": "28.12-three-family-ohlcv-v1"},
        "results": [
            {"strategy_name": "Trend", "run_dir": "trend"},
            {"strategy_name": "Compression benchmark", "run_dir": "compression"},
            {"strategy_name": "Range", "run_dir": "range"},
        ],
    }
    scorecard = build_job_scorecard(job, loader=lambda path: runs[str(path)], policy=policy)
    assert len(scorecard) == 3
    coverage = scorecard_family_coverage(scorecard)
    assert coverage["complete"] is True, coverage
    verdicts = dict(zip(scorecard["research_family"], scorecard["verdict"]))
    assert verdicts == {
        "trend_pullback": "cross_validation_candidate",
        "compression_breakout": "promising",
        "range_reversion": "reject",
    }, verdicts

    with TemporaryDirectory() as tmp:
        saved = save_scorecard_snapshot(job, scorecard, policy=policy, output_dir=tmp)
        assert Path(saved["csv_path"]).exists()
        assert Path(saved["json_path"]).exists()

    assert compileall.compile_file("app_src/evidence_review_v2813.py", quiet=1)
    assert compileall.compile_file("app_src/evidence_review_ui.py", quiet=1)
    assert compileall.compile_file("pages/05_Evidence_Review.py", quiet=1)
    print("V28.13 evidence review smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
