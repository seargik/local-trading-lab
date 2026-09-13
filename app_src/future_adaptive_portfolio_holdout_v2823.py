from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any, Callable

import pandas as pd

from .adaptive_evidence_v2820 import (
    AdaptiveEvidenceResult,
    CORE_FAMILIES,
    _integrity_for_job,
    _normalize_saved_trades,
    _representative_runs,
    evaluate_adaptive_trade_frames,
)
from .adaptive_portfolio_walk_forward_v2822 import verify_portfolio_freeze
from .backtest_core import load_saved_backtest, timeframe_minutes
from .backtest_jobs import create_task_job, list_jobs
from .historical_backfill import audit_store_integrity
from .market_state_replay_v2817 import run_market_state_replay
from .settings import OHLCV_STORE_ROOT
from .shared_account_replay_v2821 import load_shared_account_policy, prepare_portfolio_candidates, simulate_shared_account

POLICY_PATH = Path("config/future_adaptive_portfolio_holdout_policy.json")
FREEZE_DIR = Path("data/backtest_reviews/future_adaptive_portfolio_freezes")
SNAPSHOT_DIR = Path("data/backtest_reviews/future_adaptive_portfolio_holdout")
RUN_KIND = "v28_23_future_adaptive_portfolio_holdout"
SCHEMA_VERSION = "28.23-future-adaptive-portfolio-v1"

SOURCE_POLICY_FILES = {
    "adaptive_evidence": Path("config/adaptive_evidence_policy.json"),
    "market_state_router": Path("config/market_state_router_policy.json"),
    "market_state_replay": Path("config/market_state_replay_policy.json"),
    "shared_account": Path("config/shared_account_policy.json"),
    "portfolio_walk_forward": Path("config/adaptive_portfolio_walk_forward_policy.json"),
}

# V28.23 deliberately fingerprints the behavioral surface more broadly than
# V28.22. A future holdout must not quietly change strategy interpretation,
# features, exits, routing, shared-capital accounting or worker config merging.
IMPLEMENTATION_FILES = {
    "backtest_core": Path("app_src/backtest_core.py"),
    "backtest_core_legacy": Path("app_src/backtest_core_legacy.py"),
    "features": Path("app_src/features.py"),
    "strategies": Path("app_src/strategies.py"),
    "exit_families": Path("app_src/exit_families.py"),
    "trend_lifecycle": Path("app_src/trend_lifecycle.py"),
    "strategy_family_registry": Path("app_src/strategy_family_registry_v2811.py"),
    "market_state": Path("app_src/market_state_v2816.py"),
    "market_state_replay": Path("app_src/market_state_replay_v2817.py"),
    "adaptive_evidence": Path("app_src/adaptive_evidence_v2820.py"),
    "shared_account_replay": Path("app_src/shared_account_replay_v2821.py"),
    "portfolio_walk_forward": Path("app_src/adaptive_portfolio_walk_forward_v2822.py"),
    "future_holdout": Path("app_src/future_adaptive_portfolio_holdout_v2823.py"),
    "backtest_worker": Path("backtest_worker.py"),
}

_DYNAMIC_CONFIG_KEYS = {
    "symbols",
    "start_date",
    "end_date",
    "source_root",
    "entry_timeframe",
    "analysis_timeframe",
    "run_kind",
    "what_if_config",
    "base_run",
    "timing_integrity_audit",
}


@dataclass
class FutureAdaptivePortfolioHoldoutResult:
    freeze_id: str
    comparison: pd.DataFrame
    friction_stress: pd.DataFrame
    adaptive_equity: pd.DataFrame
    static_equity: pd.DataFrame
    adaptive_ledger: pd.DataFrame
    static_ledger: pd.DataFrame
    rejected_candidates: pd.DataFrame
    by_symbol: pd.DataFrame
    by_family: pd.DataFrame
    by_month: pd.DataFrame
    verdict: dict[str, Any]
    integrity: dict[str, Any]
    critique: list[str]


def load_future_holdout_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("future adaptive portfolio holdout policy must be a JSON object")
    return payload


def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return sha256(raw).hexdigest()


def _file_sha256(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return float(default)
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return float(default)


def _utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _policy_snapshot(path: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    value = dict(payload or json.loads(path.read_text(encoding="utf-8")))
    return {
        "path": str(path),
        "version": str(value.get("version") or ""),
        "sha256": _canonical_hash(value),
        "payload": value,
    }


def current_policy_snapshots(future_policy: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    rows = {name: _policy_snapshot(path) for name, path in SOURCE_POLICY_FILES.items()}
    rows["future_holdout"] = _policy_snapshot(POLICY_PATH, dict(future_policy or load_future_holdout_policy()))
    return rows


def current_implementation_fingerprints(paths: dict[str, Path] | None = None) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for name, path in (paths or IMPLEMENTATION_FILES).items():
        rows[name] = {"path": str(path), "sha256": _file_sha256(path)}
    return rows


def _clean_execution_config(config: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(config or {}).items():
        if key in _DYNAMIC_CONFIG_KEYS or str(key).startswith("v28_"):
            continue
        out[str(key)] = value
    return out


def execution_config_snapshots(
    job: dict[str, Any],
    *,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> tuple[list[dict[str, Any]], list[str]]:
    chosen, warnings = _representative_runs(job, loader)
    rows: list[dict[str, Any]] = []
    for family in CORE_FAMILIES:
        selected = chosen.get(family)
        if not selected:
            continue
        run = dict(selected.get("run") or {})
        manifest = dict(run.get("manifest") or {})
        config = _clean_execution_config(dict(manifest.get("config") or {}))
        rows.append(
            {
                "research_family": family,
                "config": config,
                "config_sha256": _canonical_hash(config),
            }
        )
    rows.sort(key=lambda x: x["research_family"])
    return rows, warnings


def _walk_forward_signature(result: Any) -> dict[str, Any]:
    def records(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient="records")
        return []

    return {
        "freeze_id": str(getattr(result, "freeze_id", "") or ""),
        "verdict": dict(getattr(result, "verdict", {}) or {}),
        "folds": records(getattr(result, "folds", None)),
        "aggregate_comparison": records(getattr(result, "aggregate_comparison", None)),
        "friction_stress": records(getattr(result, "friction_stress", None)),
        "integrity": dict(getattr(result, "integrity", {}) or {}),
    }


def future_freeze_record_hash(record: dict[str, Any]) -> str:
    core = dict(record or {})
    core.pop("record_sha256", None)
    return _canonical_hash(core)


def build_future_freeze_record_from_components(
    *,
    source_portfolio_freeze: dict[str, Any],
    source_walk_forward_signature: dict[str, Any],
    execution_configs: list[dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    implementations: dict[str, dict[str, str]],
    future_policy: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    required = str(future_policy.get("require_source_verdict") or "pass_for_future_freeze")
    actual = str((source_walk_forward_signature.get("verdict") or {}).get("verdict") or "")
    if actual != required:
        raise ValueError(f"V28.23 freeze requires V28.22 verdict {required}; got {actual or 'unknown'}.")

    source_verification = verify_portfolio_freeze(source_portfolio_freeze, check_current=False)
    if not bool(source_verification.get("ok", False)):
        raise ValueError("V28.23 cannot freeze an invalid V28.22 portfolio freeze record.")

    expected_framework = str(source_portfolio_freeze.get("framework_sha256") or "")
    result_framework = str((source_walk_forward_signature.get("verdict") or {}).get("framework_sha256") or "")
    if result_framework and result_framework != expected_framework:
        raise ValueError("V28.22 result framework hash does not match the source portfolio freeze.")
    result_freeze_id = str(source_walk_forward_signature.get("freeze_id") or "")
    if result_freeze_id and result_freeze_id != str(source_portfolio_freeze.get("freeze_id") or ""):
        raise ValueError("V28.22 result freeze id does not match the source portfolio freeze.")

    created_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = pd.Timestamp(created_dt)
    first = cutoff.normalize() + pd.Timedelta(days=1)
    target_days = max(1, int(future_policy.get("target_holdout_days", 60)))
    target_end = first + pd.Timedelta(days=target_days - 1)

    strategy_snapshots = deepcopy(source_portfolio_freeze.get("strategy_snapshots") or [])
    if len(strategy_snapshots) < len(CORE_FAMILIES):
        raise ValueError("V28.23 requires frozen strategy snapshots for all core research families.")
    if len(execution_configs) < len(CORE_FAMILIES):
        raise ValueError("V28.23 requires frozen execution config snapshots for all core research families.")

    source = deepcopy(source_portfolio_freeze.get("source") or {})
    walk_sig_hash = _canonical_hash(source_walk_forward_signature)
    source_framework = str(source_portfolio_freeze.get("framework_sha256") or "")
    uniqueness_key = _canonical_hash({"source_framework_sha256": source_framework, "source_walk_forward_signature_sha256": walk_sig_hash})
    framework_payload = {
        "source_portfolio_framework_sha256": source_framework,
        "strategy_hashes": {str(x.get("research_family")): str(x.get("strategy_sha256") or "") for x in strategy_snapshots},
        "execution_config_hashes": {str(x.get("research_family")): str(x.get("config_sha256") or "") for x in execution_configs},
        "policy_hashes": {name: str(value.get("sha256") or "") for name, value in sorted(policies.items())},
        "implementation_hashes": {name: str(value.get("sha256") or "") for name, value in sorted(implementations.items())},
    }
    future_framework = _canonical_hash(framework_payload)
    freeze_id = f"fh23_{created_dt.strftime('%Y%m%dT%H%M%SZ')}_{future_framework[:10]}"

    record = {
        "schema_version": SCHEMA_VERSION,
        "freeze_id": freeze_id,
        "created_at": created_dt.isoformat(),
        "cutoff_utc": created_dt.isoformat(),
        "first_eligible_date": first.date().isoformat(),
        "target_holdout_days": target_days,
        "target_end_date": target_end.date().isoformat(),
        "minimum_observation_days": int(future_policy.get("minimum_observation_days", 30)),
        "allow_preliminary_evaluation": bool(future_policy.get("allow_preliminary_evaluation", False)),
        "source": source,
        "symbols": [str(x).upper() for x in (source.get("symbols") or [])],
        "entry_timeframe": str(source.get("entry_timeframe") or "1h"),
        "analysis_timeframe": str(source.get("analysis_timeframe") or "4h"),
        "source_root": str(source.get("source_root") or OHLCV_STORE_ROOT),
        "strategy_snapshots": strategy_snapshots,
        "execution_config_snapshots": deepcopy(execution_configs),
        "policies": deepcopy(policies),
        "implementation_fingerprints": deepcopy(implementations),
        "source_portfolio_freeze": deepcopy(source_portfolio_freeze),
        "source_portfolio_freeze_id": str(source_portfolio_freeze.get("freeze_id") or ""),
        "source_portfolio_record_sha256": str(source_portfolio_freeze.get("record_sha256") or ""),
        "source_portfolio_framework_sha256": source_framework,
        "source_walk_forward_signature": deepcopy(source_walk_forward_signature),
        "source_walk_forward_signature_sha256": walk_sig_hash,
        "source_walk_forward_verdict": actual,
        "future_framework_sha256": future_framework,
        "freeze_uniqueness_key": uniqueness_key,
        "status": "frozen_waiting_for_genuinely_future_data",
        "genuinely_future_protocol": True,
        "no_preliminary_peeking": not bool(future_policy.get("allow_preliminary_evaluation", False)),
        "warning": "Do not tune, restart the clock, or redefine the framework after future outcomes begin. A redesign requires a new framework and a new future-data window.",
    }
    record["record_sha256"] = future_freeze_record_hash(record)
    return record


def build_future_holdout_freeze(
    job: dict[str, Any],
    source_portfolio_freeze: dict[str, Any],
    walk_forward_result: Any,
    *,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> dict[str, Any]:
    future_policy = dict(policy or load_future_holdout_policy())
    source_verification = verify_portfolio_freeze(source_portfolio_freeze, check_current=True, source_job=job)
    if not bool(source_verification.get("ok", False)):
        raise ValueError(f"Source V28.22 freeze has integrity drift: {source_verification.get('drift') or []}")
    exec_configs, warnings = execution_config_snapshots(job, loader=loader)
    policies = {name: deepcopy(value) for name, value in (source_portfolio_freeze.get("policies") or {}).items()}
    policies["future_holdout"] = _policy_snapshot(POLICY_PATH, future_policy)
    record = build_future_freeze_record_from_components(
        source_portfolio_freeze=source_portfolio_freeze,
        source_walk_forward_signature=_walk_forward_signature(walk_forward_result),
        execution_configs=exec_configs,
        policies=policies,
        implementations=current_implementation_fingerprints(),
        future_policy=future_policy,
        now=now,
    )
    record["source_warnings"] = warnings
    record["record_sha256"] = future_freeze_record_hash(record)
    return record


def verify_future_freeze(record: dict[str, Any], *, check_current: bool = True) -> dict[str, Any]:
    record_hash_ok = str(record.get("record_sha256") or "") == future_freeze_record_hash(record)
    source_freeze = dict(record.get("source_portfolio_freeze") or {})
    source_verification = verify_portfolio_freeze(source_freeze, check_current=False) if source_freeze else {"ok": False}
    source_freeze_ok = bool(source_verification.get("ok", False))
    source_hash_ok = str(record.get("source_portfolio_record_sha256") or "") == str(source_freeze.get("record_sha256") or "")
    source_framework_ok = str(record.get("source_portfolio_framework_sha256") or "") == str(source_freeze.get("framework_sha256") or "")
    walk_signature_ok = str(record.get("source_walk_forward_signature_sha256") or "") == _canonical_hash(record.get("source_walk_forward_signature") or {})
    source_verdict = str(record.get("source_walk_forward_verdict") or "")
    required_verdict = str(((record.get("policies") or {}).get("future_holdout") or {}).get("payload", {}).get("require_source_verdict") or "pass_for_future_freeze")
    source_verdict_ok = source_verdict == required_verdict

    strategies = list(record.get("strategy_snapshots") or [])
    strategy_hash_ok = bool(strategies) and all(
        str(row.get("strategy_sha256") or "") == _canonical_hash(row.get("strategy_payload") or {})
        for row in strategies
    )
    execution = list(record.get("execution_config_snapshots") or [])
    execution_hash_ok = bool(execution) and all(
        str(row.get("config_sha256") or "") == _canonical_hash(row.get("config") or {})
        for row in execution
    )

    framework_payload = {
        "source_portfolio_framework_sha256": str(record.get("source_portfolio_framework_sha256") or ""),
        "strategy_hashes": {str(x.get("research_family")): str(x.get("strategy_sha256") or "") for x in strategies},
        "execution_config_hashes": {str(x.get("research_family")): str(x.get("config_sha256") or "") for x in execution},
        "policy_hashes": {name: str(value.get("sha256") or "") for name, value in sorted((record.get("policies") or {}).items())},
        "implementation_hashes": {name: str(value.get("sha256") or "") for name, value in sorted((record.get("implementation_fingerprints") or {}).items())},
    }
    framework_hash_ok = str(record.get("future_framework_sha256") or "") == _canonical_hash(framework_payload)

    cutoff = _utc(record.get("cutoff_utc"))
    first = _utc(record.get("first_eligible_date"))
    future_boundary_ok = bool(not pd.isna(cutoff) and not pd.isna(first) and first >= cutoff.normalize() + pd.Timedelta(days=1))

    policy_match = True
    implementation_match = True
    drift: list[str] = []
    if check_current:
        try:
            frozen_policies = dict(record.get("policies") or {})
            current = current_policy_snapshots()
            for name, frozen in frozen_policies.items():
                if str((current.get(name) or {}).get("sha256") or "") != str(frozen.get("sha256") or ""):
                    policy_match = False
                    drift.append(f"policy:{name}")
        except Exception as exc:
            policy_match = False
            drift.append(f"policy_check_error:{exc}")
        try:
            current_impl = current_implementation_fingerprints()
            for name, frozen in (record.get("implementation_fingerprints") or {}).items():
                if str((current_impl.get(name) or {}).get("sha256") or "") != str(frozen.get("sha256") or ""):
                    implementation_match = False
                    drift.append(f"implementation:{name}")
        except Exception as exc:
            implementation_match = False
            drift.append(f"implementation_check_error:{exc}")

    internal_valid = bool(
        record_hash_ok
        and source_freeze_ok
        and source_hash_ok
        and source_framework_ok
        and walk_signature_ok
        and source_verdict_ok
        and strategy_hash_ok
        and execution_hash_ok
        and framework_hash_ok
        and future_boundary_ok
    )
    current_match = bool(policy_match and implementation_match)
    return {
        "record_integrity_ok": record_hash_ok,
        "source_portfolio_freeze_ok": source_freeze_ok,
        "source_portfolio_hash_ok": source_hash_ok,
        "source_portfolio_framework_ok": source_framework_ok,
        "source_walk_forward_signature_ok": walk_signature_ok,
        "source_walk_forward_verdict_ok": source_verdict_ok,
        "strategy_hashes_ok": strategy_hash_ok,
        "execution_config_hashes_ok": execution_hash_ok,
        "future_framework_hash_ok": framework_hash_ok,
        "future_boundary_ok": future_boundary_ok,
        "policy_integrity_ok": policy_match,
        "implementation_integrity_ok": implementation_match,
        "internal_valid": internal_valid,
        "current_framework_match": current_match,
        "drift": drift,
        "ok": bool(internal_valid and (current_match if check_current else True)),
    }


def save_future_freeze(record: dict[str, Any], output_dir: str | Path = FREEZE_DIR) -> Path:
    verification = verify_future_freeze(record, check_current=False)
    if not verification["ok"]:
        raise ValueError("Refusing to save an invalid V28.23 future freeze record.")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    uniqueness = str(record.get("freeze_uniqueness_key") or "")
    if bool(((record.get("policies") or {}).get("future_holdout") or {}).get("payload", {}).get("one_future_freeze_per_source_framework", True)):
        for path in root.glob("*.json"):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if uniqueness and str(existing.get("freeze_uniqueness_key") or "") == uniqueness:
                if str(existing.get("record_sha256") or "") == str(record.get("record_sha256") or ""):
                    return path
                raise ValueError("This exact passed source framework already has a future-data clock. Do not restart the clock after outcomes begin.")
    path = root / f"{record['freeze_id']}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if str(existing.get("record_sha256")) != str(record.get("record_sha256")):
            raise ValueError("Freeze id already exists with different content.")
        return path
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_future_freeze(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("future freeze must be a JSON object")
    return payload


def list_future_freezes(output_dir: str | Path = FREEZE_DIR) -> list[dict[str, Any]]:
    root = Path(output_dir)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            record = load_future_freeze(path)
            rows.append({"path": str(path), "record": record, "verification": verify_future_freeze(record, check_current=True)})
        except Exception:
            continue
    return rows


def _calendar_status(record: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = pd.Timestamp(now_dt).normalize()
    first = _utc(record.get("first_eligible_date"))
    target_end = _utc(record.get("target_end_date"))
    if pd.isna(first) or pd.isna(target_end):
        return {"calendar_complete": False, "observed_days": 0, "remaining_days": 0, "reason": "Invalid freeze dates."}
    completed_through = today - pd.Timedelta(days=1)
    observed_end = min(completed_through, target_end)
    observed_days = max(0, int((observed_end - first).days) + 1) if observed_end >= first else 0
    target_days = int(record.get("target_holdout_days") or 0)
    remaining = max(0, target_days - observed_days)
    return {
        "calendar_complete": bool(completed_through >= target_end),
        "observed_days": observed_days,
        "target_days": target_days,
        "remaining_days": remaining,
        "minimum_observation_days": int(record.get("minimum_observation_days") or 0),
        "minimum_observed": observed_days >= int(record.get("minimum_observation_days") or 0),
        "first_eligible_date": first.date().isoformat(),
        "target_end_date": target_end.date().isoformat(),
    }


def _required_last_open(target_end: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    minutes = timeframe_minutes(timeframe)
    if minutes <= 0:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return target_end.normalize() + pd.Timedelta(days=1) - pd.Timedelta(minutes=minutes)


def future_holdout_readiness(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    store_root: str | Path | None = None,
) -> dict[str, Any]:
    policy = dict(((record.get("policies") or {}).get("future_holdout") or {}).get("payload") or load_future_holdout_policy())
    require_current = bool(policy.get("require_current_framework_match", True))
    verification = verify_future_freeze(record, check_current=require_current)
    calendar = _calendar_status(record, now=now)
    first = _utc(record.get("first_eligible_date"))
    target_end = _utc(record.get("target_end_date"))
    root = Path(store_root or record.get("source_root") or OHLCV_STORE_ROOT)
    status_rows: list[dict[str, Any]] = []
    data_ready = True
    if pd.isna(first) or pd.isna(target_end):
        data_ready = False
    else:
        end_inclusive = target_end.normalize() + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
        timeframes = list(dict.fromkeys([str(record.get("entry_timeframe") or "1h"), str(record.get("analysis_timeframe") or "4h")]))
        for symbol in record.get("symbols") or []:
            for timeframe in timeframes:
                audit = audit_store_integrity(
                    str(symbol),
                    timeframe,
                    start=first,
                    end=end_inclusive,
                    store_root=root,
                    now=now,
                )
                first_open = _utc(audit.get("first_open_time"))
                last_open = _utc(audit.get("last_open_time"))
                required_last = _required_last_open(target_end, timeframe)
                boundary_ok = bool(
                    int(audit.get("rows") or 0) > 0
                    and not pd.isna(first_open)
                    and first_open <= first
                    and not pd.isna(last_open)
                    and last_open >= required_last
                )
                continuity_ok = bool(audit.get("continuity_ok", False))
                ready = bool(boundary_ok and continuity_ok)
                data_ready = data_ready and ready
                status_rows.append(
                    {
                        "symbol": str(symbol),
                        "timeframe": timeframe,
                        "rows": int(audit.get("rows") or 0),
                        "first_open_time": None if pd.isna(first_open) else first_open.isoformat(),
                        "last_open_time": None if pd.isna(last_open) else last_open.isoformat(),
                        "required_last_open_time": required_last.isoformat(),
                        "gap_count": int(audit.get("gap_count") or 0),
                        "missing_rows": int(audit.get("missing_rows") or 0),
                        "unclosed_rows": int(audit.get("unclosed_rows") or 0),
                        "continuity_ok": continuity_ok,
                        "ready": ready,
                    }
                )

    ready = bool(verification.get("ok", False) and calendar.get("calendar_complete", False) and data_ready)
    if not verification.get("internal_valid", False):
        reason = "Future freeze integrity failed."
    elif require_current and not verification.get("current_framework_match", False):
        reason = "The behavioral framework has drifted since the freeze. Run the holdout with the exact frozen implementation/policies or invalidate this test."
    elif not calendar.get("calendar_complete", False):
        reason = f"Collecting genuinely future data: {calendar.get('observed_days', 0)}/{calendar.get('target_days', 0)} full UTC days observed."
    elif not data_ready:
        reason = "The calendar window is complete, but entry/analysis OHLCV is not complete and continuous through the fixed endpoint."
    else:
        reason = "Fixed future window and data-integrity checks are complete; one frozen evaluation may be queued."
    return {
        **calendar,
        "ready": ready,
        "reason": reason,
        "freeze_integrity": verification,
        "data_ready": data_ready,
        "data_status": pd.DataFrame(status_rows),
    }


def _active_future_job_exists(freeze_id: str, record_hash: str) -> bool:
    for job in list_jobs(["queued", "running", "completed"]):
        if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
            continue
        if str(job.get("freeze_id") or "") == freeze_id and str(job.get("freeze_record_sha256") or "") == record_hash:
            return True
    return False


def queue_future_holdout(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    store_root: str | Path | None = None,
    job_creator: Callable[..., Path] = create_task_job,
    readiness_checker: Callable[..., dict[str, Any]] = future_holdout_readiness,
) -> dict[str, Any]:
    readiness = readiness_checker(record, now=now, store_root=store_root)
    if not bool(readiness.get("ready", False)):
        return {"queued": False, "reason": readiness.get("reason") or "Future holdout is not ready.", "readiness": readiness}
    freeze_id = str(record.get("freeze_id") or "")
    record_hash = str(record.get("record_sha256") or "")
    if _active_future_job_exists(freeze_id, record_hash):
        return {"queued": False, "reason": "This exact future holdout has already been queued, is running, or has completed."}

    symbols = [str(x).upper() for x in (record.get("symbols") or [])]
    exec_map = {str(x.get("research_family")): dict(x.get("config") or {}) for x in (record.get("execution_config_snapshots") or [])}
    tasks: list[dict[str, Any]] = []
    for strategy in record.get("strategy_snapshots") or []:
        family = str(strategy.get("research_family") or "")
        payload = deepcopy(strategy.get("strategy_payload") or {})
        meta = {
            "freeze_id": freeze_id,
            "freeze_record_sha256": record_hash,
            "future_framework_sha256": str(record.get("future_framework_sha256") or ""),
            "research_family": family,
            "strategy_sha256": str(strategy.get("strategy_sha256") or ""),
            "execution_config_sha256": next((str(x.get("config_sha256") or "") for x in (record.get("execution_config_snapshots") or []) if str(x.get("research_family")) == family), ""),
            "holdout_start": str(record.get("first_eligible_date") or ""),
            "holdout_end": str(record.get("target_end_date") or ""),
            "role": "genuinely_future_holdout",
        }
        overrides = deepcopy(exec_map.get(family) or {})
        overrides.update({"run_kind": RUN_KIND, "v28_23_future_holdout": meta})
        tasks.append(
            {
                "name": f"V28.23 Future Holdout | {family} | {strategy.get('strategy_name', 'Strategy')}",
                "scenario_name": f"V28.23 {family} FUTURE_HOLDOUT",
                "strategy_payload": payload,
                "symbols": symbols,
                "task_meta": {"v28_23_future_holdout": meta},
                "config_overrides": overrides,
            }
        )

    path = job_creator(
        source_root=str(store_root or record.get("source_root") or OHLCV_STORE_ROOT),
        symbols=symbols,
        entry_timeframe=str(record.get("entry_timeframe") or "1h"),
        analysis_timeframe=str(record.get("analysis_timeframe") or "4h"),
        start_date=str(record.get("first_eligible_date") or ""),
        end_date=str(record.get("target_end_date") or ""),
        base_config={},
        tasks=tasks,
        comment="V28.23 genuinely future adaptive-portfolio holdout. Research only; no paper/live execution.",
        job_type=RUN_KIND,
        extra={
            "run_kind": RUN_KIND,
            "freeze_id": freeze_id,
            "freeze_record_sha256": record_hash,
            "future_framework_sha256": str(record.get("future_framework_sha256") or ""),
            "holdout_start": str(record.get("first_eligible_date") or ""),
            "holdout_end": str(record.get("target_end_date") or ""),
            "preliminary_evaluation_allowed": False,
        },
    )
    return {"queued": True, "job_path": str(path), "tasks_created": len(tasks), "readiness": readiness}


def future_holdout_jobs(statuses: list[str] | None = None, *, freeze_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in list_jobs(statuses):
        if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
            continue
        if freeze_id and str(job.get("freeze_id") or "") != str(freeze_id):
            continue
        rows.append(job)
    return sorted(rows, key=lambda x: str(x.get("created_at") or ""), reverse=True)


def _config_subset_hash(config: dict[str, Any], expected: dict[str, Any]) -> str:
    subset = {key: config.get(key) for key in expected.keys()}
    return _canonical_hash(subset)


def verify_future_job(
    job: dict[str, Any],
    record: dict[str, Any],
    *,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
    require_completed: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    if str(job.get("job_type") or job.get("run_kind") or "") != RUN_KIND:
        issues.append("job_type")
    if require_completed and str(job.get("status") or "") != "completed":
        issues.append("job_not_completed")
    if str(job.get("freeze_id") or "") != str(record.get("freeze_id") or ""):
        issues.append("freeze_id")
    if str(job.get("freeze_record_sha256") or "") != str(record.get("record_sha256") or ""):
        issues.append("freeze_record_sha256")
    if str(job.get("future_framework_sha256") or "") != str(record.get("future_framework_sha256") or ""):
        issues.append("future_framework_sha256")
    if str(job.get("start_date") or "") != str(record.get("first_eligible_date") or ""):
        issues.append("start_date")
    if str(job.get("end_date") or "") != str(record.get("target_end_date") or ""):
        issues.append("end_date")
    if str(job.get("entry_timeframe") or "") != str(record.get("entry_timeframe") or ""):
        issues.append("entry_timeframe")
    if str(job.get("analysis_timeframe") or "") != str(record.get("analysis_timeframe") or ""):
        issues.append("analysis_timeframe")
    if sorted(str(x).upper() for x in (job.get("symbols") or [])) != sorted(str(x).upper() for x in (record.get("symbols") or [])):
        issues.append("symbols")

    expected_strategy = {str(x.get("research_family")): str(x.get("strategy_sha256") or "") for x in (record.get("strategy_snapshots") or [])}
    expected_exec = {str(x.get("research_family")): x for x in (record.get("execution_config_snapshots") or [])}
    seen: set[str] = set()
    result_rows: list[dict[str, Any]] = []
    for item in job.get("results") or []:
        run_dir = str(item.get("run_dir") or "")
        if not run_dir:
            issues.append("result_missing_run_dir")
            continue
        try:
            run = loader(run_dir)
        except Exception as exc:
            issues.append(f"load_error:{run_dir}:{exc}")
            continue
        manifest = dict(run.get("manifest") or {})
        payload = dict(manifest.get("strategy_payload") or {})
        config = dict(manifest.get("config") or {})
        payload_hash = _canonical_hash(payload)
        family = next((name for name, expected_hash in expected_strategy.items() if expected_hash == payload_hash), "")
        if not family:
            issues.append(f"unknown_strategy_hash:{payload_hash[:10]}")
            continue
        if family in seen:
            issues.append(f"duplicate_family:{family}")
        seen.add(family)
        expected_cfg = dict((expected_exec.get(family) or {}).get("config") or {})
        config_ok = _config_subset_hash(config, expected_cfg) == _canonical_hash(expected_cfg)
        if not config_ok:
            issues.append(f"execution_config:{family}")
        meta = dict(config.get("v28_23_future_holdout") or (item.get("task_meta") or {}).get("v28_23_future_holdout") or {})
        if str(meta.get("freeze_record_sha256") or "") != str(record.get("record_sha256") or ""):
            issues.append(f"result_freeze_hash:{family}")
        if str(meta.get("strategy_sha256") or "") != expected_strategy.get(family, ""):
            issues.append(f"result_strategy_hash:{family}")
        if str(meta.get("execution_config_sha256") or "") != str((expected_exec.get(family) or {}).get("config_sha256") or ""):
            issues.append(f"result_execution_hash:{family}")
        result_rows.append({"research_family": family, "payload_hash": payload_hash, "execution_config_ok": config_ok, "run_dir": run_dir})

    missing = sorted(set(expected_strategy) - seen)
    for family in missing:
        issues.append(f"missing_family:{family}")
    if len(job.get("results") or []) != len(expected_strategy):
        issues.append("result_count")
    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "expected_families": sorted(expected_strategy),
        "seen_families": sorted(seen),
        "results": pd.DataFrame(result_rows),
    }


def analyze_future_job_with_frozen_policies(
    job: dict[str, Any],
    record: dict[str, Any],
    *,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> AdaptiveEvidenceResult:
    chosen, warnings = _representative_runs(job, loader)
    family_frames: dict[str, pd.DataFrame] = {}
    family_meta: dict[str, dict[str, Any]] = {}
    for family, selected in chosen.items():
        meta = dict(selected.get("meta") or {})
        family_meta[family] = meta
        family_frames[family] = _normalize_saved_trades(selected.get("run") or {}, family, meta)
    integrity = _integrity_for_job(job, chosen)
    policies = dict(record.get("policies") or {})
    adaptive_policy = dict((policies.get("adaptive_evidence") or {}).get("payload") or {})
    replay_policy = dict((policies.get("market_state_replay") or {}).get("payload") or {})
    router_policy = dict((policies.get("market_state_router") or {}).get("payload") or {})
    replay = run_market_state_replay(
        source_root=str(job.get("source_root") or record.get("source_root") or OHLCV_STORE_ROOT),
        symbols=[str(s).upper() for s in (job.get("symbols") or [])],
        start_date=job.get("start_date"),
        end_date=job.get("end_date"),
        policy=replay_policy,
        router_policy=router_policy,
    )
    result = evaluate_adaptive_trade_frames(
        family_frames,
        replay.history,
        family_meta=family_meta,
        policy=adaptive_policy,
        integrity=integrity,
    )
    result.job_id = str(job.get("job_id") or "")
    result.critique[:0] = warnings
    return result


def _contribution(frame: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if frame is None or frame.empty or group_col not in frame.columns:
        return pd.DataFrame(columns=[group_col, "trades", "notional_usd", "pnl_usd", "win_rate"])
    work = frame.copy()
    work["pnl_usd"] = pd.to_numeric(work.get("pnl_usd"), errors="coerce").fillna(0.0)
    work["notional_usd"] = pd.to_numeric(work.get("notional_usd"), errors="coerce").fillna(0.0)
    out = work.groupby(group_col, as_index=False).agg(
        trades=("pnl_usd", "size"),
        notional_usd=("notional_usd", "sum"),
        pnl_usd=("pnl_usd", "sum"),
        win_rate=("pnl_usd", lambda s: float((s > 0).mean() * 100.0)),
    )
    for col in ["notional_usd", "pnl_usd", "win_rate"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    return out.sort_values("pnl_usd", ascending=False).reset_index(drop=True)


def evaluate_future_evidence(
    evidence: AdaptiveEvidenceResult,
    record: dict[str, Any],
    *,
    job_integrity: dict[str, Any],
    freeze_verification: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> FutureAdaptivePortfolioHoldoutResult:
    policy = dict(((record.get("policies") or {}).get("future_holdout") or {}).get("payload") or load_future_holdout_policy())
    calendar = _calendar_status(record, now=now)
    if not bool(policy.get("allow_preliminary_evaluation", False)) and not bool(calendar.get("calendar_complete", False)):
        raise ValueError("V28.23 preliminary evaluation is disabled. Wait until the precommitted future endpoint has fully elapsed.")

    require_current = bool(policy.get("require_current_framework_match", True))
    verification = dict(freeze_verification or verify_future_freeze(record, check_current=require_current))
    shared_policy = dict(((record.get("policies") or {}).get("shared_account") or {}).get("payload") or load_shared_account_policy())
    annotated = evidence.annotated_trades.copy() if isinstance(evidence.annotated_trades, pd.DataFrame) else pd.DataFrame()
    static_candidates = prepare_portfolio_candidates(annotated, "static")
    adaptive_candidates = prepare_portfolio_candidates(annotated, "adaptive")
    static_run = simulate_shared_account(static_candidates, mode="static", policy=shared_policy)
    adaptive_run = simulate_shared_account(adaptive_candidates, mode="adaptive", policy=shared_policy)
    s = static_run["summary"]
    a = adaptive_run["summary"]

    friction_rows: list[dict[str, Any]] = []
    for bps in policy.get("friction_stress_round_trip_bps") or [0, 5, 10, 20, 40]:
        sr = simulate_shared_account(static_candidates, mode="static", policy=shared_policy, extra_friction_bps=float(bps))["summary"]
        ar = simulate_shared_account(adaptive_candidates, mode="adaptive", policy=shared_policy, extra_friction_bps=float(bps))["summary"]
        friction_rows.append(
            {
                "extra_round_trip_bps": float(bps),
                "static_return_pct": _num(sr.get("return_pct")),
                "adaptive_return_pct": _num(ar.get("return_pct")),
                "return_uplift_pct_points": round(_num(ar.get("return_pct")) - _num(sr.get("return_pct")), 4),
                "static_profit_factor": _num(sr.get("profit_factor")),
                "adaptive_profit_factor": _num(ar.get("profit_factor")),
                "adaptive_ending_equity_usd": _num(ar.get("ending_equity_usd")),
            }
        )
    friction = pd.DataFrame(friction_rows)

    ledger = adaptive_run["ledger"]
    by_symbol = _contribution(ledger, "symbol")
    by_family = _contribution(ledger, "research_family")
    by_month = _contribution(ledger, "period_month")
    positive_symbol_share = float((by_symbol["pnl_usd"] > 0).mean()) if not by_symbol.empty else 0.0
    positive_month_share = float((by_month["pnl_usd"] > 0).mean()) if not by_month.empty else 0.0
    return_uplift = _num(a.get("return_pct")) - _num(s.get("return_pct"))
    pf_uplift = _num(a.get("profit_factor")) - _num(s.get("profit_factor"))
    required_bps = _num(policy.get("required_positive_friction_stress_bps"), 10.0)
    stress_rows = friction[friction["extra_round_trip_bps"] >= required_bps]
    stress_row = stress_rows.sort_values("extra_round_trip_bps").iloc[0] if not stress_rows.empty else None
    friction_survival = bool(
        stress_row is not None
        and _num(stress_row.get("adaptive_return_pct")) > 0
        and _num(stress_row.get("adaptive_profit_factor")) >= 1.0
    )
    benchmark_accepted = bool(a.get("benchmark_trade_accepted", False))

    checks = {
        "freeze_integrity": bool(verification.get("internal_valid", False)),
        "current_framework_match": bool(verification.get("current_framework_match", False)) if require_current else True,
        "job_integrity": bool(job_integrity.get("ok", False)) if bool(policy.get("require_job_integrity", True)) else True,
        "source_walk_forward_pass": str(record.get("source_walk_forward_verdict") or "") == str(policy.get("require_source_verdict") or "pass_for_future_freeze"),
        "future_window_complete": bool(calendar.get("calendar_complete", False)),
        "source_evidence_integrity": bool((evidence.integrity or {}).get("ok", False)) if bool(policy.get("require_data_integrity", True)) else True,
        "sample_size": int(a.get("accepted_trades") or 0) >= int(policy.get("min_accepted_trades", 30)),
        "active_symbols": int(a.get("active_symbols") or 0) >= int(policy.get("min_active_symbols", 2)),
        "active_months": int(a.get("active_months") or 0) >= int(policy.get("min_active_months", 2)),
        "positive_return": _num(a.get("return_pct")) > 0,
        "profit_factor": _num(a.get("profit_factor")) >= _num(policy.get("min_profit_factor"), 1.10),
        "drawdown_control": _num(a.get("max_realized_drawdown_pct")) <= _num(policy.get("max_realized_drawdown_pct"), 12.0),
        "pair_stability": positive_symbol_share >= _num(policy.get("min_positive_symbol_share"), 0.67),
        "month_stability": positive_month_share >= _num(policy.get("min_positive_month_share"), 0.50),
        "adaptive_return_uplift": return_uplift >= _num(policy.get("min_return_uplift_pct_points"), 0.0),
        "adaptive_pf_resilience": pf_uplift >= _num(policy.get("min_profit_factor_uplift"), -0.05),
        "friction_survival": friction_survival,
        "benchmark_clear": not benchmark_accepted if bool(policy.get("block_if_benchmark_accepted", True)) else True,
    }

    insufficient = int(a.get("accepted_trades") or 0) < int(policy.get("min_accepted_trades", 30)) or int(a.get("active_symbols") or 0) < int(policy.get("min_active_symbols", 2))
    hard_negative = int(a.get("accepted_trades") or 0) >= 10 and (_num(a.get("return_pct")) <= 0 or _num(a.get("profit_factor")) < 1.0)
    static_better = return_uplift < 0 and pf_uplift <= 0
    integrity_failure = not checks["freeze_integrity"] or not checks["current_framework_match"] or not checks["job_integrity"] or not checks["source_evidence_integrity"] or not checks["future_window_complete"]

    if integrity_failure:
        verdict_name = "invalid_freeze_or_test"
    elif insufficient:
        verdict_name = "insufficient_future_evidence"
    elif hard_negative:
        verdict_name = "future_holdout_fail"
    elif static_better:
        verdict_name = "static_baseline_better_future"
    elif all(checks.values()):
        verdict_name = "pass_for_frozen_paper"
    else:
        verdict_name = "future_holdout_mixed"

    if verdict_name == "pass_for_frozen_paper":
        next_step = "Run a separate frozen paper-validation protocol with the same framework and no retuning. This is still not live-trading permission."
    elif verdict_name == "static_baseline_better_future":
        next_step = "Do not promote the adaptive router. On genuinely future data the simpler shared-account baseline is stronger."
    elif verdict_name == "future_holdout_fail":
        next_step = "Reject this frozen framework version. Any redesign requires a new framework hash and a new future-data clock; do not reuse this holdout window for tuning claims."
    elif verdict_name == "invalid_freeze_or_test":
        next_step = "Repair the integrity/process failure or rerun from the exact frozen implementation. Do not interpret the economics as valid future evidence."
    else:
        next_step = "Keep this framework in research. Do not paper/live deploy until the future-evidence gates are independently satisfied."

    verdict = {
        "version": str(policy.get("version") or "28.23"),
        "verdict": verdict_name,
        "promotion_blocked": verdict_name != "pass_for_frozen_paper",
        "checks": checks,
        "checks_passed": int(sum(bool(v) for v in checks.values())),
        "checks_total": len(checks),
        "future_framework_sha256": str(record.get("future_framework_sha256") or ""),
        "future_holdout_days": int(record.get("target_holdout_days") or 0),
        "adaptive_trades": int(a.get("accepted_trades") or 0),
        "adaptive_return_pct": _num(a.get("return_pct")),
        "static_return_pct": _num(s.get("return_pct")),
        "return_uplift_pct_points": round(return_uplift, 4),
        "adaptive_profit_factor": _num(a.get("profit_factor")),
        "static_profit_factor": _num(s.get("profit_factor")),
        "profit_factor_uplift": round(pf_uplift, 4),
        "adaptive_max_realized_drawdown_pct": _num(a.get("max_realized_drawdown_pct")),
        "positive_symbol_share": round(positive_symbol_share, 4),
        "positive_month_share": round(positive_month_share, 4),
        "required_positive_friction_stress_bps": required_bps,
        "benchmark_trade_accepted": benchmark_accepted,
        "next_step": next_step,
    }

    critique = [
        "This is the first portfolio-level stage whose evaluation window begins only after the framework is frozen. It is therefore materially stronger evidence than re-slicing the historical research year.",
        "The endpoint is fixed before outcomes are known and preliminary evaluation is disabled by default, reducing optional-stopping and cherry-picking risk.",
        "Static and adaptive candidates use the same frozen shared-account constraints, so the adaptive architecture must justify its complexity economically on new data.",
        "The test still uses OHLCV backtest fills after the future window closes. It is not a record of real exchange fills, queue position, latency, funding surprises or operational failures.",
        "Portfolio drawdown is still realized-equity drawdown at exits; synchronized intratrade mark-to-market drawdown can be worse.",
        "One 60-day future window can still be regime-specific. A pass earns frozen paper validation, not a claim of durable profitability.",
    ]
    if static_better:
        critique.append("Adaptive routing underperformed the simpler static account on both return and profit factor in the genuinely future window.")
    if benchmark_accepted:
        critique.append("A benchmark-only compression trade was accepted in the future window, so production-oriented promotion remains blocked.")
    if not friction_survival:
        critique.append(f"The adaptive future account does not remain positive at the required +{required_bps:g} bps round-trip friction stress.")
    if insufficient:
        critique.append("The genuinely future sample is too small for the configured minimum evidence threshold; do not stretch the conclusion to force a pass.")
    if require_current and not verification.get("current_framework_match", False):
        critique.append("Relevant implementation or policy files changed after the freeze. Evaluating with changed logic would contaminate the future test.")

    rejected = pd.concat(
        [
            static_run["rejected"].assign(replay_mode="static") if not static_run["rejected"].empty else pd.DataFrame(),
            adaptive_run["rejected"].assign(replay_mode="adaptive") if not adaptive_run["rejected"].empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )
    integrity = {
        "freeze": verification,
        "job": job_integrity,
        "source_evidence": dict(evidence.integrity or {}),
        "calendar": calendar,
        "record_sha256": str(record.get("record_sha256") or ""),
        "future_framework_sha256": str(record.get("future_framework_sha256") or ""),
        "genuinely_future_protocol": True,
    }
    return FutureAdaptivePortfolioHoldoutResult(
        freeze_id=str(record.get("freeze_id") or ""),
        comparison=pd.DataFrame([s, a]),
        friction_stress=friction,
        adaptive_equity=adaptive_run["equity_curve"],
        static_equity=static_run["equity_curve"],
        adaptive_ledger=adaptive_run["ledger"],
        static_ledger=static_run["ledger"],
        rejected_candidates=rejected,
        by_symbol=by_symbol,
        by_family=by_family,
        by_month=by_month,
        verdict=verdict,
        integrity=integrity,
        critique=critique,
    )


def evaluate_future_holdout_job(
    job: dict[str, Any],
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    loader: Callable[[str | Path], dict[str, Any]] = load_saved_backtest,
) -> FutureAdaptivePortfolioHoldoutResult:
    policy = dict(((record.get("policies") or {}).get("future_holdout") or {}).get("payload") or load_future_holdout_policy())
    calendar = _calendar_status(record, now=now)
    if not bool(policy.get("allow_preliminary_evaluation", False)) and not bool(calendar.get("calendar_complete", False)):
        raise ValueError("V28.23 preliminary evaluation is disabled until the fixed future endpoint has fully elapsed.")
    require_current = bool(policy.get("require_current_framework_match", True))
    verification = verify_future_freeze(record, check_current=require_current)
    job_integrity = verify_future_job(job, record, loader=loader, require_completed=True)
    evidence = analyze_future_job_with_frozen_policies(job, record, loader=loader)
    return evaluate_future_evidence(
        evidence,
        record,
        job_integrity=job_integrity,
        freeze_verification=verification,
        now=now,
    )
