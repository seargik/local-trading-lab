# Strategy Packs Overview

## Best starting combinations

### 1. Main recommended workflow
- Analysis TF: `1h`
- Chart TF: `5m`
- Strategies:
  - HTF Bias + LTF Pullback Entry
  - VWAP Reclaim Trend Continuation
  - Smart Money Sweep Reversal

Use this when you want cleaner swing/intraday continuation and reversal ideas.

### 2. Sideways / scalping test set
- Analysis TF: `15m`
- Chart TF: `1m` or `5m`
- Strategies:
  - Market Maker Range Scalper
  - Range Rotation With Midline Rejection
  - Mean Reversion

Use this when the market is choppy and you want fade/range ideas.

### 3. Breakout / squeeze test set
- Analysis TF: `15m` or `1h`
- Chart TF: `5m`
- Strategies:
  - Compression Release Scalper
  - Compression Breakout OI Expansion
  - Breakout Continuation Scout

Use this when volatility contracts and you want the expansion move.

### 4. SMC-style test set
- Analysis TF: `1h`
- Chart TF: `5m`
- Strategies:
  - Smart Money Sweep Reversal
  - SMC Continuation Reclaim
  - Failed Breakout Liquidity Sweep Fade

Use this when you want structure/liquidity-flavored logic instead of pure MA logic.

## Good first testing order

1. Start with only 2 strategies enabled
2. Let the app run for a few hours or one day
3. Review Signal Inbox
4. Review Trades
5. Export packets for AI review only after you have enough examples

## My practical advice

Start with:
- HTF Bias + LTF Pullback Entry
- Smart Money Sweep Reversal
- Market Maker Range Scalper

That gives you:
- one continuation packet
- one reversal packet
- one range/scalp packet

So you can compare 3 different market styles quickly.
