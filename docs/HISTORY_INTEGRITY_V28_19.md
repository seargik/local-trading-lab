# V28.19 — Historical Data Integrity / Backfill Hardening

V28.19 hardens the local OHLCV history store before adaptive-router profitability research.

The objective is not to add another strategy. It is to make the candle history itself trustworthy enough that later research is not driven by partial candles, transient API failures, or silent internal gaps.

## What changed

### 1. Only fully closed Binance candles are stored

Binance can return the currently forming candle near the end of a backfill/update request. V28.19 compares each kline `close_time` with the allowed cutoff and drops rows that have not closed yet.

The result reports:

```text
discarded_unclosed_rows
```

Old stores may already contain a forming candle because earlier versions marked every downloaded kline as closed. V28.19 therefore also prunes rows whose `close_time` is still in the future or whose stored `is_closed` flag is false.

The result reports:

```text
pruned_unclosed_rows
```

An historical request ending in the past never deletes newer valid stored candles; pruning is based on the real current UTC time, not the requested historical end date.

### 2. Incremental refresh intentionally overlaps recent history

Previously `--update-only` started at the latest stored candle plus one interval. That was efficient but could preserve a stale final candle or fail to repair a recent boundary problem.

V28.19 refetches the latest stored candles before appending fresh data.

Default:

```text
overlap_bars = 2
```

Meaning:

```text
latest stored candle = T
refresh starts at     = T - 1 interval
```

so the two most recent stored candles are fetched again and safely deduplicated by `open_time`.

Use another value with:

```powershell
--overlap-bars 1
--overlap-bars 2
```

`--overlap-bars 0` restores the old behavior of starting after the latest stored candle.

### 3. Retry/backoff for transient Binance/API failures

Kline requests now retry transient network failures and retryable HTTP status codes:

```text
418
429
500
502
503
504
```

Defaults:

```text
max_retries = 4
retry_backoff_seconds = 0.5
```

The delay grows exponentially unless Binance supplies `Retry-After`.

The backfill report includes:

```text
retries_used
```

### 4. Internal continuity audit

After the main refresh, V28.19 checks the requested symbol/timeframe window for gaps where consecutive `open_time` values are more than about one expected interval apart.

The integrity report records:

```text
gaps_before
missing_rows_before
gaps_remaining
missing_rows_remaining
integrity_status
```

`integrity_status = ready` means that the stored rows inside the observed range are continuous and no unfinished rows remain. It does **not** replace the existing 12-month coverage/freshness requirement.

### 5. Automatic internal-gap repair

When an internal gap is found, the backfill makes a targeted Binance request for the missing time window, writes the returned candles through the normal parquet store, and audits continuity again.

Defaults:

```text
repair_gaps = true
max_gap_repairs = 50
```

The result records:

```text
repair_attempts
gaps_repaired
gaps_remaining
```

Disable automatic repairs while still keeping the audit with:

```powershell
--no-repair-gaps
```

### 6. Data / History Manager now shows continuity

The Streamlit coverage table now includes:

```text
continuity_ok
internal_gap_count
internal_missing_rows
unclosed_rows
```

A target is only shown as `ready` when the existing coverage/freshness rule passes **and** internal continuity is clean.

## Recommended commands

### First population

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo
```

This performs the initial load, closed-candle filtering, continuity audit and automatic internal-gap repair.

### Regular refresh

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --overlap-bars 2 --request-analysis
```

The regular refresh now means:

```text
prune any unfinished stored tail
-> refetch latest two candles
-> fetch newly closed candles
-> audit internal gaps
-> repair gaps when possible
-> write JSON integrity report
-> optionally request analysis
```

### More conservative network retry settings

```powershell
.\.venv\Scripts\python.exe backfill_default_history.py --lookback 12mo --update-only --max-retries 6 --retry-backoff-seconds 1
```

## Report output

Runtime reports remain outside Git under:

```text
data/backfill_reports/
```

Each symbol/timeframe result contains both download and integrity fields, including:

```text
fetched_rows
written_partitions
discarded_unclosed_rows
pruned_unclosed_rows
retries_used
overlap_bars
gaps_before
missing_rows_before
repair_attempts
gaps_repaired
gaps_remaining
missing_rows_remaining
integrity_status
```

If `integrity_status` is not `ready`, the CLI prints a warning and the unresolved counts remain in the JSON report.

## Important scope limits

V28.19 validates **OHLCV candle continuity**. It does not prove that every Binance candle is economically correct, and it does not create missing derivatives datasets.

Still separate:

```text
funding history
open-interest history
liquidations
order-book history
```

The current core historical protocol should continue to use only strategies whose declared historical inputs are actually available.

## Relationship to V28.18

V28.18 fixed the historical **information clock**:

```text
closed bar -> features -> signal
```

V28.19 strengthens the underlying **data itself**:

```text
closed candles
+ overlap refresh
+ retry/backoff
+ continuity audit
+ gap repair
```

Together the intended research foundation is now:

```text
trustworthy OHLCV history
-> closed-bar-only features
-> Market State Identifier
-> Adaptive Router
-> historical evidence
-> walk-forward
-> fresh future holdout
```

No paper or live execution behavior is changed by V28.19.
