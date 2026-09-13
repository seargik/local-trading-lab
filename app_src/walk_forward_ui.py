from __future__ import annotations

import pandas as pd
import streamlit as st

from .backtest_core import load_saved_backtest
from .evidence_review_v2813 import build_job_scorecard, completed_research_jobs, load_evidence_policy
from .walk_forward_v2814 import (
    build_anchored_walk_forward_folds,
    evaluate_walk_forward,
    load_walk_forward_policy,
    queue_walk_forward_validation,
    save_walk_forward_snapshot,
    training_fold_summary,
    walk_forward_jobs,
)


def _research_job_label(job: dict) -> str:
    created = str(job.get("created_at") or "")[:19].replace("T", " ")
    return f"{created} · {job.get('run_kind')} · {job.get('job_id')}"


def _walk_forward_index(jobs: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        wf_id = str(job.get("wf_id") or "unknown")
        grouped.setdefault(wf_id, []).append(job)
    for wf_id, items in grouped.items():
        statuses = [str(x.get("status") or "") for x in items]
        rows.append(
            {
                "wf_id": wf_id,
                "family": items[0].get("research_family"),
                "source_run": items[0].get("source_run_name"),
                "jobs": len(items),
                "completed_jobs": sum(1 for x in statuses if x == "completed"),
                "status": "completed" if statuses and all(x == "completed" for x in statuses) else ("failed" if any(x == "failed" for x in statuses) else "active"),
                "policy_version": items[0].get("policy_version"),
                "source_run_dir": items[0].get("source_run_dir"),
            }
        )
    return pd.DataFrame(rows)


def render_walk_forward_validation() -> None:
    st.title("Walk-Forward Validation")
    st.caption("V28.14 · frozen-payload chronological holdouts and pair-transfer stability")
    st.warning(
        "Method caveat: V28.13 selected candidates using the same 12-month research dataset. "
        "V28.14 therefore tests temporal stability with frozen parameters, but it is not a pristine untouched future holdout."
    )

    policy = load_walk_forward_policy()
    evidence_policy = load_evidence_policy()
    research_jobs = completed_research_jobs()

    st.subheader("Prepare validation from V28.13 evidence")
    if not research_jobs:
        st.info("No completed core research batch exists yet. V28.14 becomes actionable after V28.12/V28.13 has real saved results.")
    else:
        labels = {_research_job_label(job): job for job in research_jobs}
        selected_label = st.selectbox("Evidence batch", list(labels.keys()), index=0)
        research_job = labels[selected_label]
        scorecard = build_job_scorecard(research_job, policy=evidence_policy)
        candidates = scorecard[
            (scorecard.get("verdict", pd.Series(dtype=str)).astype(str) == "cross_validation_candidate")
            & (~scorecard.get("promotion_blocked", pd.Series(False, index=scorecard.index)).fillna(False).astype(bool))
        ].copy() if not scorecard.empty else pd.DataFrame()

        if candidates.empty:
            st.info("No non-benchmark cross-validation candidate exists in this batch. Nothing is queued automatically.")
        else:
            candidate_labels = {
                f"{row.get('research_family')} · {row.get('strategy_name')}": row
                for _, row in candidates.iterrows()
            }
            candidate_label = st.selectbox("Candidate", list(candidate_labels.keys()))
            row = candidate_labels[candidate_label]
            source_run = load_saved_backtest(str(row.get("run_dir") or ""))
            cfg = dict((source_run.get("manifest") or {}).get("config") or {})
            folds = build_anchored_walk_forward_folds(
                cfg.get("start_date"),
                cfg.get("end_date"),
                initial_train_months=int(policy.get("initial_train_months", 6)),
                test_months=int(policy.get("test_months", 2)),
                max_folds=int(policy.get("max_folds", 3)),
                min_train_days=int(policy.get("min_train_days", 90)),
                min_test_days=int(policy.get("min_test_days", 30)),
            )
            st.write("Planned expanding folds")
            st.dataframe(pd.DataFrame(folds), width="stretch", hide_index=True)
            train_preview = training_fold_summary(source_run, folds, policy=policy)
            if not train_preview.empty:
                st.write("Historical training-window sanity check")
                st.dataframe(train_preview, width="stretch", hide_index=True)

            if st.button("Queue frozen walk-forward validation", type="primary"):
                queued = queue_walk_forward_validation(
                    source_run,
                    research_family=str(row.get("research_family") or "unknown"),
                    policy=policy,
                )
                if queued.get("queued"):
                    st.success(
                        f"Queued {queued.get('jobs_created')} fold jobs / {queued.get('tasks_created')} OOS tasks. "
                        f"Validation id: {queued.get('wf_id')}"
                    )
                    st.dataframe(queued.get("preview", pd.DataFrame()), width="stretch", hide_index=True)
                else:
                    st.warning(str(queued.get("reason") or "Could not queue validation."))

    st.subheader("Walk-forward runs")
    all_jobs = walk_forward_jobs()
    index = _walk_forward_index(all_jobs)
    if index.empty:
        st.caption("No V28.14 jobs yet.")
    else:
        st.dataframe(index.drop(columns=["source_run_dir"], errors="ignore"), width="stretch", hide_index=True)
        completed = index[index["status"] == "completed"].copy()
        if not completed.empty:
            wf_id = st.selectbox("Inspect completed validation", completed["wf_id"].astype(str).tolist())
            selected = completed[completed["wf_id"].astype(str) == str(wf_id)].iloc[0]
            source_run = load_saved_backtest(str(selected.get("source_run_dir") or ""))
            report = evaluate_walk_forward(str(wf_id), all_jobs, source_run, policy=policy)
            overall = report.get("overall") or {}
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Verdict", str(report.get("verdict") or "—"))
            c2.metric("OOS trades", int(overall.get("trades") or 0))
            c3.metric("OOS expectancy R", f"{float(overall.get('expectancy_r') or 0):.3f}")
            c4.metric("OOS profit factor", f"{float(overall.get('profit_factor') or 0):.2f}")

            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Forward folds passing", f"{float(report.get('forward_fold_pass_share') or 0):.0%}")
            g2.metric("Symbols passing", f"{float(report.get('symbol_pass_share') or 0):.0%}")
            g3.metric("Pair-transfer support", f"{float(report.get('transfer_pass_share') or 0):.0%}")
            g4.metric("Integrity", "frozen" if report.get("frozen_payload_integrity") else "FAILED")

            warnings = report.get("warnings") or []
            if warnings:
                st.write("Warnings")
                for warning in warnings:
                    st.write("-", warning)
            st.write("Next step:", str(report.get("next_step") or "—"))

            tabs = st.tabs(["Gates", "Training folds", "Forward folds", "Symbols", "Sides", "Pair transfer"])
            with tabs[0]:
                gates = pd.DataFrame([{"gate": k, "passed": bool(v)} for k, v in (report.get("gates") or {}).items()])
                st.dataframe(gates, width="stretch", hide_index=True)
            with tabs[1]:
                st.dataframe(report.get("training_folds", pd.DataFrame()), width="stretch", hide_index=True)
            with tabs[2]:
                st.dataframe(report.get("forward_folds", pd.DataFrame()), width="stretch", hide_index=True)
            with tabs[3]:
                st.dataframe(report.get("symbols", pd.DataFrame()), width="stretch", hide_index=True)
            with tabs[4]:
                st.dataframe(report.get("sides", pd.DataFrame()), width="stretch", hide_index=True)
            with tabs[5]:
                st.dataframe(report.get("pair_transfer", pd.DataFrame()), width="stretch", hide_index=True)
                with st.expander("Pair-transfer fold detail"):
                    st.dataframe(report.get("pair_transfer_detail", pd.DataFrame()), width="stretch", hide_index=True)

            if st.button("Save V28.14 validation snapshot"):
                paths = save_walk_forward_snapshot(report)
                st.success(f"Saved {len(paths)} validation artifacts under data/backtest_reviews/walk_forward_scorecards.")

    with st.expander("Walk-forward policy"):
        st.json(policy)
