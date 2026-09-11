# Local Trading Lab — Project Recap and Direction

_Last updated for V28.7._

## Source of truth

GitHub is the canonical project memory and code history. ChatGPT conversations are useful for reasoning and iteration, but project state should be stored in versioned repository files.

Repository:

```text
seargik/local-trading-lab
```

## Current status

- **V28.4**: repo-ready baseline, GitHub workflow, Codespaces/devcontainer, trend lifecycle scaffold.
- **V28.5**: demo mode, synthetic ETH/BTC/SOL sample data, lifecycle-to-strategy fit labels, remote testing docs.
- **V28.6**: historical OHLCV backfill engine and CLI for preloading local candle history and later updating only the fresh missing part.
- **V28.7**: Data / History Manager page, default history target set, project recap, decision log.

## Current architecture

```text
GitHub repo
  -> code, docs, smoke tests, PR review

Local/Codespaces runtime
  -> Streamlit UI
  -> collector/analyzer workers
  -> local parquet OHLCV store under data/ohlcv_store

Historical backfill
  -> Binance USD-M futures klines
  -> data/ohlcv_store monthly parquet partitions
  -> analyzer/backtest warm-starts from stored history
```

## Data policy

Historical market data is runtime data, not source code.

Do not commit generated parquet history to GitHub. The repository ignores `data/ohlcv_store/` and related generated backfill reports. Keep persistent history on the runtime where the app is used: local PC, Codespaces, VPS, or future server.

## Default V28.7 history target set

The default requested USDT pairs are:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, BNBUSDT, UNIUSDT, AAVEUSDT, XRPUSDT, TRXUSDT
```

Default intervals:

```text
1h, 4h
```

Default lookback:

```text
12mo
```

Rationale: 1h is the primary practical analysis/backtest timeframe; 4h gives higher-timeframe context without needing the app to wait for candles to accumulate.

## What is useful already

- Local OHLCV store.
- Historical backfill and update-only refresh.
- Demo mode for Codespaces/browser/mobile UI testing.
- Trend lifecycle classification.
- Strategy-fit labels: fit, caution, blocked, direction conflict.
- Backtest/replay foundation.
- Friction-aware validation foundation.
- Cross-validation/promotion direction.

## What is experimental

- Lifecycle labels are still soft evidence. They should not hard-block live/paper decisions until backtested.
- Bundle consensus is useful, but not yet proven as superior to focused single-family testing.
- LLM packets are review aids, not trading signals.
- Historical OHLCV is only candles, not complete market microstructure.

## What should not be trusted yet

- Any score threshold that has not been validated by pair, timeframe, side, and regime.
- Any strategy promoted only from one backtest window.
- Any live/paper signal without friction, slippage, and execution assumptions.
- Any market-state classification that has not been compared against out-of-sample results.

## Honest product direction

This should not be positioned as an AI trading bot yet.

Better positioning:

```text
A local crypto market-state and strategy-validation lab.
```

The core workflow should be:

```text
Historical data -> market lifecycle -> allowed strategy family -> backtest evidence -> paper validation -> possible live execution later
```

Not:

```text
many strategies -> many signals -> hope a universal score threshold works
```

## Recommended simplified product shape

The app should collapse toward three primary modules:

1. **Data / History** — coverage, backfill, update-only refresh, gap audit.
2. **Market State** — lifecycle, direction, allowed/blocked families.
3. **Strategy Lab** — backtest, calibration, promotion/rejection.

Everything else should support those three modules.

## Next roadmap

### V28.7

- Data / History Manager page.
- Project recap and decision log.
- Default 12-month backfill target set for BTC/ETH/SOL/LTC/BNB/UNI/AAVE/XRP/TRX.

### V28.8

- Lifecycle Gate Backtest Lab.
- Compare baseline trades vs lifecycle-fit-only trades vs direction-conflict blocking vs soft penalty.

### V28.9

- Mobile-first Market State dashboard.
- One-page view: pair, lifecycle state, direction, best allowed strategy family, latest history freshness.

### V29

Only consider paper/live execution changes after historical and paper validation show evidence.
