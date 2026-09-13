from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from .market_state_replay_v2817 import load_replay_policy, run_market_state_replay, save_market_state_replay
from .research_command_center_v29 import CORE_RESEARCH_SYMBOLS
from .settings import OHLCV_STORE_ROOT

ALL_SYMBOLS = CORE_RESEARCH_SYMBOLS + ["LTCUSDT", "BNBUSDT", "UNIUSDT", "AAVEUSDT", "XRPUSDT", "TRXUSDT"]


def render_market_state_replay() -> None:
    st.title("Historical Market State Replay")
    st.caption("V28.17 · Replay the V28.16 state identifier on closed historical bars and audit how stable/useful its classifications are. Research only; no order simulation.")
    policy = load_replay_policy()

    selected = st.multiselect("Pairs", ALL_SYMBOLS, default=CORE_RESEARCH_SYMBOLS)
    c1, c2 = st.columns(2)
    with c1:
        lookback_days = st.number_input("Lookback days", min_value=60, max_value=1825, value=int(policy.get("lookback_days", 365)), step=30)
    with c2:
        st.text_input("Analysis timeframe", value=str(policy.get("analysis_timeframe") or "4h"), disabled=True)

    st.info(
        "The replay asks whether the state labels themselves make sense historically: how often states appear, how long they persist, how often they switch, and whether the later price move agrees with the router direction. It does not create simulated positions or promote strategies."
    )

    if st.button("Run historical state replay", type="primary", disabled=not selected):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(lookback_days))
        try:
            with st.spinner("Replaying closed historical states..."):
                result = run_market_state_replay(
                    source_root=OHLCV_STORE_ROOT,
                    symbols=selected,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    policy={**policy, "lookback_days": int(lookback_days)},
                )
            st.session_state["v2817_market_state_replay"] = result
        except Exception as exc:
            st.error(f"Replay could not run: {exc}")

    result = st.session_state.get("v2817_market_state_replay")
    if result is None:
        st.caption("No replay result in this session yet. Historical OHLCV must exist on the runtime first.")
        return

    audit = result.no_lookahead_audit
    action_share = 0.0
    if not result.history.empty:
        action_share = float((result.history["router_action"] == "TRADE_CANDIDATE").mean() * 100.0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Historical states", int(audit.get("observations", 0)))
    c2.metric("Timing violations", int(audit.get("lookahead_violations", 0)))
    c3.metric("State change rate", f"{float(audit.get('state_change_rate', 0.0)):.1%}")
    c4.metric("Candidate-action bars", f"{action_share:.1f}%")

    if audit.get("passed"):
        st.success("Closed-bar timing audit passed for this state replay.")
    else:
        st.error("Timing audit failed. Do not interpret the historical state diagnostics until it is fixed.")

    for text in result.conclusions:
        st.write(f"- {text}")

    tab_states, tab_routes, tab_stability, tab_confidence, tab_raw = st.tabs([
        "State quality", "Adaptive routing", "Transitions / dwell", "Confidence", "Raw history"
    ])
    with tab_states:
        st.subheader("State summary")
        st.dataframe(result.state_summary, width="stretch", hide_index=True)
    with tab_routes:
        st.subheader("Preferred family / action summary")
        st.dataframe(result.router_summary, width="stretch", hide_index=True)
        st.caption("Directional return is a diagnostic of whether the later move agreed with the router direction. It is not a strategy PnL backtest.")
    with tab_stability:
        st.subheader("State dwell")
        st.dataframe(result.dwell_summary, width="stretch", hide_index=True)
        st.subheader("Most common transitions")
        st.dataframe(result.transition_summary, width="stretch", hide_index=True)
    with tab_confidence:
        st.subheader("Confidence calibration")
        st.dataframe(result.confidence_summary, width="stretch", hide_index=True)
    with tab_raw:
        display_cols = [
            "symbol", "decision_time", "market_state", "direction", "trend_strength", "volatility_state",
            "htf_alignment", "confidence", "preferred_strategy_family", "router_action", "router_direction",
            "risk_multiplier", "lookahead_ok"
        ]
        display_cols = [col for col in display_cols if col in result.history.columns]
        st.dataframe(result.history[display_cols], width="stretch", hide_index=True)

    if st.button("Save replay snapshot"):
        paths = save_market_state_replay(result)
        st.success(f"Saved {len(paths)} replay artifacts under data/backtest_reviews/market_state_replays/")
