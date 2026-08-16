from __future__ import annotations

from itertools import combinations
from typing import Any

import pandas as pd


def _normalise_trades(run_name: str, trades: pd.DataFrame, loaded: dict[str, Any] | None = None, bucket: str = "15min") -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    manifest = (loaded or {}).get("manifest") or {}
    strategy_payload = manifest.get("strategy_payload") or {}
    frame = trades.copy()
    frame["run_name"] = run_name
    if "strategy_name" not in frame.columns:
        frame["strategy_name"] = strategy_payload.get("strategy_name") or run_name
    if "strategy_mode" not in frame.columns:
        frame["strategy_mode"] = "single"
    if "trade_owner_key" not in frame.columns:
        frame["trade_owner_key"] = frame["strategy_mode"].astype(str) + ":" + frame["strategy_name"].astype(str)
    if "pnl_usd" not in frame.columns and "pnl_pct" in frame.columns:
        stake = float((manifest.get("summary") or {}).get("stake_per_trade_usd", 100.0) or 100.0)
        frame["pnl_usd"] = pd.to_numeric(frame["pnl_pct"], errors="coerce").fillna(0.0) * stake / 100.0
    frame["entry_time"] = pd.to_datetime(frame.get("entry_time"), utc=True, errors="coerce")
    frame = frame.dropna(subset=["entry_time"])
    frame["time_bucket"] = frame["entry_time"].dt.floor(bucket)
    for col in ["symbol", "side", "strategy_name", "strategy_mode", "trade_owner_key"]:
        if col not in frame.columns:
            frame[col] = "unknown"
        frame[col] = frame[col].fillna("unknown").astype(str)
    frame["score"] = pd.to_numeric(frame.get("score"), errors="coerce")
    frame["pnl_usd"] = pd.to_numeric(frame.get("pnl_usd"), errors="coerce").fillna(0.0)
    return frame


def build_overlap_reports(loaded_runs: list[tuple[str, pd.DataFrame, dict[str, Any]]], bucket: str = "15min") -> dict[str, pd.DataFrame]:
    """Find same-symbol strategy overlaps across selected saved runs.

    The goal is to reveal duplicate/concurrent signals, not to block them automatically.
    """
    frames = [_normalise_trades(run_name, trades, loaded, bucket=bucket) for run_name, trades, loaded in loaded_runs]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return {"overlap_same_side": pd.DataFrame(), "opposite_side_conflicts": pd.DataFrame(), "owner_pair_overlap": pd.DataFrame()}
    all_trades = pd.concat(frames, ignore_index=True)

    same_rows: list[dict[str, Any]] = []
    for keys, part in all_trades.groupby(["symbol", "side", "time_bucket"], dropna=False):
        owners = sorted(set(part["trade_owner_key"].astype(str)))
        if len(owners) < 2:
            continue
        same_rows.append({
            "symbol": keys[0],
            "side": keys[1],
            "time_bucket": keys[2],
            "owners_count": len(owners),
            "trades": int(len(part)),
            "owners": " | ".join(owners[:8]),
            "runs": " | ".join(sorted(set(part["run_name"].astype(str)))[:8]),
            "avg_score": round(float(part["score"].mean()), 2) if part["score"].notna().any() else 0.0,
            "total_pnl_usd": round(float(part["pnl_usd"].sum()), 2),
            "winner_count": int((part["pnl_usd"] > 0).sum()),
        })

    conflict_rows: list[dict[str, Any]] = []
    for keys, part in all_trades.groupby(["symbol", "time_bucket"], dropna=False):
        sides = sorted(set(part["side"].astype(str)))
        if not ({"LONG", "SHORT"} <= set(sides)):
            continue
        conflict_rows.append({
            "symbol": keys[0],
            "time_bucket": keys[1],
            "sides": " / ".join(sides),
            "trades": int(len(part)),
            "owners": " | ".join(sorted(set(part["trade_owner_key"].astype(str)))[:10]),
            "runs": " | ".join(sorted(set(part["run_name"].astype(str)))[:10]),
            "long_pnl_usd": round(float(part.loc[part["side"] == "LONG", "pnl_usd"].sum()), 2),
            "short_pnl_usd": round(float(part.loc[part["side"] == "SHORT", "pnl_usd"].sum()), 2),
        })

    pair_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for _, part in all_trades.groupby(["symbol", "side", "time_bucket"], dropna=False):
        owners = sorted(set(part["trade_owner_key"].astype(str)))
        for a, b in combinations(owners, 2):
            key = (a, b)
            row = pair_rows.setdefault(key, {"owner_a": a, "owner_b": b, "overlap_buckets": 0, "joint_pnl_usd": 0.0, "symbols": set()})
            row["overlap_buckets"] += 1
            row["joint_pnl_usd"] += float(part.loc[part["trade_owner_key"].isin([a, b]), "pnl_usd"].sum())
            row["symbols"].update(part["symbol"].astype(str).tolist())
    pair_out = []
    for row in pair_rows.values():
        pair_out.append({
            "owner_a": row["owner_a"],
            "owner_b": row["owner_b"],
            "overlap_buckets": int(row["overlap_buckets"]),
            "joint_pnl_usd": round(float(row["joint_pnl_usd"]), 2),
            "symbols": ", ".join(sorted(row["symbols"])[:12]),
        })

    return {
        "overlap_same_side": pd.DataFrame(same_rows).sort_values(["owners_count", "trades", "total_pnl_usd"], ascending=[False, False, False]).reset_index(drop=True) if same_rows else pd.DataFrame(),
        "opposite_side_conflicts": pd.DataFrame(conflict_rows).sort_values(["trades", "time_bucket"], ascending=[False, True]).reset_index(drop=True) if conflict_rows else pd.DataFrame(),
        "owner_pair_overlap": pd.DataFrame(pair_out).sort_values(["overlap_buckets", "joint_pnl_usd"], ascending=[False, False]).reset_index(drop=True) if pair_out else pd.DataFrame(),
    }
