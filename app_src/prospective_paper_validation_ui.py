from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from .future_adaptive_portfolio_holdout_v2823 import list_future_freezes
from .prospective_paper_validation_v2824 import (
    build_paper_session_freeze,
    evaluate_paper_session,
    initialize_paper_session,
    list_future_holdout_snapshots,
    list_paper_session_freezes,
    load_paper_policy,
    run_prospective_paper_cycle,
    save_paper_session_freeze,
    save_paper_validation_snapshot,
    verify_event_chain,
    verify_paper_session_freeze,
)

SESSION_RECORD = "v2824_paper_record"
SESSION_RESULT = "v2824_paper_result"
SESSION_CYCLE = "v2824_last_cycle"


def _fmt(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    for col in out.columns:
        if "time" in str(col).lower() or str(col).endswith("_at"):
            out[col] = out[col].astype(str)
    return out


def _snapshot_label(item: dict) -> str:
    summary = item.get("summary") or {}
    verdict = (summary.get("verdict") or {}).get("verdict") or "unknown"
    return f"{summary.get('freeze_id', '')} | {verdict} | {Path(item.get('path', '')).name}"


def _paper_label(item: dict) -> str:
    record = item.get("record") or {}
    return f"{record.get('created_at', '')[:19]} | {record.get('session_id', '')} | {record.get('paper_framework_sha256', '')[:12]}"


def _matching_future_freeze(snapshot: dict) -> dict | None:
    freeze_id = str((snapshot.get("summary") or {}).get("freeze_id") or "")
    for item in list_future_freezes():
        record = item.get("record") or {}
        if str(record.get("freeze_id") or "") == freeze_id:
            return record
    return None


def _equity_chart(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    if "snapshot_time" not in work.columns or "mode" not in work.columns or "mtm_equity_usd" not in work.columns:
        return pd.DataFrame()
    work["snapshot_time"] = pd.to_datetime(work["snapshot_time"], utc=True, errors="coerce")
    work = work.dropna(subset=["snapshot_time"])
    if work.empty:
        return pd.DataFrame()
    return work.pivot_table(index="snapshot_time", columns="mode", values="mtm_equity_usd", aggfunc="last").sort_index()


def render_prospective_paper_validation() -> None:
    st.header("Frozen Prospective Paper Validation")
    st.caption(
        "V28.24: record the exact frozen TRAI decisions before their outcomes are known, run adaptive and static shadow accounts under the same capital rules, "
        "and measure operational quality, executable-price proxies and mark-to-market risk. No exchange orders are sent."
    )
    st.warning(
        "This is evidence-grade paper observation, not a paper-trading toy. Missed candle-close decisions are logged as missed and are never reconstructed later. "
        "A strong result can only reach manual micro-live review; it cannot enable live execution."
    )

    policy = load_paper_policy()
    with st.expander("V28.24 prospective policy"):
        st.json(policy)

    passed = [
        item for item in list_future_holdout_snapshots()
        if str(((item.get("summary") or {}).get("verdict") or {}).get("verdict") or "") == str(policy.get("require_source_verdict") or "pass_for_frozen_paper")
    ]
    if not passed:
        st.info("No saved V28.23 `pass_for_frozen_paper` result exists on this runtime. The paper clock cannot be manufactured from weaker historical evidence.")
        return

    st.subheader("1. Select the exact V28.23 pass")
    options = {_snapshot_label(item): item for item in passed}
    selected = options[st.selectbox("Passed future-holdout snapshot", list(options.keys()))]
    summary = selected["summary"]
    future_freeze = _matching_future_freeze(selected)
    if not future_freeze:
        st.error("The matching V28.23 freeze record is missing on this runtime. Restore the original freeze artifact; do not rebuild it after seeing outcomes.")
        return

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("V28.23 verdict", str((summary.get("verdict") or {}).get("verdict") or "").replace("_", " "))
    s2.metric("Future freeze", str(summary.get("freeze_id") or "")[:22])
    s3.metric("Adaptive future return", f"{float((summary.get('verdict') or {}).get('adaptive_return_pct') or 0):+.2f}%")
    s4.metric("Future PF", f"{float((summary.get('verdict') or {}).get('adaptive_profit_factor') or 0):.2f}")

    st.subheader("2. Freeze one prospective paper clock")
    st.caption(
        "The paper freeze binds the exact V28.23 result, future-framework hash, V28.24 policy and prospective implementation hashes. "
        "The first eligible decision is the close of the first full entry candle that begins after this freeze."
    )
    if st.button("Create V28.24 paper freeze", type="primary", width="stretch"):
        try:
            record = build_paper_session_freeze(future_freeze, summary, policy=policy)
            path = save_paper_session_freeze(record)
            st.session_state[SESSION_RECORD] = record
            st.success(f"Frozen: {path}")
        except Exception as exc:
            st.error(f"Could not create paper freeze: {exc}")

    source_hash = str(summary.get("freeze_record_sha256") or "")
    available = []
    for item in list_paper_session_freezes():
        record = item.get("record") or {}
        if str(record.get("source_future_freeze_sha256") or "") == source_hash:
            available.append(item)
    record = st.session_state.get(SESSION_RECORD)
    if available:
        choices = {_paper_label(item): item["record"] for item in available}
        record = choices[st.selectbox("Saved prospective paper session", list(choices.keys()))]
    if not record:
        st.caption("Create or select the frozen paper session before running observations.")
        return
    st.session_state[SESSION_RECORD] = record

    verification = verify_paper_session_freeze(record, check_current=True)
    chain = verify_event_chain(record)
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Freeze integrity", "OK" if verification.get("record_integrity_ok") else "FAIL")
    v2.metric("Framework", "OK" if verification.get("paper_framework_hash_ok") else "FAIL")
    v3.metric("Current code/policy", "MATCH" if verification.get("ok") else "DRIFT")
    v4.metric("Event chain", "OK" if chain.get("ok") else "FAIL")
    st.caption(f"Paper framework SHA-256: {record.get('paper_framework_sha256')}")
    st.caption(f"Window: {record.get('first_eligible_decision_utc')} → {record.get('target_end_utc')} | live_execution_enabled=false")
    if verification.get("drift"):
        st.error("Frozen-framework drift: " + ", ".join(str(x) for x in verification["drift"]))
    if not chain.get("ok"):
        st.error("Paper event-chain integrity failed. Do not interpret or continue this session until the original append-only evidence is restored.")

    st.subheader("3. Run the current prospective cycle")
    st.caption(
        "Run this shortly after each entry-timeframe candle closes. For unattended observation use `python paper_validation_cycle.py --freeze <freeze.json>`. "
        "If a cycle is more than the configured lag late, that signal slot is recorded as MISSED rather than reconstructed."
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Initialize session", disabled=not bool(verification.get("ok")), width="stretch"):
            try:
                state = initialize_paper_session(record)
                st.success(f"Session initialized. Status: {state.get('status')}")
            except Exception as exc:
                st.error(f"Initialization failed: {exc}")
    with c2:
        if st.button("Run one safe paper cycle", type="primary", disabled=not bool(verification.get("ok") and chain.get("ok")), width="stretch"):
            try:
                with st.spinner("Recording timely frozen decisions, updating paper positions and marking both accounts..."):
                    cycle = run_prospective_paper_cycle(record)
                st.session_state[SESSION_CYCLE] = cycle
                if cycle.get("ok"):
                    st.success(f"Cycle complete: {cycle.get('processed_decision_slots', 0)} timely slots, {cycle.get('missed_decision_slots', 0)} missed, {cycle.get('candidates', 0)} candidates.")
                else:
                    st.error(str(cycle))
            except Exception as exc:
                st.error(f"Paper cycle failed: {exc}")

    if st.session_state.get(SESSION_CYCLE):
        with st.expander("Latest cycle result"):
            st.json(st.session_state[SESSION_CYCLE])

    st.subheader("4. Prospective evidence")
    try:
        result = evaluate_paper_session(record)
        st.session_state[SESSION_RESULT] = result
    except Exception as exc:
        st.error(f"Could not evaluate paper session: {exc}")
        return

    result = st.session_state[SESSION_RESULT]
    verdict = result.verdict
    name = str(verdict.get("verdict") or "unknown")
    if name == "pass_for_micro_live_review":
        st.success("PASS FOR MICRO-LIVE REVIEW — prospective paper evidence survived the configured gates. This authorizes manual risk review only; live execution remains disabled.")
    elif name == "static_baseline_better_paper":
        st.error("STATIC BASELINE BETTER PAPER — the adaptive layer is destroying value prospectively. Do not promote it.")
    elif name in {"paper_fail", "invalid_paper_session", "paper_operationally_unreliable"}:
        st.error(f"{name.replace('_', ' ').upper()} — do not move this framework toward real capital.")
    else:
        st.warning(name.replace("_", " ").upper())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Adaptive paper return", f"{float(verdict.get('adaptive_return_pct') or 0):+.2f}%")
    m2.metric("Static paper return", f"{float(verdict.get('static_return_pct') or 0):+.2f}%")
    m3.metric("Adaptive PF", f"{float(verdict.get('adaptive_profit_factor') or 0):.2f}")
    m4.metric("MTM max DD", f"{float(verdict.get('adaptive_max_mtm_drawdown_pct') or 0):.2f}%")
    m5.metric("Closed adaptive trades", int(verdict.get("adaptive_closed_trades") or 0))

    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Decision punctuality", f"{float(verdict.get('punctuality_share') or 0):.1%}")
    q2.metric("Executable quote coverage", f"{float(verdict.get('executable_quote_share') or 0):.1%}")
    q3.metric("Data-gap share", f"{float(verdict.get('data_gap_share') or 0):.1%}")
    q4.metric("Missed decision slots", int(verdict.get("missed_decision_slots") or 0))

    chart = _equity_chart(result.equity_history)
    if not chart.empty:
        st.markdown("**Prospective mark-to-market account equity**")
        st.line_chart(chart)

    st.markdown("**Static vs adaptive paper account**")
    st.dataframe(_fmt(result.comparison), width="stretch", hide_index=True)

    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Adaptive trades by symbol**")
        st.dataframe(_fmt(result.by_symbol), width="stretch", hide_index=True)
    with t2:
        st.markdown("**Adaptive trades by family**")
        st.dataframe(_fmt(result.by_family), width="stretch", hide_index=True)

    st.markdown("**Validation gates**")
    checks = pd.DataFrame([{"check": key, "pass": bool(value)} for key, value in (verdict.get("checks") or {}).items()])
    st.dataframe(checks, width="stretch", hide_index=True)
    st.write(verdict.get("next_step") or "")

    with st.expander("Operational incidents"):
        st.dataframe(_fmt(result.incidents), width="stretch", hide_index=True)
    with st.expander("Decision ledger"):
        st.dataframe(_fmt(result.decisions.tail(1000)), width="stretch", hide_index=True)
    with st.expander("Closed adaptive paper trades"):
        st.dataframe(_fmt(result.adaptive_trades.tail(1000)), width="stretch", hide_index=True)
    with st.expander("Integrity details"):
        st.json(result.integrity)

    st.subheader("Critical interpretation")
    for item in result.critique:
        st.write(f"- {item}")

    if st.button("Save reproducible V28.24 evidence snapshot"):
        try:
            paths = save_paper_validation_snapshot(result, record)
            st.success(f"Saved: {paths.get('summary')}")
        except Exception as exc:
            st.error(f"Could not save paper snapshot: {exc}")
