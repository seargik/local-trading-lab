# Local Trading Lab — V28.4 Repo-Ready Baseline

This repository is a local-first crypto trading analysis and backtesting lab.

## Current baseline

Baseline source package: `V28.3 worker queue recovery` plus V28.4 repo-readiness and trend-lifecycle scaffold.

Locked project decisions remain unchanged:

- Crypto-only MVP.
- Local-first development.
- WebSocket-first live architecture, REST for bootstrap/backfill/gap repair/metadata.
- Shared historical working store: `data/ohlcv_store`.
- Separate backtest worker and separate Backtest Lab Streamlit window.
- Quant-first, LLM-second.
- Paper-first execution.
- Preserve strategies; improve and validate them instead of deleting weak ones.
- Single strategies and bundles are separate trade owners.

## Main entry points

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\app.py
.\start_backtest_worker.bat
.\start_backtest_only.bat
```

If port `8503` is busy:

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\backtest_app.py --server.port 8504
```

## Browser development

Use GitHub Codespaces for browser-based development and quick mobile checks.

The devcontainer forwards:

- `8501` main app
- `8503` Backtest Lab

## What is not committed

Local runtime data is intentionally ignored:

- `data/ohlcv_store/`
- `data/backtests/`
- `data/backtest_jobs/`
- `data/backtest_reviews/`
- `logs/*.log`
- `.venv/`
- `__pycache__/`

## New in V28.4

- Repo-ready `.gitignore`.
- GitHub Codespaces devcontainer.
- GitHub Actions smoke workflow.
- `README_CURRENT.md`.
- `docs/STRATEGY_CAPABILITIES_REVIEW.md`.
- `app_src/trend_lifecycle.py`.
- `config/trend_lifecycle_rules.json`.
- `tests/test_trend_lifecycle.py`.
- Main app tab: `Market State`.

## Smoke test

```powershell
.\.venv\Scripts\python.exe smoke_test_v28_4_repo_ready.py
```

Expected:

```text
V28.4 smoke test passed: repo-ready baseline and trend lifecycle scaffold are available.
```

## Suggested first Git push

```powershell
git init
git branch -M main
git add .
git commit -m "Initial import: V28.4 repo-ready local trading lab baseline"
git remote add origin https://github.com/seargik/local-trading-lab.git
git push -u origin main
git tag v28.4-repo-ready
git push origin v28.4-repo-ready
```
