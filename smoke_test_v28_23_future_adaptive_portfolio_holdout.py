from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

import pandas as pd

from app_src.adaptive_evidence_v2820 import AdaptiveEvidenceResult, CORE_FAMILIES
from app_src.adaptive_portfolio_walk_forward_v2822 import build_freeze_record_from_components
from app_src.future_adaptive_portfolio_holdout_v2823 import (
    _canonical_hash,
    build_future_freeze_record_from_components,
    current_implementation_fingerprints,
    current_policy_snapshots,
    evaluate_future_evidence,
    load_future_holdout_policy,
    queue_future_holdout,
    save_future_freeze,
    verify_future_freeze,
    verify_future_job,
)

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
    }
    strategy_snapshots.append(
        {
            "research_family": family,
            "strategy_name": payload["strategy_name"],
            "benchmark_only": False,
            "strategy_payload": payload,
            "strategy_sha256": _canonical_hash(payload),
            "source_run_dir": f"synthetic/{family}",
            "timing_integrity_version": "28.18",
        }
    )
    cfg = {
        "fixed_stake_usd": 100.0,
        "fee_bps_per_side": 4.0,
        "spread_bps_round_trip": 2.0,
        "slippage_bps_per_side": 2.0,
        "timing_integrity_version": "28.18",
    }
    execution_configs.append(
        {
            "research_family": family,
            "config": cfg,
            "config_sha256": _canonical_hash(cfg),
        }
    )

source_signature = {
    "verdict": {"verdict": "shared_account_edge_candidate"},
    "adaptive_summary": {"return_pct": 12.0, "profit_factor": 1.4},
    "static_summary": {"return_pct": 8.0, "profit_factor": 1.2},
}
source_freeze = build_freeze_record_from_components(
    source={
        "job_id": "synthetic-source",
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

freeze_now = datetime(2026, 1, 10, 12, 30, tzinfo=timezone.utc)
record = build_future_freeze_record_from_components(
    source_portfolio_freeze=source_freeze,
    source_walk_forward_signature=walk_signature,
    execution_configs=execution_configs,
    policies=current_policies,
    implementations=current_implementation_fingerprints(),
    future_policy=future_policy,
    now=freeze_now,
)
if record["first_eligible_date"] != "2026-01-11":
    raise AssertionError(f"Future holdout must start on next UTC date, got {record['first_eligible_date']}")
if record["target_holdout_days"] != 60:
    raise AssertionError("Expected fixed 60-day future window")
if record["target_end_date"] != "2026-03-11":
    raise AssertionError(f"Expected 2026-03-11 fixed endpoint, got {record['target_end_date']}")
verification = verify_future_freeze(record, check_current=True)
if not verification["ok"]:
    raise AssertionError(f"Freshly built V28.23 freeze should verify: {verification}")

# Any post-freeze strategy/config mutation must invalidate the tamper-evident record.
tampered = {**record, "strategy_snapshots": [dict(x) for x in record["strategy_snapshots"]]}
tampered["strategy_snapshots"][0] = dict(tampered["strategy_snapshots"][0])
tampered["strategy_snapshots"][0]["strategy_payload"] = dict(tampered["strategy_snapshots"][0]["strategy_payload"])
tampered["strategy_snapshots"][0]["strategy_payload"]["score_threshold"] = 75
if verify_future_freeze(tampered, check_current=False)["ok"]:
    raise AssertionError("Strategy tampering should invalidate the future freeze")

# The same passed source framework cannot quietly restart its future clock.
with tempfile.TemporaryDirectory() as tmp:
    first_path = save_future_freeze(record, output_dir=tmp)
    if not Path(first_path).exists():
        raise AssertionError("Future freeze was not saved")
    restarted = build_future_freeze_record_from_components(
        source_portfolio_freeze=source_freeze,
        source_walk_forward_signature=walk_signature,
        execution_configs=execution_configs,
        policies=current_policies,
        implementations=current_implementation_fingerprints(),
        future_policy=future_policy,
        now=datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc),
    )
    try:
        save_future_freeze(restarted, output_dir=tmp)
        raise AssertionError("Restarting the same framework clock should have been refused")
    except ValueError as exc:
        if "restart" not in str(exc).lower() and "already has" not in str(exc).lower():
            raise

# Queue only three family tasks; each task must cover the whole frozen symbol set.
captured = {}
def fake_job_creator(**kwargs):
    captured.update(kwargs)
    return Path("/tmp/v2823_synthetic_job.json")

def ready_checker(_record, **_kwargs):
    return {"ready": True, "reason": "synthetic ready"}

queue_result = queue_future_holdout(
    record,
    now=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
    job_creator=fake_job_creator,
    readiness_checker=ready_checker,
)
if not queue_result.get("queued") or queue_result.get("tasks_created") != 3:
    raise AssertionError(f"Expected one task per frozen family: {queue_result}")
if captured.get("start_date") != "2026-01-11" or captured.get("end_date") != "2026-03-11":
    raise AssertionError("Queued job did not preserve exact future window")
for task in captured.get("tasks") or []:
    if sorted(task.get("symbols") or []) != ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        raise AssertionError("Each family task must run the whole frozen symbol set")

# Build a completed synthetic job and verify exact payload/config hashes.
run_map = {}
results = []
for idx, strategy in enumerate(record["strategy_snapshots"]):
    family = strategy["research_family"]
    expected_cfg = next(x for x in record["execution_config_snapshots"] if x["research_family"] == family)
    meta = {
        "freeze_id": record["freeze_id"],
        "freeze_record_sha256": record["record_sha256"],
        "future_framework_sha256": record["future_framework_sha256"],
        "research_family": family,
        "strategy_sha256": strategy["strategy_sha256"],
        "execution_config_sha256": expected_cfg["config_sha256"],
        "holdout_start": record["first_eligible_date"],
        "holdout_end": record["target_end_date"],
        "role": "genuinely_future_holdout",
    }
    cfg = dict(expected_cfg["config"])
    cfg["v28_23_future_holdout"] = meta
    run_dir = f"synthetic-run-{idx}"
    run_map[run_dir] = {"manifest": {"strategy_payload": strategy["strategy_payload"], "config": cfg}}
    results.append({"run_dir": run_dir, "task_meta": {"v28_23_future_holdout": meta}})

synthetic_job = {
    "job_id": "future-job-1",
    "job_type": "v28_23_future_adaptive_portfolio_holdout",
    "run_kind": "v28_23_future_adaptive_portfolio_holdout",
    "status": "completed",
    "source_root": "data/ohlcv_store",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "entry_timeframe": "1h",
    "analysis_timeframe": "4h",
    "start_date": record["first_eligible_date"],
    "end_date": record["target_end_date"],
    "freeze_id": record["freeze_id"],
    "freeze_record_sha256": record["record_sha256"],
    "future_framework_sha256": record["future_framework_sha256"],
    "results": results,
}
job_check = verify_future_job(synthetic_job, record, loader=lambda path: run_map[str(path)])
if not job_check["ok"]:
    raise AssertionError(f"Exact synthetic future job should verify: {job_check}")

# Synthetic genuinely-future economics: router selects one winner per cycle while
# static also takes two small losers. This isolates adaptive selection value.
rows = []
start = pd.Timestamp("2026-01-11T00:00:00Z")
symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
for i in range(45):
    cycle = start + pd.Timedelta(days=i)
    preferred = CORE_FAMILIES[i % len(CORE_FAMILIES)]
    symbol = symbols[i % len(symbols)]
    for j, family in enumerate(CORE_FAMILIES):
        signal = cycle + pd.Timedelta(hours=j * 3)
        selected = family == preferred
        rows.append(
            {
                "symbol": symbol,
                "research_family": family,
                "strategy_name": f"Future {family}",
                "signal_time": signal,
                "entry_time": signal + pd.Timedelta(hours=1),
                "exit_time": signal + pd.Timedelta(hours=2),
                "side": "LONG",
                "score": 80.0,
                "confidence": 82.0,
                "risk_pct": 1.0,
                "pnl_pct": 1.0 if selected else -0.4,
                "mae_pct": -0.2 if selected else -0.7,
                "state_available": True,
                "router_match": selected,
                "router_risk_multiplier": 1.0 if selected else 0.0,
                "benchmark_only": False,
            }
        )

future_evidence = AdaptiveEvidenceResult(
    job_id="synthetic-future-evidence",
    family_comparison=pd.DataFrame(),
    pooled_comparison=pd.DataFrame(),
    wait_analysis=pd.DataFrame(),
    friction_stress=pd.DataFrame(),
    stability_by_symbol=pd.DataFrame(),
    stability_by_month=pd.DataFrame(),
    annotated_trades=pd.DataFrame(rows),
    concurrency={},
    integrity={"ok": True, "timing_integrity_ok": True, "data_integrity_ok": True},
    verdict={"verdict": "synthetic"},
    critique=[],
)

# No-peeking guard must block evaluation before the fixed endpoint.
try:
    evaluate_future_evidence(
        future_evidence,
        record,
        job_integrity={"ok": True},
        freeze_verification=verify_future_freeze(record, check_current=False),
        now=datetime(2026, 2, 15, 0, 0, tzinfo=timezone.utc),
    )
    raise AssertionError("Mid-window future evaluation should be blocked")
except ValueError as exc:
    if "preliminary" not in str(exc).lower():
        raise

passed = evaluate_future_evidence(
    future_evidence,
    record,
    job_integrity={"ok": True},
    freeze_verification=verify_future_freeze(record, check_current=False),
    now=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
)
if passed.verdict["verdict"] != "pass_for_frozen_paper":
    raise AssertionError(f"Expected pass_for_frozen_paper, got {passed.verdict}")
if passed.verdict["adaptive_return_pct"] <= passed.verdict["static_return_pct"]:
    raise AssertionError("Synthetic adaptive future account should outperform static baseline")
if not passed.verdict["checks"]["friction_survival"]:
    raise AssertionError("Synthetic future edge should survive required friction stress")

# If future static economics are stronger on both return and PF, the adaptive
# architecture must be rejected even if its own account remains positive.
static_better_rows = []
for row in rows:
    changed = dict(row)
    changed["pnl_pct"] = 0.15 if bool(row["router_match"]) else 1.0
    static_better_rows.append(changed)
static_better_evidence = AdaptiveEvidenceResult(
    job_id="static-better-future",
    family_comparison=pd.DataFrame(),
    pooled_comparison=pd.DataFrame(),
    wait_analysis=pd.DataFrame(),
    friction_stress=pd.DataFrame(),
    stability_by_symbol=pd.DataFrame(),
    stability_by_month=pd.DataFrame(),
    annotated_trades=pd.DataFrame(static_better_rows),
    concurrency={},
    integrity={"ok": True, "timing_integrity_ok": True, "data_integrity_ok": True},
    verdict={"verdict": "synthetic"},
    critique=[],
)
static_better = evaluate_future_evidence(
    static_better_evidence,
    record,
    job_integrity={"ok": True},
    freeze_verification=verify_future_freeze(record, check_current=False),
    now=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
)
if static_better.verdict["verdict"] != "static_baseline_better_future":
    raise AssertionError(f"Expected explicit static-baseline failure, got {static_better.verdict}")

print("V28.23 smoke test passed: future freeze, anti-restart/anti-peeking guards, exact job hashes, shared-account future economics and static-baseline rejection are available.")
