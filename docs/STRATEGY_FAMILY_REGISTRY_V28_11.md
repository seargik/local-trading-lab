# V28.11 Strategy Family Registry

V28.11 replaces research-time name guessing with an explicit registry in `config/strategy_family_registry.json`.

The registry records, per bundled strategy:

- canonical strategy family;
- whether it belongs to the narrow research set;
- historical data required by that implementation;
- whether the current historical store can replay it honestly;
- research priority inside its family.

The first controlled families remain:

```text
trend_pullback
compression_breakout
range_reversion
```

Current important result: trend-pullback and range-reversion have OHLCV-only candidates, while the existing compression strategies require historical open-interest and/or order-book data. Therefore the default three-family batch is blocked rather than pretending an OHLCV-only replay is complete.

The new `Strategy Family Audit` page shows all latest saved strategies, their explicit family, historical requirements, and core-family readiness.

The Research Command Center now checks the registry before enabling the existing batch queue. A selected strategy must match its explicit family and have the required historical data available.

This version does not change live or paper execution logic.
