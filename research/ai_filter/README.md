# BRAIN AI filter V1 — research-only scaffold

This directory is an offline, standalone prototype. It does not alter BRAIN simulation, strategy, planning, execution, databases, or live trading. It has **not** been fit on market history. Unit-test fixtures are synthetic and establish software behaviour only; no profitability or trading-performance claim is made.

## Model contract

`LogisticTakeRejectFilter` accepts exactly six signal-time features: `direction` (`LONG`/`SHORT`), `atr_pct`, `trend_strength_atr`, `directional_momentum24`, `relative_volume24`, and `rsi14`. Inputs must be finite numeric values (and the required direction); invalid inputs raise `FilterError`, which is a fail-closed refusal and must be treated as REJECT by any caller. It has no execution capability. `predict(features)` accepts no label or timestamp and returns a TAKE/REJECT decision, a logistic probability score, version, and model fingerprint. The score is a model probability estimate, **not empirically calibrated** absent a suitable calibration dataset.

`fit` takes `TrainingExample(features, net_r, signal_time, outcome_time)`; labels and outcome times are training-only. It validates that outcomes occur after signals and no training outcome exceeds the explicit timezone-aware `training_cutoff`. If `test_start` is supplied it must be strictly later than that cutoff. This provides a guardrail for a caller's chronological split; it does not supply or validate a separate held-out dataset. Labels are binarized as `net_r > 0`. Features are standardised on training data only. The optimizer is deterministic, full-batch L2-regularised logistic regression with a fixed iteration count and no random seed/RNG. The serialized JSON includes schema/model version and SHA-256 fingerprint; loading validates both.

## Reproduction

From repository root, with Python 3.11+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'pytest>=8,<9'
.venv/bin/python -m pytest -q research/ai_filter/test_filter.py
```

The model implementation itself uses only the Python standard library. Tests use pytest. The test fixtures are generated in `test_filter.py`; they are not historical trades and must never be presented as evidence of profitability. No model file or trained model is included.

## Current evidence and blockers

Demonstrated: 13 synthetic-fixture tests pass for deterministic fit/predict, invalid/missing/non-finite rejection, feature-only prediction, chronological cutoff rules, post-signal outcome timing, version/fingerprint, and score-preserving save/load plus tamper detection.

Not demonstrated: any model quality, calibrated probabilities, generalisation, profitability, or real-data performance. No historical CSV was accessed and no real model was trained. Before any real fit/evaluation, an authorised and verified dataset with precise signal/outcome timestamps, documented net-R labelling, provenance, and a leakage-safe chronological train/calibration/test plan must be supplied and reviewed. This scaffold does not read datasets, databases, exchange APIs, or sealed 2025 data.
