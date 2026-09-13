from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any, Callable

import numpy as np
import pandas as pd

from .backtest_core import timeframe_minutes
from .engine import evaluate_trade_outcome
from .future_adaptive_portfolio_holdout_v2823 import (
    SNAPSHOT_DIR as FUTURE_SNAPSHOT_DIR,
    load_future_freeze,
    verify_future_freeze,
)
from .ohlcv_store import load_range
from .prospective_paper_signals_v2824 import (
    evaluate_frozen_symbol_decision,
    fetch_or_proxy_quote,
    materialize_candidate,
    normalize_quote,
)
from .runtime_state import atomic_write_json
from .settings import OHLCV_STORE_ROOT

POLICY_PATH = Path("config/prospective_paper_validation_policy.json")
FREEZE_DIR = Path("data/backtest_reviews/prospective_paper_freezes")
SESSION_ROOT = Path("data/paper_validation/sessions")
SNAPSHOT_DIR = Path("data/backtest_reviews/prospective_paper_validation")
SCHEMA_VERSION = "28.24-prospective-paper-v1"

PAPER_IMPLEMENTATION_FILES = {
    "prospective_paper_validation": Path("app_src/prospective_paper_validation_v2824.py"),
    "prospective_paper_signals": Path("app_src/prospective_paper_signals_v2824.py"),
    "backtest_core": Path("app_src/backtest_core.py"),
    "backtest_core_legacy": Path("app_src/backtest_core_legacy.py"),
    "engine": Path("app_src/engine.py"),
    "features": Path("app_src/features.py"),
    "strategies": Path("app_src/strategies.py"),
    "exit_families": Path("app_src/exit_families.py"),
    "market_state": Path("app_src/market_state_v2816.py"),
    "adaptive_evidence": Path("app_src/adaptive_evidence_v2820.py"),
    "shared_account_replay": Path("app_src/shared_account_replay_v2821.py"),
    "market_data": Path("app_src/market_data.py"),
    "ohlcv_store": Path("app_src/ohlcv_store.py"),
}


@dataclass
class ProspectivePaperValidationResult:
    session_id: str
    comparison: pd.DataFrame
    adaptive_trades: pd.DataFrame
    static_trades: pd.DataFrame
    equity_history: pd.DataFrame
    decisions: pd.DataFrame
    incidents: pd.DataFrame
    by_symbol: pd.DataFrame
    by_family: pd.DataFrame
    verdict: dict[str, Any]
    integrity: dict[str, Any]
    critique: list[str]


def load_paper_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("prospective paper validation policy must be a JSON object")
    return payload


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
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


def _now_utc(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def current_paper_implementation_fingerprints(paths: dict[str, Path] | None = None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name, path in (paths or PAPER_IMPLEMENTATION_FILES).items():
        out[name] = {"path": str(path), "sha256": _file_sha256(path)}
    return out


def paper_policy_snapshot(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(policy or load_paper_policy())
    return {
        "path": str(POLICY_PATH),
        "version": str(payload.get("version") or "28.24"),
        "sha256": _canonical_hash(payload),
        "payload": payload,
    }


def list_future_holdout_snapshots(root: str | Path = FUTURE_SNAPSHOT_DIR) -> list[dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(base.glob("*_summary.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                rows.append({"path": str(path), "summary": payload, "sha256": _canonical_hash(payload)})
        except Exception:
            continue
    return rows


def _next_full_bar_decision(cutoff: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    minutes = timeframe_minutes(timeframe)
    if minutes <= 0:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    delta = pd.Timedelta(minutes=minutes)
    floored = cutoff.floor(f"{minutes}min")
    next_open = floored if cutoff == floored else floored + delta
    return next_open + delta


def paper_freeze_record_hash(record: dict[str, Any]) -> str:
    core = dict(record or {})
    core.pop("record_sha256", None)
    return _canonical_hash(core)


def build_paper_session_freeze(
    future_freeze: dict[str, Any],
    future_snapshot_summary: dict[str, Any],
    *,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(policy or load_paper_policy())
    required = str(policy.get("require_source_verdict") or "pass_for_frozen_paper")
    actual = str((future_snapshot_summary.get("verdict") or {}).get("verdict") or "")
    if actual != required:
        raise ValueError(f"V28.24 requires V28.23 verdict {required}; got {actual or 'unknown'}.")
    future_verification = verify_future_freeze(future_freeze, check_current=bool(policy.get("require_current_framework_match", True)))
    if not bool(future_verification.get("ok", False)):
        raise ValueError(f"Cannot start paper validation from a drifted/invalid V28.23 freeze: {future_verification.get('drift') or []}")
    if str(future_snapshot_summary.get("freeze_id") or "") != str(future_freeze.get("freeze_id") or ""):
        raise ValueError("V28.23 snapshot freeze id does not match the selected future freeze.")
    if str(future_snapshot_summary.get("freeze_record_sha256") or "") != str(future_freeze.get("record_sha256") or ""):
        raise ValueError("V28.23 snapshot freeze hash does not match the selected future freeze.")
    if str(future_snapshot_summary.get("future_framework_sha256") or "") != str(future_freeze.get("future_framework_sha256") or ""):
        raise ValueError("V28.23 snapshot framework hash does not match the selected future freeze.")

    created = pd.Timestamp(_now_utc(now))
    entry_tf = str(future_freeze.get("entry_timeframe") or "1h")
    delta = pd.Timedelta(minutes=max(1, timeframe_minutes(entry_tf)))
    first_decision = _next_full_bar_decision(created, entry_tf)
    target_days = max(1, int(policy.get("target_paper_days", 30)))
    target_end = first_decision + pd.Timedelta(days=target_days) - delta
    snapshot_hash = _canonical_hash(future_snapshot_summary)
    paper_policy = paper_policy_snapshot(policy)
    implementations = current_paper_implementation_fingerprints()
    source_framework = str(future_freeze.get("future_framework_sha256") or "")
    uniqueness_key = _canonical_hash({
        "source_future_freeze_sha256": str(future_freeze.get("record_sha256") or ""),
        "source_future_snapshot_sha256": snapshot_hash,
    })
    framework_payload = {
        "source_future_framework_sha256": source_framework,
        "source_future_snapshot_sha256": snapshot_hash,
        "paper_policy_sha256": paper_policy["sha256"],
        "implementation_hashes": {k: v["sha256"] for k, v in sorted(implementations.items())},
    }
    paper_framework = _canonical_hash(framework_payload)
    session_id = f"pp24_{created.strftime('%Y%m%dT%H%M%SZ')}_{paper_framework[:10]}"
    shared_policy = dict((((future_freeze.get("policies") or {}).get("shared_account") or {}).get("payload") or {}))
    record = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "created_at": created.isoformat(),
        "cutoff_utc": created.isoformat(),
        "first_eligible_decision_utc": first_decision.isoformat(),
        "target_end_utc": target_end.isoformat(),
        "target_paper_days": target_days,
        "source_future_freeze": deepcopy(future_freeze),
        "source_future_freeze_id": str(future_freeze.get("freeze_id") or ""),
        "source_future_freeze_sha256": str(future_freeze.get("record_sha256") or ""),
        "source_future_framework_sha256": source_framework,
        "source_future_snapshot_summary": deepcopy(future_snapshot_summary),
        "source_future_snapshot_sha256": snapshot_hash,
        "source_future_verdict": actual,
        "symbols": [str(x).upper() for x in (future_freeze.get("symbols") or [])],
        "entry_timeframe": entry_tf,
        "analysis_timeframe": str(future_freeze.get("analysis_timeframe") or "4h"),
        "source_root": str(future_freeze.get("source_root") or OHLCV_STORE_ROOT),
        "paper_policy": paper_policy,
        "implementation_fingerprints": implementations,
        "paper_framework_sha256": paper_framework,
        "session_uniqueness_key": uniqueness_key,
        "starting_equity_usd": _num(shared_policy.get("starting_equity_usd"), 10000.0),
        "live_execution_enabled": False,
        "retroactive_signal_backfill_allowed": False,
        "status": "frozen_waiting_for_first_prospective_decision",
        "warning": "Paper decisions must be written within the configured lag after a candle closes. Missed decision bars are incidents, not reconstructed signals.",
    }
    record["record_sha256"] = paper_freeze_record_hash(record)
    return record


def verify_paper_session_freeze(record: dict[str, Any], *, check_current: bool = True) -> dict[str, Any]:
    record_ok = str(record.get("record_sha256") or "") == paper_freeze_record_hash(record)
    future = dict(record.get("source_future_freeze") or {})
    future_ok = bool(future and verify_future_freeze(future, check_current=False).get("ok", False))
    source_hash_ok = str(record.get("source_future_freeze_sha256") or "") == str(future.get("record_sha256") or "")
    source_framework_ok = str(record.get("source_future_framework_sha256") or "") == str(future.get("future_framework_sha256") or "")
    snapshot = dict(record.get("source_future_snapshot_summary") or {})
    snapshot_ok = str(record.get("source_future_snapshot_sha256") or "") == _canonical_hash(snapshot)
    required = str((((record.get("paper_policy") or {}).get("payload") or {}).get("require_source_verdict") or "pass_for_frozen_paper"))
    source_verdict_ok = str(record.get("source_future_verdict") or "") == required
    policy = dict(record.get("paper_policy") or {})
    policy_hash_ok = str(policy.get("sha256") or "") == _canonical_hash(policy.get("payload") or {})
    impl = dict(record.get("implementation_fingerprints") or {})
    framework_payload = {
        "source_future_framework_sha256": str(record.get("source_future_framework_sha256") or ""),
        "source_future_snapshot_sha256": str(record.get("source_future_snapshot_sha256") or ""),
        "paper_policy_sha256": str(policy.get("sha256") or ""),
        "implementation_hashes": {k: str(v.get("sha256") or "") for k, v in sorted(impl.items())},
    }
    framework_ok = str(record.get("paper_framework_sha256") or "") == _canonical_hash(framework_payload)
    first = _utc(record.get("first_eligible_decision_utc"))
    cutoff = _utc(record.get("cutoff_utc"))
    prospective_boundary_ok = bool(not pd.isna(first) and not pd.isna(cutoff) and first > cutoff)
    policy_match = True
    impl_match = True
    future_current_ok = True
    drift: list[str] = []
    if check_current:
        try:
            current_policy = paper_policy_snapshot()
            if str(current_policy.get("sha256") or "") != str(policy.get("sha256") or ""):
                policy_match = False
                drift.append("paper_policy")
        except Exception as exc:
            policy_match = False
            drift.append(f"paper_policy_check:{exc}")
        try:
            current_impl = current_paper_implementation_fingerprints()
            for name, frozen in impl.items():
                if str((current_impl.get(name) or {}).get("sha256") or "") != str(frozen.get("sha256") or ""):
                    impl_match = False
                    drift.append(f"implementation:{name}")
        except Exception as exc:
            impl_match = False
            drift.append(f"implementation_check:{exc}")
        if bool(((policy.get("payload") or {}).get("require_current_framework_match", True))):
            current_future = verify_future_freeze(future, check_current=True)
            future_current_ok = bool(current_future.get("ok", False))
            if not future_current_ok:
                drift.extend([f"source_future:{x}" for x in (current_future.get("drift") or [])])
    internal = bool(record_ok and future_ok and source_hash_ok and source_framework_ok and snapshot_ok and source_verdict_ok and policy_hash_ok and framework_ok and prospective_boundary_ok)
    return {
        "record_integrity_ok": record_ok,
        "source_future_freeze_ok": future_ok,
        "source_future_hash_ok": source_hash_ok,
        "source_future_framework_ok": source_framework_ok,
        "source_snapshot_integrity_ok": snapshot_ok,
        "source_verdict_ok": source_verdict_ok,
        "paper_policy_hash_ok": policy_hash_ok,
        "paper_framework_hash_ok": framework_ok,
        "prospective_boundary_ok": prospective_boundary_ok,
        "paper_policy_current_match": policy_match,
        "implementation_current_match": impl_match,
        "source_future_current_match": future_current_ok,
        "internal_valid": internal,
        "drift": drift,
        "ok": bool(internal and (policy_match and impl_match and future_current_ok if check_current else True)),
    }


def save_paper_session_freeze(record: dict[str, Any], output_dir: str | Path = FREEZE_DIR) -> Path:
    if not verify_paper_session_freeze(record, check_current=False).get("ok", False):
        raise ValueError("Refusing to save invalid V28.24 paper freeze.")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    uniqueness = str(record.get("session_uniqueness_key") or "")
    if bool((((record.get("paper_policy") or {}).get("payload") or {}).get("one_paper_session_per_source_result", True))):
        for path in root.glob("*.json"):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if uniqueness and str(existing.get("session_uniqueness_key") or "") == uniqueness:
                if str(existing.get("record_sha256") or "") == str(record.get("record_sha256") or ""):
                    return path
                raise ValueError("This exact passed V28.23 result already has a prospective paper clock. Do not restart it after outcomes begin.")
    path = root / f"{record['session_id']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_paper_session_freeze(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("paper session freeze must be a JSON object")
    return payload


def list_paper_session_freezes(root: str | Path = FREEZE_DIR) -> list[dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return []
    rows = []
    for path in sorted(base.glob("*.json"), reverse=True):
        try:
            record = load_paper_session_freeze(path)
            rows.append({"path": str(path), "record": record, "verification": verify_paper_session_freeze(record, check_current=True)})
        except Exception:
            continue
    return rows


def _session_dir(record: dict[str, Any], root: str | Path = SESSION_ROOT) -> Path:
    return Path(root) / str(record.get("session_id") or "unknown_session")


def _state_path(record: dict[str, Any], root: str | Path = SESSION_ROOT) -> Path:
    return _session_dir(record, root) / "state.json"


def _events_path(record: dict[str, Any], root: str | Path = SESSION_ROOT) -> Path:
    return _session_dir(record, root) / "events.jsonl"


def _new_account(starting_equity: float) -> dict[str, Any]:
    return {
        "starting_equity_usd": starting_equity,
        "realized_equity_usd": starting_equity,
        "mtm_equity_usd": starting_equity,
        "peak_mtm_equity_usd": starting_equity,
        "max_mtm_drawdown_pct": 0.0,
        "open_positions": [],
        "closed_trades": [],
        "accepted_trades": 0,
        "rejected_candidates": 0,
    }


def _initial_state(record: dict[str, Any]) -> dict[str, Any]:
    starting = max(1.0, _num(record.get("starting_equity_usd"), 10000.0))
    return {
        "schema_version": "28.24-paper-state-v1",
        "session_id": record.get("session_id"),
        "session_record_sha256": record.get("record_sha256"),
        "status": "collecting",
        "created_at": record.get("created_at"),
        "last_cycle_at": None,
        "last_event_sha256": None,
        "processed_decisions": {},
        "pending_confirmations": [],
        "virtual_positions": [],
        "cooldown_until": {},
        "accounts": {
            "adaptive": _new_account(starting),
            "static": _new_account(starting),
        },
        "counters": {
            "cycles": 0,
            "recorded_decision_slots": 0,
            "missed_decision_slots": 0,
            "data_gap_slots": 0,
            "executable_quote_slots": 0,
            "proxy_quote_slots": 0,
            "strategy_signals": 0,
            "adaptive_candidates": 0,
            "static_candidates": 0,
        },
    }


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def verify_event_chain(record: dict[str, Any], *, session_root: str | Path = SESSION_ROOT) -> dict[str, Any]:
    path = _events_path(record, session_root)
    previous = ""
    count = 0
    for idx, event in enumerate(_read_events(path)):
        expected_prev = str(event.get("previous_event_sha256") or "")
        if expected_prev != previous:
            return {"ok": False, "events": count, "bad_index": idx, "reason": "previous_hash_mismatch"}
        core = dict(event)
        stored = str(core.pop("event_sha256", "") or "")
        actual = _canonical_hash(core)
        if not stored or stored != actual:
            return {"ok": False, "events": count, "bad_index": idx, "reason": "event_hash_mismatch"}
        previous = stored
        count += 1
    return {"ok": True, "events": count, "last_event_sha256": previous}


def _append_event(
    record: dict[str, Any],
    state: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    *,
    recorded_at: datetime | pd.Timestamp | None = None,
    session_root: str | Path = SESSION_ROOT,
) -> dict[str, Any]:
    root = _session_dir(record, session_root)
    root.mkdir(parents=True, exist_ok=True)
    path = _events_path(record, session_root)
    ts = pd.Timestamp(recorded_at or datetime.now(timezone.utc))
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    event = {
        "event_index": len(_read_events(path)),
        "recorded_at": ts.isoformat(),
        "event_type": str(event_type),
        "session_id": record.get("session_id"),
        "session_record_sha256": record.get("record_sha256"),
        "previous_event_sha256": str(state.get("last_event_sha256") or ""),
        "payload": _json_clean(payload),
    }
    event["event_sha256"] = _canonical_hash(event)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    state["last_event_sha256"] = event["event_sha256"]
    return event


def initialize_paper_session(record: dict[str, Any], *, session_root: str | Path = SESSION_ROOT) -> dict[str, Any]:
    verification = verify_paper_session_freeze(record, check_current=True)
    if not verification.get("ok", False):
        raise ValueError(f"Cannot initialize invalid/drifted paper session: {verification.get('drift') or []}")
    path = _state_path(record, session_root)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _initial_state(record)
    _append_event(
        record,
        state,
        "SESSION_STARTED",
        {
            "paper_framework_sha256": record.get("paper_framework_sha256"),
            "source_future_framework_sha256": record.get("source_future_framework_sha256"),
            "first_eligible_decision_utc": record.get("first_eligible_decision_utc"),
            "target_end_utc": record.get("target_end_utc"),
            "live_execution_enabled": False,
        },
        recorded_at=_utc(record.get("created_at")),
        session_root=session_root,
    )
    atomic_write_json(path, state)
    return state


def load_paper_state(record: dict[str, Any], *, session_root: str | Path = SESSION_ROOT) -> dict[str, Any]:
    path = _state_path(record, session_root)
    if not path.exists():
        return initialize_paper_session(record, session_root=session_root)
    return json.loads(path.read_text(encoding="utf-8"))


def _shared_policy(record: dict[str, Any]) -> dict[str, Any]:
    future = dict(record.get("source_future_freeze") or {})
    return dict((((future.get("policies") or {}).get("shared_account") or {}).get("payload") or {}))


def _paper_policy(record: dict[str, Any]) -> dict[str, Any]:
    return dict(((record.get("paper_policy") or {}).get("payload") or {}))


def _interval_delta(record: dict[str, Any]) -> pd.Timedelta:
    return pd.Timedelta(minutes=max(1, timeframe_minutes(str(record.get("entry_timeframe") or "1h"))))


def _quote_close_price(position: dict[str, Any], quote: dict[str, Any], fallback: float) -> float:
    side = str(position.get("side") or "").upper()
    if quote.get("executable_top_of_book"):
        value = quote.get("best_bid" if side == "LONG" else "best_ask")
        price = _num(value, 0.0)
        if price > 0:
            return price
    return _num(quote.get("mark_price"), fallback) or fallback


def _position_outcome(position: dict[str, Any], record: dict[str, Any], through_time: pd.Timestamp, *, store_root: str | Path) -> dict[str, Any] | None:
    entry_time = _utc(position.get("entry_time"))
    if pd.isna(entry_time):
        return None
    delta = _interval_delta(record)
    end_open = through_time - delta
    candles = load_range(
        str(position.get("symbol")),
        str(record.get("entry_timeframe") or "1h"),
        start=entry_time,
        end=end_open,
        store_root=store_root,
    )
    if candles.empty:
        return None
    levels = dict(position.get("trade_levels") or {})
    outcome = evaluate_trade_outcome(
        candles,
        side=str(position.get("side")),
        entry_price=_num(position.get("paper_entry_price")),
        stop_loss=_num(levels.get("stop_loss_initial"), _num(levels.get("stop_loss"))),
        take_profit=_num(levels.get("take_profit")),
        opened_at=entry_time,
        tp_levels=list(levels.get("tp_levels") or []),
        late_trigger_index=int(levels.get("late_trigger_index") or 0),
        be_trigger_index=int(levels.get("be_trigger_index") or 0),
        lock_trigger_index=int(levels.get("lock_trigger_index") or 0),
        lock_to_tp_index=int(levels.get("lock_to_tp_index") or 0),
    )
    if not outcome:
        return None
    max_hold = max(1, int(position.get("max_hold_bars") or 288))
    if str(outcome.get("status")) == "OPEN" and len(candles) >= max_hold:
        last_close = _num(candles.iloc[-1].get("close"), _num(outcome.get("last_price")))
        outcome = dict(outcome)
        outcome.update({
            "status": "CLOSED",
            "close_reason": "MAX_HOLD_TIME_EXIT",
            "outcome_label": "MAX_HOLD_TIME_EXIT",
            "close_price": last_close,
        })
    outcome["bars_observed"] = int(len(candles))
    return outcome


def _exit_fill_price(side: str, model_close: float, config: dict[str, Any]) -> float:
    slippage = max(0.0, _num(config.get("slippage_bps_per_side"), 0.0))
    spread_rt = max(0.0, _num(config.get("spread_bps_round_trip"), _num(config.get("spread_bps"), 0.0)))
    adverse = slippage + spread_rt / 2.0
    if str(side).upper() == "LONG":
        return model_close * (1.0 - adverse / 10000.0)
    return model_close * (1.0 + adverse / 10000.0)


def _close_account_position(
    account: dict[str, Any],
    position: dict[str, Any],
    outcome: dict[str, Any],
    close_time: pd.Timestamp,
) -> dict[str, Any]:
    side = str(position.get("side") or "").upper()
    entry = _num(position.get("paper_entry_price"))
    model_close = _num(outcome.get("close_price"), _num(outcome.get("last_price")))
    config = dict(position.get("execution_config") or {})
    exit_price = _exit_fill_price(side, model_close, config)
    qty = _num(position.get("quantity"))
    direction = 1.0 if side == "LONG" else -1.0
    gross = qty * (exit_price - entry) * direction
    fee_bps = max(0.0, _num(config.get("fee_bps_per_side"), 0.0))
    exit_fee = abs(qty * exit_price) * fee_bps / 10000.0
    opened = _utc(position.get("entry_time"))
    hours = max(0.0, float((close_time - opened).total_seconds() / 3600.0)) if not pd.isna(opened) else 0.0
    funding_bps_8h = _num(config.get("funding_bps_per_8h"), 0.0)
    funding = _num(position.get("notional_usd")) * funding_bps_8h / 10000.0 * (hours / 8.0)
    entry_fee = _num(position.get("entry_fee_usd"))
    realized_delta = gross - exit_fee - funding
    account["realized_equity_usd"] = _num(account.get("realized_equity_usd")) + realized_delta
    net = gross - entry_fee - exit_fee - funding
    closed = {
        **{k: v for k, v in position.items() if k != "signal_features"},
        "exit_time": close_time.isoformat(),
        "model_exit_price": model_close,
        "paper_exit_price": exit_price,
        "close_reason": outcome.get("close_reason"),
        "outcome_label": outcome.get("outcome_label"),
        "mfe_pct": outcome.get("mfe_pct"),
        "mae_pct": outcome.get("mae_pct"),
        "gross_pnl_usd": round(gross, 6),
        "entry_fee_usd": round(entry_fee, 6),
        "exit_fee_usd": round(exit_fee, 6),
        "funding_cost_usd": round(funding, 6),
        "net_pnl_usd": round(net, 6),
        "duration_hours": round(hours, 4),
    }
    account.setdefault("closed_trades", []).append(closed)
    return closed


def _update_virtual_positions(
    record: dict[str, Any],
    state: dict[str, Any],
    through_time: pd.Timestamp,
    *,
    store_root: str | Path,
    session_root: str | Path,
) -> None:
    active = []
    delta = _interval_delta(record)
    for position in list(state.get("virtual_positions") or []):
        outcome = _position_outcome(position, record, through_time, store_root=store_root)
        if not outcome or str(outcome.get("status")) == "OPEN":
            active.append(position)
            continue
        key = f"{position.get('symbol')}|{position.get('research_family')}|{position.get('side')}"
        cooldown = max(0, int(position.get("cooldown_bars") or 0))
        state.setdefault("cooldown_until", {})[key] = (through_time + delta * cooldown).isoformat()
        _append_event(record, state, "VIRTUAL_STRATEGY_EXIT", {
            "candidate_id": position.get("candidate_id"),
            "symbol": position.get("symbol"),
            "research_family": position.get("research_family"),
            "side": position.get("side"),
            "close_reason": outcome.get("close_reason"),
            "cooldown_until": state["cooldown_until"][key],
        }, recorded_at=through_time, session_root=session_root)
    state["virtual_positions"] = active


def _update_account_positions(
    record: dict[str, Any],
    state: dict[str, Any],
    mode: str,
    through_time: pd.Timestamp,
    *,
    store_root: str | Path,
    session_root: str | Path,
) -> None:
    account = state["accounts"][mode]
    active = []
    for position in list(account.get("open_positions") or []):
        outcome = _position_outcome(position, record, through_time, store_root=store_root)
        if not outcome or str(outcome.get("status")) == "OPEN":
            active.append(position)
            continue
        closed = _close_account_position(account, position, outcome, through_time)
        _append_event(record, state, "PAPER_POSITION_CLOSED", {"mode": mode, **closed}, recorded_at=through_time, session_root=session_root)
    account["open_positions"] = active


def _virtual_blocked(state: dict[str, Any], signal: dict[str, Any], decision_time: pd.Timestamp) -> tuple[bool, str]:
    symbol = str(signal.get("symbol"))
    family = str(signal.get("research_family"))
    side = str(signal.get("side"))
    for position in state.get("virtual_positions") or []:
        if str(position.get("symbol")) == symbol and str(position.get("research_family")) == family and str(position.get("side")) == side:
            return True, "virtual_strategy_position_open"
    key = f"{symbol}|{family}|{side}"
    cooldown = _utc((state.get("cooldown_until") or {}).get(key))
    if not pd.isna(cooldown) and decision_time < cooldown:
        return True, "virtual_strategy_cooldown"
    return False, ""


def _candidate_id(signal: dict[str, Any], entry_time: pd.Timestamp) -> str:
    raw = {
        "symbol": signal.get("symbol"),
        "family": signal.get("research_family"),
        "side": signal.get("side"),
        "signal_time": signal.get("decision_time"),
        "entry_time": entry_time.isoformat(),
        "strategy_sha256": signal.get("strategy_sha256"),
    }
    return f"pp24_{_canonical_hash(raw)[:18]}"


def _open_virtual_candidate(state: dict[str, Any], candidate: dict[str, Any], entry_time: pd.Timestamp) -> None:
    state.setdefault("virtual_positions", []).append({
        **deepcopy(candidate),
        "entry_time": entry_time.isoformat(),
    })


def _process_pending_confirmations(
    record: dict[str, Any],
    state: dict[str, Any],
    symbol: str,
    decision_time: pd.Timestamp,
    current_close: float,
    quote: dict[str, Any],
    *,
    session_root: str | Path,
) -> list[dict[str, Any]]:
    delta = _interval_delta(record)
    keep: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for pending in state.get("pending_confirmations") or []:
        if str(pending.get("symbol")) != symbol:
            keep.append(pending)
            continue
        due = _utc(pending.get("confirm_decision_time"))
        if pd.isna(due) or decision_time < due:
            keep.append(pending)
            continue
        side = str(pending.get("side"))
        signal_close = _num(pending.get("reference_price"))
        confirmed = (side == "LONG" and current_close > signal_close) or (side == "SHORT" and current_close < signal_close)
        if not confirmed:
            _append_event(record, state, "CONFIRMATION_REJECTED", {
                "symbol": symbol,
                "research_family": pending.get("research_family"),
                "side": side,
                "signal_time": pending.get("decision_time"),
                "confirm_time": decision_time.isoformat(),
            }, recorded_at=decision_time, session_root=session_root)
            continue
        blocked, reason = _virtual_blocked(state, pending, decision_time)
        if blocked:
            _append_event(record, state, "CONFIRMATION_BLOCKED", {"reason": reason, **{k: pending.get(k) for k in ["symbol", "research_family", "side", "decision_time"]}}, recorded_at=decision_time, session_root=session_root)
            continue
        candidate = materialize_candidate(pending, quote, signal_features=dict(pending.get("features") or {}))
        if not candidate:
            continue
        candidate["entry_time"] = decision_time.isoformat()
        candidate["candidate_id"] = _candidate_id(pending, decision_time)
        candidate["confirmed_from_signal_time"] = pending.get("decision_time")
        _open_virtual_candidate(state, candidate, decision_time)
        candidates.append(candidate)
        _append_event(record, state, "CONFIRMATION_ACCEPTED", {
            "candidate_id": candidate["candidate_id"],
            "symbol": symbol,
            "research_family": candidate.get("research_family"),
            "side": side,
            "entry_time": decision_time.isoformat(),
        }, recorded_at=decision_time, session_root=session_root)
    state["pending_confirmations"] = keep
    return candidates


def _candidate_stream_for_decision(
    record: dict[str, Any],
    state: dict[str, Any],
    symbol: str,
    decision_time: pd.Timestamp,
    quote: dict[str, Any],
    *,
    store_root: str | Path,
    session_root: str | Path,
) -> list[dict[str, Any]]:
    paper_policy = _paper_policy(record)
    decision = evaluate_frozen_symbol_decision(
        record.get("source_future_freeze") or {},
        symbol,
        decision_time,
        source_root=store_root,
        paper_policy=paper_policy,
    )
    if not decision.get("ok"):
        _append_event(record, state, "DECISION_EVALUATION_FAILED", {"symbol": symbol, "decision_time": decision_time.isoformat(), "reason": decision.get("reason")}, recorded_at=decision_time, session_root=session_root)
        return []
    current_close = _num(decision.get("reference_price"))
    candidates = _process_pending_confirmations(record, state, symbol, decision_time, current_close, quote, session_root=session_root)
    delta = _interval_delta(record)
    for signal in decision.get("signals") or []:
        side = str(signal.get("side") or "")
        event_base = {
            "symbol": symbol,
            "decision_time": decision_time.isoformat(),
            "research_family": signal.get("research_family"),
            "strategy_name": signal.get("strategy_name"),
            "side": side,
            "score": signal.get("score"),
            "router_match": signal.get("router_match"),
            "market_state": signal.get("market_state"),
            "router_action": signal.get("router_action"),
            "preferred_strategy_family": signal.get("preferred_strategy_family"),
        }
        if not bool(signal.get("signal_allowed")) or side not in {"LONG", "SHORT"}:
            _append_event(record, state, "NO_STRATEGY_SIGNAL", event_base, recorded_at=decision_time, session_root=session_root)
            continue
        blocked, reason = _virtual_blocked(state, signal, decision_time)
        if blocked:
            _append_event(record, state, "STRATEGY_SIGNAL_BLOCKED", {**event_base, "reason": reason}, recorded_at=decision_time, session_root=session_root)
            continue
        state["counters"]["strategy_signals"] = int(state["counters"].get("strategy_signals") or 0) + 1
        _append_event(record, state, "STRATEGY_SIGNAL", event_base, recorded_at=decision_time, session_root=session_root)
        entry_mode = str(signal.get("entry_mode") or "next_open").lower()
        if entry_mode == "confirm_bar":
            pending = deepcopy(signal)
            pending["confirm_decision_time"] = (decision_time + delta).isoformat()
            state.setdefault("pending_confirmations", []).append(pending)
            _append_event(record, state, "CONFIRMATION_PENDING", {**event_base, "confirm_decision_time": pending["confirm_decision_time"]}, recorded_at=decision_time, session_root=session_root)
            continue
        if entry_mode != "next_open":
            _append_event(record, state, "UNSUPPORTED_ENTRY_MODE", {**event_base, "entry_mode": entry_mode}, recorded_at=decision_time, session_root=session_root)
            continue
        candidate = materialize_candidate(signal, quote)
        if not candidate:
            continue
        candidate["entry_time"] = decision_time.isoformat()
        candidate["candidate_id"] = _candidate_id(signal, decision_time)
        _open_virtual_candidate(state, candidate, decision_time)
        candidates.append(candidate)
        _append_event(record, state, "PORTFOLIO_CANDIDATE", {
            "candidate_id": candidate["candidate_id"],
            **event_base,
            "paper_entry_price": candidate.get("paper_entry_price"),
            "risk_distance_pct": candidate.get("risk_distance_pct"),
            "entry_adverse_slippage_vs_reference_bps": candidate.get("entry_adverse_slippage_vs_reference_bps"),
        }, recorded_at=decision_time, session_root=session_root)
    return candidates


def _account_exposures(account: dict[str, Any]) -> dict[str, Any]:
    positions = list(account.get("open_positions") or [])
    gross = sum(_num(p.get("notional_usd")) for p in positions)
    open_risk = sum(_num(p.get("risk_usd")) for p in positions)
    by_symbol: dict[str, float] = {}
    by_side: dict[str, float] = {}
    by_family: dict[str, int] = {}
    for p in positions:
        by_symbol[str(p.get("symbol"))] = by_symbol.get(str(p.get("symbol")), 0.0) + _num(p.get("notional_usd"))
        by_side[str(p.get("side"))] = by_side.get(str(p.get("side")), 0.0) + _num(p.get("notional_usd"))
        by_family[str(p.get("research_family"))] = by_family.get(str(p.get("research_family")), 0) + 1
    return {"gross": gross, "open_risk": open_risk, "by_symbol": by_symbol, "by_side": by_side, "by_family": by_family}


def _candidate_priority(candidate: dict[str, Any], mode: str) -> tuple[Any, ...]:
    if mode == "adaptive":
        return (-_num(candidate.get("router_confidence")), -_num(candidate.get("router_risk_multiplier")), -_num(candidate.get("score")), str(candidate.get("symbol")), str(candidate.get("research_family")), str(candidate.get("candidate_id")))
    return (-_num(candidate.get("score")), str(candidate.get("symbol")), str(candidate.get("research_family")), str(candidate.get("candidate_id")))


def _allocate_candidates(
    record: dict[str, Any],
    state: dict[str, Any],
    mode: str,
    candidates: list[dict[str, Any]],
    decision_time: pd.Timestamp,
    *,
    session_root: str | Path,
) -> None:
    policy = _shared_policy(record)
    account = state["accounts"][mode]
    base_risk_pct = max(0.0, _num(policy.get("base_risk_per_trade_pct"), 0.5))
    max_total_risk_pct = max(0.0, _num(policy.get("max_total_open_risk_pct"), 1.5))
    max_position_pct = max(0.0, _num(policy.get("max_position_notional_pct"), 35.0))
    max_symbol_pct = max(0.0, _num(policy.get("max_symbol_notional_pct"), 35.0))
    max_gross_pct = max(0.0, _num(policy.get("max_gross_exposure_pct"), 100.0))
    max_same_side_pct = max(0.0, _num(policy.get("max_same_direction_exposure_pct"), 70.0))
    max_positions = max(1, int(policy.get("max_concurrent_positions", 3)))
    max_family = max(1, int(policy.get("max_positions_per_family", 2)))
    min_notional = max(0.0, _num(policy.get("min_position_notional_usd"), 25.0))
    one_symbol = bool(policy.get("one_position_per_symbol", True))

    eligible = [c for c in candidates if mode == "static" or bool(c.get("router_match"))]
    if mode == "static":
        state["counters"]["static_candidates"] = int(state["counters"].get("static_candidates") or 0) + len(eligible)
    else:
        state["counters"]["adaptive_candidates"] = int(state["counters"].get("adaptive_candidates") or 0) + len(eligible)
    for candidate in sorted(eligible, key=lambda c: _candidate_priority(c, mode)):
        exposures = _account_exposures(account)
        equity = max(1.0, _num(account.get("mtm_equity_usd"), _num(account.get("realized_equity_usd"), 1.0)))
        symbol = str(candidate.get("symbol"))
        family = str(candidate.get("research_family"))
        side = str(candidate.get("side"))
        reject_reason = None
        if one_symbol and exposures["by_symbol"].get(symbol, 0.0) > 0:
            reject_reason = "symbol_already_open"
        elif len(account.get("open_positions") or []) >= max_positions:
            reject_reason = "max_concurrent_positions"
        elif exposures["by_family"].get(family, 0) >= max_family:
            reject_reason = "max_positions_per_family"

        weight = 1.0 if mode == "static" else max(0.0, _num(candidate.get("router_risk_multiplier"), 0.0))
        risk_pct = max(1e-6, _num(candidate.get("risk_distance_pct"), 0.0))
        requested_risk = equity * (base_risk_pct / 100.0) * weight
        requested_notional = requested_risk / (risk_pct / 100.0) if risk_pct > 0 else 0.0
        caps = [
            equity * max_position_pct / 100.0,
            max(0.0, equity * max_symbol_pct / 100.0 - exposures["by_symbol"].get(symbol, 0.0)),
            max(0.0, equity * max_gross_pct / 100.0 - exposures["gross"]),
            max(0.0, equity * max_same_side_pct / 100.0 - exposures["by_side"].get(side, 0.0)),
        ]
        risk_room = max(0.0, equity * max_total_risk_pct / 100.0 - exposures["open_risk"])
        caps.append(risk_room / (risk_pct / 100.0) if risk_pct > 0 else 0.0)
        notional = max(0.0, min([requested_notional] + caps))
        if reject_reason is None and (requested_risk <= 0 or notional < min_notional):
            reject_reason = "capital_or_risk_limit"
        if reject_reason:
            account["rejected_candidates"] = int(account.get("rejected_candidates") or 0) + 1
            _append_event(record, state, "PAPER_CANDIDATE_REJECTED", {"mode": mode, "candidate_id": candidate.get("candidate_id"), "symbol": symbol, "research_family": family, "reason": reject_reason}, recorded_at=decision_time, session_root=session_root)
            continue

        entry_price = _num(candidate.get("paper_entry_price"))
        qty = notional / entry_price if entry_price > 0 else 0.0
        risk_usd = notional * risk_pct / 100.0
        config = dict(candidate.get("execution_config") or {})
        fee_bps = max(0.0, _num(config.get("fee_bps_per_side"), 0.0))
        entry_fee = notional * fee_bps / 10000.0
        account["realized_equity_usd"] = _num(account.get("realized_equity_usd")) - entry_fee
        position = {
            **deepcopy(candidate),
            "mode": mode,
            "entry_time": decision_time.isoformat(),
            "allocation_weight": weight,
            "notional_usd": round(notional, 6),
            "quantity": round(qty, 12),
            "risk_usd": round(risk_usd, 6),
            "entry_fee_usd": round(entry_fee, 6),
        }
        account.setdefault("open_positions", []).append(position)
        account["accepted_trades"] = int(account.get("accepted_trades") or 0) + 1
        _append_event(record, state, "PAPER_POSITION_OPENED", {"mode": mode, **{k: position.get(k) for k in ["candidate_id", "symbol", "research_family", "strategy_name", "side", "entry_time", "paper_entry_price", "notional_usd", "risk_usd", "entry_fee_usd", "benchmark_only", "entry_adverse_slippage_vs_reference_bps"]}}, recorded_at=decision_time, session_root=session_root)


def _mark_account(account: dict[str, Any], quotes: dict[str, dict[str, Any]], *, at_time: pd.Timestamp) -> dict[str, Any]:
    unrealized = 0.0
    for position in account.get("open_positions") or []:
        entry = _num(position.get("paper_entry_price"))
        quote = quotes.get(str(position.get("symbol"))) or {}
        mark = _quote_close_price(position, quote, entry)
        qty = _num(position.get("quantity"))
        direction = 1.0 if str(position.get("side")) == "LONG" else -1.0
        gross = qty * (mark - entry) * direction
        cfg = dict(position.get("execution_config") or {})
        fee_bps = max(0.0, _num(cfg.get("fee_bps_per_side"), 0.0))
        exit_fee = abs(qty * mark) * fee_bps / 10000.0
        opened = _utc(position.get("entry_time"))
        hours = max(0.0, float((at_time - opened).total_seconds() / 3600.0)) if not pd.isna(opened) else 0.0
        funding = _num(position.get("notional_usd")) * _num(cfg.get("funding_bps_per_8h"), 0.0) / 10000.0 * (hours / 8.0)
        unrealized += gross - exit_fee - funding
    mtm = _num(account.get("realized_equity_usd")) + unrealized
    account["mtm_equity_usd"] = round(mtm, 6)
    peak = max(_num(account.get("peak_mtm_equity_usd"), mtm), mtm)
    account["peak_mtm_equity_usd"] = round(peak, 6)
    dd_pct = ((peak - mtm) / peak * 100.0) if peak > 0 else 0.0
    account["max_mtm_drawdown_pct"] = round(max(_num(account.get("max_mtm_drawdown_pct"), 0.0), dd_pct), 6)
    exposures = _account_exposures(account)
    return {
        "mtm_equity_usd": mtm,
        "realized_equity_usd": _num(account.get("realized_equity_usd")),
        "unrealized_pnl_usd": unrealized,
        "drawdown_pct": dd_pct,
        "open_positions": len(account.get("open_positions") or []),
        "gross_exposure_usd": exposures["gross"],
        "open_risk_usd": exposures["open_risk"],
    }


def _due_decisions(record: dict[str, Any], state: dict[str, Any], symbol: str, now_ts: pd.Timestamp, *, store_root: str | Path) -> list[dict[str, Any]]:
    delta = _interval_delta(record)
    first = _utc(record.get("first_eligible_decision_utc"))
    target_end = _utc(record.get("target_end_utc"))
    if pd.isna(first) or pd.isna(target_end) or now_ts < first:
        return []
    latest_boundary = now_ts.floor(f"{int(delta.total_seconds() // 60)}min")
    latest_due = min(latest_boundary, target_end)
    last = _utc((state.get("processed_decisions") or {}).get(symbol))
    next_due = first if pd.isna(last) else last + delta
    if next_due > latest_due:
        return []
    expected = list(pd.date_range(next_due, latest_due, freq=delta, tz="UTC" if next_due.tzinfo is None else None))
    start_open = next_due - delta
    end_open = latest_due - delta
    candles = load_range(symbol, str(record.get("entry_timeframe") or "1h"), start=start_open, end=end_open, store_root=store_root)
    row_map: dict[str, dict[str, Any]] = {}
    if not candles.empty:
        for _, row in candles.iterrows():
            open_ts = _utc(row.get("open_time"))
            if pd.isna(open_ts):
                continue
            row_map[(open_ts + delta).isoformat()] = row.to_dict()
    return [{"decision_time": ts, "candle": row_map.get(ts.isoformat())} for ts in expected]


def run_prospective_paper_cycle(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    quote_provider: Callable[..., dict[str, Any]] | None = None,
    store_root: str | Path | None = None,
    session_root: str | Path = SESSION_ROOT,
) -> dict[str, Any]:
    policy = _paper_policy(record)
    verification = verify_paper_session_freeze(record, check_current=bool(policy.get("require_current_framework_match", True)))
    if not verification.get("ok", False):
        return {"ok": False, "reason": "paper_freeze_integrity_failed", "verification": verification}
    state = load_paper_state(record, session_root=session_root)
    chain = verify_event_chain(record, session_root=session_root)
    if bool(policy.get("require_event_chain_integrity", True)) and not chain.get("ok", False):
        return {"ok": False, "reason": "event_chain_integrity_failed", "event_chain": chain}
    now_dt = _now_utc(now)
    now_ts = pd.Timestamp(now_dt)
    target_end = _utc(record.get("target_end_utc"))
    root = str(store_root or record.get("source_root") or OHLCV_STORE_ROOT)
    lag_limit = pd.Timedelta(minutes=max(1, int(policy.get("max_decision_lag_minutes", 10))))
    candidates: list[dict[str, Any]] = []
    quotes: dict[str, dict[str, Any]] = {}
    processed_now = 0
    missed_now = 0
    gaps_now = 0

    # Exits are known only after the relevant closed candle exists. On a late
    # restart this is allowed for already-open paper positions, but new missed
    # signals are never reconstructed.
    through = min(now_ts.floor(f"{max(1, timeframe_minutes(str(record.get('entry_timeframe') or '1h')))}min"), target_end)
    if through >= _utc(record.get("first_eligible_decision_utc")):
        _update_virtual_positions(record, state, through, store_root=root, session_root=session_root)
        _update_account_positions(record, state, "static", through, store_root=root, session_root=session_root)
        _update_account_positions(record, state, "adaptive", through, store_root=root, session_root=session_root)

    for symbol in record.get("symbols") or []:
        symbol = str(symbol).upper()
        due_rows = _due_decisions(record, state, symbol, now_ts, store_root=root)
        for due in due_rows:
            decision_time = pd.Timestamp(due["decision_time"])
            candle = due.get("candle")
            lag = now_ts - decision_time
            state.setdefault("processed_decisions", {})[symbol] = decision_time.isoformat()
            if candle is None:
                if lag <= lag_limit:
                    # Give the collector the remainder of the punctuality window.
                    state["processed_decisions"].pop(symbol, None)
                    break
                state["counters"]["data_gap_slots"] = int(state["counters"].get("data_gap_slots") or 0) + 1
                state["counters"]["missed_decision_slots"] = int(state["counters"].get("missed_decision_slots") or 0) + 1
                gaps_now += 1
                missed_now += 1
                _append_event(record, state, "DATA_GAP_MISSED_DECISION", {"symbol": symbol, "decision_time": decision_time.isoformat(), "lag_minutes": round(lag.total_seconds() / 60.0, 2)}, recorded_at=now_ts, session_root=session_root)
                continue
            if lag > lag_limit:
                state["counters"]["missed_decision_slots"] = int(state["counters"].get("missed_decision_slots") or 0) + 1
                missed_now += 1
                _append_event(record, state, "MISSED_DECISION", {"symbol": symbol, "decision_time": decision_time.isoformat(), "lag_minutes": round(lag.total_seconds() / 60.0, 2), "retroactive_signal_created": False}, recorded_at=now_ts, session_root=session_root)
                continue

            reference = _num(candle.get("close"), 0.0)
            quote = fetch_or_proxy_quote(symbol, reference_price=reference, now=now_dt, quote_provider=quote_provider)
            quotes[symbol] = quote
            state["counters"]["recorded_decision_slots"] = int(state["counters"].get("recorded_decision_slots") or 0) + 1
            if quote.get("executable_top_of_book"):
                state["counters"]["executable_quote_slots"] = int(state["counters"].get("executable_quote_slots") or 0) + 1
            else:
                state["counters"]["proxy_quote_slots"] = int(state["counters"].get("proxy_quote_slots") or 0) + 1
            processed_now += 1
            _append_event(record, state, "DECISION_SLOT_RECORDED", {"symbol": symbol, "decision_time": decision_time.isoformat(), "lag_minutes": round(lag.total_seconds() / 60.0, 2), "quote": quote}, recorded_at=now_ts, session_root=session_root)
            candidates.extend(_candidate_stream_for_decision(record, state, symbol, decision_time, quote, store_root=root, session_root=session_root))

    # Static sees all frozen family signals. Adaptive sees only the exact router
    # matches, under the same capital constraints.
    if candidates:
        entry_times = [_utc(c.get("entry_time")) for c in candidates]
        decision_for_alloc = max([x for x in entry_times if not pd.isna(x)], default=through)
        _allocate_candidates(record, state, "static", candidates, decision_for_alloc, session_root=session_root)
        _allocate_candidates(record, state, "adaptive", candidates, decision_for_alloc, session_root=session_root)

    # Quotes for positions without a new decision are still useful for MTM. If
    # Binance is unavailable, this becomes an explicit close-price proxy.
    for mode in ["static", "adaptive"]:
        for position in state["accounts"][mode].get("open_positions") or []:
            symbol = str(position.get("symbol"))
            if symbol in quotes:
                continue
            fallback = _num(position.get("paper_entry_price"), 0.0)
            if now_ts <= target_end:
                quotes[symbol] = fetch_or_proxy_quote(symbol, reference_price=fallback, now=now_dt, quote_provider=quote_provider)
            else:
                quotes[symbol] = normalize_quote(symbol, None, reference_price=fallback, now=now_dt)

    snapshot_time = min(now_ts, target_end)
    marks = {}
    for mode in ["static", "adaptive"]:
        marks[mode] = _mark_account(state["accounts"][mode], quotes, at_time=snapshot_time)
        _append_event(record, state, "MTM_SNAPSHOT", {"mode": mode, "snapshot_time": snapshot_time.isoformat(), **marks[mode]}, recorded_at=now_ts, session_root=session_root)

    state["counters"]["cycles"] = int(state["counters"].get("cycles") or 0) + 1
    state["last_cycle_at"] = now_ts.isoformat()
    if now_ts > target_end:
        state["status"] = "paper_window_complete"
    atomic_write_json(_state_path(record, session_root), state)
    return {
        "ok": True,
        "session_id": record.get("session_id"),
        "processed_decision_slots": processed_now,
        "missed_decision_slots": missed_now,
        "data_gap_slots": gaps_now,
        "candidates": len(candidates),
        "adaptive": marks.get("adaptive"),
        "static": marks.get("static"),
        "target_end_utc": record.get("target_end_utc"),
        "status": state.get("status"),
    }


def _profit_factor(trades: list[dict[str, Any]]) -> float:
    pnl = pd.Series([_num(x.get("net_pnl_usd")) for x in trades], dtype=float)
    if pnl.empty:
        return 0.0
    gp = float(pnl[pnl > 0].sum())
    gl = abs(float(pnl[pnl < 0].sum()))
    if gl <= 1e-12:
        return 99.0 if gp > 0 else 0.0
    return gp / gl


def _account_summary(account: dict[str, Any]) -> dict[str, Any]:
    trades = list(account.get("closed_trades") or [])
    start = max(1.0, _num(account.get("starting_equity_usd"), 10000.0))
    ending = _num(account.get("mtm_equity_usd"), _num(account.get("realized_equity_usd"), start))
    pnl = pd.Series([_num(x.get("net_pnl_usd")) for x in trades], dtype=float)
    by_symbol = pd.Series(dtype=float)
    by_family = pd.Series(dtype=float)
    if trades:
        frame = pd.DataFrame(trades)
        by_symbol = frame.groupby("symbol")["net_pnl_usd"].sum()
        by_family = frame.groupby("research_family")["notional_usd"].sum()
    slips = [_num(x.get("entry_adverse_slippage_vs_reference_bps"), np.nan) for x in trades]
    slips += [_num(x.get("entry_adverse_slippage_vs_reference_bps"), np.nan) for x in (account.get("open_positions") or [])]
    slips = [x for x in slips if not pd.isna(x)]
    benchmark = any(bool(x.get("benchmark_only")) for x in trades + list(account.get("open_positions") or []))
    return {
        "starting_equity_usd": round(start, 2),
        "ending_mtm_equity_usd": round(ending, 2),
        "return_pct": round((ending / start - 1.0) * 100.0, 4),
        "realized_equity_usd": round(_num(account.get("realized_equity_usd"), start), 2),
        "closed_trades": len(trades),
        "open_positions": len(account.get("open_positions") or []),
        "accepted_trades": int(account.get("accepted_trades") or 0),
        "rejected_candidates": int(account.get("rejected_candidates") or 0),
        "win_rate": round(float((pnl > 0).mean() * 100.0), 2) if len(pnl) else 0.0,
        "profit_factor": round(_profit_factor(trades), 4),
        "max_mtm_drawdown_pct": round(_num(account.get("max_mtm_drawdown_pct"), 0.0), 4),
        "active_symbols": int(len(by_symbol)),
        "positive_symbol_share": round(float((by_symbol > 0).mean()), 4) if len(by_symbol) else 0.0,
        "family_notional_concentration": round(float(by_family.max() / by_family.sum()), 4) if len(by_family) and float(by_family.sum()) > 0 else 0.0,
        "benchmark_trade_accepted": benchmark,
        "avg_adverse_entry_slippage_bps": round(float(np.mean(slips)), 4) if slips else None,
        "p95_adverse_entry_slippage_bps": round(float(np.percentile(slips, 95)), 4) if slips else None,
    }


def _events_frames(record: dict[str, Any], *, session_root: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = _read_events(_events_path(record, session_root))
    decision_rows = []
    incident_rows = []
    equity_rows = []
    for event in events:
        payload = dict(event.get("payload") or {})
        et = str(event.get("event_type") or "")
        if et in {"DECISION_SLOT_RECORDED", "MISSED_DECISION", "DATA_GAP_MISSED_DECISION"}:
            decision_rows.append({"event_type": et, "recorded_at": event.get("recorded_at"), **payload})
        if et in {"MISSED_DECISION", "DATA_GAP_MISSED_DECISION", "DECISION_EVALUATION_FAILED", "UNSUPPORTED_ENTRY_MODE"}:
            incident_rows.append({"event_type": et, "recorded_at": event.get("recorded_at"), **payload})
        if et == "MTM_SNAPSHOT":
            equity_rows.append({"recorded_at": event.get("recorded_at"), **payload})
    return pd.DataFrame(decision_rows), pd.DataFrame(incident_rows), pd.DataFrame(equity_rows)


def evaluate_paper_session(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    session_root: str | Path = SESSION_ROOT,
) -> ProspectivePaperValidationResult:
    policy = _paper_policy(record)
    verification = verify_paper_session_freeze(record, check_current=bool(policy.get("require_current_framework_match", True)))
    chain = verify_event_chain(record, session_root=session_root)
    state = load_paper_state(record, session_root=session_root)
    now_ts = pd.Timestamp(_now_utc(now))
    target_end = _utc(record.get("target_end_utc"))
    calendar_complete = bool(not pd.isna(target_end) and now_ts >= target_end)
    adaptive = _account_summary(state["accounts"]["adaptive"])
    static = _account_summary(state["accounts"]["static"])
    comparison = pd.DataFrame([{"mode": "static", **static}, {"mode": "adaptive", **adaptive}])
    counters = dict(state.get("counters") or {})
    recorded = int(counters.get("recorded_decision_slots") or 0)
    missed = int(counters.get("missed_decision_slots") or 0)
    gaps = int(counters.get("data_gap_slots") or 0)
    executable = int(counters.get("executable_quote_slots") or 0)
    total_expected_seen = recorded + missed
    punctuality = float(recorded / total_expected_seen) if total_expected_seen else 0.0
    quote_share = float(executable / recorded) if recorded else 0.0
    gap_share = float(gaps / total_expected_seen) if total_expected_seen else 0.0
    return_uplift = _num(adaptive.get("return_pct")) - _num(static.get("return_pct"))
    pf_uplift = _num(adaptive.get("profit_factor")) - _num(static.get("profit_factor"))
    p95_slip = adaptive.get("p95_adverse_entry_slippage_bps")
    benchmark_clear = not bool(adaptive.get("benchmark_trade_accepted")) if bool(policy.get("block_if_benchmark_accepted", True)) else True
    checks = {
        "freeze_integrity": bool(verification.get("ok", False)),
        "event_chain_integrity": bool(chain.get("ok", False)),
        "calendar_complete": calendar_complete,
        "decision_sample": recorded >= int(policy.get("min_recorded_decision_slots", 120)),
        "completed_trade_sample": int(adaptive.get("closed_trades") or 0) >= int(policy.get("min_completed_trades", 20)),
        "active_symbols": int(adaptive.get("active_symbols") or 0) >= int(policy.get("min_active_symbols", 2)),
        "positive_return": _num(adaptive.get("return_pct")) > 0,
        "profit_factor": _num(adaptive.get("profit_factor")) >= _num(policy.get("min_profit_factor"), 1.05),
        "mtm_drawdown": _num(adaptive.get("max_mtm_drawdown_pct")) <= _num(policy.get("max_mtm_drawdown_pct"), 10.0),
        "pair_stability": _num(adaptive.get("positive_symbol_share")) >= _num(policy.get("min_positive_symbol_share"), 0.67),
        "punctuality": punctuality >= _num(policy.get("min_punctuality_share"), 0.95),
        "quote_coverage": quote_share >= _num(policy.get("min_executable_quote_share"), 0.90),
        "data_gap_control": gap_share <= _num(policy.get("max_data_gap_share"), 0.02),
        "entry_slippage": p95_slip is not None and _num(p95_slip) <= _num(policy.get("max_p95_adverse_entry_slippage_bps"), 25.0),
        "adaptive_return_uplift": return_uplift >= _num(policy.get("min_return_uplift_pct_points_vs_static"), 0.0),
        "adaptive_pf_uplift": pf_uplift >= _num(policy.get("min_profit_factor_uplift_vs_static"), -0.05),
        "family_concentration": _num(adaptive.get("family_notional_concentration")) <= _num(policy.get("max_family_notional_concentration"), 0.75),
        "benchmark_clear": benchmark_clear,
        "live_execution_disabled": not bool(record.get("live_execution_enabled", False)),
    }
    insufficient = recorded < int(policy.get("min_recorded_decision_slots", 120)) or int(adaptive.get("closed_trades") or 0) < int(policy.get("min_completed_trades", 20))
    operational_bad = not checks["punctuality"] or not checks["quote_coverage"] or not checks["data_gap_control"] or not checks["event_chain_integrity"]
    hard_negative = int(adaptive.get("closed_trades") or 0) >= 10 and (_num(adaptive.get("return_pct")) <= 0 or _num(adaptive.get("profit_factor")) < 1.0)
    static_better = return_uplift < 0 and pf_uplift <= 0
    if not checks["freeze_integrity"] or not checks["event_chain_integrity"]:
        verdict_name = "invalid_paper_session"
    elif not calendar_complete:
        verdict_name = "collecting_prospective_paper_evidence"
    elif operational_bad:
        verdict_name = "paper_operationally_unreliable"
    elif insufficient:
        verdict_name = "insufficient_paper_evidence"
    elif hard_negative:
        verdict_name = "paper_fail"
    elif static_better:
        verdict_name = "static_baseline_better_paper"
    elif all(checks.values()):
        verdict_name = "pass_for_micro_live_review"
    else:
        verdict_name = "paper_mixed"

    critique = [
        "V28.24 records strategy/router decisions prospectively. Missed bars are logged as incidents and are never reconstructed as if the system had been running.",
        "Static and adaptive paper accounts use the same prospective candidate stream and finite-capital policy; adaptive complexity must justify itself against the simpler shadow account.",
        "Account drawdown is mark-to-market at recorded paper cycles, improving materially on realized-exit-only historical drawdown. It can still miss intrabar extremes between cycles.",
        "Top-of-book quotes are executable-price proxies for small paper size, not exchange fill confirmations. Queue position, partial fills, latency spikes and market impact remain unproven.",
        "This release never submits an exchange order. Even pass_for_micro_live_review means manual risk review only, not automatic live permission.",
    ]
    if operational_bad:
        critique.append(f"Operational evidence is weak: punctuality={punctuality:.1%}, executable quote coverage={quote_share:.1%}, data-gap share={gap_share:.1%}.")
    if static_better:
        critique.append("The static shadow account is better on both return and profit factor; adaptive routing has not earned its complexity prospectively.")
    if not benchmark_clear:
        critique.append("At least one benchmark-only strategy trade reached the adaptive paper account; production-oriented promotion remains blocked.")
    if p95_slip is not None and not checks["entry_slippage"]:
        critique.append(f"95th-percentile adverse entry slippage is {float(p95_slip):.1f} bps, above the paper policy limit.")

    decisions, incidents, equity = _events_frames(record, session_root=session_root)
    adaptive_trades = pd.DataFrame(state["accounts"]["adaptive"].get("closed_trades") or [])
    static_trades = pd.DataFrame(state["accounts"]["static"].get("closed_trades") or [])
    by_symbol = pd.DataFrame()
    by_family = pd.DataFrame()
    if not adaptive_trades.empty:
        by_symbol = adaptive_trades.groupby("symbol", as_index=False).agg(trades=("net_pnl_usd", "size"), pnl_usd=("net_pnl_usd", "sum"), win_rate=("net_pnl_usd", lambda s: float((s > 0).mean() * 100.0)))
        by_family = adaptive_trades.groupby("research_family", as_index=False).agg(trades=("net_pnl_usd", "size"), pnl_usd=("net_pnl_usd", "sum"), notional_usd=("notional_usd", "sum"))
    verdict = {
        "version": str(policy.get("version") or "28.24"),
        "verdict": verdict_name,
        "promotion_blocked": verdict_name != "pass_for_micro_live_review",
        "checks": checks,
        "checks_passed": int(sum(bool(v) for v in checks.values())),
        "checks_total": len(checks),
        "recorded_decision_slots": recorded,
        "missed_decision_slots": missed,
        "data_gap_slots": gaps,
        "punctuality_share": round(punctuality, 4),
        "executable_quote_share": round(quote_share, 4),
        "data_gap_share": round(gap_share, 4),
        "adaptive_return_pct": adaptive.get("return_pct"),
        "static_return_pct": static.get("return_pct"),
        "return_uplift_pct_points": round(return_uplift, 4),
        "adaptive_profit_factor": adaptive.get("profit_factor"),
        "static_profit_factor": static.get("profit_factor"),
        "profit_factor_uplift": round(pf_uplift, 4),
        "adaptive_max_mtm_drawdown_pct": adaptive.get("max_mtm_drawdown_pct"),
        "adaptive_closed_trades": adaptive.get("closed_trades"),
        "paper_framework_sha256": record.get("paper_framework_sha256"),
        "next_step": (
            "Manual micro-live risk review only; do not enable automated live execution." if verdict_name == "pass_for_micro_live_review"
            else "Keep this framework out of live trading until the failed paper gates are resolved with a new prospective session if the framework changes."
        ),
    }
    integrity = {
        "freeze": verification,
        "event_chain": chain,
        "session_record_sha256": record.get("record_sha256"),
        "paper_framework_sha256": record.get("paper_framework_sha256"),
        "live_execution_enabled": False,
    }
    return ProspectivePaperValidationResult(
        session_id=str(record.get("session_id") or ""),
        comparison=comparison,
        adaptive_trades=adaptive_trades,
        static_trades=static_trades,
        equity_history=equity,
        decisions=decisions,
        incidents=incidents,
        by_symbol=by_symbol,
        by_family=by_family,
        verdict=verdict,
        integrity=integrity,
        critique=critique,
    )


def save_paper_validation_snapshot(
    result: ProspectivePaperValidationResult,
    record: dict[str, Any],
    *,
    output_dir: str | Path = SNAPSHOT_DIR,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d_%H%M%S")
    prefix = root / f"{stamp}_{result.session_id}_v28_24_paper"
    paths: dict[str, str] = {}
    for name, frame in {
        "comparison": result.comparison,
        "adaptive_trades": result.adaptive_trades,
        "static_trades": result.static_trades,
        "equity_history": result.equity_history,
        "decisions": result.decisions,
        "incidents": result.incidents,
        "by_symbol": result.by_symbol,
        "by_family": result.by_family,
    }.items():
        if frame is not None and not frame.empty:
            path = Path(f"{prefix}_{name}.csv")
            frame.to_csv(path, index=False)
            paths[name] = str(path)
    summary = {
        "schema_version": "28.24-prospective-paper-snapshot-v1",
        "session_id": result.session_id,
        "session_record_sha256": record.get("record_sha256"),
        "paper_framework_sha256": record.get("paper_framework_sha256"),
        "source_future_freeze_id": record.get("source_future_freeze_id"),
        "source_future_snapshot_sha256": record.get("source_future_snapshot_sha256"),
        "verdict": result.verdict,
        "integrity": result.integrity,
        "critique": result.critique,
    }
    path = Path(f"{prefix}_summary.json")
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    paths["summary"] = str(path)
    return paths
