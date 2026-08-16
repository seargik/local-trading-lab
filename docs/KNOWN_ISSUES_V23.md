# Known issues — V23

## 1. Bundle engine is backtest-focused
Bundle consensus mode is available in Backtest Lab, but it is not yet fully wired into the live scanner / live execution path.

## 2. Comparison UI is improved, not final-perfect
Saved comparison is strong enough to use, but it is not yet the final polished side-by-side dashboard for every KPI and every graph.

## 3. Scanner history can still look incomplete
If the shared store is incomplete for the selected symbol/timeframe or if the chart is not requesting enough history, scanner plots may still look short.

## 4. Large backtests can still be slow
Very large windows can be slow when feature building and what-if reruns happen repeatedly.

## 5. Runtime fixes were already needed
Queue, comparison, and what-if flows already needed multiple fixes. New queue-related changes should be tested carefully.

## 6. Zero-trade runs must stay safe
Any branch that loads or compares empty runs must handle empty CSVs and empty summaries gracefully.

## 7. Port conflicts
The separate backtest app can conflict on port `8503`.

## 8. Execution realism is still incomplete
Several backtests used fixed `$100` stake and `0 fees`. Realistic fee/slippage/funding modeling still needs to be pushed further.
