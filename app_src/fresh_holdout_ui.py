from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from .backtest_core import load_saved_backtest
from .fresh_holdout_v2815 import (
    build_freeze_record,
    evaluate_fresh_holdout,
    fresh_holdout_jobs,
    holdout_readiness,
    list_freeze_records,
    load_fresh_holdout_policy,
    queue_fresh_holdout,
    save_freeze_record,
    save_holdout_snapshot,
    verify_freeze_record,
)
from .walk_forward_v2814 import evaluate_walk_forward, load_walk_forward_policy, walk_forward_jobs


def _completed_walk_forward_passes() -> list[dict]:
    jobs = walk_forward_jobs(["completed"])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        wf_id = str(job.get("wf_id") or "")
        if wf_id:
            grouped[wf_id].append(job)
    out = []
    for wf_id, group in grouped.items():
        source_run_dir = str(group[0].get("source_run_dir") or "")
        if not source_run_dir:
            continue
        try:
            source_run = load_saved_backtest(source_run_dir)
            report = evaluate_walk_forward(wf_id, group, source_run, policy=load_walk_forward_policy())
        except Exception:
            continue
        if str(report.get("verdict") or "") != "pass_for_next_validation":
            continue
        out.append(
            {
                "wf_id": wf_id,
                "source_run": source_run,
                "report": report,
                "research_family": str(group[0].get("research_family") or "unknown"),
                "source_run_name": str(group[0].get("source_run_name") or ""),
                "created_at": str(group[0].get("created_at") or ""),
            }
        )
    return sorted(out, key=lambda x: x.get("created_at", ""), reverse=True)


def render_fresh_holdout() -> None:
    st.title("Fresh Holdout Protocol")
    st.caption("V28.15 · freeze first, then evaluate only candles that did not exist when the candidate was frozen")

    policy = load_fresh_holdout_policy()
    st.info(
        "This page is intentionally slow by design. A candidate must first pass V28.14, then its full strategy payload, "
        "execution assumptions, symbols and timeframes are hashed and frozen. The default holdout waits for a fixed "
        f"{int(policy.get('target_holdout_days', 60))}-day future window before evaluation."
    )

    st.subheader("1. Freeze a V28.14 survivor")
    candidates = _completed_walk_forward_passes()
    if not candidates:
        st.caption("No V28.14 candidate has passed `pass_for_next_validation` yet. Nothing can be frozen for fresh holdout.")
    else:
        labels = {
            f"{x['research_family']} · {x['source_run_name']} · {x['wf_id']}": x
            for x in candidates
        }
        selected_label = st.selectbox("Eligible walk-forward survivor", list(labels.keys()))
        selected = labels[selected_label]
        report = selected["report"]
        c1, c2, c3 = st.columns(3)
        c1.metric("V28.14 verdict", report.get("verdict", "—"))
        c2.metric("OOS trades", int((report.get("overall") or {}).get("trades") or 0))
        c3.metric("OOS PF", f"{float((report.get('overall') or {}).get('profit_factor') or 0):.2f}")
        if st.button("Freeze candidate for future holdout", type="primary"):
            try:
                record = build_freeze_record(
                    selected["source_run"],
                    report,
                    research_family=selected["research_family"],
                    now=datetime.now(timezone.utc),
                    policy=policy,
                )
                path = save_freeze_record(record)
                st.success(
                    f"Frozen: {record['freeze_id']} · first eligible date {record['first_eligible_date']} · "
                    f"fixed target end {record['target_end_date']} · saved to {path}"
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.subheader("2. Frozen candidates and future-data clock")
    freezes = list_freeze_records()
    if not freezes:
        st.caption("No freeze records exist yet.")
        with st.expander("Holdout policy"):
            st.json(policy)
        return

    overview = []
    readiness_by_id = {}
    for record in freezes:
        ready = holdout_readiness(record)
        readiness_by_id[str(record.get("freeze_id"))] = ready
        overview.append(
            {
                "freeze_id": record.get("freeze_id"),
                "family": record.get("research_family"),
                "strategy": record.get("strategy_name"),
                "cutoff_utc": record.get("cutoff_utc"),
                "holdout_start": record.get("first_eligible_date"),
                "target_end": record.get("target_end_date"),
                "observed_days": ready.get("observed_days"),
                "target_days": ready.get("target_days"),
                "remaining_days": ready.get("remaining_days"),
                "data_ready": ready.get("data_ready"),
                "ready_to_evaluate": ready.get("ready"),
                "integrity_valid": record.get("integrity_valid"),
            }
        )
    st.dataframe(pd.DataFrame(overview), width="stretch", hide_index=True)

    freeze_labels = {
        f"{r.get('research_family')} · {r.get('strategy_name')} · {r.get('freeze_id')}": r
        for r in freezes
    }
    selected_freeze_label = st.selectbox("Inspect frozen candidate", list(freeze_labels.keys()))
    record = freeze_labels[selected_freeze_label]
    freeze_id = str(record.get("freeze_id"))
    readiness = readiness_by_id[freeze_id]
    verification = verify_freeze_record(record)

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Observed full days", f"{readiness.get('observed_days', 0)}/{readiness.get('target_days', 0)}")
    a2.metric("Remaining", readiness.get("remaining_days", 0))
    a3.metric("Freeze integrity", "OK" if verification.get("valid") else "INVALID")
    a4.metric("Data ready", "YES" if readiness.get("data_ready") else "NO")
    st.write(readiness.get("reason"))

    data_status = readiness.get("data_status")
    if isinstance(data_status, pd.DataFrame) and not data_status.empty:
        with st.expander("Per-symbol data freshness"):
            st.dataframe(data_status, width="stretch", hide_index=True)

    if readiness.get("ready"):
        if st.button("Queue one frozen fresh-holdout evaluation"):
            result = queue_fresh_holdout(record, policy=policy)
            if result.get("queued"):
                st.success(f"Queued {result.get('tasks_created')} frozen tasks: {result.get('job_path')}")
                st.rerun()
            else:
                st.warning(result.get("reason") or "Holdout was not queued.")
    else:
        st.button("Queue one frozen fresh-holdout evaluation", disabled=True)
        st.caption("Evaluation stays disabled until the fixed future window is complete and local OHLCV is fresh through its end date.")

    st.subheader("3. Fresh holdout result")
    all_jobs = fresh_holdout_jobs(["queued", "running", "completed", "failed"])
    jobs_for_freeze = [j for j in all_jobs if str(j.get("freeze_id") or "") == freeze_id]
    if not jobs_for_freeze:
        st.caption("No V28.15 job exists for this freeze yet.")
    else:
        job_rows = []
        for job in jobs_for_freeze:
            progress = job.get("progress") or {}
            job_rows.append(
                {
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                    "created_at": job.get("created_at"),
                    "completed": progress.get("completed", 0),
                    "total": progress.get("total", 0),
                    "holdout_start": job.get("holdout_start"),
                    "holdout_end": job.get("holdout_end"),
                }
            )
        st.dataframe(pd.DataFrame(job_rows), width="stretch", hide_index=True)
        completed = [j for j in jobs_for_freeze if str(j.get("status")) == "completed"]
        if completed:
            report = evaluate_fresh_holdout(record, completed, policy=policy)
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Verdict", report.get("verdict", "—"))
            r2.metric("Fresh trades", int((report.get("overall") or {}).get("trades") or 0))
            r3.metric("Fresh expectancy R", f"{float((report.get('overall') or {}).get('expectancy_r') or 0):.3f}")
            r4.metric("Fresh PF", f"{float((report.get('overall') or {}).get('profit_factor') or 0):.2f}")
            st.write("Next step:", report.get("next_step"))
            if report.get("warnings"):
                st.warning(" | ".join(report.get("warnings") or []))
            tabs = st.tabs(["By symbol", "By side", "By month", "Integrity gates"])
            with tabs[0]:
                frame = report.get("by_symbol", pd.DataFrame())
                st.dataframe(frame, width="stretch", hide_index=True) if not frame.empty else st.caption("No symbol rows.")
            with tabs[1]:
                frame = report.get("by_side", pd.DataFrame())
                st.dataframe(frame, width="stretch", hide_index=True) if not frame.empty else st.caption("No side rows.")
            with tabs[2]:
                frame = report.get("by_month", pd.DataFrame())
                st.dataframe(frame, width="stretch", hide_index=True) if not frame.empty else st.caption("No month rows.")
            with tabs[3]:
                st.json(report.get("gates") or {})
            if st.button("Save fresh holdout snapshot"):
                paths = save_holdout_snapshot(report)
                st.success(f"Saved holdout evidence: {paths.get('summary')}")

    with st.expander("Frozen record"):
        st.json(record)
    with st.expander("Fresh holdout policy"):
        st.json(policy)
