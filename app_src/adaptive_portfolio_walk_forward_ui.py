from __future__ import annotations

import pandas as pd
import streamlit as st

from .adaptive_evidence_v2820 import completed_adaptive_source_jobs
from .adaptive_portfolio_walk_forward_report_v2822 import save_portfolio_walk_forward_snapshot
from .adaptive_portfolio_walk_forward_v2822 import (
    build_portfolio_freeze,
    list_portfolio_freezes,
    load_portfolio_walk_forward_policy,
    run_portfolio_walk_forward_job,
    save_portfolio_freeze,
    verify_portfolio_freeze,
)
from .shared_account_replay_v2821 import analyze_shared_account_job

SESSION_SOURCE = "v2822_source_shared_result"
SESSION_SOURCE_JOB = "v2822_source_job"
SESSION_RESULT = "v2822_walk_forward_result"
SESSION_FREEZE = "v2822_walk_forward_freeze"


def _job_label(job: dict) -> str:
    created = str(job.get("created_at") or "")[:19]
    symbols = ",".join(job.get("symbols") or [])
    return f"{created} | {symbols} | {job.get('job_id', '')}"


def _fmt(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    for col in out.columns:
        if col.endswith("_time") or col == "time":
            out[col] = out[col].astype(str)
    return out


def _equity_chart(static_equity: pd.DataFrame, adaptive_equity: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for label, frame in [("static_oos", static_equity), ("adaptive_oos", adaptive_equity)]:
        if frame is None or frame.empty or "time" not in frame.columns or "equity_usd" not in frame.columns:
            continue
        part = frame[["time", "equity_usd"]].copy()
        part["time"] = pd.to_datetime(part["time"], utc=True, errors="coerce")
        part = part.dropna(subset=["time"])
        part["scenario"] = label
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    joined = pd.concat(frames, ignore_index=True)
    return joined.pivot_table(index="time", columns="scenario", values="equity_usd", aggfunc="last").sort_index()


def render_adaptive_portfolio_walk_forward() -> None:
    st.header("Frozen Adaptive Portfolio Walk-Forward")
    st.caption(
        "V28.22: freeze the complete adaptive framework and test the same strategy/router/arbitration/capital rules through chronological OOS portfolio folds. "
        "Research only; this does not enable paper or live execution."
    )
    st.warning(
        "Methodology boundary: this historical year has already influenced research/candidate selection. V28.22 measures frozen temporal stability, "
        "not a pristine never-seen future holdout. A pass only earns the right to start a genuinely future freeze."
    )

    policy = load_portfolio_walk_forward_policy()
    jobs = completed_adaptive_source_jobs()
    if not jobs:
        st.info("No completed controlled research job is available yet. Populate trustworthy history and run the research/evidence pipeline first.")
        return

    options = {_job_label(job): job for job in jobs}
    label = st.selectbox("Source research job", options=list(options.keys()))
    job = options[label]
    job_id = str(job.get("job_id") or "")

    with st.expander("V28.22 validation policy"):
        st.json(policy)

    st.subheader("1. Rebuild the V28.21 source verdict")
    if st.button("Check source shared-account eligibility", width="stretch"):
        try:
            with st.spinner("Rebuilding V28.20 evidence and V28.21 shared-account result..."):
                shared = analyze_shared_account_job(job)
            st.session_state[SESSION_SOURCE] = shared
            st.session_state[SESSION_SOURCE_JOB] = job
        except Exception as exc:
            st.error(f"Source eligibility check failed: {exc}")
            st.session_state.pop(SESSION_SOURCE, None)
            st.session_state.pop(SESSION_SOURCE_JOB, None)

    source_result = st.session_state.get(SESSION_SOURCE)
    source_job = st.session_state.get(SESSION_SOURCE_JOB)
    source_matches = source_result is not None and str((source_job or {}).get("job_id") or "") == job_id
    eligible = False
    if source_matches:
        source_verdict = str((source_result.verdict or {}).get("verdict") or "")
        eligible = source_verdict == str(policy.get("require_source_verdict") or "shared_account_edge_candidate")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Source verdict", source_verdict.replace("_", " "))
        c2.metric("Adaptive return", f"{float(source_result.adaptive_summary.get('return_pct') or 0):+.2f}%")
        c3.metric("Static return", f"{float(source_result.static_summary.get('return_pct') or 0):+.2f}%")
        c4.metric("Adaptive PF", f"{float(source_result.adaptive_summary.get('profit_factor') or 0):.2f}")
        if eligible:
            st.success("Source is eligible to be frozen for V28.22 historical temporal-stability validation.")
        else:
            st.error("Source is not a V28.21 shared_account_edge_candidate. Do not manufacture a walk-forward pass from weaker evidence.")

    st.subheader("2. Freeze the complete framework")
    st.caption(
        "The freeze hashes representative strategy payloads, Market State/Router policies, adaptive-evidence policy, shared-account capital policy, "
        "V28.22 policy, and the core implementation files."
    )
    if st.button("Create tamper-evident V28.22 freeze", type="primary", disabled=not eligible, width="stretch"):
        try:
            freeze = build_portfolio_freeze(job, shared_result=source_result, walk_forward_policy=policy)
            path = save_portfolio_freeze(freeze)
            st.session_state[SESSION_FREEZE] = freeze
            st.success(f"Frozen: {path}")
        except Exception as exc:
            st.error(f"Could not freeze framework: {exc}")

    saved = []
    for item in list_portfolio_freezes():
        record = item["record"]
        if str((record.get("source") or {}).get("job_id") or "") == job_id:
            saved.append(item)
    freeze = st.session_state.get(SESSION_FREEZE)
    if saved:
        freeze_options = {
            f"{item['record'].get('created_at', '')[:19]} | {item['record'].get('framework_sha256', '')[:12]} | {item['record'].get('freeze_id', '')}": item["record"]
            for item in saved
        }
        selected_freeze_label = st.selectbox("Saved freeze", options=list(freeze_options.keys()))
        freeze = freeze_options[selected_freeze_label]
    if not freeze or str((freeze.get("source") or {}).get("job_id") or "") != job_id:
        st.caption("Create or select a freeze for this source job before running the folds.")
        return

    integrity = verify_portfolio_freeze(freeze, check_current=True, source_job=job)
    i1, i2, i3, i4 = st.columns(4)
    i1.metric("Freeze record", "OK" if integrity.get("record_integrity_ok") else "FAIL")
    i2.metric("Policies", "OK" if integrity.get("policy_integrity_ok") else "DRIFT")
    i3.metric("Strategies", "OK" if integrity.get("strategy_integrity_ok") else "DRIFT")
    i4.metric("Implementation", "OK" if integrity.get("implementation_integrity_ok") else "DRIFT")
    st.caption(f"Framework SHA-256: {freeze.get('framework_sha256')}")
    if integrity.get("drift"):
        st.error("Freeze drift detected: " + ", ".join(str(x) for x in integrity["drift"]))

    st.subheader("3. Run the exact frozen framework through chronological OOS folds")
    if st.button("Run frozen portfolio walk-forward", type="primary", disabled=not bool(integrity.get("ok")), width="stretch"):
        try:
            with st.spinner("Running frozen chronological portfolio folds and aggregate OOS shared-account replay..."):
                result = run_portfolio_walk_forward_job(job, freeze)
            st.session_state[SESSION_RESULT] = result
            st.session_state[SESSION_FREEZE] = freeze
        except Exception as exc:
            st.error(f"Walk-forward validation failed: {exc}")
            st.session_state.pop(SESSION_RESULT, None)
            return

    result = st.session_state.get(SESSION_RESULT)
    result_freeze = st.session_state.get(SESSION_FREEZE)
    if result is None or str((result_freeze or {}).get("freeze_id") or "") != str(freeze.get("freeze_id") or ""):
        st.caption("Run the frozen walk-forward to see OOS evidence.")
        return

    verdict = result.verdict
    name = str(verdict.get("verdict") or "unknown")
    if name == "pass_for_future_freeze":
        st.success("PASS FOR FUTURE FREEZE — the exact adaptive portfolio is historically stable enough to justify a genuinely future test. This is not trading permission.")
    elif name == "static_baseline_better_oos":
        st.error("STATIC BASELINE BETTER OOS — the extra adaptive logic does not justify itself economically in the chronological tests.")
    elif name in {"fail", "invalid_freeze_or_evidence"}:
        st.error(f"{name.replace('_', ' ').upper()} — do not promote this frozen portfolio version.")
    else:
        st.warning(f"{name.replace('_', ' ').upper()} — keep this version in research.")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("OOS adaptive return", f"{float(verdict.get('aggregate_adaptive_return_pct') or 0):+.2f}%")
    m2.metric("OOS static return", f"{float(verdict.get('aggregate_static_return_pct') or 0):+.2f}%")
    m3.metric("Return uplift", f"{float(verdict.get('aggregate_return_uplift_pct_points') or 0):+.2f} pp")
    m4.metric("OOS adaptive PF", f"{float(verdict.get('aggregate_adaptive_profit_factor') or 0):.2f}")
    m5.metric("Realized OOS DD", f"{float(verdict.get('aggregate_realized_drawdown_pct') or 0):.2f}%")

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Chronological folds", int(verdict.get("folds") or 0))
    f2.metric("Fold pass share", f"{float(verdict.get('fold_pass_share') or 0):.0%}")
    f3.metric("Adaptive > static folds", f"{float(verdict.get('adaptive_outperform_fold_share') or 0):.0%}")
    f4.metric("OOS accepted trades", int(verdict.get("aggregate_oos_trades") or 0))

    st.subheader("Chronological folds")
    st.dataframe(_fmt(result.folds), width="stretch", hide_index=True)

    chart = _equity_chart(result.static_equity, result.adaptive_equity)
    if not chart.empty:
        st.subheader("Aggregate OOS shared-account equity")
        st.line_chart(chart)
    st.caption("Aggregate OOS replays all non-overlapping test windows as one chronological account. Per-fold results reset capital only for fold comparability.")

    st.subheader("Aggregate static vs adaptive")
    st.dataframe(_fmt(result.aggregate_comparison), width="stretch", hide_index=True)

    st.subheader("OOS friction stress")
    st.dataframe(_fmt(result.friction_stress), width="stretch", hide_index=True)

    st.subheader("OOS stability")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**By pair**")
        st.dataframe(_fmt(result.by_symbol), width="stretch", hide_index=True)
    with c2:
        st.markdown("**By family**")
        st.dataframe(_fmt(result.by_family), width="stretch", hide_index=True)
    with c3:
        st.markdown("**By month**")
        st.dataframe(_fmt(result.by_month), width="stretch", hide_index=True)

    st.subheader("Validation gates")
    checks = pd.DataFrame([{"check": key, "pass": bool(value)} for key, value in (verdict.get("checks") or {}).items()])
    st.dataframe(checks, width="stretch", hide_index=True)
    st.write(verdict.get("next_step") or "")

    st.subheader("Critical interpretation")
    for item in result.critique:
        st.write(f"- {item}")

    with st.expander("Integrity details"):
        st.json(result.integrity)
    with st.expander("Accepted OOS adaptive ledger"):
        st.dataframe(_fmt(result.adaptive_ledger.head(1000)), width="stretch", hide_index=True)

    if st.button("Save reproducible V28.22 snapshot"):
        try:
            path = save_portfolio_walk_forward_snapshot(result, freeze)
            st.success(f"Saved: {path}")
        except Exception as exc:
            st.error(f"Could not save V28.22 snapshot: {exc}")
