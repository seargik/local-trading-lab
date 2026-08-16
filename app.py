from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app_src.app_state import load_user_config, save_user_config
from app_src.engine import analyze_symbol, build_trade_levels, evaluate_trade_outcome, normalize_slot_rows, parse_rr, resolve_tp_settings, DEFAULT_TP_MODE
from app_src.analysis_core import current_trade_signal_state as core_current_trade_signal_state, evaluate_open_trades as core_evaluate_open_trades, run_scanner as core_run_scanner
from app_src.features import INDICATOR_CATALOG, INDICATOR_LIBRARY, INDICATOR_SUGGESTIONS, enrich_features
from app_src.market_data import AnalyzerService, BinanceFuturesClient, CollectorService
from app_src.settings import (
    ANALYSIS_TIMEFRAME_OPTIONS,
    APP_NAME,
    CHART_TIMEFRAME_OPTIONS,
    LAB_DB_PATH,
    DEFAULT_ANALYSIS_TIMEFRAME,
    DEFAULT_CHART_TIMEFRAME,
    DEFAULT_LOOKBACK,
    DEFAULT_SELECTED_SYMBOLS,
    DEFAULT_TIMEFRAME,
    EXCHANGE_NAME,
    HTF_MAP,
    MAX_CHART_POINTS,
    REFRESH_INTERVAL_MS,
    SYMBOL_LIST_LIMIT,
    STRATEGY_SLOT_COUNT,
)
from app_src.storage import Storage
from app_src.analysis_core import build_outcome_summary, build_setup_summary, build_trade_summary
from app_src.strategies import TEMPLATE_OPTIONS
from app_src.backtest_ui import render_backtest_tab
from app_src.ohlcv_store import load_range as load_range_from_store
from app_src.trend_lifecycle import classify_analysis_map

st.set_page_config(page_title=APP_NAME, layout="wide")


@st.cache_data(ttl=60)
def fetch_store_chart_history(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    try:
        requested = max(int(limit or 0), 1200)
        return load_range_from_store(symbol, timeframe, limit=max(requested * 12, 5000))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400)
def load_available_symbols() -> list[dict[str, Any]]:
    client = BinanceFuturesClient()
    return client.list_symbols()


@st.cache_data(ttl=60)
def fetch_market_snapshot(symbol: str, timeframe: str) -> dict[str, Any]:
    client = BinanceFuturesClient()
    return client.fetch_market_snapshot(symbol, oi_period=timeframe if timeframe in {"5m", "15m", "1h"} else "5m")


@st.cache_data(ttl=300)
def fetch_htf_frames(symbol: str, main_timeframe: str) -> dict[str, pd.DataFrame]:
    client = BinanceFuturesClient()
    timeframes = HTF_MAP.get(main_timeframe, ["15m", "1h", "4h"])
    out: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        try:
            out[tf] = client.fetch_history_df(symbol, tf, limit=300)
        except Exception:
            continue
    return out


def build_chart(price_df: pd.DataFrame, feature_df: pd.DataFrame, symbol: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=price_df["open_time"],
            open=price_df["open"],
            high=price_df["high"],
            low=price_df["low"],
            close=price_df["close"],
            name="Price",
        )
    )
    for col, name in [("ema_20", "EMA 20"), ("ema_50", "EMA 50"), ("ma_200", "MA 200")]:
        if col in feature_df.columns:
            fig.add_trace(go.Scatter(x=feature_df["open_time"], y=feature_df[col], mode="lines", name=name))
    if "vwap_session" in feature_df.columns:
        fig.add_trace(go.Scatter(x=feature_df["open_time"], y=feature_df["vwap_session"], mode="lines", name="VWAP"))
    fig.update_layout(title=f"{symbol} – latest candles", xaxis_rangeslider_visible=False, height=520, margin=dict(l=10, r=10, t=50, b=10))
    return fig


def safe_json_load(raw: Any, fallback: Any) -> Any:
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return fallback


BOOL_INDICATORS = {"volume_spike", "bullish_divergence", "bearish_divergence", "liquidity_sweep_high", "liquidity_sweep_low"}
LABEL_INDICATORS = {"market_structure", "local_trend", "global_trend", "htf_alignment", "htf_15m_trend", "htf_1h_trend", "htf_4h_trend"}

def indicator_data_type(indicator: str) -> str:
    if indicator in BOOL_INDICATORS:
        return "boolean"
    if indicator in LABEL_INDICATORS or indicator.endswith("_trend"):
        return "label/string"
    return "number"

def _rehydrate_analysis_map(raw_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for symbol, analysis in (raw_map or {}).items():
        if not isinstance(analysis, dict):
            continue
        item = dict(analysis)
        frame = item.get("frame")
        if isinstance(frame, list):
            item["frame"] = pd.DataFrame(frame)
        elif isinstance(frame, dict):
            item["frame"] = pd.DataFrame(frame)
        elif not isinstance(frame, pd.DataFrame):
            item["frame"] = pd.DataFrame()
        out[str(symbol)] = item
    return out


def prepare_display_df(df: pd.DataFrame, stringify_columns: list[str] | None = None) -> pd.DataFrame:
    out = df.copy()
    stringify_columns = stringify_columns or []
    for col in stringify_columns:
        if col in out.columns:
            out[col] = out[col].map(lambda x: "" if pd.isna(x) else str(x))
    return out

def indicator_items_df(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for key, value in sorted(payload.items()):
        rows.append({"indicator": str(key), "value": "" if value is None else str(value)})
    return pd.DataFrame(rows)

def build_trade_packet(trade_row: pd.Series | dict[str, Any], current_score: Any = None, current_bias: Any = None) -> dict[str, Any]:
    row = dict(trade_row)
    return {
        "packet_type": "local_lab_trade_v1",
        "trade_id": int(row.get("trade_id") or 0),
        "created_at": str(row.get("created_at") or ""),
        "symbol": row.get("symbol"),
        "interval": row.get("interval"),
        "strategy_name": row.get("strategy_name"),
        "version_no": int(row.get("version_no") or 0),
        "side": row.get("side"),
        "decision": row.get("decision"),
        "status": row.get("status"),
        "start_score": row.get("confidence"),
        "current_score": current_score,
        "current_bias": current_bias,
        "tp_mode": row.get("tp_mode"),
        "tp_count": row.get("tp_count"),
        "tp_progress": format_tp_progress(row),
        "setup_summary": row.get("setup_summary") or build_setup_summary(row.get("symbol"), row.get("interval"), safe_json_load(row.get("strategy_snapshot_json"), {}), {"features": safe_json_load(row.get("feature_json"), {}), "htf_context": safe_json_load(row.get("htf_context_json"), {}), "summary": {"regime": "n/a"}}),
        "trade_summary": row.get("trade_summary") or build_trade_summary(row),
        "outcome_summary": row.get("outcome_summary") or build_outcome_summary(row),
        "strategy_snapshot": safe_json_load(row.get("strategy_snapshot_json"), {}),
        "features": safe_json_load(row.get("feature_json"), {}),
        "htf_context": safe_json_load(row.get("htf_context_json"), {}),
        "recent_bars": safe_json_load(row.get("recent_bars_json"), []),
        "tp_levels": safe_json_load(row.get("tp_levels_json"), []),
        "tp_hits": safe_json_load(row.get("tp_hits_json"), {}),
    }

def slot_suggest_changed(slot_id: int) -> None:
    if st.session_state.get(f"slot_suggest_{slot_id}"):
        st.session_state[f"slot_analyze_{slot_id}"] = True

def slot_analyze_changed(slot_id: int) -> None:
    if not st.session_state.get(f"slot_analyze_{slot_id}"):
        st.session_state[f"slot_suggest_{slot_id}"] = False

def current_trade_signal_state(analysis_map: dict[str, dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    return core_current_trade_signal_state(analysis_map)

def price_to_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        return format(float(value), '.12g')
    except Exception:
        return str(value)


def parse_price_text(text: str, fallback: float = 0.0) -> float:
    raw = str(text or '').strip()
    if raw == '':
        return float(fallback)
    return float(raw)


def strategy_tp_defaults(rule_params: dict[str, Any] | None, expected_rr: str | None) -> dict[str, Any]:
    return resolve_tp_settings(expected_rr, rule_params or {})


def format_tp_progress(row: pd.Series | dict[str, Any]) -> str:
    r = dict(row)
    try:
        hit = int(r.get('highest_tp_hit') or 0)
    except Exception:
        hit = 0
    try:
        total = int(r.get('tp_count') or 0)
    except Exception:
        total = 0
    return f"{hit}/{total}" if total else f"{hit}/0"


FIELD_IMPACT_GUIDE: list[dict[str, Any]] = [
    {"field": "strategy_name", "impacts_live_logic": False, "notes": "Human label only."},
    {"field": "human_thesis", "impacts_live_logic": False, "notes": "Research context for you and LLM review."},
    {"field": "expected_outcome", "impacts_live_logic": False, "notes": "Research expectation only."},
    {"field": "indicator_description", "impacts_live_logic": False, "notes": "Reasoning notes only."},
    {"field": "template_key", "impacts_live_logic": True, "notes": "Selects the scoring engine."},
    {"field": "indicators", "impacts_live_logic": False, "notes": "Documentation / export context unless also referenced in rules or params."},
    {"field": "indicator_rules", "impacts_live_logic": True, "notes": "Directly changes rule-based scoring."},
    {"field": "rule_params", "impacts_live_logic": True, "notes": "Directly changes thresholds, weights, overlays, and staged TP/SL behavior."},
    {"field": "expected_rr", "impacts_live_logic": True, "notes": "Changes final target distance and staged exit construction."},
    {"field": "tp_mode", "impacts_live_logic": True, "notes": "Controls how interim TP ladder levels are built."},
    {"field": "tp_count", "impacts_live_logic": True, "notes": "Controls how many TP milestones exist for the trade."},
    {"field": "tp_late_trigger_ratio", "impacts_live_logic": True, "notes": "Controls when SL advances from breakeven to TP1 lock-in for larger TP ladders."},
    {"field": "score_threshold", "impacts_live_logic": True, "notes": "Controls when a setup becomes actionable."},
    {"field": "notes", "impacts_live_logic": False, "notes": "Journal / context only."},
]

EXPECTED_AI_ANSWER_FORMAT = {
    "summary": "What should change and why",
    "keep": ["What should stay unchanged"],
    "change_requests": [
        {
            "field": "human_thesis | expected_outcome | template_key | expected_rr | score_threshold | indicator_rules | rule_params | indicators",
            "reason": "Why to change it",
            "proposed_value": "New value or object"
        }
    ],
    "new_indicator_requests": [
        {
            "indicator": "name",
            "why_needed": "What edge it adds",
            "formula_or_definition": "How to calculate it"
        }
    ],
    "final_check": "State whether the revised strategy is trend, range, breakout, or observation oriented"
}


def _impact_label(flag: bool) -> str:
    return "Impacts live trading logic" if flag else "Context only / does not affect live logic"


def build_strategy_packet(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    rule_params = safe_json_load(r.get("rule_params_json"), r.get("rule_params", {}))
    tp_cfg = strategy_tp_defaults(rule_params, r.get("expected_rr", "1:3"))
    strategy_payload = {
        "strategy_id": int(r.get("strategy_id", 0) or 0),
        "version_id": int(r.get("version_id", 0) or 0),
        "version_no": int(r.get("version_no", 0) or 0),
        "created_at": str(r.get("created_at") or ""),
        "strategy_name": r.get("strategy_name", ""),
        "template_key": r.get("template_key", ""),
        "human_thesis": r.get("human_thesis", ""),
        "expected_outcome": r.get("expected_outcome", ""),
        "indicator_description": r.get("indicator_description", ""),
        "indicators": safe_json_load(r.get("indicators_json"), r.get("indicators", [])),
        "indicator_rules": clean_rule_rows(safe_json_load(r.get("indicator_rules_json"), r.get("indicator_rules", []))),
        "rule_params": rule_params,
        "expected_rr": r.get("expected_rr", "1:3"),
        "tp_mode": rule_params.get("tp_mode", tp_cfg["tp_mode"]),
        "tp_count": int(rule_params.get("tp_count") or tp_cfg["tp_count"]),
        "tp_late_trigger_ratio": float(rule_params.get("tp_late_trigger_ratio") or tp_cfg["tp_late_trigger_ratio"]),
        "score_threshold": float(r.get("score_threshold", 70) or 70),
        "notes": r.get("notes", ""),
    }
    return {
        "packet_type": "strategy_lab_strategy_v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy_payload,
        "field_impact_guide": FIELD_IMPACT_GUIDE,
        "available_kpis": [{**item, "data_type": indicator_data_type(item["indicator"])} for item in INDICATOR_LIBRARY],
        "indicator_rule_syntax": {
            "supported_operators": [">", ">=", "<", "<=", "==", "!=", "between", "contains", "true", "false"],
            "value_examples": [
                {"indicator": "rsi_14", "operator": ">=", "value": 55},
                {"indicator": "ema_20", "operator": ">", "value": "ema_50"},
                {"indicator": "htf_1h_trend", "operator": "==", "value": "up"},
                {"indicator": "volume_spike", "operator": "==", "value": True},
                {"indicator": "rsi_14", "operator": "between", "value": 45, "value_2": 68},
            ],
            "indicator_name_syntax": {
                "main_tf": "plain indicator name such as rsi_14 or ema_20",
                "htf": "htf_<timeframe>_<field>, for example htf_1h_rsi_14 or htf_4h_trend",
                "comparisons": "use another indicator name in the value field for comparisons such as ema_20 > ema_50"
            },
            "new_indicator_definition_template": {
                "indicator": "new_indicator_name",
                "category": "Momentum | Trend | Volatility | Order book | Derivatives | HTF context | SMC proxy",
                "data_type": "number | boolean | label/string",
                "formula": "Write the exact calculation formula or detection logic",
                "source_fields": ["close", "high", "low", "volume"],
                "timeframe_scope": "main_tf | htf_15m | htf_1h | htf_4h",
                "why_it_matters": "What edge or filter it adds"
            },
        },
        "suggested_future_kpis": INDICATOR_SUGGESTIONS,
        "expected_ai_answer_format": EXPECTED_AI_ANSWER_FORMAT,
        "instructions": [
            "Use only fields listed in the strategy object when proposing revisions.",
            "Keep the answer structured using the expected_ai_answer_format object.",
            "If you suggest new KPIs, explain the formula and why they matter.",
            "Do not assume hidden code changes; propose values that can be pasted back into Strategy Lab.",
        ],
    }

def extract_import_payload(packet: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(packet, dict):
        return None
    if isinstance(packet.get("strategy"), dict):
        src = packet["strategy"]
    else:
        src = packet
    if not src.get("strategy_name"):
        return None
    rule_params = dict(src.get("rule_params", {}))
    if "tp_mode" in src:
        rule_params["tp_mode"] = src.get("tp_mode")
    if "tp_count" in src:
        rule_params["tp_count"] = src.get("tp_count")
    if "tp_late_trigger_ratio" in src:
        rule_params["tp_late_trigger_ratio"] = src.get("tp_late_trigger_ratio")
    return {
        "strategy_name": src.get("strategy_name", ""),
        "template_key": src.get("template_key", TEMPLATE_OPTIONS[0][0]),
        "human_thesis": src.get("human_thesis", ""),
        "expected_outcome": src.get("expected_outcome", ""),
        "indicator_description": src.get("indicator_description", ""),
        "indicators": list(src.get("indicators", [])),
        "indicator_rules": clean_rule_rows(src.get("indicator_rules", [])),
        "rule_params": rule_params,
        "expected_rr": src.get("expected_rr", "1:3"),
        "score_threshold": float(src.get("score_threshold", rule_params.get("score_threshold", 70) or 70),),
        "notes": src.get("notes", ""),
    }


def load_payload_into_editor(prefix: str, payload: dict[str, Any]) -> None:
    st.session_state[f"{prefix}_name"] = payload.get("strategy_name", "")
    st.session_state[f"{prefix}_template"] = payload.get("template_key", TEMPLATE_OPTIONS[0][0])
    st.session_state[f"{prefix}_thesis"] = payload.get("human_thesis", "")
    st.session_state[f"{prefix}_expected"] = payload.get("expected_outcome", "")
    st.session_state[f"{prefix}_indicator_desc"] = payload.get("indicator_description", "")
    st.session_state[f"{prefix}_indicators"] = list(payload.get("indicators", []))
    st.session_state[f"{prefix}_custom_indicators"] = ""
    st.session_state[f"{prefix}_indicator_rules"] = clean_rule_rows(payload.get("indicator_rules", []))
    st.session_state[f"{prefix}_rule_param_rows"] = param_rows_to_df(payload.get("rule_params", {})).to_dict(orient="records")
    st.session_state[f"{prefix}_rule_params"] = "{}"
    st.session_state[f"{prefix}_rr"] = payload.get("expected_rr", "1:3")
    tp_cfg = strategy_tp_defaults(payload.get("rule_params", {}), payload.get("expected_rr", "1:3"))
    st.session_state[f"{prefix}_tp_mode"] = payload.get("rule_params", {}).get("tp_mode", tp_cfg["tp_mode"])
    st.session_state[f"{prefix}_tp_count"] = int(payload.get("rule_params", {}).get("tp_count") or tp_cfg["tp_count"])
    st.session_state[f"{prefix}_tp_late_ratio"] = float(payload.get("rule_params", {}).get("tp_late_trigger_ratio") or tp_cfg["tp_late_trigger_ratio"])
    st.session_state[f"{prefix}_threshold"] = float(payload.get("score_threshold", payload.get("rule_params", {}).get("score_threshold", 70) or 70))
    st.session_state[f"{prefix}_notes"] = payload.get("notes", "")

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
        f"TP mode: {strategy_row.get('tp_mode', strategy_row.get('rule_params', {}).get('tp_mode', DEFAULT_TP_MODE))}",
        f"TP count: {strategy_row.get('tp_count', strategy_row.get('rule_params', {}).get('tp_count', 'auto'))}",
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
    tp_levels = safe_json_load(row.get('tp_levels_json'), row.get('tp_levels', []))
    return "\n".join(
        [
            f"Trade ID: {row.get('trade_id')}",
            f"Symbol: {row.get('symbol')}",
            f"Strategy: {row.get('strategy_name')} v{row.get('version_no')}",
            f"Decision: {row.get('decision')}",
            f"Side: {row.get('side')}",
            f"Entry: {row.get('entry_price')}",
            f"SL initial: {row.get('stop_loss_initial', row.get('stop_loss'))}",
            f"SL current: {row.get('stop_loss_current', row.get('stop_loss'))}",
            f"SL state: {row.get('sl_state')}",
            f"TP1: {row.get('tp1_price')}",
            f"TP final: {row.get('take_profit')}",
            f"TP mode: {row.get('tp_mode')}",
            f"TP progress: {format_tp_progress(row)}",
            f"Expected RR: {row.get('expected_rr')}",
            f"TP ladder: {tp_levels}",
            f"Comment: {row.get('user_comment') or ''}",
        ]
    )


def build_outcome_summary(trade_row: pd.Series | dict[str, Any]) -> str:
    row = dict(trade_row)
    hits = [f"TP{i}@{row.get(f'tp{i}_hit_at')}" for i in range(1, 5) if row.get(f'tp{i}_hit_at')]
    return "\n".join(
        [
            f"Trade ID: {row.get('trade_id')}",
            f"Status: {row.get('status')}",
            f"Close reason: {row.get('close_reason')}",
            f"Outcome label: {row.get('outcome_label')}",
            f"Close price: {row.get('close_price')}",
            f"Final SL state: {row.get('sl_state')}",
            f"Highest TP hit: {row.get('highest_tp_hit')}",
            f"TP hits: {', '.join(hits) if hits else 'none'}",
            f"PnL %: {row.get('pnl_pct')}",
            f"MFE %: {row.get('mfe_pct')}",
            f"MAE %: {row.get('mae_pct')}",
            f"Follow-through score: {row.get('follow_through_score')}",
        ]
    )

def init_state() -> None:
    if "storage" not in st.session_state:
        st.session_state.storage = Storage(LAB_DB_PATH)
    if "collector" not in st.session_state:
        st.session_state.collector = CollectorService(st.session_state.storage)
    if "analyzer" not in st.session_state:
        st.session_state.analyzer = AnalyzerService(st.session_state.storage)
    if "config_loaded" not in st.session_state:
        config = load_user_config()
        st.session_state.selected_symbols = config.get("selected_symbols", DEFAULT_SELECTED_SYMBOLS)
        st.session_state.analysis_timeframe = config.get("analysis_timeframe", config.get("timeframe", DEFAULT_ANALYSIS_TIMEFRAME))
        st.session_state.chart_timeframe = config.get("chart_timeframe", DEFAULT_CHART_TIMEFRAME)
        st.session_state.timeframe = st.session_state.analysis_timeframe
        st.session_state.auto_paper_mode = config.get("auto_paper_mode", False)
        st.session_state.live_bundle_mode = config.get("live_bundle_mode", False)
        st.session_state.lookback = int(config.get("lookback", DEFAULT_LOOKBACK))
        st.session_state.poll_seconds = int(config.get("poll_seconds", 300))
        st.session_state.config_loaded = True
        st.session_state.last_strategy_save = None
        st.session_state.analysis_cache = {"scanner_rows": [], "analysis_map": {}, "score_map": {}, "last_run_at": None, "last_event_time": None, "slot_fingerprint": None, "timeframe": None, "analysis_timeframe": None, "chart_timeframe": None, "symbols": []}
        st.session_state.analysis_stale = True


def save_current_config() -> None:
    save_user_config(
        {
            "selected_symbols": st.session_state.selected_symbols,
            "timeframe": st.session_state.analysis_timeframe,
            "analysis_timeframe": st.session_state.analysis_timeframe,
            "chart_timeframe": st.session_state.chart_timeframe,
            "auto_paper_mode": st.session_state.auto_paper_mode,
            "live_bundle_mode": st.session_state.get("live_bundle_mode", False),
            "lookback": st.session_state.get("lookback", DEFAULT_LOOKBACK),
            "poll_seconds": st.session_state.get("poll_seconds", 300),
        }
    )


def evaluate_open_trades(storage: Storage) -> None:
    core_evaluate_open_trades(storage)

def run_scanner(storage: Storage, main_timeframe: str, selected_symbols: list[str], slot_rows: list[dict[str, Any]], auto_paper_mode: bool, live_bundle_mode: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    return core_run_scanner(storage, main_timeframe, selected_symbols, slot_rows, auto_paper_mode, live_bundle_mode=live_bundle_mode)

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


def get_cached_or_run_analysis(storage: Storage, timeframe: str, selected_symbols: list[str], slot_rows: list[dict[str, Any]], auto_paper_mode: bool, collector_status, analyzer_service, live_bundle_mode: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[tuple[Any, ...], dict[str, Any]], bool]:
    force_refresh = bool(st.session_state.pop("force_analysis_refresh", False))
    if force_refresh:
        analyzer_service.request_run(reason="manual_ui_refresh")
    cache = storage.read_analysis_cache()
    scanner_rows = cache.get("scanner_rows", []) or []
    analysis_map = _rehydrate_analysis_map(cache.get("analysis_map", {}) or {})
    meta = cache.get("meta", {}) or {}
    score_map = current_trade_signal_state(analysis_map) if analysis_map else {}
    st.session_state.analysis_cache = {
        "scanner_rows": scanner_rows,
        "analysis_map": analysis_map,
        "score_map": score_map,
        "last_run_at": meta.get("last_run_at"),
        "last_event_time": meta.get("latest_closed_candle_time"),
        "slot_fingerprint": meta.get("slot_fingerprint"),
        "timeframe": meta.get("timeframe"),
        "analysis_timeframe": meta.get("analysis_timeframe") or meta.get("timeframe"),
        "chart_timeframe": meta.get("chart_timeframe"),
        "symbols": meta.get("symbols", []),
        "live_bundle_mode": meta.get("live_bundle_mode", False),
    }
    analysis_ran = bool(meta.get("last_run_at") and meta.get("last_run_at") != st.session_state.get("last_seen_analysis_run_at"))
    st.session_state["last_seen_analysis_run_at"] = meta.get("last_run_at")
    return scanner_rows, analysis_map, score_map, analysis_ran

def _coerce_rule_value(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw
    text = str(raw).strip()
    if text == "":
        return ""
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        if "." in text:
            return float(text)
        return int(text)
    except Exception:
        return text


def rule_rows_to_df(rule_rows: list[dict[str, Any]] | None) -> pd.DataFrame:
    rows = rule_rows or []
    if not rows:
        rows = [{"enabled": True, "indicator": "rsi_14", "operator": ">=", "value": 50, "value_2": "", "weight": 10, "bias": "LONG"}]
    normalized = []
    for row in rows:
        normalized.append(
            {
                "enabled": bool(row.get("enabled", True)),
                "indicator": str(row.get("indicator", "") or ""),
                "operator": str(row.get("operator", ">=") or ">="),
                "value": "" if row.get("value") is None else str(row.get("value")),
                "value_2": "" if row.get("value_2") is None else str(row.get("value_2")),
                "weight": float(row.get("weight", 10) or 10),
                "bias": str(row.get("bias", "BOTH") or "BOTH").upper(),
            }
        )
    return pd.DataFrame(normalized)


def clean_rule_rows(raw_rows: Any) -> list[dict[str, Any]]:
    if isinstance(raw_rows, pd.DataFrame):
        rows = raw_rows.to_dict(orient="records")
    else:
        rows = list(raw_rows or [])
    out: list[dict[str, Any]] = []
    for row in rows:
        indicator = str(row.get("indicator", "") or "").strip()
        if not indicator:
            continue
        cleaned = {
            "enabled": bool(row.get("enabled", True)),
            "indicator": indicator,
            "operator": str(row.get("operator", ">=") or ">=").strip(),
            "value": _coerce_rule_value(row.get("value")),
            "value_2": _coerce_rule_value(row.get("value_2")),
            "weight": float(row.get("weight", 10) or 10),
            "bias": str(row.get("bias", "BOTH") or "BOTH").upper(),
        }
        out.append(cleaned)
    return out


def param_rows_to_df(params: dict[str, Any] | None) -> pd.DataFrame:
    items = []
    for key, value in sorted((params or {}).items()):
        if key == "score_threshold":
            continue
        items.append({"param": str(key), "value": "" if value is None else str(value)})
    if not items:
        items = [{"param": "rule_overlay_weight", "value": "0.35"}]
    df = pd.DataFrame(items)
    if not df.empty:
        df["param"] = df["param"].astype(str)
        df["value"] = df["value"].astype(str)
    return df


def clean_param_rows(raw_rows: Any) -> dict[str, Any]:
    if isinstance(raw_rows, pd.DataFrame):
        rows = raw_rows.to_dict(orient="records")
    else:
        rows = list(raw_rows or [])
    out: dict[str, Any] = {}
    for row in rows:
        key = str(row.get("param", "") or "").strip()
        if not key:
            continue
        out[key] = _coerce_rule_value(row.get("value"))
    return out


def strategy_editor_payload(prefix: str) -> dict[str, Any]:
    indicators = st.session_state.get(f"{prefix}_indicators", [])
    custom = st.session_state.get(f"{prefix}_custom_indicators", "")
    extra_indicators = [x.strip() for x in custom.split(",") if x.strip()]
    base_params = clean_param_rows(st.session_state.get(f"{prefix}_rule_param_rows", []))
    json_overrides = safe_json_load(st.session_state.get(f"{prefix}_rule_params", "{}"), {})
    tp_mode = st.session_state.get(f"{prefix}_tp_mode", DEFAULT_TP_MODE)
    tp_count = int(st.session_state.get(f"{prefix}_tp_count", 4) or 4)
    tp_late_ratio = float(st.session_state.get(f"{prefix}_tp_late_ratio", 0.75) or 0.75)
    merged_params = {
        **base_params,
        **json_overrides,
        "score_threshold": float(st.session_state.get(f"{prefix}_threshold", 70.0)),
        "tp_mode": tp_mode,
        "tp_count": tp_count,
        "tp_late_trigger_ratio": tp_late_ratio,
    }
    return {
        "strategy_name": st.session_state.get(f"{prefix}_name", ""),
        "template_key": st.session_state.get(f"{prefix}_template", TEMPLATE_OPTIONS[0][0]),
        "human_thesis": st.session_state.get(f"{prefix}_thesis", ""),
        "expected_outcome": st.session_state.get(f"{prefix}_expected", ""),
        "indicator_description": st.session_state.get(f"{prefix}_indicator_desc", ""),
        "indicators": list(OrderedDict.fromkeys(indicators + extra_indicators)),
        "indicator_rules": clean_rule_rows(st.session_state.get(f"{prefix}_indicator_rules", [])),
        "rule_params": merged_params,
        "expected_rr": st.session_state.get(f"{prefix}_rr", "1:3"),
        "tp_mode": tp_mode,
        "tp_count": tp_count,
        "tp_late_trigger_ratio": tp_late_ratio,
        "score_threshold": float(st.session_state.get(f"{prefix}_threshold", 70.0)),
        "notes": st.session_state.get(f"{prefix}_notes", ""),
    }


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 8)
    return value


def payload_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return json.dumps(_canonicalize(a), sort_keys=True, default=str) == json.dumps(_canonicalize(b), sort_keys=True, default=str)


def main() -> None:
    init_state()
    storage: Storage = st.session_state.storage
    collector: CollectorService = st.session_state.collector
    analyzer: AnalyzerService = st.session_state.analyzer

    st.session_state.setdefault("ui_auto_refresh", True)
    if st.session_state.get("ui_auto_refresh", True) and (collector.get_status().running or analyzer.get_status().running):
        st_autorefresh(interval=REFRESH_INTERVAL_MS, key="scanner_refresh")

    st.title(APP_NAME)
    st.caption("Local Lab: local-first crypto strategy research workflow with editable strategy versioning, ten runnable strategy slots, auto-paper mode, richer indicator context, and structured LLM-ready packets, 10 strategy slots, manual trade journaling, and extended KPI coverage.")

    with st.sidebar:
        st.header("Market data")
        chart_timeframe = st.selectbox("Chart timeframe", CHART_TIMEFRAME_OPTIONS, index=CHART_TIMEFRAME_OPTIONS.index(st.session_state.chart_timeframe if st.session_state.chart_timeframe in CHART_TIMEFRAME_OPTIONS else DEFAULT_CHART_TIMEFRAME))
        analysis_timeframe = st.selectbox("Analysis timeframe", ANALYSIS_TIMEFRAME_OPTIONS, index=ANALYSIS_TIMEFRAME_OPTIONS.index(st.session_state.analysis_timeframe if st.session_state.analysis_timeframe in ANALYSIS_TIMEFRAME_OPTIONS else DEFAULT_ANALYSIS_TIMEFRAME))
        st.session_state.chart_timeframe = chart_timeframe
        st.session_state.analysis_timeframe = analysis_timeframe
        st.session_state.timeframe = analysis_timeframe
        st.caption("Use the chart timeframe for visual entry review and the analysis timeframe for regime/scanner logic.")
        lookback = st.number_input("Bootstrap candles per symbol", min_value=120, max_value=1000, step=20, key="lookback")
        poll_seconds = st.slider("Collector poll seconds", min_value=30, max_value=600, step=30, key="poll_seconds")
        visible_limit = st.slider("How many exchange pairs to show as checkboxes", 20, 150, SYMBOL_LIST_LIMIT, 10)
        search = st.text_input("Filter pairs", value="")
        st.session_state.auto_paper_mode = st.toggle("Auto-paper trade mode", value=st.session_state.auto_paper_mode, help="When on, every directional qualifying signal becomes a paper trade candidate. Decision starts as SKIPPED until you review it.")
        st.session_state.live_bundle_mode = st.toggle("Live bundle presets", value=st.session_state.get("live_bundle_mode", False), help="When on, the analyzer evaluates config/live_bundle_presets.json as separate bundle trade owners. Off by default for safe rollout.")
        st.session_state.ui_auto_refresh = st.toggle("Auto-refresh UI while live workers run", value=st.session_state.get("ui_auto_refresh", True), help="Turn this off when you want to work in the main UI without Streamlit refreshing every minute. The collector/analyzer can keep running in the background.")

        try:
            available = load_available_symbols()
            available_symbols = [item["symbol"] for item in available]
            visible_symbols = [s for s in available_symbols if search.upper() in s][:visible_limit] if search else available_symbols[:visible_limit]
            st.caption(f"Loaded {len(available_symbols)} tradable USDT perpetual pairs from {EXCHANGE_NAME}. Pair universe cache refreshes once per day.")
        except Exception as exc:
            available_symbols = list(OrderedDict.fromkeys(st.session_state.selected_symbols + DEFAULT_SELECTED_SYMBOLS))
            visible_symbols = [s for s in available_symbols if search.upper() in s][:visible_limit] if search else available_symbols
            st.warning(f"Could not fetch symbol list right now. Falling back to saved/default pairs. Error: {exc}")

        st.subheader("Choose pairs")
        currently_selected = set(st.session_state.selected_symbols)
        for symbol in visible_symbols:
            key = f"pair_{symbol}"
            if key not in st.session_state:
                st.session_state[key] = symbol in currently_selected
        columns = st.columns(2)
        for idx, symbol in enumerate(visible_symbols):
            with columns[idx % 2]:
                st.checkbox(symbol, key=f"pair_{symbol}")
        visible_selected = [symbol for symbol in visible_symbols if st.session_state.get(f"pair_{symbol}", False)]
        hidden_selected = [symbol for symbol in st.session_state.selected_symbols if symbol not in visible_symbols]
        st.session_state.selected_symbols = list(OrderedDict.fromkeys(hidden_selected + visible_selected))
        st.caption(f"Selected: {', '.join(st.session_state.selected_symbols) if st.session_state.selected_symbols else 'none'}")
        save_current_config()

        if st.button("Save app settings", width="stretch"):
            save_current_config()
            st.success("Saved")

        if st.button("Refresh analysis now", width="stretch"):
            st.session_state.force_analysis_refresh = True
            st.session_state.analysis_stale = False

        st.subheader("Run")
        sidebar_status = collector.get_status()
        collector_label = "Stop" if sidebar_status.running else "Start"
        collector_type = "secondary" if sidebar_status.running else "primary"
        if st.button(collector_label, width="stretch", type=collector_type):
            if sidebar_status.running:
                collector.stop()
                try:
                    analyzer.stop()
                except Exception:
                    pass
                save_current_config()
                st.info("Collector and analyzer stopped")
            else:
                save_current_config()
                collector.start(st.session_state.selected_symbols, st.session_state.analysis_timeframe, st.session_state.chart_timeframe, int(lookback), int(poll_seconds))
                try:
                    analyzer.start()
                except Exception:
                    pass
                st.success("Collector and analyzer started in background")

        st.subheader(f"Strategy slots (1–{STRATEGY_SLOT_COUNT})")
        latest_versions = storage.get_latest_strategy_versions()
        version_options = [(None, "— none —")] + [
            (int(row["version_id"]), f"{row['strategy_name']} v{int(row['version_no'])} | {row['template_key']}")
            for _, row in latest_versions.iterrows()
        ]
        option_map = {label: vid for vid, label in version_options}
        reverse_option_map = {vid: label for vid, label in version_options}
        version_labels = [label for _, label in version_options]
        active_slots = storage.get_active_slots()
        if st.button("Enable all strategies", width="stretch"):
            storage.set_all_slots_enabled(enabled=True, analyze=True)
            refreshed_slots = storage.get_active_slots()
            for _, refreshed_slot in refreshed_slots.iterrows():
                sid = int(refreshed_slot["slot_id"])
                label = reverse_option_map.get(refreshed_slot["version_id"], "— none —")
                st.session_state[f"slot_choice_{sid}"] = label if label in version_labels else "— none —"
                st.session_state[f"slot_analyze_{sid}"] = bool(refreshed_slot["analyze"])
                st.session_state[f"slot_suggest_{sid}"] = bool(refreshed_slot["enabled"])
            st.session_state.analysis_stale = True
            st.success("All populated strategy slots are now set to Analyse + Suggest.")
            st.rerun()
        for _, slot in active_slots.iterrows():
            slot_id = int(slot["slot_id"])
            current_label = reverse_option_map.get(slot["version_id"], "— none —")
            choice_key = f"slot_choice_{slot_id}"
            analyze_key = f"slot_analyze_{slot_id}"
            suggest_key = f"slot_suggest_{slot_id}"
            if choice_key not in st.session_state:
                st.session_state[choice_key] = current_label if current_label in version_labels else "— none —"
            if analyze_key not in st.session_state:
                st.session_state[analyze_key] = bool(slot["analyze"])
            if suggest_key not in st.session_state:
                st.session_state[suggest_key] = bool(slot["enabled"])
            chosen_label = st.selectbox(f"Slot {slot_id}", options=version_labels, key=choice_key)
            analyze = st.checkbox("Analyse", key=analyze_key, on_change=slot_analyze_changed, args=(slot_id,))
            suggest = st.checkbox("Suggest", key=suggest_key, on_change=slot_suggest_changed, args=(slot_id,))
            desired_version_id = option_map[chosen_label]
            desired_analyze = bool(st.session_state.get(analyze_key, False) or st.session_state.get(suggest_key, False))
            desired_suggest = bool(st.session_state.get(suggest_key, False))
            slot_dirty = (desired_version_id != slot["version_id"]) or (desired_analyze != bool(slot["analyze"])) or (desired_suggest != bool(slot["enabled"]))
            if st.button("Apply" if slot_dirty else "Applied", key=f"save_slot_{slot_id}", type="primary" if slot_dirty else "secondary", disabled=not slot_dirty, width="stretch"):
                storage.save_slot(slot_id, desired_version_id, desired_analyze, desired_suggest)
                st.session_state.analysis_stale = True
                try:
                    analyzer.request_run(reason=f"slot_{slot_id}_updated")
                except Exception:
                    pass
                st.success(f"Applied slot {slot_id}. Analyzer has been asked to rerun on the next cycle.")
                st.rerun()

    status = collector.get_status()
    analyzer_status = analyzer.get_status()
    slot_rows = normalize_slot_rows(storage.get_active_slots())
    scanner_rows, analysis_map, score_map, analysis_ran = get_cached_or_run_analysis(storage, st.session_state.analysis_timeframe, st.session_state.selected_symbols, slot_rows, st.session_state.auto_paper_mode, status, analyzer, live_bundle_mode=st.session_state.get("live_bundle_mode", False))

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Collector", "RUNNING" if status.running else "STOPPED")
    m2.metric("Candles stored", storage.count_candles())
    m3.metric("Selected symbols", len(st.session_state.selected_symbols))
    m4.metric("Analyzer", "RUNNING" if analyzer_status.running else "STOPPED")
    m5.metric("Auto-paper", "ON" if st.session_state.auto_paper_mode else "OFF")
    m6.metric("Bundles", "ON" if st.session_state.get("live_bundle_mode", False) else "OFF")
    st.caption(f"Collector bootstrap: {'YES' if status.history_bootstrapped else 'NO'} | Last collector event: {status.last_event_time or '—'} | Poll seconds: {getattr(status, 'poll_seconds', st.session_state.get('poll_seconds', 300))} | Analysis TF: {getattr(status, 'analysis_timeframe', st.session_state.analysis_timeframe)} | Chart TF: {getattr(status, 'chart_timeframe', st.session_state.chart_timeframe)} | Collector error: {status.last_error or '—'}")
    cache_meta = st.session_state.get("analysis_cache", {})
    st.caption(f"Analyzer: {'RUNNING' if analyzer_status.running else 'STOPPED'} | Last analysis: {cache_meta.get('last_run_at') or analyzer_status.last_run_at or '—'} | Latest analyzed candle: {cache_meta.get('last_event_time') or cache_meta.get('latest_closed_candle_time') or analyzer_status.last_analyzed_candle_time or '—'} | Analysis TF: {cache_meta.get('analysis_timeframe') or getattr(analyzer_status, 'analysis_timeframe', st.session_state.analysis_timeframe)} | Chart TF: {cache_meta.get('chart_timeframe') or getattr(analyzer_status, 'chart_timeframe', st.session_state.chart_timeframe)} | Analysis refresh: {'just ran' if analysis_ran else 'cached'} | Live bundles: {'ON' if cache_meta.get('live_bundle_mode') else 'OFF'}")

    tab_scanner, tab_lifecycle, tab_strategy, tab_signals, tab_trades, tab_backtest, tab_export = st.tabs(["Scanner", "Market State", "Strategy Lab", "Signal Inbox", "Trades", "Backtest / Replay", "LLM Export"])

    with tab_scanner:
        st.subheader("Latest scanner view")
        if scanner_rows:
            snapshot_df = pd.DataFrame(scanner_rows).sort_values(["final_score", "symbol"], ascending=[False, True])
            st.dataframe(prepare_display_df(snapshot_df, ["last_open_time"]), width="stretch", hide_index=True)
            detail_symbol = st.selectbox("Inspect symbol", options=snapshot_df["symbol"].tolist())
            chart_points = st.slider("Scanner chart candles", min_value=180, max_value=10000, value=max(MAX_CHART_POINTS, 1200), step=60)
            analysis = analysis_map[detail_symbol]
            runtime_df = storage.get_candles(detail_symbol, st.session_state.chart_timeframe, limit=max(chart_points, 1200))
            store_df = fetch_store_chart_history(detail_symbol, st.session_state.chart_timeframe, limit=chart_points)
            if not store_df.empty and len(store_df) >= chart_points:
                price_df = store_df.tail(chart_points).reset_index(drop=True)
            elif not store_df.empty:
                price_df = pd.concat([store_df, runtime_df], ignore_index=True).sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True).tail(chart_points)
            else:
                price_df = runtime_df.tail(chart_points).reset_index(drop=True)
            feature_frame = analysis.get("frame")
            if not isinstance(feature_frame, pd.DataFrame):
                feature_frame = pd.DataFrame(feature_frame) if isinstance(feature_frame, (list, dict)) else pd.DataFrame()
            chart_feature_frame = feature_frame
            try:
                if price_df is not None and not price_df.empty:
                    chart_feature_frame = enrich_features(price_df).frame
            except Exception:
                chart_feature_frame = feature_frame
            if chart_feature_frame is None or chart_feature_frame.empty:
                chart_feature_frame = pd.DataFrame({"open_time": price_df.tail(chart_points)["open_time"]})
            st.plotly_chart(build_chart(price_df.tail(chart_points), chart_feature_frame.tail(chart_points), detail_symbol), width="stretch")
            st.caption(f"Chart timeframe: {st.session_state.chart_timeframe} | Analysis timeframe: {st.session_state.analysis_timeframe} | Chart candles shown: {len(price_df.tail(chart_points))} | Warm-start history source: {'store + live merge' if not store_df.empty else 'live/runtime only'} | Store candles pulled: {len(store_df)}")
            c1, c2 = st.columns(2)
            with c1:
                st.write("All calculated indicators")
                feat_df = indicator_items_df(analysis["features"])
                st.dataframe(feat_df, width="stretch", hide_index=True)
            with c2:
                st.write("HTF context")
                st.json(analysis.get("htf_context", {}))
            st.write("Strategy opinions")
            st.dataframe(pd.DataFrame(analysis["strategies"]), width="stretch", hide_index=True)
        else:
            st.info("No scanner rows yet. Start the collector/analyzer and wait for the first closed-candle analysis cycle to complete.")

    with tab_lifecycle:
        st.subheader("Market State / Trend Lifecycle")
        st.caption("V28.4 scaffold: classifies the current market phase and maps it to strategy families. This is a soft evidence layer first, not a hard live gate.")
        if analysis_map:
            lifecycle_df = classify_analysis_map(analysis_map, analysis_tf=st.session_state.analysis_timeframe)
            if not lifecycle_df.empty:
                display_cols = [
                    "symbol",
                    "analysis_tf",
                    "lifecycle_state",
                    "trend_direction",
                    "confidence",
                    "entry_mode",
                    "exit_family",
                    "allowed_strategy_families",
                    "blocked_strategy_families",
                    "reason",
                ]
                st.dataframe(lifecycle_df[[c for c in display_cols if c in lifecycle_df.columns]], width="stretch", hide_index=True)
                selected_lifecycle_symbol = st.selectbox("Inspect lifecycle details", options=lifecycle_df["symbol"].tolist(), key="lifecycle_symbol_select")
                selected_row = lifecycle_df[lifecycle_df["symbol"] == selected_lifecycle_symbol].iloc[0].to_dict()
                st.write("Lifecycle detail")
                st.json(selected_row)
                with st.expander("How to use this"):
                    st.markdown("""
- **trend_entering / trend_pullback_entry** → prefer HTF Pullback, VWAP Reclaim, RSI Regime, Trend Following.
- **trend_running** → manage existing trend trades; wait for next pullback before fresh entries.
- **trend_extended_late** → avoid chasing; protect profit and watch exhaustion.
- **compression_building** → wait; prepare breakout/compression-release strategies.
- **breakout_attempt** → breakout/reclaim strategies can be considered after LTF confirmation.
- **range_chop** → range/reversion strategies only, preferably at range edges.
- **liquidity_sweep_reversal_risk / panic_volatility** → defensive mode; no-trade or reversal confirmation only.

This tab does not remove strategies. It helps decide which strategy family fits the current market state.
""")
            else:
                st.info("No lifecycle rows yet. Run scanner analysis first.")
        else:
            st.info("No analysis map available yet. Start collector/analyzer or run the scanner first.")

    with tab_strategy:
        st.subheader("Strategy library and automatic versioning")
        st.caption("Edit strategies as real runtime inputs, export them for GPT iteration, import them back, and keep version history with timestamps.")
        st.info("Legend: 🟢 impacts live logic | ⚪ context only / does not affect live scoring")
        with st.expander("Baby steps: edit an existing strategy, create a new one, and understand what changes live logic"):
            st.markdown("""
1. **Pick a strategy** from the dropdown. The latest version loads into the editor.
2. **Edit context-only fields** like Strategy name, Human thesis, Expected outcome, Indicator description, or Notes when you only want better documentation and cleaner AI iteration. These do not change live scoring by themselves.
3. **Edit live logic fields** when you want the scanner to behave differently: Runtime scoring template, Expected SL/TP ratio, Signal score threshold, Live indicator rules, and Runtime parameter knobs.
4. **Live indicator rules** change which conditions score LONG, SHORT, WATCH, or BOTH. Example: `ema_20 > ema_50` with weight 25 adds bullish score.
5. **Runtime parameter knobs** change weights and overlays used by the chosen template, for example `trend_weight`, `htf_weight`, or `rule_overlay_weight`.
6. **Save as new version** whenever you want to test a revised strategy. Versioning is automatic and timestamps are kept.
7. **Update a running slot** from the sidebar. Choose the version, turn on Analyse, and optionally turn on Suggest. Suggest depends on Analyse.
8. **Create a new strategy** at the bottom. Start with a template, then refine rules and thresholds.
9. **Export a strategy packet** to iterate with GPT/Claude, then import the revised JSON back into Strategy Lab.
10. **Trade behavior impact:** new signals and auto-paper trades are blocked while an older OPEN trade exists for the same symbol + strategy version + direction. Scanner scores still keep updating.
            """)
        library = storage.get_strategy_library()
        latest = storage.get_latest_strategy_versions()
        if latest.empty:
            st.warning("No strategies found.")
        else:
            options = {f"{row['strategy_name']} (latest v{int(row['version_no'])})": int(row["strategy_id"]) for _, row in latest.iterrows()}
            selected_label = st.selectbox("Edit strategy", options=list(options.keys()))
            strategy_id = options[selected_label]
            history = library[library["strategy_id"] == strategy_id].sort_values("version_no", ascending=False).reset_index(drop=True)
            base = history.iloc[0]
            prefix = f"editor_{strategy_id}"

            base_rule_params = safe_json_load(base.get("rule_params_json"), {})
            base_tp_cfg = strategy_tp_defaults(base_rule_params, base["expected_rr"])
            defaults = {
                f"{prefix}_name": base["strategy_name"],
                f"{prefix}_template": base["template_key"],
                f"{prefix}_thesis": base["human_thesis"],
                f"{prefix}_expected": base["expected_outcome"],
                f"{prefix}_indicator_desc": base["indicator_description"],
                f"{prefix}_indicators": safe_json_load(base.get("indicators_json"), []),
                f"{prefix}_custom_indicators": "",
                f"{prefix}_indicator_rules": clean_rule_rows(safe_json_load(base.get("indicator_rules_json"), [])),
                f"{prefix}_rule_param_rows": param_rows_to_df(base_rule_params).to_dict(orient="records"),
                f"{prefix}_rule_params": "{}",
                f"{prefix}_rr": base["expected_rr"],
                f"{prefix}_tp_mode": base_rule_params.get("tp_mode", base_tp_cfg["tp_mode"]),
                f"{prefix}_tp_count": int(base_rule_params.get("tp_count") or base_tp_cfg["tp_count"]),
                f"{prefix}_tp_late_ratio": float(base_rule_params.get("tp_late_trigger_ratio") or base_tp_cfg["tp_late_trigger_ratio"]),
                f"{prefix}_threshold": float(base["score_threshold"]),
                f"{prefix}_notes": base["notes"],
            }
            if st.session_state.get("current_edit_strategy_id") != strategy_id:
                for key, value in defaults.items():
                    st.session_state[key] = value
                st.session_state["current_edit_strategy_id"] = strategy_id
            else:
                for key, value in defaults.items():
                    st.session_state.setdefault(key, value)

            packet = build_strategy_packet(base)
            packet_json = json.dumps(packet, indent=2, ensure_ascii=False)
            top_left, top_mid, top_right = st.columns([1.2, 1.4, 1.4])
            with top_left:
                st.metric("Latest version", f"v{int(base['version_no'])}")
                st.caption(f"Created at: {base['created_at']}")
            with top_mid:
                st.download_button(
                    "Download full strategy packet (.json)",
                    data=packet_json,
                    file_name=f"strategy_{base['strategy_name'].lower().replace(' ', '_')}_v{int(base['version_no'])}.json",
                    mime="application/json",
                    width="stretch",
                )
            with top_right:
                imported_file = st.file_uploader("Import strategy packet (.json)", type=["json"], key=f"{prefix}_import_file")
                if imported_file is not None:
                    try:
                        imported_packet = json.loads(imported_file.getvalue().decode("utf-8"))
                        imported_payload = extract_import_payload(imported_packet)
                        if imported_payload:
                            if st.button("Load import into editor", key=f"{prefix}_load_import", width="stretch"):
                                load_payload_into_editor(prefix, imported_payload)
                                st.success("Imported packet loaded into editor")
                                st.rerun()
                            if st.button("Create brand-new strategy from import", key=f"{prefix}_create_import", width="stretch"):
                                storage.create_strategy(imported_payload["strategy_name"], imported_payload["template_key"], imported_payload)
                                st.success("Imported packet created as a new strategy")
                                st.rerun()
                        else:
                            st.warning("Could not find a valid strategy payload in that JSON file.")
                    except Exception as exc:
                        st.warning(f"Could not parse imported packet: {exc}")

            st.text_input("Strategy name ⚪ context only", key=f"{prefix}_name", help="Human label only; does not change scoring by itself.")
            template_labels = {label: key for key, label in TEMPLATE_OPTIONS}
            template_display = [label for _, label in TEMPLATE_OPTIONS]
            current_template_label = next(label for key, label in TEMPLATE_OPTIONS if key == st.session_state[f"{prefix}_template"])
            chosen_template = st.selectbox("Runtime scoring template 🟢 impacts live logic", options=template_display, index=template_display.index(current_template_label), help="This chooses which scoring engine runs for the strategy.")
            st.session_state[f"{prefix}_template"] = template_labels[chosen_template]
            st.text_area("Human thesis ⚪ context only", key=f"{prefix}_thesis", height=100)
            st.text_area("Expected outcome ⚪ context only", key=f"{prefix}_expected", height=80)
            st.text_area("Indicator description / reasoning ⚪ context only", key=f"{prefix}_indicator_desc", height=120)
            st.multiselect("Indicators used ⚪ context + export context", options=INDICATOR_CATALOG, key=f"{prefix}_indicators", help="This documents the strategy and is included in exports. It only affects live logic when those indicators are also used in rules or template params.")
            st.text_input(
                "Extra indicators not yet in catalog ⚪ context unless referenced in rules",
                key=f"{prefix}_custom_indicators",
                help="You can also reference flattened HTF fields like htf_15m_rsi_14, htf_1h_trend, htf_4h_adx_14.",
            )
            rr_col, tp_mode_col, tp_count_col, th_col = st.columns(4)
            with rr_col:
                st.text_input("Expected SL/TP ratio 🟢 impacts auto-paper trade levels", key=f"{prefix}_rr", help="Example: 1:4 means 1% risk from entry and 4% final target from entry.")
            with tp_mode_col:
                st.selectbox("TP mode 🟢", options=["structure_atr", "equal_rr", "fibo"], key=f"{prefix}_tp_mode", help="Default is structure_atr: hybrid ATR/structure checkpoints with final target anchored by the strategy RR.")
            with tp_count_col:
                st.number_input("TP count 🟢", min_value=2, max_value=8, step=1, key=f"{prefix}_tp_count")
            with th_col:
                st.number_input("Signal score threshold 🟢 impacts live logic", min_value=0.0, max_value=100.0, step=1.0, key=f"{prefix}_threshold")
            st.slider("Late TP trigger ratio 🟢", min_value=0.5, max_value=0.95, step=0.05, key=f"{prefix}_tp_late_ratio", help="After TP1 SL moves to entry. After this late milestone ratio is reached, SL moves to TP1.")

            st.markdown("#### Live indicator rules 🟢")
            st.caption("These rules directly affect live scoring. Use another feature name in the value column if you want comparisons like ema_20 > ema_50.")
            rules_df = st.data_editor(
                rule_rows_to_df(st.session_state.get(f"{prefix}_indicator_rules", [])),
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                key=f"{prefix}_rules_editor",
                column_config={
                    "enabled": st.column_config.CheckboxColumn("On"),
                    "indicator": st.column_config.SelectboxColumn("Indicator", options=INDICATOR_CATALOG),
                    "operator": st.column_config.SelectboxColumn("Operator", options=[">", ">=", "<", "<=", "==", "!=", "between", "contains", "true", "false"]),
                    "value": st.column_config.TextColumn("Value / other indicator"),
                    "value_2": st.column_config.TextColumn("Second value"),
                    "weight": st.column_config.NumberColumn("Weight", min_value=0.0, max_value=100.0, step=1.0),
                    "bias": st.column_config.SelectboxColumn("Bias", options=["LONG", "SHORT", "WATCH", "BOTH"]),
                },
            )
            st.session_state[f"{prefix}_indicator_rules"] = clean_rule_rows(rules_df)

            st.markdown("#### Runtime parameter knobs 🟢")
            st.caption("These values directly affect weights, thresholds, overlays, or other runtime behavior. All values are edited as text, then converted safely on save.")
            param_seed = pd.DataFrame(st.session_state.get(f"{prefix}_rule_param_rows", [])) if f"{prefix}_rule_param_rows" in st.session_state else param_rows_to_df(safe_json_load(base.get("rule_params_json"), {}))
            if not param_seed.empty:
                param_seed["param"] = param_seed["param"].astype(str)
                param_seed["value"] = param_seed["value"].astype(str)
            param_df = st.data_editor(
                param_seed,
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                key=f"{prefix}_params_editor",
                column_config={
                    "param": st.column_config.TextColumn("Param"),
                    "value": st.column_config.TextColumn("Value"),
                },
            )
            param_df = param_df.astype({"param": "string", "value": "string"}) if not param_df.empty else param_df
            st.session_state[f"{prefix}_rule_param_rows"] = param_df.fillna("").to_dict(orient="records")
            st.text_area("Advanced JSON overrides 🟢 optional", key=f"{prefix}_rule_params", height=120, help="Optional extra runtime parameters in JSON. These override matching values from the parameter table.")
            st.text_area("Notes ⚪ context only", key=f"{prefix}_notes", height=80)

            current_payload = strategy_editor_payload(prefix)
            saved_payload = {
                "strategy_name": base["strategy_name"],
                "template_key": base["template_key"],
                "human_thesis": base["human_thesis"],
                "expected_outcome": base["expected_outcome"],
                "indicator_description": base["indicator_description"],
                "indicators": safe_json_load(base["indicators_json"], []),
                "indicator_rules": clean_rule_rows(safe_json_load(base.get("indicator_rules_json"), [])),
                "rule_params": safe_json_load(base["rule_params_json"], {}),
                "expected_rr": base["expected_rr"],
                "tp_mode": base_rule_params.get("tp_mode", base_tp_cfg["tp_mode"]),
                "tp_count": int(base_rule_params.get("tp_count") or base_tp_cfg["tp_count"]),
                "tp_late_trigger_ratio": float(base_rule_params.get("tp_late_trigger_ratio") or base_tp_cfg["tp_late_trigger_ratio"]),
                "score_threshold": float(base["score_threshold"]),
                "notes": base["notes"],
            }
            dirty = not payload_equal(current_payload, saved_payload)
            if dirty:
                st.warning("Strategies edited – not saved yet")
            else:
                st.success("Strategies saved")
            col_save, col_hint = st.columns([1, 2])
            with col_save:
                if st.button("Save as new version", type="primary"):
                    version_id = storage.save_strategy_version(strategy_id, current_payload)
                    st.session_state.last_strategy_save = f"Saved new version: {current_payload['strategy_name']} -> version id {version_id}"
                    st.success(st.session_state.last_strategy_save)
                    st.cache_data.clear()
                    st.rerun()
            with col_hint:
                if st.session_state[f"{prefix}_template"] == "rule_builder":
                    st.info("Rule Builder template uses the live rules table directly. Other templates combine built-in logic with your rule overlay.")

            st.write("Version history")
            hist_view = history[["version_no", "created_at", "expected_rr", "score_threshold", "human_thesis", "expected_outcome"]]
            st.dataframe(prepare_display_df(hist_view, ["created_at"]), width="stretch", hide_index=True)

            st.divider()
            st.subheader("Strategy field impact guide")
            field_df = pd.DataFrame(FIELD_IMPACT_GUIDE)
            field_df["logic_impact"] = field_df["impacts_live_logic"].map(_impact_label)
            st.dataframe(field_df[["field", "logic_impact", "notes"]], width="stretch", hide_index=True)

            st.subheader("Available KPI / indicator library")
            category_filter = st.selectbox("Indicator library category", options=["All"] + sorted({row["category"] for row in INDICATOR_LIBRARY}), key=f"{prefix}_library_category")
            library_df = pd.DataFrame(INDICATOR_LIBRARY)
            if category_filter != "All":
                library_df = library_df[library_df["category"] == category_filter]
            library_df = library_df.copy()
            library_df["data_type"] = library_df["indicator"].map(indicator_data_type)
            st.dataframe(library_df[["indicator", "data_type", "category", "what_it_is", "formula"]], width="stretch", hide_index=True)
            with st.expander("How to describe a new indicator for AI iteration"):
                st.code(json.dumps({
                    "indicator": "new_indicator_name",
                    "category": "Momentum | Trend | Volatility | Order book | Derivatives | HTF context | SMC proxy",
                    "data_type": "number | boolean | label/string",
                    "formula": "Write the exact calculation formula or detection logic",
                    "source_fields": ["close", "high", "low", "volume"],
                    "timeframe_scope": "main_tf | htf_15m | htf_1h | htf_4h",
                    "rule_example": {"indicator": "new_indicator_name", "operator": ">=", "value": 1.5, "weight": 20, "bias": "LONG"},
                }, indent=2, ensure_ascii=False), language="json")
                st.caption("HTF field syntax example: htf_1h_rsi_14 or htf_4h_trend. To compare indicators directly, put the other indicator name in the value field, for example ema_20 > ema_50.")
            with st.expander("Suggested future KPIs / indicators"):
                st.dataframe(pd.DataFrame(INDICATOR_SUGGESTIONS), width="stretch", hide_index=True)

            st.divider()
            st.subheader("Create a new strategy")
            with st.form("new_strategy_form"):
                new_name = st.text_input("New strategy name")
                new_template = st.selectbox("New strategy template", options=[label for _, label in TEMPLATE_OPTIONS])
                submitted = st.form_submit_button("Create strategy")
                if submitted and new_name.strip():
                    template_key = dict((label, key) for key, label in TEMPLATE_OPTIONS)[new_template]
                    new_payload = {
                        "strategy_name": new_name.strip(),
                        "template_key": template_key,
                        "human_thesis": "",
                        "expected_outcome": "",
                        "indicator_description": "",
                        "indicators": [],
                        "indicator_rules": [],
                        "rule_params": {"score_threshold": 70, "rule_overlay_weight": 0.35},
                        "expected_rr": "1:3",
                        "score_threshold": 70,
                        "notes": "",
                    }
                    storage.create_strategy(new_name.strip(), template_key, new_payload)
                    st.success(f"Created {new_name.strip()}")
                    st.rerun()

    with tab_signals:
        st.subheader("Signal inbox")
        signals = storage.get_signal_events(limit=200)
        if signals.empty:
            st.info("No signals logged yet.")
        else:
            summary_cols = ["created_at", "symbol", "strategy_name", "version_no", "bias", "score", "interval"]
            signals_view = prepare_display_df(signals[summary_cols], ["created_at"])
            st.dataframe(signals_view, width="stretch", hide_index=True)
            for _, signal in signals.head(30).iterrows():
                title = f"{signal['symbol']} | {signal['strategy_name']} v{int(signal['version_no'])} | {signal['bias']} | score {signal['score']}"
                with st.expander(title):
                    st.code(signal["setup_summary"], language="markdown")
                    st.json(safe_json_load(signal["htf_context_json"], {}))
                    if st.button(f"Create paper trade for signal {int(signal['signal_id'])}"):
                        if storage.has_open_trade_conflict(str(signal["symbol"]), int(signal["version_id"]), str(signal["bias"])):
                            st.warning("An OPEN trade already exists for this symbol + strategy version + direction. Wait until it closes.")
                        else:
                            strategy_snapshot = safe_json_load(signal["strategy_snapshot_json"], {})
                            levels = build_trade_levels(safe_json_load(signal["feature_json"], {}), signal["bias"], expected_rr=strategy_snapshot.get("expected_rr"), rule_params=strategy_snapshot.get("rule_params", {}))
                            if levels:
                                trade_id = storage.create_or_update_paper_trade_from_signal(
                                {
                                    "signal_id": int(signal["signal_id"]),
                                    "symbol": signal["symbol"],
                                    "interval": signal["interval"],
                                    "slot_id": signal["slot_id"],
                                    "strategy_id": signal["strategy_id"],
                                    "version_id": signal["version_id"],
                                    "strategy_name": signal["strategy_name"],
                                    "version_no": signal["version_no"],
                                    "side": signal["bias"],
                                    "entry_price": levels["entry_price"],
                                    "stop_loss": levels["stop_loss"],
                                    "take_profit": levels["take_profit"],
                                    "expected_rr": levels["expected_rr"],
                                    "risk_pct": levels["risk_pct"],
                                    "reward_pct": levels["reward_pct"],
                                    "confidence": signal["score"],
                                    "decision": "SKIPPED",
                                    "user_comment": "",
                                    "setup_summary": signal["setup_summary"],
                                    "trade_summary": f"Created manually from signal inbox. TP mode: {levels.get('tp_mode')}. TP ladder: {levels.get('tp_levels')}",
                                    "feature_json": safe_json_load(signal["feature_json"], {}),
                                    "htf_context_json": safe_json_load(signal["htf_context_json"], {}),
                                    "recent_bars_json": safe_json_load(signal["recent_bars_json"], []),
                                    "strategy_snapshot_json": strategy_snapshot,
                                }
                            )
                            st.success(f"Paper trade {trade_id} ready")

    with tab_trades:
        st.subheader("Paper trades")
        with st.expander("Create manual trade"):
            latest_versions_for_manual = storage.get_latest_strategy_versions()
            manual_strategy_options = ["— no linked strategy —"] + [f"{r['strategy_name']} v{int(r['version_no'])}" for _, r in latest_versions_for_manual.iterrows()]
            with st.form("manual_trade_form"):
                mc1, mc2, mc3 = st.columns(3)
                with mc1:
                    manual_symbol = st.selectbox("Symbol", options=st.session_state.selected_symbols or DEFAULT_SELECTED_SYMBOLS, key="manual_symbol")
                    manual_side = st.selectbox("Side", options=["LONG", "SHORT"], key="manual_side")
                    manual_interval = st.selectbox("Timeframe", options=CHART_TIMEFRAME_OPTIONS, index=CHART_TIMEFRAME_OPTIONS.index(st.session_state.chart_timeframe if st.session_state.chart_timeframe in CHART_TIMEFRAME_OPTIONS else DEFAULT_CHART_TIMEFRAME), key="manual_interval")
                with mc2:
                    manual_strategy = st.selectbox("Linked strategy/version", options=manual_strategy_options, key="manual_strategy")
                    manual_entry = st.text_input("Entry", value="", key="manual_entry")
                    manual_sl = st.text_input("Stop loss", value="", key="manual_sl")
                with mc3:
                    manual_tp = st.text_input("Take profit", value="", key="manual_tp")
                    manual_rr = st.text_input("Expected RR", value="1:3", key="manual_rr")
                    manual_decision = st.selectbox("Decision", options=["TOOK", "WATCHING", "MODIFIED", "SKIPPED", "REJECTED"], key="manual_decision")
                manual_comment = st.text_area("Comment", value="", key="manual_comment")
                manual_submit = st.form_submit_button("Create manual trade")
                if manual_submit:
                    try:
                        entry_val = parse_price_text(manual_entry)
                        sl_val = parse_price_text(manual_sl)
                        tp_val = parse_price_text(manual_tp)
                    except Exception:
                        st.warning("Please enter valid numeric prices for Entry, Stop loss, and Take profit.")
                    else:
                        risk_pct, reward_pct, rr_norm = parse_rr(manual_rr)
                        selected_row = None
                        if manual_strategy != "— no linked strategy —":
                            label_map = {f"{r['strategy_name']} v{int(r['version_no'])}": r for _, r in latest_versions_for_manual.iterrows()}
                            selected_row = label_map.get(manual_strategy)
                        analysis = analysis_map.get(manual_symbol)
                        feature_json = analysis.get("features", {}) if analysis else {}
                        htf_json = analysis.get("htf_context", {}) if analysis else {}
                        recent_bars = []
                        if analysis is not None:
                            bars_df = storage.get_candles(manual_symbol, manual_interval, limit=20)
                            if not bars_df.empty:
                                recent_bars = bars_df.tail(20)[["open_time", "open", "high", "low", "close", "volume"]].to_dict(orient="records")
                        strategy_snapshot = build_strategy_packet(selected_row)["strategy"] if selected_row is not None else {"strategy_name": "Manual Trade", "template_key": "manual", "notes": "Manual trade without linked signal."}
                        confidence = float(analysis.get("summary", {}).get("final_score", 0.0)) if analysis else 0.0
                        setup_summary = f"Manual trade journal entry for {manual_symbol} on {manual_interval}. Linked strategy: {strategy_snapshot.get('strategy_name', 'Manual Trade')}. Current context attached if available."
                        trade_summary = f"Manual trade created. Decision: {manual_decision}. Entry: {manual_entry}. SL: {manual_sl}. TP: {manual_tp}. RR: {rr_norm}. Comment: {manual_comment}"
                        trade_id = storage.create_manual_trade({
                            "symbol": manual_symbol,
                            "interval": manual_interval,
                            "slot_id": None,
                            "strategy_id": int(selected_row['strategy_id']) if selected_row is not None else None,
                            "version_id": int(selected_row['version_id']) if selected_row is not None else None,
                            "strategy_name": strategy_snapshot.get("strategy_name", "Manual Trade"),
                            "version_no": int(selected_row['version_no']) if selected_row is not None else 0,
                            "side": manual_side,
                            "entry_price": entry_val,
                            "stop_loss": sl_val,
                            "take_profit": tp_val,
                            "expected_rr": rr_norm,
                            "risk_pct": risk_pct,
                            "reward_pct": reward_pct,
                            "confidence": confidence,
                            "decision": manual_decision,
                            "user_comment": manual_comment,
                            "setup_summary": setup_summary,
                            "trade_summary": trade_summary,
                            "feature_json": feature_json,
                            "htf_context_json": htf_json,
                            "recent_bars_json": recent_bars,
                            "strategy_snapshot_json": strategy_snapshot,
                        })
                        st.success(f"Manual trade {trade_id} created")
                        st.rerun()
        trades = storage.get_paper_trades(limit=500)
        if trades.empty:
            st.info("No paper trades yet.")
        else:
            trades = trades.copy()
            trades["start_score"] = trades["confidence"]
            trades["current_score"] = trades["live_score"]
            trades["current_bias"] = trades["live_bias"]
            def _trade_last_price(row):
                if str(row.get("status")) == "CLOSED" and pd.notna(row.get("close_price")):
                    return row.get("close_price")
                state = score_map.get((str(row.get("symbol")), int(row.get("version_id")))) if pd.notna(row.get("version_id")) else None
                if state and state.get("last_price") is not None:
                    return state.get("last_price")
                feature_payload = safe_json_load(row.get("feature_json"), {})
                return feature_payload.get("close")
            trades["last_price"] = trades.apply(_trade_last_price, axis=1)
            st.markdown("#### Filters")
            f1, f2, f3, f4 = st.columns(4)
            with f1:
                trade_id_filter = st.text_input("Trade ID contains", value="")
                symbol_filter = st.multiselect("Symbol", options=sorted(trades["symbol"].dropna().unique().tolist()))
                side_filter = st.multiselect("Side", options=sorted(trades["side"].dropna().unique().tolist()))
            with f2:
                created_from = st.text_input("Created from (YYYY-MM-DD)", value="")
                strategy_filter = st.multiselect("Strategy name", options=sorted(trades["strategy_name"].dropna().unique().tolist()))
                decision_filter = st.multiselect("Decision", options=sorted(trades["decision"].dropna().unique().tolist()))
            with f3:
                created_to = st.text_input("Created to (YYYY-MM-DD)", value="")
                version_filter = st.multiselect("Version", options=sorted([int(x) for x in trades["version_no"].dropna().unique().tolist()]))
                status_filter = st.multiselect("Status", options=sorted(trades["status"].dropna().unique().tolist()))
            with f4:
                pnl_min = st.number_input("Min pnl_pct", value=-100.0, step=1.0)
                pnl_max = st.number_input("Max pnl_pct", value=100.0, step=1.0)
                outcome_filter = st.multiselect("Outcome", options=sorted(trades["outcome_label"].fillna("").unique().tolist()))

            trades_view = trades.copy()
            if trade_id_filter:
                trades_view = trades_view[trades_view["trade_id"].astype(str).str.contains(trade_id_filter, na=False)]
            if symbol_filter:
                trades_view = trades_view[trades_view["symbol"].isin(symbol_filter)]
            if strategy_filter:
                trades_view = trades_view[trades_view["strategy_name"].isin(strategy_filter)]
            if version_filter:
                trades_view = trades_view[trades_view["version_no"].astype(int).isin(version_filter)]
            if side_filter:
                trades_view = trades_view[trades_view["side"].isin(side_filter)]
            if decision_filter:
                trades_view = trades_view[trades_view["decision"].isin(decision_filter)]
            if status_filter:
                trades_view = trades_view[trades_view["status"].isin(status_filter)]
            if outcome_filter:
                trades_view = trades_view[trades_view["outcome_label"].fillna("").isin(outcome_filter)]
            trades_view = trades_view[(trades_view["pnl_pct"].fillna(0.0) >= pnl_min) & (trades_view["pnl_pct"].fillna(0.0) <= pnl_max)]
            if created_from.strip():
                created_from_date = pd.to_datetime(created_from, errors="coerce")
                if pd.notna(created_from_date):
                    trades_view = trades_view[pd.to_datetime(trades_view["created_at"], errors="coerce") >= created_from_date]
            if created_to.strip():
                created_to_date = pd.to_datetime(created_to, errors="coerce")
                if pd.notna(created_to_date):
                    trades_view = trades_view[pd.to_datetime(trades_view["created_at"], errors="coerce") <= created_to_date + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)]

            trades_view = trades_view.copy()
            trades_view["tp_progress"] = trades_view.apply(format_tp_progress, axis=1)
            trade_cols = ["trade_id", "created_at", "symbol", "interval", "strategy_name", "version_no", "side", "decision", "status", "entry_price", "stop_loss_initial", "stop_loss_current", "tp1_price", "take_profit", "tp_progress", "sl_state", "close_reason", "last_price", "outcome_label", "start_score", "current_score", "pnl_pct"]
            st.dataframe(prepare_display_df(trades_view[trade_cols], ["created_at"]), width="stretch", hide_index=True)
            for _, trade in trades_view.head(80).iterrows():
                title = f"Trade {int(trade['trade_id'])} | {trade['symbol']} | {trade['interval']} | {trade['strategy_name']} v{int(trade['version_no'])} | {trade['side']} | {trade['decision']} | {trade['status']}"
                with st.expander(title):
                    current_score_value = trade["live_score"]
                    current_bias_value = trade["live_bias"]
                    if trade["status"] == "OPEN" and pd.notna(trade.get("version_id")):
                        current_state = score_map.get((str(trade["symbol"]), int(trade["version_id"])), {})
                        current_score_value = current_state.get("score", trade.get("live_score"))
                        current_bias_value = current_state.get("bias", trade.get("live_bias"))
                    packet = build_trade_packet(trade, current_score=current_score_value, current_bias=current_bias_value)
                    packet_json = json.dumps(packet, indent=2, ensure_ascii=False)
                    k1, k2 = st.columns(2)
                    with k1:
                        decision = st.selectbox("Decision", options=["SKIPPED", "TOOK", "WATCHING", "MODIFIED", "REJECTED"], index=["SKIPPED", "TOOK", "WATCHING", "MODIFIED", "REJECTED"].index(trade["decision"] or "SKIPPED"), key=f"decision_{int(trade['trade_id'])}")
                        user_comment = st.text_area("Comment", value=trade["user_comment"] or "", key=f"comment_{int(trade['trade_id'])}")
                        entry_price = st.text_input("Entry", value=price_to_text(trade["entry_price"]), key=f"entry_{int(trade['trade_id'])}")
                        stop_loss = st.text_input("Stop loss", value=price_to_text(trade["stop_loss"]), key=f"sl_{int(trade['trade_id'])}")
                        take_profit = st.text_input("Take profit", value=price_to_text(trade["take_profit"]), key=f"tp_{int(trade['trade_id'])}")
                        expected_rr = st.text_input("Expected RR", value=trade["expected_rr"] or "1:3", key=f"rr_{int(trade['trade_id'])}")
                        risk_pct, reward_pct, rr_norm = parse_rr(expected_rr)
                        last_price_value = trade.get("last_price")
                        st.caption(f"Start score: {trade['start_score']} | Current score: {current_score_value} | Current bias: {current_bias_value} | Last price: {price_to_text(last_price_value)} | TP progress: {format_tp_progress(trade)} | SL state: {trade.get('sl_state')}")
                        tp_hits = [f"TP{i}@{trade.get(f'tp{i}_hit_at')}" for i in range(1, 5) if trade.get(f'tp{i}_hit_at')]
                        st.caption(f"TP mode: {trade.get('tp_mode')} | TP1: {price_to_text(trade.get('tp1_price'))} | TP final: {price_to_text(trade.get('take_profit'))} | Hits: {', '.join(tp_hits) if tp_hits else 'none'}")
                        if st.button("Save trade review", key=f"save_trade_{int(trade['trade_id'])}"):
                            storage.update_trade_user_fields(int(trade["trade_id"]), decision, user_comment, parse_price_text(entry_price, trade["entry_price"]), parse_price_text(stop_loss, trade["stop_loss"]), parse_price_text(take_profit, trade["take_profit"]), rr_norm, risk_pct, reward_pct)
                            st.success("Trade updated")
                            st.rerun()
                        if trade["status"] == "OPEN":
                            manual_close_price = st.text_input("Manual close price", value=price_to_text(trade.get("close_price") or trade["entry_price"]), key=f"manual_close_{int(trade['trade_id'])}")
                            if st.button("Close manually", key=f"close_trade_{int(trade['trade_id'])}"):
                                storage.manual_close_trade(int(trade["trade_id"]), parse_price_text(manual_close_price, trade["entry_price"]))
                                st.success("Trade closed manually")
                                st.rerun()
                    with k2:
                        st.download_button("Download trade packet", data=packet_json, file_name=f"trade_packet_{int(trade['trade_id'])}.json", mime="application/json", width="stretch", key=f"dl_trade_{int(trade['trade_id'])}")
                        st.write("Strategy reasoning snapshot")
                        st.json(safe_json_load(trade["strategy_snapshot_json"], {}))
                    st.write("All calculated indicators")
                    feature_payload = safe_json_load(trade["feature_json"], {})
                    st.dataframe(indicator_items_df(feature_payload), width="stretch", hide_index=True)
                    st.write("HTF context")
                    st.json(safe_json_load(trade["htf_context_json"], {}))
                    bars = safe_json_load(trade["recent_bars_json"], [])
                    if bars:
                        bars_df = pd.DataFrame(bars)
                        st.write("Last price bars snapshot")
                        st.dataframe(prepare_display_df(bars_df, ["open_time", "close_time"]), width="stretch", hide_index=True)
                    st.write("Setup summary")
                    st.code(trade["setup_summary"] or build_setup_summary(trade["symbol"], trade["interval"], safe_json_load(trade["strategy_snapshot_json"], {}), {"features": feature_payload, "htf_context": safe_json_load(trade["htf_context_json"], {}), "summary": {"regime": feature_payload.get("local_trend")}}), language="markdown")
                    st.write("Trade summary")
                    st.code(trade["trade_summary"] or build_trade_summary(trade), language="markdown")
                    st.write("Outcome summary")
                    st.code(trade["outcome_summary"] or build_outcome_summary(trade), language="markdown")

    with tab_backtest:
        render_backtest_tab(storage)

    with tab_export:
        st.subheader("LLM export packets")
        trades = storage.get_paper_trades(limit=200)
        strategies = storage.get_latest_strategy_versions()
        left, right = st.columns(2)
        with left:
            if not trades.empty:
                trade_option = st.selectbox("Trade packet", options=trades["trade_id"].tolist())
                trade_row = trades[trades["trade_id"] == trade_option].iloc[0]
                current_score_value = trade_row.get("live_score")
                current_bias_value = trade_row.get("live_bias")
                if trade_row["status"] == "OPEN" and pd.notna(trade_row.get("version_id")):
                    current_state = score_map.get((str(trade_row["symbol"]), int(trade_row["version_id"])), {})
                    current_score_value = current_state.get("score", trade_row.get("live_score"))
                    current_bias_value = current_state.get("bias", trade_row.get("live_bias"))
                packet = build_trade_packet(trade_row, current_score=current_score_value, current_bias=current_bias_value)
                packet_json = json.dumps(packet, indent=2, ensure_ascii=False)
                st.download_button("Download trade packet", data=packet_json, file_name=f"trade_packet_{int(trade_row['trade_id'])}.json", mime="application/json", width="stretch")
                st.code(packet_json, language="json")
            else:
                st.info("No trades to export yet.")
        with right:
            if not strategies.empty:
                strategy_labels = [f"{r['strategy_name']} v{int(r['version_no'])}" for _, r in strategies.iterrows()]
                strategy_option = st.selectbox("Strategy packet", options=strategy_labels)
                row = strategies.iloc[strategy_labels.index(strategy_option)]
                packet = build_strategy_packet(row)
                packet_json = json.dumps(packet, indent=2, ensure_ascii=False)
                st.download_button("Download strategy packet", data=packet_json, file_name=f"strategy_packet_{row['strategy_name'].lower().replace(' ', '_')}_v{int(row['version_no'])}.json", mime="application/json", width="stretch")
                st.code(packet_json, language="json")
            else:
                st.info("No strategies to export yet.")


if __name__ == "__main__":
    main()
