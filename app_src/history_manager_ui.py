from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .historical_backfill import backfill_symbol_history
from .history_manager import (
    DEFAULT_HISTORY_INTERVALS,
    DEFAULT_HISTORY_LOOKBACK,
    DEFAULT_HISTORY_SYMBOLS,
    audit_history_gaps,
    build_backfill_command,
    normalize_intervals,
    normalize_symbols,
    summarize_history_coverage,
)


def _display_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in ["first_open_time", "last_open_time", "gap_after", "next_open_time"]:
        if col in out.columns:
            out[col] = out[col].map(lambda x: "" if pd.isna(x) or x is None else str(x))
    return out


def render_history_manager_tab() -> None:
    st.header("Data / History Manager")
    st.caption("V28.19: inspect OHLCV coverage and continuity, exclude unfinished candles, repair gaps, and refresh history safely before research.")

    st.warning(
        "Historical candles are stored locally under data/ohlcv_store and are intentionally not committed to Git. "
        "Run this on the machine/runtime where you want the history to persist: local PC, Codespaces, VPS, or a later always-on server."
    )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        default_symbols = DEFAULT_HISTORY_SYMBOLS
        symbols = st.multiselect(
            "USDT pairs",
            options=default_symbols,
            default=default_symbols,
            help="Default target set: BTC, ETH, SOL, LTC, BNB, UNI, AAVE, XRP, TRX with USDT quote.",
        )
        custom_symbols = st.text_input("Add custom comma-separated pairs", value="", placeholder="DOGEUSDT, LINKUSDT")
        if custom_symbols.strip():
            symbols = normalize_symbols(list(symbols) + normalize_symbols(custom_symbols))
    with c2:
        intervals = st.multiselect("Intervals", options=["5m", "15m", "1h", "4h", "1d"], default=DEFAULT_HISTORY_INTERVALS)
    with c3:
        lookback = st.selectbox("Lookback", options=["30d", "90d", "6mo", "12mo", "1y", "3y", "5y"], index=3)

    symbols = normalize_symbols(symbols)
    intervals = normalize_intervals(intervals)

    st.subheader("Coverage + integrity")
    if st.button("Refresh coverage table", width="stretch"):
        st.cache_data.clear()
    try:
        coverage = summarize_history_coverage(symbols, intervals, lookback=lookback)
        if coverage.empty:
            st.info("No local OHLCV coverage found yet.")
        else:
            summary_cols = [
                "symbol",
                "interval",
                "status",
                "coverage_pct",
                "continuity_ok",
                "internal_gap_count",
                "internal_missing_rows",
                "unclosed_rows",
                "rows_in_lookback",
                "expected_rows_approx",
                "missing_rows_approx",
                "first_open_time",
                "last_open_time",
                "freshness_minutes",
            ]
            st.dataframe(_display_time_columns(coverage[[c for c in summary_cols if c in coverage.columns]]), width="stretch", hide_index=True)
            ready = int((coverage["status"] == "ready").sum()) if "status" in coverage.columns else 0
            partial = int((coverage["status"] == "partial").sum()) if "status" in coverage.columns else 0
            missing = int((coverage["status"] == "missing").sum()) if "status" in coverage.columns else 0
            m1, m2, m3 = st.columns(3)
            m1.metric("Ready", ready)
            m2.metric("Partial / integrity issue", partial)
            m3.metric("Missing", missing)
    except Exception as exc:
        st.error(f"Could not read history coverage: {exc}")

    st.subheader("Backfill commands")
    st.caption("Recommended first load: run in a terminal so it can continue even if the Streamlit page refreshes. Gap repair is enabled by default.")
    st.code(build_backfill_command(symbols, intervals, lookback=lookback, update_only=False, request_analysis=False), language="powershell")
    st.caption("Regular refresh overlaps the latest two stored candles before appending new closed candles:")
    st.code(build_backfill_command(symbols, intervals, lookback=lookback, update_only=True, request_analysis=True), language="powershell")

    with st.expander("Run selected backfill inside Streamlit (blocking)"):
        st.caption("Use this for small updates. For 9 symbols x 12 months x multiple intervals, terminal or a server job is safer.")
        run_update_only = st.checkbox("Update only", value=True)
        request_analysis = st.checkbox("Request analyzer after backfill", value=True)
        max_pages_raw = st.number_input("Optional max pages per symbol/interval; 0 = no cap", min_value=0, max_value=10000, value=0, step=1)
        confirm = st.checkbox("I understand this can take several minutes and depends on Binance/network access")
        if st.button("Run backfill now", disabled=not confirm, type="primary", width="stretch"):
            rows: list[dict[str, Any]] = []
            progress = st.progress(0.0)
            total = max(1, len(symbols) * len(intervals))
            done = 0
            for symbol in symbols:
                for interval in intervals:
                    with st.status(f"Backfilling {symbol} {interval}...", expanded=False):
                        result = backfill_symbol_history(
                            symbol,
                            interval,
                            lookback=lookback,
                            update_only=run_update_only,
                            max_pages=int(max_pages_raw) if int(max_pages_raw) > 0 else None,
                        )
                        rows.append(result.to_dict())
                    done += 1
                    progress.progress(done / total)
            result_df = pd.DataFrame(rows)
            st.success("Backfill run finished")
            st.dataframe(_display_time_columns(result_df), width="stretch", hide_index=True)
            if "integrity_status" in result_df.columns and (result_df["integrity_status"] != "ready").any():
                st.warning("At least one target still has an integrity issue. Review gaps_remaining / missing_rows_remaining before research.")
            if request_analysis:
                st.info("Analyzer request is not started from this Streamlit page yet. Use the CLI with --request-analysis or start analyzer from the main app sidebar.")

    st.subheader("Gap audit")
    g1, g2 = st.columns(2)
    with g1:
        gap_symbol = st.selectbox("Gap audit pair", options=symbols or DEFAULT_HISTORY_SYMBOLS)
    with g2:
        gap_interval = st.selectbox("Gap audit interval", options=intervals or DEFAULT_HISTORY_INTERVALS)
    if st.button("Audit selected pair/interval", width="stretch"):
        try:
            gaps = audit_history_gaps(gap_symbol, gap_interval, lookback=lookback)
            if gaps.empty:
                st.success("No large open_time gaps found in the selected lookback window.")
            else:
                st.warning(f"Found {len(gaps)} gaps. The V28.19 backfill will try to repair these automatically; unresolved gaps remain visible in its report.")
                st.dataframe(_display_time_columns(gaps), width="stretch", hide_index=True)
        except Exception as exc:
            st.error(f"Gap audit failed: {exc}")

    with st.expander("What data is this?"):
        st.write(
            "This manager covers OHLCV candles only: open, high, low, close, volume, open_time, close_time. "
            "V28.19 excludes the currently forming candle and audits internal continuity. "
            "It does not yet backfill funding history, open-interest history, liquidation data, or order-book snapshots."
        )
