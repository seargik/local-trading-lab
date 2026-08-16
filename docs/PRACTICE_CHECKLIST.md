# Practice Checklist

## Daily / per-symbol review
- Check whether the best result was long-driven or short-driven.
- Check whether the strategy made money only in one regime.
- Check whether `edge_partial_vs_full_usd` is negative.
- Check whether the score deciles are actually monotonic.
- Check whether the strategy packet is structurally valid.

## Weekly improvement loop
1. Audit dormant packets.
2. Re-test core strategies with realistic fee presets.
3. Tune exit family for one archetype at a time.
4. Update bundle templates only after single-strategy stability is proven.
5. Keep a changelog of what was changed and why.

## Promotion rules
Promote a strategy to `core` only if:
- it survives recent-window testing
- it survives realistic friction assumptions
- it has acceptable drawdown
- it is not dependent on a tiny sample
- its score or regime filter actually adds value

## Repair rules
Keep a strategy in `diagnostic` if:
- threshold is unreachable
- it repeatedly returns zero trades
- its best result depends on extreme tuning only
- the edge disappears after simple friction assumptions
