from __future__ import annotations

import pandas as pd
import streamlit as st

from .adaptive_evidence_v2820 import completed_adaptive_source_jobs
from .adaptive_portfolio_walk_forward_v2822 import build_portfolio_freeze, run_portfolio_walk_forward_job
from .future_adaptive_portfolio_holdout_report_v2823 import save_future_holdout_snapshot
from .future_adaptive_portfolio_holdout_v2823 import (
    build_future_holdout_freeze,
    evaluate_future_holdout_job,
    future_holdout_jobs,
    future_holdout_readiness,
    list_future_freezes,
    load_future_holdout_policy,
    queue_future_holdout,
    save_future_freeze,
)

SESSION_WF_RESULT = "v2823_source_wf_result"
SESSION_PORTFOLIO_FREEZE = "v2823_source_portfolio_freeze"
SESSION_SOURCE_JOB = "v2823_source_job"
SESSION_HOLDOUT_RESULT = "v2823_holdout_result"
SESSION_HOLDOUT_JOB_ID = "v2823_holdout_job_id"
SESSION_HOLDOUT_FREEZE_ID = "v2823_holdout_freeze_id"


def _job_label(job: dict) -> str:
    created = str(job.get("created_at") or "")[:19]
    symbols = ",".join(job.get("symbols") or [])
    return f"{created} | {symbols} | {job.get('job_id', '')}"


def _fmt(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    for col in out.columns:
        if col.endswith("_time") or col in {"time", "decision_time", "entry_time", "exit_time", "signal_time"}:
            out[col] = out[col].astype(str)
    return out


def _equity_chart(static_equity: pd.DataFrame, adaptive_equity: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for label, frame in [("static", static_equity), ("adaptive", adaptive_equity)]:
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


def render_future_adaptive_portfolio_holdout() -> None:
    st.header("Genuinely Future Adaptive Portfolio Holdout")
    st.caption(
        "V28.23: freeze the complete adaptive portfolio before new market data exists, wait for a fixed future endpoint, "
        "then run one shared-capital evaluation with the exact frozen framework. Research only; no paper/live execution is changed."
    )
    policy = load_future_holdout_policy()
    st.info(
        "This page deliberately prevents a mid-window backtest by default. The future clock starts on the next UTC date after the freeze, "
        "the endpoint is fixed in advance, and the same passed framework cannot simply restart its clock after outcomes become inconvenient."
    )

    with st.expander("V28.23 future-evidence policy"):
        st.json(policy)

    st.subheader("1. Earn the right to start a future-data clock")
    jobs = completed_adaptive_source_jobs()
    if not jobs:
        st.warning("No completed controlled research job is available. Populate trustworthy history and complete the historical evidence ladder first.")
    else:
        options = {_job_label(job): job for job in jobs}
        selected_label = st.selectbox("Completed research job", options=list(options.keys()))
        source_job = options[selected_label]
        if st.button("Rebuild V28.22 eligibility", type="primary", width="stretch"):
            try:
                with st.spinner("Rebuilding the shared-account source freeze and frozen portfolio walk-forward..."):
                    portfolio_freeze = build_portfolio_freeze(source_job)
                    wf_result = run_portfolio_walk_forward_job(source_job, portfolio_freeze)
                st.session_state[SESSION_WF_RESULT] = wf_result
                st.session_state[SESSION_PORTFOLIO_FREEZE] = portfolio_freeze
                st.session_state[SESSION_SOURCE_JOB] = source_job
            except Exception as exc:
                st.error(f"Could not establish V28.22 eligibility: {exc}")
                st.session_state.pop(SESSION_WF_RESULT, None)
                st.session_state.pop(SESSION_PORTFOLIO_FREEZE, None)
                st.session_state.pop(SESSION_SOURCE_JOB, None)

        wf_result = st.session_state.get(SESSION_WF_RESULT)
        frozen_source = st.session_state.get(SESSION_PORTFOLIO_FREEZE)
        session_job = st.session_state.get(SESSION_SOURCE_JOB)
        if wf_result is not None and str((session_job or {}).get("job_id") or "") == str(source_job.get("job_id") or ""):
            verdict = dict(wf_result.verdict or {})
            name = str(verdict.get("verdict") or "unknown")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("V28.22 verdict", name.replace("_", " "))
            c2.metric("OOS adaptive return", f"{float(verdict.get('aggregate_adaptive_return_pct') or 0):+.2f}%")
            c3.metric("OOS static return", f"{float(verdict.get('aggregate_static_return_pct') or 0):+.2f}%")
            c4.metric("OOS PF", f"{float(verdict.get('aggregate_adaptive_profit_factor') or 0):.2f}")
            if name == str(policy.get("require_source_verdict") or "pass_for_future_freeze"):
                st.success("Historical stability gates passed. This framework may start one genuinely future holdout clock.")
                if st.button("Freeze exact framework and start future-data clock", width="stretch"):
                    try:
                        record = build_future_holdout_freeze(source_job, frozen_source, wf_result, policy=policy)
                        path = save_future_freeze(record)
                        st.success(f"Future freeze saved: {path}")
                        st.session_state.pop(SESSION_HOLDOUT_RESULT, None)
                    except Exception as exc:
                        st.error(f"Could not create future freeze: {exc}")
            else:
                st.warning("This framework has not earned a future holdout. Do not bypass the V28.22 gate by starting the clock manually.")

    st.subheader("2. Frozen future clocks")
    freezes = list_future_freezes()
    if not freezes:
        st.caption("No V28.23 future freeze exists yet.")
        return

    freeze_options = {}
    for item in freezes:
        record = item["record"]
        label = f"{record.get('created_at', '')[:19]} | {record.get('freeze_id', '')} | end {record.get('target_end_date', '')}"
        freeze_options[label] = item
    freeze_label = st.selectbox("Future freeze", options=list(freeze_options.keys()))
    selected = freeze_options[freeze_label]
    record = selected["record"]
    readiness = future_holdout_readiness(record)

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Observed full UTC days", f"{int(readiness.get('observed_days') or 0)} / {int(readiness.get('target_days') or 0)}")
    r2.metric("Remaining days", int(readiness.get("remaining_days") or 0))
    r3.metric("Calendar complete", "yes" if readiness.get("calendar_complete") else "no")
    r4.metric("Data ready", "yes" if readiness.get("data_ready") else "no")
    st.write(readiness.get("reason") or "")

    verification = dict(readiness.get("freeze_integrity") or {})
    if not verification.get("internal_valid", False):
        st.error("Freeze integrity failed. Do not evaluate this record.")
    elif not verification.get("current_framework_match", False):
        st.error("Relevant code/policies drifted after the freeze. A valid evaluation requires the exact frozen behavioral framework.")
    else:
        st.success("Freeze and current-framework fingerprints match.")

    with st.expander("Freeze identity and exact future window"):
        st.json({
            "freeze_id": record.get("freeze_id"),
            "record_sha256": record.get("record_sha256"),
            "future_framework_sha256": record.get("future_framework_sha256"),
            "cutoff_utc": record.get("cutoff_utc"),
            "first_eligible_date": record.get("first_eligible_date"),
            "target_end_date": record.get("target_end_date"),
            "target_holdout_days": record.get("target_holdout_days"),
            "source_walk_forward_verdict": record.get("source_walk_forward_verdict"),
            "source_portfolio_framework_sha256": record.get("source_portfolio_framework_sha256"),
        })
    with st.expander("OHLCV readiness by pair/timeframe"):
        st.dataframe(_fmt(readiness.get("data_status", pd.DataFrame())), width="stretch", hide_index=True)

    queue_disabled = not bool(readiness.get("ready", False))
    if st.button("Queue one frozen future evaluation", disabled=queue_disabled, width="stretch"):
        outcome = queue_future_holdout(record)
        if outcome.get("queued"):
            st.success(f"Queued {outcome.get('tasks_created')} frozen strategy-family tasks: {outcome.get('job_path')}")
        else:
            st.warning(outcome.get("reason") or "Could not queue future holdout.")

    st.subheader("3. Future holdout jobs and final evidence")
    holdout_jobs = future_holdout_jobs(freeze_id=str(record.get("freeze_id") or ""))
    if not holdout_jobs:
        st.caption("No job exists for this freeze yet. The queue button remains locked until the precommitted endpoint and data checks are complete.")
        return
    job_options = {_job_label(job): job for job in holdout_jobs}
    selected_job_label = st.selectbox("Future holdout job", options=list(job_options.keys()))
    holdout_job = job_options[selected_job_label]
    j1, j2, j3 = st.columns(3)
    j1.metric("Status", str(holdout_job.get("status") or "unknown"))
    progress = dict(holdout_job.get("progress") or {})
    j2.metric("Tasks", f"{int(progress.get('completed') or 0)} / {int(progress.get('total') or 0)}")
    j3.metric("Job id", str(holdout_job.get("job_id") or ""))

    if str(holdout_job.get("status") or "") != "completed":
        st.caption("Evaluation becomes available only after the exact frozen backtests have completed.")
        return

    if st.button("Evaluate genuinely future shared-account evidence", width="stretch"):
        try:
            with st.spinner("Verifying frozen hashes, rebuilding Market State with frozen policies, and replaying the shared account..."):
                result = evaluate_future_holdout_job(holdout_job, record)
            st.session_state[SESSION_HOLDOUT_RESULT] = result
            st.session_state[SESSION_HOLDOUT_JOB_ID] = str(holdout_job.get("job_id") or "")
            st.session_state[SESSION_HOLDOUT_FREEZE_ID] = str(record.get("freeze_id") or "")
        except Exception as exc:
            st.error(f"Future holdout evaluation failed: {exc}")
            st.session_state.pop(SESSION_HOLDOUT_RESULT, None)
            return

    result = st.session_state.get(SESSION_HOLDOUT_RESULT)
    if result is None:
        return
    if st.session_state.get(SESSION_HOLDOUT_JOB_ID) != str(holdout_job.get("job_id") or "") or st.session_state.get(SESSION_HOLDOUT_FREEZE_ID) != str(record.get("freeze_id") or ""):
        st.warning("The displayed result belongs to a different freeze/job. Evaluate the selected job again.")
        return

    verdict = dict(result.verdict or {})
    name = str(verdict.get("verdict") or "unknown")
    if name == "pass_for_frozen_paper":
        st.success("Genuinely future holdout passed — strong enough to justify a separate frozen paper-validation stage, not live deployment.")
    elif name in {"future_holdout_fail", "static_baseline_better_future", "invalid_freeze_or_test"}:
        st.error(f"{name.replace('_', ' ').title()} — do not promote this framework.")
    else:
        st.warning(f"{name.replace('_', ' ').title()} — keep the framework in research.")

    v1, v2, v3, v4, v5 = st.columns(5)
    v1.metric("Adaptive future return", f"{float(verdict.get('adaptive_return_pct') or 0):+.2f}%")
    v2.metric("Static future return", f"{float(verdict.get('static_return_pct') or 0):+.2f}%")
    v3.metric("Return uplift", f"{float(verdict.get('return_uplift_pct_points') or 0):+.2f} pp")
    v4.metric("Adaptive PF", f"{float(verdict.get('adaptive_profit_factor') or 0):.2f}")
    v5.metric("Realized max DD", f"{float(verdict.get('adaptive_max_realized_drawdown_pct') or 0):.2f}%")

    chart = _equity_chart(result.static_equity, result.adaptive_equity)
    if not chart.empty:
        st.subheader("Future shared-account equity")
        st.line_chart(chart)

    st.subheader("Future-evidence gates")
    checks = pd.DataFrame([{"check": key, "pass": bool(value)} for key, value in (verdict.get("checks") or {}).items()])
    st.dataframe(checks, width="stretch", hide_index=True)
    st.write(verdict.get("next_step") or "")

    st.subheader("Static vs adaptive on new data")
    st.dataframe(_fmt(result.comparison), width="stretch", hide_index=True)
    st.subheader("Future friction stress")
    st.dataframe(_fmt(result.friction_stress), width="stretch", hide_index=True)

    st.markdown("**Contribution by pair**")
    st.dataframe(_fmt(result.by_symbol), width="stretch", hide_index=True)
    st.markdown("**Contribution by family**")
    st.dataframe(_fmt(result.by_family), width="stretch", hide_index=True)
    st.markdown("**Contribution by month**")
    st.dataframe(_fmt(result.by_month), width="stretch", hide_index=True)

    st.subheader("Critical interpretation")
    for item in result.critique:
        st.write(f"- {item}")

    with st.expander("Accepted adaptive ledger"):
        st.dataframe(_fmt(result.adaptive_ledger.head(1000)), width="stretch", hide_index=True)
    with st.expander("Rejected candidate ledger"):
        st.dataframe(_fmt(result.rejected_candidates.head(1000)), width="stretch", hide_index=True)

    if st.button("Save reproducible V28.23 future-evidence snapshot"):
        try:
            paths = save_future_holdout_snapshot(result, record, job=holdout_job)
            st.success(f"Saved summary: {paths.get('summary')}")
        except Exception as exc:
            st.error(f"Could not save V28.23 snapshot: {exc}")
