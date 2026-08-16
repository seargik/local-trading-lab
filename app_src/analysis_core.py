from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .engine import analyze_symbol, build_trade_levels, evaluate_trade_outcome, resolve_tp_settings
from .bundle_engine import build_live_bundle_payloads
from .market_data import BinanceFuturesClient
from .settings import HTF_MAP


def fetch_market_snapshot(symbol: str, timeframe: str, storage=None) -> dict[str, Any]:
    if storage is not None:
        try:
            return storage.get_market_snapshot(symbol)
        except Exception:
            pass
    client = BinanceFuturesClient()
    return client.fetch_market_snapshot(symbol, oi_period=timeframe if timeframe in {"5m", "15m", "1h"} else "5m")


def fetch_htf_frames(symbol: str, main_timeframe: str, storage=None) -> dict[str, pd.DataFrame]:
    if storage is not None:
        try:
            frames = storage.get_htf_frames(symbol, main_timeframe)
            if frames:
                return frames
        except Exception:
            pass
    client = BinanceFuturesClient()
    timeframes = HTF_MAP.get(main_timeframe, ["15m", "1h", "4h"])
    out: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        try:
            out[tf] = client.fetch_history_df(symbol, tf, limit=300)
        except Exception:
            continue
    return out


def build_setup_summary(symbol: str, analysis_timeframe: str, strategy_row: dict[str, Any], analysis: dict[str, Any]) -> str:
    features = analysis["features"]
    htf_context = analysis.get("htf_context", {})
    lines = [
        f"Symbol: {symbol}",
        f"Analysis timeframe: {analysis_timeframe}",
        f"Strategy: {strategy_row['strategy_name']} v{strategy_row['version_no']}",
        f"Strategy version created at: {strategy_row.get('version_created_at', '')}",
        f"Template: {strategy_row['template_key']}",
        f"Regime: {analysis['summary']['regime']}",
        f"Bias: {strategy_row['bias']}",
        f"Score: {strategy_row['score']} / threshold {strategy_row['threshold']}",
        f"Expected outcome: {strategy_row.get('expected_outcome', '')}",
        f"Human thesis: {strategy_row.get('human_thesis', '')}",
        f"Indicator description: {strategy_row.get('indicator_description', '')}",
        f"Expected RR: {strategy_row.get('expected_rr', '')}",
        "",
        "Main indicators:",
        f"- close: {features.get('close')}",
        f"- RSI14: {features.get('rsi_14')}",
        f"- ATR%: {features.get('atr_pct')}",
        f"- ADX14: {features.get('adx_14')}",
        f"- VWAP: {features.get('vwap_session')}",
        f"- MA200 distance %: {features.get('distance_to_ma200_pct')}",
        f"- Range position 20: {features.get('range_position_20')}",
        f"- Market structure: {features.get('market_structure')}",
        f"- Volume spike: {features.get('volume_spike')}",
        f"- Bullish divergence: {features.get('bullish_divergence')}",
        f"- Bearish divergence: {features.get('bearish_divergence')}",
        f"- Funding: {features.get('funding_rate')}",
        f"- Open interest: {features.get('open_interest')}",
        f"- Order book imbalance: {features.get('order_book_imbalance')}",
        f"- HTF alignment: {features.get('htf_alignment')}",
        "",
        "HTF context:",
        json.dumps(htf_context, indent=2, default=str),
        "",
        "Use this packet to critique the setup, highlight weak assumptions, and suggest tighter filters.",
    ]
    return "\n".join(lines)


def build_trade_summary(trade_row: pd.Series | dict[str, Any]) -> str:
    row = dict(trade_row)
    return "\n".join([
        f"Trade ID: {row.get('trade_id')}",
        f"Symbol: {row.get('symbol')}",
        f"Strategy: {row.get('strategy_name')} v{row.get('version_no')}",
        f"Decision: {row.get('decision')}",
        f"Side: {row.get('side')}",
        f"Entry: {row.get('entry_price')}",
        f"SL: {row.get('stop_loss')}",
        f"TP: {row.get('take_profit')}",
        f"Expected RR: {row.get('expected_rr')}",
        f"Comment: {row.get('user_comment') or ''}",
    ])


def build_outcome_summary(trade_row: pd.Series | dict[str, Any]) -> str:
    row = dict(trade_row)
    return "\n".join([
        f"Trade ID: {row.get('trade_id')}",
        f"Status: {row.get('status')}",
        f"Close reason: {row.get('close_reason')}",
        f"Outcome label: {row.get('outcome_label')}",
        f"Close price: {row.get('close_price')}",
        f"PnL %: {row.get('pnl_pct')}",
        f"MFE %: {row.get('mfe_pct')}",
        f"MAE %: {row.get('mae_pct')}",
        f"Follow-through score: {row.get('follow_through_score')}",
    ])


def evaluate_open_trades(storage) -> None:
    open_trades = storage.get_open_paper_trades()
    if open_trades.empty:
        return
    for _, trade in open_trades.iterrows():
        candles = storage.get_candles(trade["symbol"], trade["interval"], limit=400)
        tp_levels = trade.get("tp_levels_json")
        if isinstance(tp_levels, str):
            try:
                tp_levels = json.loads(tp_levels)
            except Exception:
                tp_levels = None
        outcome = evaluate_trade_outcome(
            candles,
            side=trade["side"],
            entry_price=float(trade["entry_price"]),
            stop_loss=float(trade.get("stop_loss_initial") or trade["stop_loss"]),
            take_profit=float(trade["take_profit"]),
            opened_at=trade.get("opened_at"),
            tp_levels=tp_levels,
            late_trigger_index=int(trade.get("late_trigger_index") or 0),
            be_trigger_index=int(trade.get("be_trigger_index") or 0),
            lock_trigger_index=int(trade.get("lock_trigger_index") or 0),
            lock_to_tp_index=int(trade.get("lock_to_tp_index") or 0),
        )
        if not outcome:
            continue
        if outcome["status"] == "OPEN":
            storage.update_trade_progress(int(trade["trade_id"]), outcome)
            continue
        storage.mark_trade_outcome(
            int(trade["trade_id"]),
            status=outcome["status"],
            close_reason=outcome["close_reason"],
            close_price=float(outcome["close_price"]),
            outcome_label=outcome["outcome_label"],
            mfe_pct=float(outcome["mfe_pct"]),
            mae_pct=float(outcome["mae_pct"]),
            pnl_pct=float(outcome["pnl_pct"]),
            follow_through_score=float(outcome["follow_through_score"]),
            outcome_summary=build_outcome_summary({**trade.to_dict(), **outcome}),
            progress=outcome,
        )


def current_trade_signal_state(analysis_map: dict[str, dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for symbol, analysis in analysis_map.items():
        last_price = analysis.get("features", {}).get("close")
        for opinion in (analysis.get("strategies", []) or []) + (analysis.get("bundles", []) or []):
            owner_key = opinion.get("trade_owner_key")
            if not owner_key:
                owner_key = f"single:{opinion.get('version_id')}"
            out[(symbol, owner_key)] = {"score": opinion.get("score"), "bias": opinion.get("bias"), "note": opinion.get("note"), "last_price": last_price}
            try:
                out[(symbol, int(opinion.get("version_id") or -1))] = {"score": opinion.get("score"), "bias": opinion.get("bias"), "note": opinion.get("note"), "last_price": last_price}
            except Exception:
                pass
    return out

def run_scanner(storage, analysis_timeframe: str, selected_symbols: list[str], slot_rows: list[dict[str, Any]], auto_paper_mode: bool, live_bundle_mode: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    scanner_rows: list[dict[str, Any]] = []
    analysis_map: dict[str, dict[str, Any]] = {}
    for symbol in selected_symbols:
        df = storage.get_candles(symbol, analysis_timeframe, limit=400)
        if df.empty or len(df) < 50:
            continue
        extras = fetch_market_snapshot(symbol, analysis_timeframe, storage=storage)
        htf_frames = fetch_htf_frames(symbol, analysis_timeframe, storage=storage)
        bundle_payloads = build_live_bundle_payloads(slot_rows, symbol, enabled=live_bundle_mode)
        analysis = analyze_symbol(df, slot_rows, extras=extras, htf_frames=htf_frames, bundle_payloads=bundle_payloads)
        analysis_map[symbol] = analysis
        best_bundle = None
        if analysis.get("bundles"):
            actionable = [b for b in analysis.get("bundles", []) if b.get("bias") in {"LONG", "SHORT"}]
            if actionable:
                best_bundle = max(actionable, key=lambda b: float(b.get("score") or 0)).get("bundle_name")
        scanner_rows.append({
            "symbol": symbol,
            "last_open_time": analysis["features"].get("open_time"),
            "close": analysis["features"].get("close"),
            "regime": analysis["summary"].get("regime"),
            "final_bias": analysis["summary"].get("final_bias"),
            "final_score": analysis["summary"].get("final_score"),
            "recommendation": analysis["summary"].get("recommendation"),
            "htf_alignment": analysis["features"].get("htf_alignment"),
            "funding_rate": analysis["features"].get("funding_rate"),
            "oi": analysis["features"].get("open_interest"),
            "ob_imbalance": analysis["features"].get("order_book_imbalance"),
            "bundle_count": len(analysis.get("bundles", []) or []),
            "best_bundle": best_bundle,
        })
        recent_bars = df.tail(20)[["open_time", "open", "high", "low", "close", "volume"]].to_dict(orient="records")
        for strategy_row in (analysis.get("strategies", []) or []) + (analysis.get("bundles", []) or []):
            strategy_mode = strategy_row.get("strategy_mode") or "single"
            owner_key = strategy_row.get("trade_owner_key") or f"single:{strategy_row.get('version_id')}"
            if strategy_row.get("bias") in {"OFF", "WAIT"}:
                continue
            if strategy_mode == "single" and int(strategy_row.get("version_id") or -1) <= 0:
                continue
            if strategy_mode == "bundle":
                slot_cfg = {
                    "slot_id": strategy_row.get("slot_id", -100),
                    "strategy_id": strategy_row.get("strategy_id", -1),
                    "version_id": strategy_row.get("version_id", -1),
                    "version_no": strategy_row.get("version_no", 1),
                    "strategy_name": strategy_row.get("bundle_name") or strategy_row.get("strategy_name"),
                    "template_key": "bundle",
                    "expected_rr": strategy_row.get("expected_rr", "1:3"),
                    "rule_params": strategy_row.get("rule_params", {}),
                    "exit_family": strategy_row.get("exit_family"),
                }
            else:
                slot_cfg = next((x for x in slot_rows if int(x.get("slot_id")) == int(strategy_row["slot_id"])), None)
                if not slot_cfg:
                    continue
            if storage.has_open_trade_owner_conflict(symbol, str(strategy_row["bias"]), owner_key, strategy_mode, version_id=(strategy_row.get("version_id") if strategy_mode == "single" else None)):
                continue
            strategy_packet = {**slot_cfg, **strategy_row}
            setup_summary = build_setup_summary(symbol, analysis_timeframe, strategy_packet, analysis)
            signal_key = f"{symbol}|{analysis_timeframe}|{strategy_mode}|{owner_key}|{analysis['features'].get('open_time')}|{strategy_row['bias']}"
            signal_id = storage.upsert_signal({
                "signal_key": signal_key,
                "symbol": symbol,
                "interval": analysis_timeframe,
                "bar_open_time": analysis["features"].get("open_time"),
                "slot_id": strategy_row.get("slot_id"),
                "strategy_id": strategy_row.get("strategy_id"),
                "version_id": strategy_row.get("version_id"),
                "strategy_name": strategy_packet.get("strategy_name"),
                "version_no": strategy_packet.get("version_no"),
                "strategy_mode": strategy_mode,
                "trade_owner_key": owner_key,
                "exit_family": strategy_packet.get("exit_family") or (strategy_packet.get("rule_params") or {}).get("exit_family"),
                "bundle_components_json": strategy_row.get("bundle_components", []),
                "regime": analysis["summary"].get("regime"),
                "bias": strategy_row["bias"],
                "score": strategy_row["score"],
                "recommendation": strategy_row["note"],
                "setup_summary": setup_summary,
                "feature_json": analysis["features"],
                "htf_context_json": analysis.get("htf_context", {}),
                "recent_bars_json": recent_bars,
                "strategy_snapshot_json": strategy_packet,
                "market_snapshot_json": analysis.get("market_snapshot", {}),
            })
            if auto_paper_mode and strategy_row["bias"] in {"LONG", "SHORT"}:
                levels = build_trade_levels(analysis["features"], strategy_row["bias"], expected_rr=slot_cfg.get("expected_rr"), rule_params=slot_cfg.get("rule_params", {}))
                if levels:
                    storage.create_or_update_paper_trade_from_signal({
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "interval": analysis_timeframe,
                        "slot_id": strategy_row.get("slot_id"),
                        "strategy_id": strategy_row.get("strategy_id"),
                        "version_id": strategy_row.get("version_id"),
                        "strategy_name": strategy_packet.get("strategy_name"),
                        "version_no": strategy_packet.get("version_no"),
                        "strategy_mode": strategy_mode,
                        "trade_owner_key": owner_key,
                        "exit_family": levels.get("exit_family"),
                        "bundle_components_json": strategy_row.get("bundle_components", []),
                        "side": strategy_row["bias"],
                        "entry_price": levels["entry_price"],
                        "stop_loss": levels["stop_loss"],
                        "stop_loss_initial": levels.get("stop_loss_initial"),
                        "stop_loss_current": levels.get("stop_loss_current"),
                        "sl_state": levels.get("sl_state", "INITIAL"),
                        "take_profit": levels["take_profit"],
                        "expected_rr": levels["expected_rr"],
                        "tp_mode": levels.get("tp_mode"),
                        "tp_count": levels.get("tp_count"),
                        "tp_late_trigger_ratio": levels.get("tp_late_trigger_ratio"),
                        "late_trigger_index": levels.get("late_trigger_index"),
                        "be_trigger_index": levels.get("be_trigger_index"),
                        "lock_trigger_index": levels.get("lock_trigger_index"),
                        "lock_to_tp_index": levels.get("lock_to_tp_index"),
                        "tp_levels": levels.get("tp_levels"),
                        "tp1_price": levels.get("tp1_price"),
                        "tp2_price": levels.get("tp2_price"),
                        "tp3_price": levels.get("tp3_price"),
                        "tp4_price": levels.get("tp4_price"),
                        "highest_tp_hit": levels.get("highest_tp_hit", 0),
                        "tp_hit_count": levels.get("tp_hit_count", 0),
                        "risk_pct": levels["risk_pct"],
                        "reward_pct": levels["reward_pct"],
                        "confidence": strategy_row["score"],
                        "decision": "SKIPPED",
                        "user_comment": "",
                        "setup_summary": setup_summary,
                        "trade_summary": f"Auto-paper trade candidate created. Default decision = SKIPPED until you review it. Exit family: {levels.get('exit_family')}. TP mode: {levels.get('tp_mode')}. TP ladder: {levels.get('tp_levels')}",
                        "feature_json": analysis["features"],
                        "htf_context_json": analysis.get("htf_context", {}),
                        "recent_bars_json": recent_bars,
                        "strategy_snapshot_json": strategy_packet,
                    })
    return scanner_rows, analysis_map

def slot_fingerprint(slot_rows: list[dict[str, Any]]) -> str:
    payload = []
    for row in slot_rows:
        payload.append({
            "slot_id": row.get("slot_id"),
            "version_id": row.get("version_id"),
            "analyze": row.get("analyze"),
            "enabled": row.get("enabled"),
            "expected_rr": row.get("expected_rr"),
            "score_threshold": row.get("score_threshold"),
            "indicator_rules_json": row.get("indicator_rules_json"),
            "rule_params_json": row.get("rule_params_json"),
        })
    return json.dumps(payload, sort_keys=True, default=str)
