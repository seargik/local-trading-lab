# V28.14 — Evidence to Walk-Forward Validation

## Purpose

V28.13 decides whether a completed core-family backtest deserves more validation. V28.14 takes only non-benchmark `cross_validation_candidate` results and tests whether the exact strategy payload remains useful when moved forward through chronological holdout windows.

The strategy payload is hashed with SHA-256 before queueing. Every saved V28.14 result is checked against that hash. If the payload changes during validation, the test is marked invalid.

## Default fold design

For a roughly 12-month source run, the default policy creates expanding training windows and non-overlapping two-month forward tests:

```text
Fold 1
train: months 1-6
 test: months 7-8

Fold 2
train: months 1-8
 test: months 9-10

Fold 3
train: months 1-10
 test: months 11-12
```

The training windows are historical sanity checks only. V28.14 does not retune score thresholds, stops, targets or strategy rules inside a fold.

## Important methodology caveat

V28.13 currently chooses candidates after looking at the full 12-month research run. Therefore the V28.14 two-month windows are useful tests of **temporal stability with frozen parameters**, but they are not a pristine untouched future holdout in the strict statistical sense.

A later fresh period that was never used for candidate selection is still required before paper/live promotion. The UI and saved reports surface this caveat explicitly.

## OOS tasks

Each fold queues one frozen strategy task per source symbol. For the first core protocol that means BTCUSDT, ETHUSDT and SOLUSDT.

The worker uses the same entry/analysis timeframes and the same execution-friction configuration as the source run. V28.14 only changes the test date window and adds metadata. It does not alter strategy parameters.

## Validation gates

The versioned policy lives in:

```text
config/walk_forward_policy.json
```

Default gates include:

- frozen-payload hash integrity;
- adequate expanding-window training evidence;
- adequate total OOS trade count;
- positive OOS PnL and expectancy;
- OOS profit factor threshold;
- majority of forward folds passing;
- majority of symbols passing;
- drawdown relative to OOS net profit;
- pair-transfer support.

Possible verdicts:

```text
pass_for_next_validation
mixed
insufficient_evidence
fail
invalid_test
```

A pass is still not a live-trading approval. The next stage remains stronger replay / fresh holdout / paper validation.

## Pair-transfer view

V28.14 reuses the source run's training trades and the forward-test results to ask questions such as:

```text
BTC historical edge -> does the same frozen strategy work on ETH/SOL forward windows?
ETH historical edge -> does it transfer to BTC/SOL?
SOL historical edge -> does it transfer to BTC/ETH?
```

No new model is trained per pair. This is a stability diagnostic: an anchor pair is eligible only when its training segment has enough trades and positive evidence, then the target pair's OOS segment is evaluated without parameter changes.

## Side stability

The completed report also groups OOS trades by LONG and SHORT. Side results are diagnostic rather than a hard promotion gate in V28.14, because some legitimate strategies may be structurally asymmetric.

## Runtime flow

```text
V28.12 controlled family batch
        -> V28.13 evidence scorecard
        -> cross_validation_candidate
        -> V28.14 frozen walk-forward queue
        -> backtest_worker
        -> fold / symbol / side / transfer scorecards
        -> pass, mixed, insufficient, fail or invalid
```

## Streamlit

Open:

```text
Walk-Forward Validation
```

The page can:

1. select a completed V28.13 evidence batch;
2. show only eligible non-benchmark candidates;
3. preview the expanding folds and historical training sanity checks;
4. queue the frozen forward tests;
5. inspect completed fold/symbol/side/pair-transfer evidence;
6. save a validation snapshot under `data/backtest_reviews/walk_forward_scorecards`.

## What remains frozen

V28.14 does not:

- change live execution;
- change paper-trading behavior;
- auto-promote a strategy;
- tune parameters between folds;
- use an LLM to decide pass/fail;
- claim same-history walk-forward is a pristine future holdout.
