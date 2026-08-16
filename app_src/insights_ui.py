from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app_src.settings import OHLCV_STORE_ROOT

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_CONFIG_PATH = PROJECT_ROOT / 'config' / 'bundle_strategy_examples.json'
EXIT_CONFIG_PATH = PROJECT_ROOT / 'config' / 'exit_family_templates.json'
SAVED_RUNS_ROOT = PROJECT_ROOT / 'data' / 'backtests'
STRATEGY_DIR = PROJECT_ROOT / 'bundled_strategies'
ANALYSIS_REPORTS_DIR = PROJECT_ROOT / 'analysis_reports'
HANDOVER_MD_PATH = PROJECT_ROOT / 'docs' / 'HANDOVER_V23.md'
BABY_STEPS_MD_PATH = PROJECT_ROOT / 'docs' / 'BABY_STEPS_V23.md'
KNOWN_ISSUES_MD_PATH = PROJECT_ROOT / 'docs' / 'KNOWN_ISSUES_V23.md'
NEW_CHAT_PROMPT_PATH = PROJECT_ROOT / 'docs' / 'NEW_CHAT_PROMPT_V23.txt'


TOOLTIPS = {
    'bundle strategies': 'A bundle strategy combines several single strategies into one decision rule, such as all-pass or 2-of-3 consensus.',
    'symbol+direction+strategy concurrency logic': 'Direction-scoped concurrency means BTC long for Strategy A does not block BTC short for Strategy A, and bundle trades are tracked separately from single-strategy trades.',
    'better exit families': 'Exit families group strategies by trade behavior so you can tune TP1, runner size, and trail timing per archetype instead of using one exit design everywhere.',
    'stronger analytics by long vs short and by regime': 'These reports split trades into long-only, short-only, and regime buckets so one profitable side does not hide weakness on the other side.',
    'bundle strategy engine': 'The engine foundation is the bundle config and practice flow. Full live/backtest execution wiring can be layered on top of the same bundle definitions.',
    'exit-family refactor': 'This means storing different exit templates for trend, breakout, range, and reversal strategies, then testing those templates consistently.',
    'long/short split analytics': 'Saved runs are summarized separately for LONG and SHORT so you can see where edge really comes from.',
    'strategy validity audit': 'This audit checks whether a strategy can realistically reach its threshold, or whether its rule weights make it impossible or nearly impossible to trigger.',
    'score calibration upgrade': 'Score calibration studies whether higher scores actually lead to better outcomes, and whether scoring works differently by strategy or regime.'
}


def _badge(text: str) -> str:
    return f"<span style='padding:2px 8px;border-radius:999px;background:#eef2ff;border:1px solid #c7d2fe;font-size:0.85rem'>{text}</span>"


def _section_title(title: str) -> None:
    st.markdown(f"### {title}")
    if title.lower() in TOOLTIPS:
        st.caption(TOOLTIPS[title.lower()])


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _load_packet(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding='utf-8'))
    return raw.get('strategy') or raw


def _audit_rows() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(STRATEGY_DIR.glob('*.json')):
        try:
            packet = _load_packet(path)
            rules = packet.get('indicator_rules') or []
            threshold = float(packet.get('score_threshold') or (packet.get('rule_params') or {}).get('score_threshold') or 70)
            enabled = [r for r in rules if r and r.get('enabled', True)]
            long_weights = sum(float(r.get('weight') or 0) for r in enabled if str(r.get('bias') or 'BOTH').upper() == 'LONG')
            short_weights = sum(float(r.get('weight') or 0) for r in enabled if str(r.get('bias') or 'BOTH').upper() == 'SHORT')
            both_weights = sum(float(r.get('weight') or 0) for r in enabled if str(r.get('bias') or 'BOTH').upper() == 'BOTH')
            max_long = long_weights + both_weights / 2.0
            max_short = short_weights + both_weights / 2.0
            status = []
            if max_long < threshold:
                status.append('LONG_UNREACHABLE')
            if max_short < threshold:
                status.append('SHORT_UNREACHABLE')
            margin_long = round(max_long - threshold, 2)
            margin_short = round(max_short - threshold, 2)
            if 0 <= margin_long <= 5 or 0 <= margin_short <= 5:
                status.append('NEAR_PERFECT_CONFLUENCE')
            if not enabled:
                status.append('NO_ENABLED_RULES')
            rows.append({
                'strategy_name': packet.get('strategy_name') or path.stem,
                'template_key': packet.get('template_key'),
                'score_threshold': threshold,
                'enabled_rules': len(enabled),
                'max_long_score': round(max_long, 2),
                'max_short_score': round(max_short, 2),
                'margin_long': margin_long,
                'margin_short': margin_short,
                'status': ', '.join(status) if status else 'OK',
                'source_file': path.name,
            })
        except Exception as exc:
            rows.append({'strategy_name': path.stem, 'template_key': '', 'score_threshold': '', 'enabled_rules': '', 'max_long_score': '', 'max_short_score': '', 'margin_long': '', 'margin_short': '', 'status': f'LOAD_ERROR: {exc}', 'source_file': path.name})
    if not rows:
        return pd.DataFrame(columns=['strategy_name', 'template_key', 'score_threshold', 'enabled_rules', 'max_long_score', 'max_short_score', 'margin_long', 'margin_short', 'status', 'source_file'])
    return pd.DataFrame(rows).sort_values(['status', 'strategy_name'], ascending=[True, True])


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _build_saved_run_reports(saved_runs_root: Path) -> dict[str, pd.DataFrame]:
    manifests = []
    for manifest_path in saved_runs_root.glob('*/manifest.json'):
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['run_dir'] = str(manifest_path.parent)
            manifests.append(manifest)
        except Exception:
            continue
    meta_rows: list[dict[str, Any]] = []
    side_rows: list[dict[str, Any]] = []
    regime_rows: list[pd.DataFrame] = []
    decile_rows: list[pd.DataFrame] = []
    for manifest in manifests:
        run_dir = Path(manifest['run_dir'])
        trades = _safe_read_csv(run_dir / 'trades.csv')
        name = manifest.get('name') or run_dir.name
        strategy = ((manifest.get('strategy_payload') or {}) or {}).get('strategy_name') or 'Unknown'
        summary = manifest.get('summary') or {}
        config = manifest.get('config') or {}
        meta_rows.append({
            'run_name': name,
            'strategy_name': strategy,
            'symbols': ','.join(config.get('symbols') or []),
            'start_date': config.get('start_date'),
            'end_date': config.get('end_date'),
            'total_trades': summary.get('total_trades', 0),
            'profit_factor': summary.get('profit_factor', 0.0),
            'total_pnl_usd': summary.get('total_pnl_usd', 0.0),
            'win_rate': summary.get('win_rate', 0.0),
        })
        if trades.empty:
            continue
        if 'pnl_usd' not in trades.columns and 'pnl_pct' in trades.columns:
            trades['pnl_usd'] = pd.to_numeric(trades['pnl_pct'], errors='coerce').fillna(0.0)
        else:
            trades['pnl_usd'] = pd.to_numeric(trades.get('pnl_usd'), errors='coerce').fillna(0.0)
        trades['score'] = pd.to_numeric(trades.get('score'), errors='coerce')
        for side in ['LONG', 'SHORT']:
            subset = trades.loc[trades.get('side') == side].copy()
            pnl = subset['pnl_usd'] if not subset.empty else pd.Series(dtype=float)
            gross_profit = pnl[pnl > 0].sum() if not subset.empty else 0.0
            gross_loss = abs(pnl[pnl < 0].sum()) if not subset.empty else 0.0
            side_rows.append({
                'run_name': name,
                'strategy_name': strategy,
                'segment': side,
                'trades': int(len(subset)),
                'win_rate': round(float((pnl > 0).mean() * 100), 2) if not subset.empty else 0.0,
                'total_pnl_usd': round(float(pnl.sum()), 2) if not subset.empty else 0.0,
                'profit_factor': round(float(gross_profit / gross_loss), 4) if gross_loss else 0.0,
                'avg_score': round(float(subset['score'].mean()), 2) if not subset.empty and subset['score'].notna().any() else 0.0,
            })
        if 'regime' in trades.columns:
            reg = trades.groupby('regime', as_index=False).agg(
                trades=('regime', 'size'),
                total_pnl_usd=('pnl_usd', 'sum'),
                win_rate=('pnl_usd', lambda s: (s > 0).mean() * 100),
            )
            reg.insert(0, 'run_name', name)
            reg.insert(1, 'strategy_name', strategy)
            regime_rows.append(reg)
        work = trades.dropna(subset=['score']).copy()
        if not work.empty:
            bucket_count = max(1, min(10, len(work)))
            if bucket_count == 1:
                work['score_decile'] = 'D1'
            else:
                labels = [f'D{i}' for i in range(1, bucket_count + 1)]
                work['score_decile'] = pd.qcut(work['score'].rank(method='first'), q=bucket_count, labels=labels, duplicates='drop')
            dec = work.groupby('score_decile', as_index=False).agg(
                trades=('score', 'size'),
                avg_score=('score', 'mean'),
                total_pnl_usd=('pnl_usd', 'sum'),
                win_rate=('pnl_usd', lambda s: (s > 0).mean() * 100),
            )
            dec.insert(0, 'run_name', name)
            dec.insert(1, 'strategy_name', strategy)
            decile_rows.append(dec)
    overview = pd.DataFrame(meta_rows).sort_values(['profit_factor', 'total_pnl_usd'], ascending=[False, False]) if meta_rows else pd.DataFrame()
    long_short = pd.DataFrame(side_rows).sort_values(['run_name', 'segment']) if side_rows else pd.DataFrame()
    regime = pd.concat(regime_rows, ignore_index=True) if regime_rows else pd.DataFrame(columns=['run_name', 'strategy_name', 'regime', 'trades', 'total_pnl_usd', 'win_rate'])
    deciles = pd.concat(decile_rows, ignore_index=True) if decile_rows else pd.DataFrame(columns=['run_name', 'strategy_name', 'score_decile', 'trades', 'avg_score', 'total_pnl_usd', 'win_rate'])
    return {'overview': overview, 'long_short': long_short, 'regime': regime, 'deciles': deciles}


def _show_bundle_lab() -> None:
    _section_title('Bundle strategies')
    bundle_cfg = _load_json(BUNDLE_CONFIG_PATH)
    bundles = bundle_cfg.get('bundles') or []
    st.markdown('Use these presets to practice bundle design before full engine wiring. The same JSON structure is the basis for future live and backtest bundle execution.')
    if not bundles:
        st.warning('No bundle strategy examples found.')
        return
    names = [b.get('bundle_name', f'Bundle {i+1}') for i, b in enumerate(bundles)]
    selected = st.selectbox('Bundle preset', names, help=TOOLTIPS['bundle strategies'])
    bundle = bundles[names.index(selected)]
    top_cols = st.columns(4)
    top_cols[0].markdown(_badge(f"Mode: {bundle.get('mode', 'n/a')}"), unsafe_allow_html=True)
    top_cols[1].markdown(_badge(f"Symbols: {', '.join(bundle.get('symbols') or ['ALL'])}"), unsafe_allow_html=True)
    top_cols[2].markdown(_badge(f"Components: {len(bundle.get('components') or [])}"), unsafe_allow_html=True)
    top_cols[3].markdown(_badge(f"Concurrency: symbol + direction + bundle"), unsafe_allow_html=True)
    st.caption(bundle.get('notes') or '')
    comp_df = pd.DataFrame(bundle.get('components') or [])
    if not comp_df.empty:
        st.dataframe(comp_df, width='stretch', hide_index=True)
    st.code(json.dumps(bundle, indent=2), language='json')
    st.info('Practice tip: compare the component strategies first, then treat the bundle as a separate trade owner. A bundle long should not block a single-strategy long unless you deliberately design account-level risk caps.')


def _show_exit_families() -> None:
    _section_title('Better exit families')
    cfg = _load_json(EXIT_CONFIG_PATH)
    exit_families = (cfg.get('exit_families') or {}) if cfg else {}
    if not exit_families:
        st.warning('No exit family templates found.')
        return
    rows = []
    for key, val in exit_families.items():
        row = {'exit_family': key}
        row.update(val)
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    st.info('Baby step: map each strategy family to one exit family. Trend and pullback strategies usually want smaller TP1 fractions and more runner retention than range or reversal strategies.')


def _show_validity_audit() -> None:
    _section_title('Strategy validity audit')
    st.caption('This tells you whether a strategy can realistically fire at all, before you spend time tuning it.')
    audit_df = _audit_rows()
    if audit_df.empty:
        st.warning('No bundled strategies found to audit.')
        return
    status_filter = st.multiselect('Status filter', sorted(audit_df['status'].dropna().unique().tolist()), default=[])
    filtered = audit_df[audit_df['status'].isin(status_filter)] if status_filter else audit_df
    st.dataframe(filtered, width='stretch', hide_index=True)
    ANALYSIS_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(ANALYSIS_REPORTS_DIR / 'strategy_validity_audit.csv', index=False)
    st.caption(f'Saved latest audit to {ANALYSIS_REPORTS_DIR / "strategy_validity_audit.csv"}')


def _show_score_calibration() -> None:
    _section_title('Score calibration upgrade')
    st.caption('Build saved-run reports to see whether higher scores really behave better, and whether edge is long- or short-driven.')
    saved_root = Path(st.text_input('Saved runs root', value=str(SAVED_RUNS_ROOT), help='Folder that contains saved backtest runs, each with a manifest.json and trades.csv.')).expanduser()
    if not saved_root.exists():
        st.warning(f'Saved runs root not found: {saved_root}')
        return
    reports = _build_saved_run_reports(saved_root)
    overview = reports['overview']
    long_short = reports['long_short']
    regime = reports['regime']
    deciles = reports['deciles']
    tabs = st.tabs(['Overview', 'Long / Short split', 'Regime split', 'Score deciles'])
    with tabs[0]:
        st.dataframe(overview, width='stretch', hide_index=True)
    with tabs[1]:
        st.dataframe(long_short, width='stretch', hide_index=True)
    with tabs[2]:
        st.dataframe(regime, width='stretch', hide_index=True)
    with tabs[3]:
        st.dataframe(deciles, width='stretch', hide_index=True)
    ANALYSIS_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    overview.to_csv(ANALYSIS_REPORTS_DIR / 'saved_runs_overview.csv', index=False)
    long_short.to_csv(ANALYSIS_REPORTS_DIR / 'saved_runs_long_short.csv', index=False)
    regime.to_csv(ANALYSIS_REPORTS_DIR / 'saved_runs_regime.csv', index=False)
    deciles.to_csv(ANALYSIS_REPORTS_DIR / 'saved_runs_score_deciles.csv', index=False)
    st.caption(f'Reports written to {ANALYSIS_REPORTS_DIR}')


def _show_feature_tour() -> None:
    _section_title('Feature tour & baby steps')
    st.markdown('''
1. **Backtest Lab**: run one strategy on one symbol first, then queue multi-strategy or what-if jobs.
2. **Queue & comparison**: use the worker queue for long jobs; save runs and compare up to your chosen limit.
3. **Bundle strategies**: open the Bundle preset section, read the component list, then backtest those component strategies separately before promoting the bundle.
4. **Direction-scoped concurrency**: interpret `one_trade_at_time` as symbol + direction + strategy. A BTC long does not block a BTC short for the same strategy family in the upgraded design direction.
5. **Exit families**: assign each strategy to one of the exit templates before doing more TP / stop tuning.
6. **Validity audit**: find strategies that are structurally unreachable or too strict.
7. **Score calibration**: build long/short, regime, and decile reports from your saved runs.
8. **Scanner warm-start**: keep the shared store up to date, then restart the main app so scanner charts and live analysis can reuse history.
''')
    st.markdown('**Current shared store root:**')
    st.code(str(OHLCV_STORE_ROOT))
    st.info('This tab is your practice layer. It helps you understand and validate the new concepts before you promote them into live trading defaults.')


def render_foundation_toolkit_tab() -> None:
    st.subheader('Foundation Toolkit: bundles, exits, audit, calibration')
    toolkit_tabs = st.tabs([
        'Bundle Lab',
        'Exit Families',
        'Validity Audit',
        'Calibration Reports',
        'Feature Tour',
    ])
    with toolkit_tabs[0]:
        _show_bundle_lab()
        st.divider()
        _section_title('Symbol+direction+strategy trade concurrency logic')
        st.markdown('''
- A **single strategy trade owner** should be tracked as `symbol + direction + strategy`.
- A **bundle trade owner** should be tracked as `symbol + direction + bundle`.
- A bundle is **not blocked** just because one of its component strategies already has a trade.
- This lets you compare single-strategy and bundle behavior separately in signals, trades, and backtests.
''')
    with toolkit_tabs[1]:
        _show_exit_families()
    with toolkit_tabs[2]:
        _show_validity_audit()
    with toolkit_tabs[3]:
        _show_score_calibration()
        st.divider()
        _section_title('Long/short split analytics')
        st.markdown('Use the Long / Short split tab to verify whether a strategy is really balanced or whether recent performance is mostly coming from only one side of the market.')
        _section_title('Stronger analytics by long vs short and by regime')
        st.markdown('Use the Regime split and Score deciles tabs to see which strategies own trend, range, squeeze, or expansion conditions and whether the score is acting like a ranking signal.')
    with toolkit_tabs[4]:
        _show_feature_tour()


def _read_text_or_fallback(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return fallback


def render_handover_tab() -> None:
    st.subheader('Project handover and new-chat starter')
    st.caption('Use this tab to reopen the project in a new chat without losing architecture context.')
    top = st.columns(3)
    top[0].markdown(_badge('Package: V23'), unsafe_allow_html=True)
    top[1].markdown(_badge('Purpose: working handover build'), unsafe_allow_html=True)
    top[2].markdown(_badge('Best use: resume in new chat'), unsafe_allow_html=True)

    tabs = st.tabs(['Handover summary', 'Baby steps', 'Known issues', 'New-chat prompt'])
    with tabs[0]:
        st.markdown(_read_text_or_fallback(HANDOVER_MD_PATH, 'Handover doc not found.'))
    with tabs[1]:
        st.markdown(_read_text_or_fallback(BABY_STEPS_MD_PATH, 'Baby steps doc not found.'))
    with tabs[2]:
        st.markdown(_read_text_or_fallback(KNOWN_ISSUES_MD_PATH, 'Known issues doc not found.'))
    with tabs[3]:
        prompt = _read_text_or_fallback(NEW_CHAT_PROMPT_PATH, 'New-chat prompt not found.')
        st.code(prompt)
        st.info('Copy this prompt into a new chat and mention the V23 package plus the handover doc.')
