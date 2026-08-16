# Strategy Capabilities Review — V28.4

This review is based on the packaged strategy JSON files in `bundled_strategies/` and the current app architecture. It is not a claim that any strategy is profitable live. Use it as a map for testing, backtesting, calibration, and lifecycle routing.

## Executive criticism

The strategy set is broad and ambitious. It covers trend continuation, HTF pullback, VWAP reclaim, compression breakout, range/reversion, liquidity sweep fade, order-book absorption, OI/funding exhaustion, SMC-style continuation, and a no-trade regime gate.

The strength is breadth: many market archetypes have a candidate strategy.

The weakness is operational clarity: without a market-state router, too many strategies can be simultaneously plausible. This creates three risks:

1. **Overlapping signals** — trend, VWAP, RSI, and HTF pullback can all fire on the same move.
2. **Regime mismatch** — range/reversion strategies can fight a real trend, while trend strategies can overtrade chop.
3. **False precision** — many strategies use a similar default score threshold around 70, but each strategy family needs its own calibration by side, symbol, friction, and regime.

V28.4 therefore introduces `app_src/trend_lifecycle.py` as a soft router. It does not delete or disable strategies. It classifies the current market phase and shows which strategy families fit that phase.

## Capability matrix

| Strategy | Family | Best market fit | Main strength | Main weakness / risk | Suggested lifecycle states |
|---|---|---|---|---|---|
| Compression Breakout + OI Expansion | Breakout / expansion | Compression then participation-led expansion | Catches early expansion legs | Fakeouts if compression breaks without follow-through | `compression_building`, `breakout_attempt`, `trend_entering` |
| Compression Release Scalper | Breakout / scalp | Tight low-volatility squeeze with first expansion | Fast momentum entries | Sensitive to fees/slippage and fake first breaks | `compression_building`, `breakout_attempt` |
| Elliott Wave Proxy Continuation | Trend continuation proxy | Impulse continuation after controlled retrace | Avoids literal wave counting while using impulse/retrace structure | Proxy can overfit and mislabel noisy retraces | `trend_pullback_entry`, `trend_running` |
| Failed Breakout / Liquidity Sweep Fade | Reversal / sweep fade | Stop-run or failed break with rejection | Good fit for fakeouts and sharp reversals | Dangerous against real breakouts | `liquidity_sweep_reversal_risk`, `trend_exhaustion`, `range_chop` |
| HTF Bias + LTF Pullback Entry | HTF continuation | Higher-timeframe trend with local pullback | Cleaner directional filtering | Can enter late if confirmation is too strict | `trend_pullback_entry`, `trend_entering` |
| HTF Pullback Continuation | HTF continuation | 1h/4h agreement and healthy retrace | One of the core trend/pullback archetypes | Misses early moves and can fail in transition/chop | `trend_pullback_entry`, `trend_running` |
| Market Maker Range Scalper | Range scalp | Neutral HTF, local range edge, one-sided liquidity | Captures smaller repeatable rotations | High friction sensitivity; dangerous in strong trends | `range_chop` |
| Mean Reversion Z-Score Reverter | Range / mean reversion | Statistical stretch with muted trend | Simple, explainable stretch/reversion logic | Fights strong trend if regime filter is weak | `range_chop`, `trend_exhaustion` |
| OI/Funding Exhaustion Reversal | Positioning reversal | Crowded perp positioning and stretched price | Uses derivatives context for unwind risk | OI/funding proxies may be incomplete/noisy | `trend_exhaustion`, `panic_volatility`, `liquidity_sweep_reversal_risk` |
| Order Book Absorption Reversal | Microstructure reversal | Stretched move with order-book pressure flip | Good idea for short-term snapbacks | Requires reliable order-book data; high noise | `trend_exhaustion`, `liquidity_sweep_reversal_risk` |
| Range Rotation With Midline Rejection | Range rotation | Non-trending market near range edge | Clear range-edge model | Should be blocked during strong trend | `range_chop` |
| Regime Filter / No-Trade Gate | Filter / observation | Low-quality mixed environment | Reduces forced trading | Must not become a universal blunt filter | `no_trade_data_missing`, `compression_building`, `panic_volatility` |
| RSI Best Practices Regime Trader | Momentum pullback / RSI regime | RSI pullback in trend regime | Uses RSI as context, not standalone | Needs separate long/short calibration | `trend_pullback_entry`, `trend_entering`, `range_chop` if filtered |
| Smart Money Sweep Reversal | SMC / sweep reversal | Liquidity grab + HTF-compatible reversal | Good for stop-hunt reversal thesis | SMC proxies are not true order-flow confirmation | `liquidity_sweep_reversal_risk`, `trend_exhaustion` |
| SMC Continuation Reclaim | SMC / continuation | Structure break then reclaim instead of chase | Reduces late impulse chasing | BOS/CHOCH/FVG/order-block are proxies, not full structure engine | `trend_entering`, `trend_pullback_entry` |
| Trend Following Alignment Rider | Trend following | Local + HTF + long-horizon alignment | Strong clean directional filter | Can enter late and give back profit without exit tuning | `trend_running`, `trend_pullback_entry` |
| VWAP Reclaim Trend Continuation | Fair-value continuation | Dominant trend plus VWAP reclaim after pullback | Practical trend continuation trigger | Bad in chop if VWAP is crossed repeatedly | `trend_pullback_entry`, `trend_entering` |

## What should improve next

### 1. Trend lifecycle router first

Before adding more strategies, classify the market phase:

- `compression_building`
- `breakout_attempt`
- `trend_entering`
- `trend_pullback_entry`
- `trend_running`
- `trend_extended_late`
- `trend_exhaustion`
- `range_chop`
- `liquidity_sweep_reversal_risk`
- `panic_volatility`

Then map strategy families to the phase.

### 2. Soft gate before hard gate

Do not block strategies immediately. First add evidence fields:

- `allowed_by_lifecycle`
- `lifecycle_state`
- `fit_reason`
- `suggested_exit_family`

Only later test hard gating in backtests.

### 3. Strategy-family calibration

Avoid one global score threshold. Calibrate by:

- strategy
- symbol
- side
- lifecycle state
- detailed regime
- friction preset

### 4. Reduce signal overlap

Trend continuation strategies can overlap heavily:

- HTF Pullback Continuation
- HTF Bias + LTF Pullback Entry
- Trend Following Alignment Rider
- VWAP Reclaim Trend Continuation
- RSI Best Practices Regime Trader

Overlap is not automatically bad, but the app should know whether overlap improves confidence or simply duplicates exposure.

### 5. Better execution realism for scalps

Range and scalper strategies need stricter friction testing than trend strategies.

## Recommended strategy usage by lifecycle

| Lifecycle | Prefer | Avoid / reduce |
|---|---|---|
| `compression_building` | Observe, prepare breakout plans | Premature trend entry, random range scalp |
| `breakout_attempt` | Compression Breakout, Compression Release, VWAP Reclaim | Fade unless failed breakout evidence appears |
| `trend_entering` | VWAP Reclaim, HTF Pullback, SMC Continuation | Mean reversion against trend |
| `trend_pullback_entry` | HTF Pullback, RSI Regime, VWAP Reclaim | Late breakout chase |
| `trend_running` | Trend Following, manage existing trades | New reversal without exhaustion |
| `trend_extended_late` | Protect profit, partials, exhaustion watch | New trend chase |
| `trend_exhaustion` | OI/Funding Exhaustion, Sweep Fade, Smart Money Sweep | Trend continuation unless pullback resets |
| `range_chop` | Range Rotation, Mean Reversion, Market Maker Range Scalper | Trend following unless breakout occurs |
| `liquidity_sweep_reversal_risk` | Sweep Fade, SMC Sweep Reversal | Breakout chase |
| `panic_volatility` | No-trade or defensive reversal only | Normal playbooks |

## Bottom line

The strategy library is advanced enough. The next advantage should come from **routing and selection**, not from adding more strategies. The app should answer:

> What phase is the market in now, and which strategy family is allowed to speak?
