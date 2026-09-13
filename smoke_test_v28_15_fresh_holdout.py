from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

import pandas as pd

import app_src.fresh_holdout_v2815 as fh


def _source_run() -> dict:
    return {
        "run_dir": "data/backtests/source_run",
        "manifest": {
            "name": "Trend Pullback Research",
            "config": {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "entry_timeframe": "1h",
                "analysis_timeframe": "4h",
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
                "source_root": "data/ohlcv_store",
                "fixed_stake_usd": 100,
                "fee_bps_per_side": 4.0,
                "slippage_bps_per_side": 1.0,
                "spread_bps": 1.0,
                "allow_long": True,
                "allow_short": True,
                "one_trade_at_time": True,
            },
            "strategy_payload": {
                "strategy_name": "HTF Pullback Continuation",
                "template_key": "rule_builder",
                "research_family": "trend_pullback",
                "score_threshold": 72,
                "rule_params": {"stop_multiplier": 1.5, "tp_count": 3},
            },
        },
        "trades": pd.DataFrame(),
    }


def _trades(symbol: str, start: str) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp(start, tz="UTC")
    # 8 winners at +1%, 2 losses at -1% => positive expectancy/PF for each symbol.
    for idx in range(10):
        pnl = 1.0 if idx < 8 else -1.0
        rows.append(
            {
                "symbol": symbol,
                "side": "LONG" if idx % 2 == 0 else "SHORT",
                "entry_time": base + pd.Timedelta(hours=idx * 12),
                "exit_time": base + pd.Timedelta(hours=idx * 12 + 4),
                "pnl_pct": pnl,
                "raw_pnl_pct": pnl + 0.10,
                "execution_cost_pct": 0.10,
                "risk_pct": 1.0,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    source = _source_run()
    wf_report = {
        "wf_id": "wf14_test",
        "verdict": "pass_for_next_validation",
        "policy_version": "28.14-walk-forward-v1",
    }
    fixed_now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    record = fh.build_freeze_record(
        source,
        wf_report,
        research_family="trend_pullback",
        now=fixed_now,
    )
    assert record["first_eligible_date"] == "2026-01-16"
    assert record["strategy_sha256"]
    assert fh.verify_freeze_record(record)["valid"]

    tampered = dict(record)
    tampered["strategy_payload"] = dict(record["strategy_payload"])
    tampered["strategy_payload"]["score_threshold"] = 99
    assert not fh.verify_freeze_record(tampered)["valid"]

    with tempfile.TemporaryDirectory() as tmp:
        path = fh.save_freeze_record(record, freeze_dir=tmp)
        loaded = fh.load_freeze_record(path)
        assert loaded["record_sha256"] == record["record_sha256"]
        assert len(fh.list_freeze_records(tmp)) == 1

    captured = {}

    def fake_job_creator(**kwargs):
        captured.update(kwargs)
        return Path("queued/fake_holdout.json")

    original_readiness = fh.holdout_readiness
    original_active = fh._active_holdout_exists
    try:
        fh.holdout_readiness = lambda *args, **kwargs: {
            "ready": True,
            "reason": "ready",
            "observed_days": record["target_holdout_days"],
            "target_days": record["target_holdout_days"],
            "data_ready": True,
        }
        fh._active_holdout_exists = lambda *args, **kwargs: False
        queued = fh.queue_fresh_holdout(record, job_creator=fake_job_creator)
    finally:
        fh.holdout_readiness = original_readiness
        fh._active_holdout_exists = original_active

    assert queued["queued"]
    assert captured["start_date"] == record["first_eligible_date"]
    assert captured["end_date"] == record["target_end_date"]
    assert len(captured["tasks"]) == 3
    for task in captured["tasks"]:
        assert fh.strategy_payload_hash(task["strategy_payload"]) == record["strategy_sha256"]
        meta = task["task_meta"]["v28_15_fresh_holdout"]
        assert meta["freeze_record_sha256"] == record["record_sha256"]

    jobs = [{
        "job_id": "holdout_test",
        "job_type": fh.RUN_KIND,
        "run_kind": fh.RUN_KIND,
        "status": "completed",
        "freeze_id": record["freeze_id"],
        "freeze_record_sha256": record["record_sha256"],
        "frozen_strategy_sha256": record["strategy_sha256"],
        "holdout_start": record["first_eligible_date"],
        "holdout_end": record["target_end_date"],
        "results": [
            {"run_dir": "run_btc", "symbol": "BTCUSDT"},
            {"run_dir": "run_eth", "symbol": "ETHUSDT"},
            {"run_dir": "run_sol", "symbol": "SOLUSDT"},
        ],
    }]

    def loader(run_dir: str | Path) -> dict:
        symbol = {
            "run_btc": "BTCUSDT",
            "run_eth": "ETHUSDT",
            "run_sol": "SOLUSDT",
        }[str(run_dir)]
        meta = {
            "freeze_id": record["freeze_id"],
            "freeze_record_sha256": record["record_sha256"],
            "frozen_strategy_sha256": record["strategy_sha256"],
            "holdout_start": record["first_eligible_date"],
            "holdout_end": record["target_end_date"],
            "symbol": symbol,
        }
        return {
            "manifest": {
                "strategy_payload": record["strategy_payload"],
                "config": {
                    "start_date": record["first_eligible_date"],
                    "end_date": record["target_end_date"],
                    "v28_15_fresh_holdout": meta,
                },
            },
            "trades": _trades(symbol, record["first_eligible_date"]),
        }

    report = fh.evaluate_fresh_holdout(record, jobs, loader=loader)
    assert report["verdict"] == "fresh_holdout_pass", report
    assert report["overall"]["trades"] == 30
    assert report["gates"]["freeze_integrity"]
    assert report["gates"]["result_integrity"]
    assert report["gates"]["symbol_stability"]

    print("V28.15 smoke test passed: tamper-evident freeze and pristine future holdout protocol are available.")


if __name__ == "__main__":
    main()
