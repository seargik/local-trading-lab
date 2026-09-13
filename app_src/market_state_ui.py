from __future__ import annotations

import pandas as pd
import streamlit as st

from .history_manager import DEFAULT_HISTORY_SYMBOLS
from .market_state_v2816 import identify_analysis_map, identify_market_state, load_market_state_policy
from .settings import LAB_DB_PATH
from .storage import Storage


def _analysis_age_text(meta: dict) -> str:
    raw = meta.get("last_run_at") or meta.get("updated_at") or ""
    if not raw:
        return "unknown"
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        return str(raw)
    age = pd.Timestamp.now(tz="UTC") - ts
    minutes = max(0, int(age.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def render_market_state_identifier() -> None:
    st.title("Market State")
    st.caption("V28.16 · current trend/state identifier + explainable adaptive router")
    st.info(
        "This page identifies the current market state and shows what the adaptive router would prefer. "
        "It is still a research/display layer and does not gate paper or live execution."
    )

    storage = Storage(LAB_DB_PATH)
    cache = storage.read_analysis_cache()
    analysis_map = cache.get("analysis_map") or {}
    meta = cache.get("meta") or {}
    policy = load_market_state_policy()

    available = list(dict.fromkeys([*DEFAULT_HISTORY_SYMBOLS, *[str(x).upper() for x in analysis_map.keys()]]))
    default_selected = [s for s in DEFAULT_HISTORY_SYMBOLS if s in available]
    selected = st.multiselect("Pairs", available, default=default_selected)
    if not selected:
        st.warning("Select at least one pair.")
        st.stop()

    analyzed_map = {s: analysis_map[s] for s in selected if s in analysis_map}
    analysis_tf = str(meta.get("analysis_timeframe") or "")
    state_df = identify_analysis_map(analyzed_map, analysis_timeframe=analysis_tf, policy=policy)

    rows = []
    state_by_symbol = {str(r.get("symbol") or "").upper(): r for r in state_df.to_dict(orient="records")} if not state_df.empty else {}
    for symbol in selected:
        row = state_by_symbol.get(symbol)
        if row:
            rows.append(row)
        else:
            rows.append(
                {
                    "symbol": symbol,
                    "state_label": "Not Analyzed",
                    "direction": "—",
                    "trend_strength": "—",
                    "volatility_state": "—",
                    "structure_state": "—",
                    "htf_alignment": "—",
                    "confidence": 0.0,
                    "preferred_strategy_family": "—",
                    "router_action": "RUN_ANALYSIS",
                    "route_direction": "—",
                    "risk_multiplier": 0.0,
                    "lifecycle_state": "not_analyzed",
                }
            )
    dashboard = pd.DataFrame(rows)

    analyzed_count = int((dashboard["lifecycle_state"] != "not_analyzed").sum()) if not dashboard.empty else 0
    candidate_count = int((dashboard["router_action"] == "TRADE_CANDIDATE").sum()) if not dashboard.empty else 0
    wait_count = int(dashboard["router_action"].astype(str).str.startswith("WAIT").sum()) if not dashboard.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pairs", len(selected))
    c2.metric("Analyzed", analyzed_count)
    c3.metric("Trade candidates", candidate_count)
    c4.metric("Waiting", wait_count)
    st.caption(f"Latest analysis cache: {_analysis_age_text(meta)} · timeframe: {analysis_tf or 'unknown'}")

    display_cols = [
        "symbol",
        "state_label",
        "direction",
        "trend_strength",
        "volatility_state",
        "structure_state",
        "htf_alignment",
        "confidence",
        "preferred_strategy_family",
        "router_action",
        "route_direction",
        "risk_multiplier",
    ]
    st.subheader("Current market state")
    st.dataframe(dashboard[display_cols], width="stretch", hide_index=True)

    analyzed_symbols = [s for s in selected if s in analyzed_map]
    if not analyzed_symbols:
        st.warning("No selected pair has a current analysis payload. Run the Runtime Cycle / scanner analysis first.")
        with st.expander("Router policy"):
            st.json(policy)
        st.stop()

    st.subheader("Pair detail")
    detail_symbol = st.selectbox("Inspect pair", analyzed_symbols)
    result = identify_market_state(analyzed_map[detail_symbol], symbol=detail_symbol, analysis_timeframe=analysis_tf, policy=policy)

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("State", result.state_label)
    d2.metric("Direction", result.direction)
    d3.metric("Confidence", f"{result.confidence:.0f}%")
    d4.metric("Risk", f"{result.risk_multiplier:.2f}x")

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Trend strength", result.trend_strength)
    r2.metric("Volatility", result.volatility_state)
    r3.metric("Structure", result.structure_state)
    r4.metric("HTF", result.htf_alignment)

    st.write("**Router action:**", result.router_action)
    st.write("**Preferred family:**", result.preferred_strategy_family)
    st.write("**Route direction:**", result.route_direction)
    st.write("**Entry mode:**", result.entry_mode)
    st.write("**Exit family:**", result.exit_family)

    st.write("**Why the system classified it this way:**")
    for reason in result.reason:
        st.write(f"- {reason}")

    with st.expander("State metrics"):
        st.json(result.metrics)
    with st.expander("Router policy"):
        st.json(policy)
