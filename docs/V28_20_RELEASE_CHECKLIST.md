# V28.20 Release Checklist

- [x] Adaptive economic policy is versioned.
- [x] Static-vs-adaptive comparison uses causal backward state joins.
- [x] Multi-symbol state joins are tested.
- [x] WAIT value reports avoided losses and missed profits.
- [x] Additional friction stress is reported.
- [x] Pair/month/family stability gates are explicit.
- [x] Trade concurrency is audited and disclosed as a capital-realism limitation.
- [x] V28.18 timing-integrity gate is enforced.
- [x] V28.19 source-data continuity gate is enforced.
- [x] Benchmark-only compression blocks unrestricted promotion.
- [x] Evidence snapshots fingerprint adaptive/router/replay policies.
- [x] No paper/live execution behavior is changed.
- [x] CI smoke suite covers causal joins, no future-state leakage, economic uplift, friction stress, benchmark blocking and snapshot integrity.

## Interpretation rule

An `adaptive_edge_candidate` is permission to run stronger validation only. It is not a profit forecast and it does not enable execution.

## Next methodological requirement

Build an exact shared-account adaptive replay before treating pooled adaptive PnL as deployable economics. The replay must arbitrate simultaneous candidates, enforce finite capital and portfolio risk, and produce one causal equity curve.
