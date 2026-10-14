# BRAIN-AUDIT-0004 — Retest confirmation is attached to the earlier observation candle

- Severity: **P2** (misleading event timing / evidence attribution in chart UI).
- Status: **CONFIRMED** using actual detector output from a synthetic series.
- Component: `web/static/js/breakout_stages.js:291–342` (`firstRetest` versus `heldCount`); actual source facts produced by `pattern_liquidity/retests.py` and `web/chart_evidence.py::_event_item`.
- Affected commit: #62 `a0f1fafd7ed6841eaec14809c03b7556ff0949c4`; file unchanged by #65–#67.
- Scope: individual baseline, not integration-only. **No backend decision lookahead demonstrated**; the backend correctly keeps observation and confirmation separate.

## Expected / actual

Expected: the chart's Held event must expose the actual held evidence candle/known-at instant, or explicitly distinguish “first touch” from “later confirmed held.” Jump-to-event and metadata must not imply the earlier observation proved the later result.

Actual detector facts for one breakout:

- Observed: candle open **06:00**, known **07:00**, distance 0, elapsed 1.
- Held: candle open **07:00**, known **08:00**, distance 1, elapsed 2.

Actual stage: `Held`, green, summary “the retest held,” but anchored to **06:00**, price 109, metadata distance 0 / elapsed 1, and no confirmation time. It counts “2 retest records … 1 of 2 held,” although these are lifecycle facts for one retest, not two independent tests. The stage mixes later status with earlier price/time and clicking it selects the observation candle.

## Reproduction / evidence

```sh
.venv/bin/python docs/brain-audit/probes/retest-evidence.py > docs/brain-audit/evidence/retest-detector-input.json
node docs/brain-audit/probes/retest-anchor.mjs
```

No network/database. The Python script uses real pattern analysis and real chart projection, seeded by the existing delayed-confirmation fixture (explicitly SYNTHETIC, not market data). Both lifecycle records and complete production-stage output: `../evidence/retest-anchor-baseline.txt`.

## Acceptance

1. Distinguish observed time and confirmed outcome time explicitly.
2. Held/Failed stage must link metadata and selection to the corresponding outcome record, or clearly label first-touch anchoring and separately expose confirmation known-at.
3. Add delayed Held and delayed Failed tests, plus same-candle observation/confirmation control.
4. Preserve backend causality and detector thresholds; do not rewrite source records.
