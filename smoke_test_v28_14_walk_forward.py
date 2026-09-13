from __future__ import annotations

from pathlib import Path

import pandas as pd

from app_src.walk_forward_v2814 import (
    RUN_KIND,
    build_anchored_walk_forward_folds,
    evaluate_walk_forward,
    queue_walk_forward_validation,
    strategy_payload_hash,
)


def _strategy() -> dict:
    return {
        "strategy_name": "Smoke Trend Pullback",
        "template_key": "rule_builder",
        "indicator_rules": [],
        "rule_params": {"score_threshold": 70, "stop_multiplier": 1.0},
        "score_threshold": 70,
        "expected_rr": "1:2",
        "research_family": "trend_pullback",
        "benchmark_only": False,
    }


def _source_trades() -> pd.DataFrame:
    rows = []
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for month in range(1, 13):
        for symbol in symbols:
            for day, pnl in [(5, 0.9), (20, -0.25)]:
                ts = pd.Timestamp(year=2025, month=month, day=day, tz="UTC")
                rows.append(
                    {
                        "symbol": symbol,
                        "signal_time": ts,
                        "entry_time": ts,
                        "exit_time": ts + pd.Timedelta(hours=12),
                        "side": "LONG" if day == 5 else "SHORT",
                        "pnl_pct": pnl,
                        "risk_pct": 1.0,
                    }
                )
    return pd.DataFrame(rows)


def _oos_trades(fold: dict, symbol: str) -> pd.DataFrame:
    start = pd.Timestamp(fold["test_start"], tz="UTC")
    rows = []
    for idx in range(8):
        ts = start + pd.Timedelta(days=idx * 4)
        pnl = 0.8 if idx % 3 != 2 else -0.2
        rows.append(
            {
                "symbol": symbol,
                "signal_time": ts,
                "entry_time": ts,
                "exit_time": ts + pd.Timedelta(hours=10),
                "side": "LONG" if idx % 2 == 0 else "SHORT",
                "pnl_pct": pnl,
                "risk_pct": 1.0,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    policy = {
        "version": "smoke",
        "initial_train_months": 6,
        "test_months": 2,
        "max_folds": 3,
        "min_train_days": 90,
        "min_test_days": 30,
        "min_train_trades_per_fold": 30,
        "min_train_profit_factor": 1.05,
        "min_train_expectancy_r": 0.0,
        "min_positive_train_fold_share": 0.67,
        "min_total_oos_trades": 30,
        "min_oos_trades_per_fold": 8,
        "min_oos_trades_per_symbol": 8,
        "min_oos_profit_factor": 1.05,
        "min_oos_expectancy_r": 0.0,
        "min_positive_test_fold_share": 0.67,
        "min_positive_symbol_share": 0.67,
        "max_drawdown_to_net_profit": 1.5,
        "min_anchor_train_trades": 10,
        "min_transfer_edges": 2,
        "min_transfer_pass_share": 0.5,
        "cell_min_profit_factor": 1.0,
        "cell_min_expectancy_r": 0.0,
        "require_transfer_gate": True,
    }
    payload = _strategy()
    source_run = {
        "run_dir": "source_run",
        "manifest": {
            "name": "Smoke source",
            "config": {
                "source_root": "data/ohlcv_store",
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "entry_timeframe": "1h",
                "analysis_timeframe": "4h",
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
                "fixed_stake_usd": 100,
                "fee_bps_per_side": 4.0,
                "slippage_bps_per_side": 1.0,
            },
            "strategy_payload": payload,
        },
        "trades": _source_trades(),
    }

    folds = build_anchored_walk_forward_folds("2025-01-01", "2025-12-31", initial_train_months=6, test_months=2, max_folds=3)
    assert len(folds) == 3, folds
    assert folds[0]["train_end"] == "2025-06-30"
    assert folds[0]["test_start"] == "2025-07-01"
    assert folds[-1]["test_end"] == "2025-12-31"

    created = []

    def fake_job_creator(**kwargs):
        created.append(kwargs)
        return Path(f"/tmp/fake_wf_{len(created)}.json")

    queued = queue_walk_forward_validation(
        source_run,
        research_family="trend_pullback",
        policy=policy,
        job_creator=fake_job_creator,
    )
    assert queued["queued"] is True, queued
    assert queued["jobs_created"] == 3
    assert queued["tasks_created"] == 9
    frozen_hash = strategy_payload_hash(payload)
    for job in created:
        for task in job["tasks"]:
            assert strategy_payload_hash(task["strategy_payload"]) == frozen_hash
            assert set(task["config_overrides"]) == {"run_kind", "v28_14_walk_forward"}

    wf_id = queued["wf_id"]
    loader_map = {}
    completed_jobs = []
    for fold in folds:
        results = []
        for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            run_dir = f"{fold['fold_id']}_{symbol}"
            meta = {
                "wf_id": wf_id,
                "fold_id": fold["fold_id"],
                "symbol": symbol,
                "research_family": "trend_pullback",
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "frozen_strategy_sha256": frozen_hash,
            }
            trades = _oos_trades(fold, symbol)
            loader_map[run_dir] = {
                "manifest": {
                    "config": {"v28_14_walk_forward": meta},
                    "strategy_payload": payload,
                    "summary": {
                        "total_trades": len(trades),
                        "total_pnl_usd": float(trades["pnl_pct"].sum()),
                        "expectancy_r": float((trades["pnl_pct"] / trades["risk_pct"]).mean()),
                        "profit_factor": 5.0,
                        "win_rate": 75.0,
                        "max_drawdown_usd": 0.5,
                    },
                },
                "trades": trades,
            }
            results.append(
                {
                    "run_dir": run_dir,
                    "symbol": symbol,
                    "task_meta": {"v28_14_walk_forward": meta},
                }
            )
        completed_jobs.append(
            {
                "job_type": RUN_KIND,
                "run_kind": RUN_KIND,
                "status": "completed",
                "wf_id": wf_id,
                "fold": fold,
                "results": results,
            }
        )

    def fake_loader(run_dir):
        return loader_map[str(run_dir)]

    report = evaluate_walk_forward(wf_id, completed_jobs, source_run, policy=policy, loader=fake_loader)
    assert report["frozen_payload_integrity"] is True
    assert report["verdict"] == "pass_for_next_validation", report
    assert report["forward_fold_pass_share"] == 1.0
    assert report["symbol_pass_share"] == 1.0
    assert report["transfer_edges"] >= 2
    assert report["transfer_pass_share"] == 1.0
    assert report["selection_contamination_warning"] is True
    print("V28.14 smoke test passed: frozen payload, anchored folds, OOS gates and pair transfer are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
