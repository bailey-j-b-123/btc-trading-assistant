# Evidence-Driven Cryptocurrency Trading Assistant

A foundation for an evidence-driven cryptocurrency analysis assistant. The intended purpose is to organize reliable market evidence and future analysis for human review.

**This is not an automated trading bot. It contains deterministic candidate setup definitions and a deterministic, read-only trade *planning* layer, but no order placement, backtesting, alerts, trade execution, AI/LLM features, or user interface. It does not make trading decisions or place/execute trades. QUALIFIED means rules satisfied, not a profitable trade or recommendation; PLANNABLE means a complete deterministic proposal was derived from a rule-qualified setup, not that a trade is profitable, advisable, or should be executed.** Numerical market facts are derived from source data and deterministic code; missing candles remain missing rather than being guessed or synthesized. The Step 3 market-structure engine is descriptive only: it reports measured structural facts (swings, trend, ranges, levels, volatility, volume) for human review and for later deterministic steps, and never emits a trade, signal, or recommendation. Step 4 adds deterministic pattern/liquidity events as evidence only, with explicit knowable timestamps. Step 5 combines those existing facts into auditable NO_SETUP, WATCH and QUALIFIED states. Step 6 converts only a *currently QUALIFIED* Step 5 candidate into a transparent, fully traceable proposed plan (entry, invalidation, stop, targets, unit-neutral R metrics) or an explicit refusal.

**Software calculates → rules qualify → statistics validate → AI explains → Bailey decides.** Statistics and AI remain future work; candidate performance is not established.

## Current architecture

```text
src/trading_assistant/
├── config.py                 Environment-backed runtime configuration
├── database/                 SQLAlchemy base and SQLite engine factory
├── logging_config.py         Structured JSON-lines logging
├── market_data/
│   ├── exchange.py           Credential-free CCXT public OHLCV adapter
│   ├── models.py             UTC candle model and exact decimal storage types
│   ├── raw_storage.py        Append-only raw CCXT response archive
│   ├── repository.py         Idempotent inserts and ordered retrieval
│   ├── service.py            Pagination, closed-candle filtering, orchestration
│   ├── timeframes.py         Fixed-interval and UTC conversion helpers
│   ├── types.py              Immutable candle/results types
│   └── validation.py         Parsing, validation, and gap reports
├── market_structure/         Deterministic structure engine (Step 3)
│   ├── swings.py             Confirmed swing highs/lows and confirmation timing
│   ├── trend.py              Structural trend from confirmed swings
│   ├── ranges.py             Consolidation/range rules and active flag
│   ├── levels.py             Support/resistance zone clustering evidence
│   ├── volatility.py         True Range and Wilder ATR
│   ├── volume.py             Rolling and relative volume context
│   ├── higher_timeframe.py   Higher-timeframe context from stored candles
│   ├── completeness.py       Candle-window completeness and gap reporting
│   ├── candles.py            Read-only candle window and as-of helpers
│   ├── parameters.py         Typed, validated calculation parameters
│   ├── analysis.py           Per-timeframe composition of all components
│   ├── snapshot.py           Typed snapshot and JSON projection
│   ├── service.py            Read-only snapshot service over Step 2 storage
│   └── numeric.py            Reproducible decimal arithmetic helpers
├── pattern_liquidity/        Deterministic evidence engine (Step 4)
│   ├── events.py             Immutable events, structural references and stable IDs
│   ├── parameters.py         Validated per-request detector rules
│   ├── references.py         Adapters for existing Step 3 evidence
│   ├── breakouts.py          Closed-candle breakout confirmation
│   ├── failures.py           Later whole-band breakout re-entry
│   ├── sweeps.py             Potential liquidity sweep evidence
│   ├── retests.py            Observed, held and failed retests
│   ├── equal_levels.py       Same-kind reuse of Step 3 zone clustering
│   ├── classical_patterns.py Four confirmed-swing geometries and state changes
│   ├── analysis.py           Gap-bounded chronological replay
│   ├── snapshot.py           Typed evidence catalog and JSON projection
│   └── service.py            Read-only snapshots and historical enumeration
├── setup_qualification/      Deterministic setup qualification (Step 5)
│   ├── models.py             Frozen frames, evidence, rule results and snapshots
│   ├── parameters.py         Validated thresholds and versioned fingerprints
│   ├── families.py           Three explicit seed/family/direction definitions
│   ├── rules.py              Required gates and optional supporting evidence
│   ├── engine.py             As-of lifecycle replay, invalidation and expiry
│   └── service.py            Read-only composition of existing Step 2–4 APIs
├── trade_planning/           Deterministic read-only trade planning (Step 6)
│   ├── models.py             Frozen plan states, traceable levels and results
│   ├── parameters.py         Validated planning rules and versioned fingerprints
│   ├── levels.py             Family-specific level derivation from frozen evidence
│   └── planner.py            Rule pipeline, R math, plan identity, refusals
├── journaling/               Reserved; no journal functionality implemented
├── statistics/               Reserved; no statistical analysis implemented
└── ai_explanation/           Reserved; no AI/LLM feature implemented

migrations/                   Explicit Alembic schema migrations
data/raw/                      Protected raw-response archive
data/processed/                Protected location for future derived datasets
backups/                       Protected local backup location
```

## Installation and configuration

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

`.env` is optional and ignored by Git. Never put credentials in `.env.example` or commit them. Public OHLCV requests use CCXT without API credentials or trading permissions.

The instrument remains configurable with `TRADING_ASSISTANT_SYMBOL`, `TRADING_ASSISTANT_BASE_ASSET`, and `TRADING_ASSISTANT_QUOTE_ASSET` (defaults: `BTC/USDT`, `BTC`, `USDT`). The default local database URL remains `sqlite:///data/trading_assistant.sqlite3`. The default exchange is `kraken`, configurable with `TRADING_ASSISTANT_EXCHANGE`. Initial configured timeframes are `5m`, `15m`, `1h`, `4h`, and `1d`; `TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES` and `TRADING_ASSISTANT_DEFAULT_TIMEFRAME` can be changed. Fixed-duration seconds/minutes/hours/days/weeks are supported by the timeframe parser, so the configured set can be extended without treating the initial list as exhaustive. The raw archive root is configurable with `TRADING_ASSISTANT_RAW_DATA_DIR` and defaults to `data/raw/`.

## Market-data behavior

### Downloading and incremental updates

Run `alembic upgrade head` before using the database-backed service. For an initial historical download, pass an explicit timezone-aware UTC start time. A later `update_history` call starts after the latest stored candle; without existing history, it requires an explicit start time. Pagination uses CCXT's `since` and configured page limit, advancing by the requested timeframe. Empty or truncated exchange responses are reported through gaps; pagination stalls and exchange/network errors fail clearly.

Example from the project root after installation and migration:

```python
from datetime import UTC, datetime

from trading_assistant.database import create_database_engine
from trading_assistant.market_data import create_market_data_service

engine = create_database_engine()
with create_market_data_service(engine) as market_data:
    initial = market_data.download_history(
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        timeframe="1h",
    )
    print(initial.complete, initial.inserted_count, initial.missing_candle_count)

    update = market_data.update_history(timeframe="1h")
    print(update.complete, update.inserted_count)

engine.dispose()
```

`download_history` and `update_history` return counts, raw-file paths, and any gaps. To retry a reported gap, request that aligned historical range explicitly; unchanged candles are skipped and only absent identities are inserted. Stored candles can be retrieved with `get_candles(exchange=..., symbol=..., timeframe=..., start_time=..., end_time=...)`; the returned `CandleQueryResult.candles` are chronologically ordered, while `.gaps`, `.missing_candle_count`, and `.complete` make incomplete ranges explicit. Optional time bounds are inclusive. Without explicit bounds, retrieval can assess only gaps between stored endpoints; completeness cannot be inferred beyond the range available from the configured exchange.

### Raw source and processed/database storage

Each page returned by CCXT is archived separately under `data/raw/<exchange>/<symbol>/<timeframe>/`. Filenames and JSON metadata identify the exchange, symbol, timeframe, request cursor, and retrieval time. The archive includes the CCXT OHLCV page and, when exposed by CCXT, its HTTP response text. Files are created exclusively: an existing raw file is never overwritten or silently removed. Raw files remain separate from the processed candle table and are ignored by Git.

Validated closed candles are stored in SQLite via SQLAlchemy. The candle identity is `(exchange, symbol, timeframe, UTC open time)`, enforced by the database primary key. Price and volume values are parsed as `Decimal` and persisted as base-10 text rather than SQLite floating-point values. Repeated identical candles are skipped; if the exchange returns different values for a previously stored key, the update fails and the existing history is left unchanged.

### Validation and closed candles

Only candles whose full timeframe interval has ended at the service's UTC `as_of` time are eligible for historical storage. The current/forming candle is archived in the source response but excluded from the processed/database records. Validation reports malformed/missing values, duplicate or unaligned timestamps, out-of-order rows, invalid OHLC relationships, negative prices/volume, and missing/gapped candle intervals. Invalid rows fail the update before database writes. Gaps make the result `complete=False` and are logged/reported; **missing candles are never fabricated, interpolated, or presented as complete**. Previously stored history remains untouched after exchange, validation, or database conflicts/failures.

Configure structured JSON-lines logging with:

```python
from trading_assistant.logging_config import configure_logging

configure_logging()
```

The default log level is `INFO`; use `TRADING_ASSISTANT_LOG_LEVEL` to override it. Logs include request, validation, gap, and storage counts but never raw payloads or credentials.

## Market-structure engine (Step 3)

`trading_assistant.market_structure` derives deterministic structural facts from the validated, closed Step 2 candles. Everything is calculated by code from stored candles; nothing is guessed, generated by a model, or inferred from non-price data. The engine contains no trading execution, setup qualification, pattern signals, backtesting, alerts, or AI/LLM logic.

### Guarantees and the anti-lookahead rule

Every public entry point requires an explicit UTC `as_of` instant (`MarketStructureService.snapshot`, `MarketStructureService.timeframe_analysis`, `analyze_candles` and the individual component functions). A naive datetime raises `ValueError`; a service instance without an explicit `as_of` uses an injectable UTC clock (default `datetime.now(UTC)`).

Two independent mechanisms enforce that only knowable information is used:

1. **Candle selection.** A candle opened at `t` on timeframe `T` only becomes knowable at `t + duration(T)`. The service fetches stored candles with an inclusive end bound of `latest_closed_candle_open_time(as_of, T)` (the Step 2 helper), and every component additionally filters its input to candles fully closed at `as_of`.
2. **Swing confirmation.** A swing is only returned when its confirming candle had fully closed by `as_of` (`confirmed_at <= as_of`). Swings detected in a longer candle set but not yet confirmable are excluded and counted in `unconfirmed_count` / `excluded_unconfirmed_swing_count`.

Consequences, each covered by tests:

- Analyzing a full history with `as_of = T` equals analyzing only the prefix available at `T` (`excluded_future_candle_count` reports how many input candles were dropped).
- A historical snapshot for `T` is byte-identical whether it is calculated at `T` or recomputed later from a database that has since received many more candles, and it is identical to the same calculation performed against a database that never contained the later candles.
- Insertion order into the database does not affect results, and no calculation writes to `ohlcv_candles` or creates any derived table.

When a component function is called directly with a candle set that extends past `as_of` (a convenience for callers that already hold a full frame), the structural outputs are still identical to the prefix-limited call, while diagnostics that describe the supplied window — such as `candle_count`, `evaluated_candidate_count`, or `gap_window_count` — describe the input that was actually passed. The service always passes the `as_of`-limited window, so whole snapshots are identical in every field.

All datetimes in results are timezone-aware UTC. Derived means, ratios, and percentages are rounded with `ROUND_HALF_EVEN` to a fixed 8-decimal scale inside an explicit 28-digit decimal context, so identical candle data always produces identical values on any machine.

### Swings (`swings.py`)

- **Parameters:** `SwingParameters(left_window=2, right_window=2, tie_policy="strict")`.
- **Rule:** a candle is a swing high when its high is the extreme of the symmetric window spanning `left_window` candles before it and `right_window` candles after it, under the configured tie policy; a swing low is the mirrored test on lows. A candle can in principle be both a high and a low if it is the extreme both ways; the high is then emitted first.
- **Confirmation timing:** a swing is confirmed only once *every* right-window candle has closed. `confirmed_at` is the close instant of the right-window edge candle (`edge candle open time + timeframe duration`) and `confirmed_by_timestamp` is that candle's open time. Nothing may consume a swing before `confirmed_at`.
- **Equal highs/lows (tie policy).**
  - `strict` (default): the candle's high must be *strictly* greater than every other high in the window (low strictly lower). If another window candle shares the extreme value, no swing is reported for any candle of that tied group. Every candidate evaluation rejected this way is counted in `tie_rejection_count`.
  - `earliest_equal`: a candle qualifies when it attains the window extreme and no *earlier* candle in the same window attains it. The earliest candle of an equal plateau is therefore the single reported swing.
- **Gaps:** a candidate whose window contains a missing candle (neighbouring timestamps not exactly one timeframe interval apart) is skipped and counted in `gap_window_count`. Structure is never inferred across missing data, and no candle is fabricated to complete a window.
- **Diagnostics:** `candle_count`, `required_candle_count` (`left + right + 1`), `sufficient`, `evaluated_candidate_count`, `insufficient_window_count` (candidates too close to the start/end of the available window), `gap_window_count`, `tie_rejection_count`, `unconfirmed_count`.
- **Known limitation:** swings inside the first `left_window` and last `right_window` candles of the available history can never be evaluated; the sweeps are reported explicitly rather than padded.

### Structural trend (`trend.py`)

- **Parameters:** `TrendParameters(swing_count=2)` — how many of the most recent confirmed swings *per side* are compared (minimum 2, i.e. at least one comparison per side).
- **Rule:** take the most recent `swing_count` confirmed swing highs and the most recent `swing_count` confirmed swing lows, ordered by candle time, and require strict monotonicity across every consecutive pair on both sides.
  - bullish: highs strictly rising **and** lows strictly rising → `higher_highs_and_higher_lows`.
  - bearish: highs strictly falling **and** lows strictly falling → `lower_highs_and_lower_lows`.
  - neutral with `equal_extremes` when both sequences are flat (consecutive equals).
  - neutral with `conflicting_structure` for any other mix (for example rising highs with falling lows, or a single equal step inside an otherwise rising sequence).
  - neutral with `insufficient_swings` when either side has fewer than `swing_count` confirmed swings.
- **Evidence:** the result carries the exact highs and lows used (`swing_highs`, `swing_lows`, `evidence`), the `higher_highs` / `higher_lows` / `lower_highs` / `lower_lows` / `equal_highs` / `equal_lows` booleans (`None` when not evaluable), `confirmed_swing_count`, and `excluded_unconfirmed_swing_count`. A trend is never forced.
- **Known limitation:** the classification describes the most recent swing comparisons only. It is not a breakout or "structure broken" model and does not consider candle closes relative to the swings.

### Range detection (`ranges.py`)

- **Parameters:** `RangeParameters(lookback_candles=120, min_touches_per_side=2, tolerance_pct=1, max_width_pct=10, min_span_candles=20, active_max_candles_since_last_touch=40)`.
- **Rule (single deterministic candidate per analysis).** Restrict to the last `lookback_candles` intervals; take the confirmed swing highs/lows inside that window; set `range_high` to the highest swing high and `range_low` to the lowest swing low. The candidate is only reported when **all** of the following hold, checked in this order:
  1. at least `min_touches_per_side` swing highs and swing lows exist in the window (`insufficient_swings`);
  2. `range_high > range_low` (`inverted_bounds`);
  3. at least `min_touches_per_side` swing highs lie within `tolerance_pct` percent of `range_high`, and the same for swing lows at `range_low` (`insufficient_touches`); the counts are reported as `upper_touch_count` / `lower_touch_count`;
  4. the first-to-last qualifying touch spans at least `min_span_candles` candles (`span_too_short`);
  5. `width_pct = (range_high - range_low) / range_high * 100` is at most `max_width_pct` (`width_exceeds_max`).
- **Active flag:** `active` is `True` only when the latest closed candle's close is inside `[range_low, range_high]`, no closed candle since `start_timestamp` closed outside the band by more than `tolerance_pct` (`band_broken`, `closes_outside_band_count`), and the most recent qualifying touch is at most `active_max_candles_since_last_touch` candles old (`candles_since_last_touch`). A detected but inactive range is still returned with `active=False`; `analysis.active_range` / `snapshot.active_range` is `None` in that case.
- **Evidence:** `range_high`, `range_low`, `width`, `width_pct`, `start_timestamp` (first qualifying touch), `end_timestamp` (last qualifying touch), `current_timestamp` (latest closed candle), both touch timestamp tuples and counts, `touch_count`, `candles_since_last_touch`, `close_within_bounds`, `band_broken`, `active`, the parameters used, and `as_of`.
- **Known limitation:** a single far outlier swing inside the window becomes the boundary and can prevent detection (there is no boundary fitting or optimisation, by design). A detected-but-inactive range is retained as historical evidence even after price leaves it.

### Support/resistance zones (`levels.py`)

- **Parameters:** `LevelParameters(lookback_swings=40, tolerance_pct=1, min_touches=1, max_zones=12)`.
- **Clustering:** take the most recent `lookback_swings` confirmed swings in chronological order. Each swing joins the *nearest* existing cluster whose anchor price — the first swing that created the cluster — is within `tolerance_pct` percent of the swing's price; ties in distance go to the earliest-created cluster. Otherwise the swing starts a new cluster. Clusters with fewer than `min_touches` swings are discarded and counted in `discarded_below_min_touches`.
- **Zone evidence:** `band_low`, `band_high`, `center`, `band_width`, `band_width_pct`, `touch_count`, `high_source_count`, `low_source_count`, `first_observed_timestamp`, `last_tested_timestamp`, `source_swing_timestamps`, `role`, `latest_close`, `distance_pct_from_latest_close`, and the tolerance used. `tested_as_support` / `tested_as_resistance` expose whether low/high swings were observed in the zone.
- **Role:** `role` is purely positional and measurable: `support` when the zone center is below the latest closed price, `resistance` when above, `at_price` when exactly equal or when no latest close was supplied. No subjective strength label exists — strength must be read from `touch_count`, band width, timestamps, and distance.
- **Ordering and cap:** zones are ordered deterministically by `touch_count` descending, then `last_tested_timestamp` descending, then center ascending, and truncated to `max_zones` (`discarded_beyond_max_zones` reports the truncation). Near-identical levels are merged by the tolerance rule instead of being emitted separately.

### Volatility (`volatility.py`)

- **Parameters:** `VolatilityParameters(period=14)`.
- **True Range:** `max(high - low, |high - previous close|, |low - previous close|)` for each candle after the first.
- **ATR smoothing (documented):** Wilder's recursive moving average (`wilder_rma` is reported as `smoothing`). The first ATR is the simple mean of the first `period` True Ranges; each later True Range updates it as `previous + (true_range - previous) / period`. It is *not* a simple moving average.
- **ATR percentage:** `atr_percent_of_price = atr / latest closed close * 100`.
- **Insufficient data:** ATR requires `period + 1` closed candles. Otherwise `available=False`, `reason="insufficient_candles"`, `atr=None`, and `required_candle_count` / `candle_count` are reported. The requested lookback is never shortened. `latest_true_range` is still reported whenever at least two closed candles exist, because it needs no lookback.

### Volume context (`volume.py`)

- **Parameters:** `VolumeParameters(period=20)`.
- **Fields:** `current_volume` (latest closed candle), `rolling_average_volume` (simple mean of the last `period` closed candles, including the latest), `reference_average_volume` (simple mean of the `period` closed candles *before* the latest), and `relative_volume = current_volume / reference_average_volume` with the documented basis string `latest_volume_over_prior_period_average`.
- **Sufficient/insufficient status:** `sufficient` is `True` only when `period + 1` closed candles exist. Every field whose own window is unavailable is `None` (`relative_volume` needs `period + 1` candles; `rolling_average_volume` needs `period`), and `reason="insufficient_candles"` is set when the full request cannot be satisfied. Lookbacks are never silently shortened.
- Only ordinary OHLCV volume is used. The engine makes no buyer/seller intent, order-flow, or participation claim.

### Higher-timeframe context (`higher_timeframe.py`)

- The higher timeframes for a request are either `parameters.higher_timeframes` (validated to be configured supported timeframes that are strictly longer than the requested timeframe) or, by default, every longer fixed-duration timeframe in `TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES`, sorted by duration.
- Each higher timeframe is read **independently** from its own stored Step 2 candles at the same `as_of` (same inclusive end bound), then analyzed with the same components and parameters. Higher-timeframe candles are never resampled, aggregated, or synthesized from lower-timeframe data — `synthesized` is always `False` and no such code path exists.
- `HigherTimeframeContext` always carries `completeness`, so missing data is explicit:
  - `available=False, reason="no_stored_candles"` when nothing is stored for that timeframe;
  - `available=False, reason="insufficient_candles"` when fewer than one swing window (`left + right + 1`) of candles exist;
  - `available=True, reason="incomplete_data"` when the timeframe is analyzed but its window contains gaps or is missing trailing candles;
  - `available=True, reason=None` only for a fully complete window.

### Snapshot (`snapshot.py`, `service.py`)

`MarketStructureService(engine, settings=..., clock=...)` reads stored candles only. `snapshot(exchange=None, symbol=None, timeframe=None, as_of=None, parameters=None)` resolves defaults from configuration (`exchange`, `symbol`, `default_timeframe`) exactly like Step 2, and `timeframe_analysis(...)` returns just the per-timeframe analysis.

`MarketStructureSnapshot` exposes, for one exchange/symbol/timeframe/`as_of`: `latest_closed_candle`, `swings`, `swing_detection` diagnostics, `trend`, `detected_range` and `active_range`, `zones`, `volatility`, `volume`, `higher_timeframes`, `completeness` (gaps, missing counts, window bounds, expected latest closed open time) and the `parameters` used. `to_json_dict()` projects the whole snapshot onto JSON-safe values (UTC ISO-8601 strings, exact decimal strings, enum string values) with one documented key per section.

`latest_closed_candle` is the newest stored candle whose interval had closed by `as_of`. When candles are missing at the end of the window it is therefore older than `completeness.expected_latest_closed_open_time`, and `completeness.missing_candles_after_latest_stored` (plus `gaps` from Step 2) states exactly how much is missing. Nothing is fabricated to fill the gap.

### Insufficient-data summary

| Component | Requirement | Result when unavailable |
| --- | --- | --- |
| Swings | `left + right + 1` closed candles | empty `swings`, `sufficient=False`, counts of skipped windows |
| Trend | `swing_count` confirmed swings per side | `neutral` with reason `insufficient_swings`, flags `None` |
| Range | `min_touches_per_side` swing highs and lows in the lookback | no range plus the exact `RangeRejectionReason` and touch counts |
| Zones | at least one confirmed swing | empty `zones`, `sufficient=False` |
| Volatility | `period + 1` closed candles | `available=False`, `reason="insufficient_candles"`, `atr=None` |
| Volume | `period + 1` closed candles | `sufficient=False`, `reason="insufficient_candles"`, unavailable fields `None` |
| Higher timeframe | one swing window of that timeframe's own candles | `available=False` with `no_stored_candles` / `insufficient_candles` |
| Data window | no internal gaps and no trailing gap | `completeness.complete=False`, explicit gaps and counts |

### Parameters reference

| Parameter | Default | Meaning |
| --- | --- | --- |
| `swings.left_window` / `swings.right_window` | `2` / `2` | Candles required left/right of a swing candidate |
| `swings.tie_policy` | `strict` | Equal-extreme handling (`strict` or `earliest_equal`) |
| `trend.swing_count` | `2` | Most recent confirmed swings per side compared |
| `ranges.lookback_candles` | `120` | Intervals considered for the range candidate |
| `ranges.min_touches_per_side` | `2` | Required qualifying touches at each boundary |
| `ranges.tolerance_pct` | `1` (percent) | Distance from a boundary that still counts as a touch |
| `ranges.max_width_pct` | `10` (percent) | Maximum `width_pct` for a detected range |
| `ranges.min_span_candles` | `20` | Minimum candle span between first and last qualifying touch |
| `ranges.active_max_candles_since_last_touch` | `40` | Recency required for `active=True` |
| `levels.lookback_swings` | `40` | Most recent confirmed swings clustered into zones |
| `levels.tolerance_pct` | `1` (percent) | Clustering tolerance around each cluster anchor |
| `levels.min_touches` | `1` | Minimum swings for a reported zone |
| `levels.max_zones` | `12` | Maximum zones returned |
| `volatility.period` | `14` | Wilder ATR period |
| `volume.period` | `20` | Rolling/reference volume window |
| `higher_timeframes` | `None` | Explicit higher timeframes; `None` derives them from configuration |

Percent parameters are percent values (`1` means one percent), and `width_pct` / `band_width_pct` / `atr_percent_of_price` are reported as percent values.

Structure parameters are supplied per request as typed `MarketStructureParameters` objects rather than through environment variables, so every result records the exact parameters it used. The only structure-related configuration is the shared Step 1/2 configuration: `exchange`, `symbol`, `default_timeframe`, and `supported_timeframes` (which determines the default higher-timeframe list).

### Example

```python
from datetime import UTC, datetime

from trading_assistant.database import create_database_engine
from trading_assistant.market_structure import (
    MarketStructureParameters,
    RangeParameters,
    create_market_structure_service,
)

engine = create_database_engine()
service = create_market_structure_service(engine)
snapshot = service.snapshot(
    timeframe="1h",
    as_of=datetime(2025, 1, 1, tzinfo=UTC),
    parameters=MarketStructureParameters(ranges=RangeParameters(min_touches_per_side=3)),
)
print(snapshot.trend.direction, snapshot.trend.reason)
print(snapshot.latest_closed_candle, snapshot.complete, snapshot.missing_candle_count)
for context in snapshot.higher_timeframes:
    print(context.timeframe, context.available, context.reason, context.trend)
engine.dispose()
```

### Known limitations

- Structure is derived only from closed candles that exist in the database. Missing candles are reported, never interpolated, and no structure is inferred across a gap.
- Swing detection is a window-extremum rule: it is not a ZigZag/percentage-threshold algorithm, and equal extremes are resolved by the documented tie policy (the default `strict` policy can suppress a swing in a flat-topped plateau).
- Trend classification uses only the most recent `swing_count` swings per side; it has no concept of broken structure, break of structure, or liquidity sweeps.
- Range detection evaluates one conservative candidate per analysis from the extremes of the lookback window, so a single outlier swing can prevent detection; it also does not track historical breakout events beyond the documented close-based `band_broken` test.
- Support/resistance zones are pure swing clusters; zones formed by more than `lookback_swings` swings, or clusters wider than the tolerance, are not represented, and no volume-at-price evidence is used.
- Higher-timeframe structure depends on separately downloaded candles for that timeframe; if you never stored `4h` data, the `4h` context will honestly report `no_stored_candles` instead of resampling `1h` candles.
- Stability sets such as `complete=False` are point-in-time observations of the retrieved window; a "complete" window only means no internal or trailing gap was detected between the earliest stored candle and the expected latest closed candle.

## Database initialization and migrations

Database creation and schema changes remain explicit. From the project root:

```bash
alembic upgrade head
```

The Step 1 foundation revision is unchanged. The Step 2 OHLCV migration adds only the candle table. It does not delete or recreate a database or alter other tables. Its downgrade refuses to drop the candle table if historical candles exist; use a reviewed forward migration for schema corrections. Back up local data before schema changes. There is no reset function, and application startup does not run migrations implicitly.

**Step 3 adds no schema change at all.** Market structure is derived in memory on every request, so no new tables, columns, or migration revisions were introduced. The engine only reads the `ohlcv_candles` table created by Step 2 (verified by a test asserting the table set is unchanged after structure calculations).

## Tests

The full test suite uses mocked exchange responses and temporary SQLite/raw-data locations; it does not contact an exchange or modify project-persistent data.

```bash
python -m pytest
python -m pytest tests/test_market_data.py
python -m pytest tests/test_market_structure.py tests/test_market_structure_service.py
```

`tests/test_market_structure.py` exercises the pure calculations with deterministic synthetic fixtures (`tests/market_structure_fixtures.py`), including swing confirmation timing, equal-extreme tie policies, gap-blocked windows, trend classification, range acceptance/rejection, zone clustering, Wilder ATR, and volume windows. `tests/test_market_structure_service.py` exercises the read-only service against temporary SQLite databases: snapshot contents, higher-timeframe contexts, gap/tail propagation, insertion-order independence, source-record immutability, and the anti-lookahead guarantees below.

## Step 4 — deterministic pattern & liquidity evidence

**These detections are market evidence, NOT trade recommendations.** A chart
shape is a feature, not a standalone signal. No execution, setup qualification,
entries, stops, targets, R:R, profitability, win/loss labels, AI/LLM logic,
alerts, backtesting or UI is included. Step 5 has not been started.

### API and evidence catalog

```python
from datetime import UTC, datetime
from trading_assistant.database import create_database_engine
from trading_assistant.pattern_liquidity import (
    PatternLiquidityParameters,
    PatternLiquidityService,
)

engine = create_database_engine()
service = PatternLiquidityService(engine)
snapshot = service.snapshot(
    exchange="binance", symbol="BTC/USDT", timeframe="1h",
    as_of=datetime(2025, 1, 1, tzinfo=UTC),
    parameters=PatternLiquidityParameters(breakout_confirmation_candles=2),
)
json_ready = snapshot.to_json_dict()  # Decimal strings and UTC ISO timestamps
occurrences = service.enumerate_events(
    exchange="binance", symbol="BTC/USDT", timeframe="1h",
    as_of=datetime(2025, 1, 1, tzinfo=UTC),
    known_since=datetime(2024, 12, 1, tzinfo=UTC),
)
engine.dispose()
```

Exchange, symbol, timeframe and aware `as_of` are required; there is no implicit
wall clock or hardcoded instrument. Aware times normalize to UTC using Step 2.
The pure `analyze_patterns(candles, exchange=..., symbol=..., timeframe=...,
as_of=...)` API uses the same replay without database I/O. Both APIs accept
`structure_parameters=MarketStructureParameters(...)`; Step 3 defaults apply
otherwise. The service reads **all stored history through the last closed
candle**, not just a short recent lookback. `known_since` filters occurrences
inclusively *after* replay so structural warmup is preserved.

Immutable typed dataclasses record:

- `breakouts`, `failed_breakouts`, `sweeps`, `retests`, `equal_levels`,
  `chart_patterns` — chronological tuples, with ties sorted by stable event ID;
- `structure` — reused Step 3 single-timeframe analysis of the final contiguous
  segment, including its parameters, swings, trend, zones, range, ATR and volume;
- `completeness` — Step 2 gap report and Step 3 completeness summary for the
  entire retrieved window, including trailing missing candles;
- `parameters`, `as_of`, instrument, `status` and machine-readable `reasons`.

`snapshot.events()` merges all families into chronological occurrences. Pattern
state transitions and retest outcomes are separate records, **not revisions**
to earlier evidence. Group chart records by `pattern_id`, and retests/failures by
`breakout.id`. Same-time records are ordered by ID, not by an assumed intrabar
sequence. There is no claim about the order of a candle's high and low.

### Timing, reference freezing and anti-lookahead

A candle with open timestamp `t` and fixed interval `I` is usable only at
`t + I <= as_of`. The replay evaluates each candle against Step 3 structure
known at its **open**, using only the preceding closed prefix. Multi-candle
breakout candidates freeze that reference for their entire confirmation window.
Every detector is bounded by this replay's explicit UTC time. Component helper
functions are internal replay operations, not alternative unbounded public APIs.

Swing highs are upward references, swing lows downward references. Zones can be
crossed in either direction. Active ranges supply separate upper/lower boundary
references, with their original Step 3 range evidence attached. Reference bands
are Step 3's actual source-price bands, not newly calculated padded zones.
Each reference records its source swings and, where applicable, zone/range.
Zones and swings representing the same price remain **distinct structural
references**, not independent votes or a confidence score.

Both the read API and pure API exclude future candles before calculating the
snapshot context. A database containing valid Step 2 data only through time T
and one containing that same data plus future candles produce identical Step 4
snapshots at T, including diagnostics and IDs. Pattern formation, swing
confirmation and event confirmation are deliberately separate times. This is
an event-time guarantee; it does not model late data arrival or subsequent
backfills into *past* gaps. Changing past inputs can change derived results.

### Exact event definitions

All percentages are percent values (`0.1` means 0.1%, not 10%). Price tolerances
use Step 3's `tolerance_band`; percentages use its eight-decimal derived rounding
and ratios use its deterministic Decimal division.

**Breakout:** the previous closed close must be at or inside the relevant outer
boundary. For bullish events, each of N consecutive closed candles must close
**strictly above** `band_high + tolerance`; bearish closes must be **strictly
below** `band_low - tolerance`. Equality at the threshold is not confirmation.
A close already slightly outside the raw boundary does not seed a fresh crossing.
A wick alone never confirms a breakout. `candle` is the first beyond-threshold
candle, `confirmation_candles` contains all N, and `known_at` is the final close
time. `breakout_close` and penetration use that final confirming close;
`candle.close` preserves the original crossing close. Penetration is distance
beyond the relevant outer boundary; percentage divides by that boundary price.
ATR-relative penetration is distance / Step 3 ATR at confirmation, or `None`
when unavailable/zero. Step 3 volatility/volume objects retain availability
reasons. `previous_candle` makes the crossing precondition independently visible.
Repeated closes outside a reference are not repeated breakouts; a fresh return
inside and subsequent crossing can produce another occurrence.

**Failed breakout:** within the configured number of candles **after the
breakout became knowable**, the first later closed close must re-enter through
the **whole** frozen band: bullish failure is below `band_low - reentry tolerance`,
bearish failure above `band_high + reentry tolerance`. Threshold equality does
not fail. This conservative rule is stronger than merely closing back inside a
wide zone. Evidence includes the immutable original breakout, all post-confirmation
candles through failure, re-entry distance, elapsed candles and elapsed seconds
(measured from breakout confirmation). Expiration means unknown, not success.
Future failure never appears in the original breakout record.

**Potential liquidity sweep:** the prior close must be at/inside the reference.
For `above`, the high must penetrate strictly beyond `band_high + penetration
tolerance`, and the same candle must close at/below `band_high - reclaim tolerance`.
For `below`, the low must penetrate strictly below `band_low - penetration
tolerance` and close at/above `band_low + reclaim tolerance`. Reclaim equality
is accepted. Confirmation is only at this candle's close. Evidence includes
extreme, reclaim close, penetration distance/percentage/ATR ratio, previous
candle, reference and Step 3 volume/ATR context. For a given reference/direction
on a given candle, the sweep reclaim and breakout close conditions are mutually
exclusive. These are **liquidity references**, not proof that actual stop orders
or institutional liquidity existed there. Equal-level clusters are cataloged
separately; sweeps use existing Step 3 swings/zones/ranges, not a duplicate
cluster-reference hierarchy.

**Retest:** only candles whose open is at/after breakout confirmation are eligible.
The first closed candle whose `[low, high]` intersects the frozen band expanded
by retest tolerance emits `observed`. On that candle or a subsequent closed candle
within the window, `held` requires a close strictly outside the breakout-side
boundary by hold tolerance; `failed` requires the same whole-band re-entry rule
as failed breakouts. Both observations and outcomes retain the entire
post-breakout candle sequence used. Until an outcome exists, only `observed` is
reported. A candle can establish observed+held or observed+failed at its close;
this is not intrabar forecasting. Only the first retest sequence and its first
terminal outcome are recorded per breakout. A failure before any band visit
terminates retest tracking without inventing a retest. A held retest does not
prevent a later failed-breakout event. Windows are inclusive candle counts;
because evaluation is contiguous, they also enforce elapsed-time limits of
`window * timeframe interval`.

**Equal highs/lows:** reuse Step 3 `detect_zones` separately for confirmed highs
and lows. Its chronological greedy rule joins the nearest eligible first-member
anchor within percentage tolerance (earliest-created anchor breaks ties). This
is anchored, not transitive chaining or pairwise equality: two members on opposite
sides of an anchor can be up to twice the tolerance apart. At least
`equal_min_members` of the most recent `equal_lookback_swings` *per kind* are
required. Band, mean center, exact swings, member count and parameters are
recorded. Each distinct membership set has its own immutable occurrence ID;
`first_known_at` and `known_at` are that set's first discovery time (equal for
this immutable version). `first_member_confirmed_at` and
`latest_member_confirmed_at` separately expose member timing. Usually discovery
coincides with the newest member's confirmation, but rolling-lookback eviction
can regroup older swings: that new group is recorded **now**, never backdated
to its older members. Expansion does not rewrite the earlier smaller set.

**Classical patterns:** only consecutive, strictly time-ordered, alternating
**confirmed Step 3 swings** are used. No synthetic ZigZag, partial/unconfirmed
swings, preceding-trend inference or subjective visual scoring is added.

- Double top: high–low–high; double bottom: low–high–low. The two outside
  extremes must differ by no more than geometry tolerance as a percentage of
  the first extreme. The middle swing price is the horizontal neckline.
- Head and shoulders: high–low–high–low–high; inverse: low–high–low–high–low.
  Outside shoulders satisfy the same equality test. The central head must
  exceed **both** shoulders in the appropriate direction by at least head
  prominence (percentage of mean shoulder price). The two neckline swings
  must differ by no more than geometry tolerance (percentage of their mean).
  The neckline is their mean. Sloping-neckline variants are intentionally absent.
- For either family, the smallest signed distance from any outer-side extreme
  to any neckline-side swing, as a percentage of the neckline, must be at least
  minimum depth. First-to-last component span must not exceed the configured
  candle limit. Geometric comparisons accept exact tolerance/depth boundaries.
- `formation_timestamp` is the last component swing's open time; `formed_at`
  and the `formed` event's `known_at` are the latest component confirmation time.
  Merely having geometry does **not** confirm the pattern. Partial `forming`
  candidates are intentionally not emitted.
- On a candle **opening at/after `formed_at`**, a top requires a close strictly
  below neckline minus neckline tolerance; a bottom requires a close strictly
  above neckline plus tolerance. `confirmed` records that close time separately
  as `confirmation_timestamp`. Earlier neckline crossings, including one during
  the final swing's confirmation window, are not retroactively confirmations.
- Before confirmation, a top close strictly above its highest component extreme
  plus invalidation tolerance (bottom: below its lowest minus tolerance) emits
  `invalidated`. The first confirmation or invalidation is terminal. Confirmation
  does not mean success; post-confirmation outcomes are outside this initial
  pattern lifecycle. Geometry and source/confirming candles are retained.

### All Step 4 defaults

| Parameter | Default | Meaning |
| --- | --- | --- |
| `breakout_tolerance_pct` | `0.1` | Strict outside-close threshold |
| `breakout_confirmation_candles` | `1` | Consecutive qualifying closed candles |
| `failure_window_candles` | `10` | Maximum closed candles after breakout confirmation |
| `failure_reentry_pct` | `0.1` | Whole-band failure margin, also used for retest failure |
| `sweep_penetration_pct` | `0.1` | Strict extreme penetration margin |
| `sweep_reclaim_pct` | `0` | Required close reclaim margin; equality accepted |
| `retest_window_candles` | `10` | Maximum closed candles after breakout confirmation |
| `retest_tolerance_pct` | `0.2` | Band expansion for first retest observation |
| `retest_hold_pct` | `0.1` | Strict breakout-side closing margin for held |
| `equal_tolerance_pct` | `0.2` | Step 3 clustering tolerance, separately per swing kind |
| `equal_min_members` | `2` | Minimum confirmed members |
| `equal_lookback_swings` | `40` | Per-kind clustering lookback |
| `pattern_tolerance_pct` | `0.5` | Peak/shoulder equality and H&S neckline flatness |
| `pattern_min_depth_pct` | `1` | Minimum shape depth relative to neckline |
| `head_min_prominence_pct` | `1` | Minimum head distance beyond both shoulders |
| `neckline_tolerance_pct` | `0.1` | Strict closed-candle confirmation margin |
| `pattern_invalidation_pct` | `0.1` | Strict opposite-side invalidation margin |
| `pattern_max_span_candles` | `120` | Maximum first-to-last component span |

All percentage values must be finite and in `[0, 100)`; equal tolerance, pattern
depth and head prominence must be positive. Counts must be positive integers,
not booleans/floats; equal minimum membership must be between two and its
lookback. These request-scoped validated parameters are recorded with every
occurrence. Defaults require confirmed swings, closed-candle evidence and
nonzero breakout/shape margins; they are **not optimized for profitability**.

### Identity, history safety and incomplete data

IDs are versioned SHA-256 hashes of canonical JSON structural keys: instrument,
reference kind and source swings/bands as appropriate, event type/direction and
crossing timestamp. Related failures/retests derive IDs from the original
breakout ID and state. Pattern occurrences use component swings and pattern type;
state IDs derive from `pattern_id`. Source timestamps, prices and swing confirmation
parameters are part of the evidence keys. No random UUIDs or process hash values
are used. Presentation-time fields such as zone `as_of`/role are deliberately not
identity keys. Different Step 4 parameter runs may share an underlying event ID;
store the parameter configuration alongside any externally saved catalog rather
than merging different configurations as though they were one run.

History is derived/in-memory only. There is **no migration or event table**, no
write to OHLCV, and no alteration of Step 3 source calculations. Enumeration
records market events and transitions, not trades or winning/losing outcomes.

Step 2 validation calculates gaps; Step 3 helpers calculate completeness. Missing
candles are never filled. At every internal gap, pending confirmations, live
breakouts/retests/patterns and the structural segment are reset. Pre-gap recorded
facts remain in history. Post-gap detectors must rebuild their evidence from
fresh contiguous candles. Thus **no candidate can bridge a gap**, even if a price
jumps over a historical boundary. Unresolved pre-gap outcomes remain unknown;
missing evidence is not a failure or success. A trailing gap marks the snapshot
incomplete without fabricating more events. Invalid source OHLCV is rejected,
not silently repaired or dropped. Step 4 requires strictly positive OHLC prices
for percentage geometry; zero-price candles (permitted in Step 2 storage) produce
a clear unsupported-input error without modifying the source.

`status` is `insufficient` for empty/short/no-reference contiguous history,
`incomplete` for a nonempty history with missing candles, otherwise `evaluated`.
Reasons include `no_stored_candles`, `insufficient_contiguous_candles`,
`no_confirmed_structural_references`, and `gaps_reset_detector_state` (which also
indicates unknown coverage at a trailing gap). Empty families do not assert
that an event was impossible: they mean no qualifying occurrence was observed
in the available history under the recorded rules. ATR/volume retain their own
Step 3 insufficient/unknown states. Coverage begins at the earliest stored
candle; missing history before that point is not knowable.

### Limitations and verification

- Replay favors auditability over throughput: it repeatedly invokes existing
  Step 3 calculations and retains evidence, so long histories can be expensive
  in CPU and memory. There is no cache, incremental checkpoint or persistent
  event store yet. Query narrower *stored datasets* when evaluating large histories;
  `known_since` does not reduce warmup cost.
- Only one timeframe is evaluated per call. No higher-timeframe feature was
  duplicated; obtain Step 3 higher-timeframe context from its existing service
  if needed. `structure_parameters.higher_timeframes` does not cause resampling
  or higher-timeframe evaluation in this single-timeframe engine.
- Only horizontal, alternating-swing chart geometries are supported. There are
  no time-symmetry rules, sloping necklines, volume confirmation gates, partial
  patterns, candlestick pattern library, or claims about actual order placement.
- Breakout/sweep references can be correlated. The catalog does not aggregate
  them into confidence, qualification, or profitability scores.
- Gaps intentionally discard potentially useful older references rather than
  silently connecting uncertain evidence. Inserted/backfilled *past* candles
  can legitimately change a replay; future candles cannot.

Offline tests: `tests/test_pattern_liquidity.py` and
`tests/test_pattern_liquidity_service.py` cover directional events, strict
boundaries, multi-close confirmation, retest states/windows, all four geometries,
negative near-matches, formation/confirmation/invalidation timing, deterministic
IDs/order, every-prefix anti-lookahead, temporary-database future insertion,
gap resets, event enumeration, unchanged OHLCV and unchanged Step 3 behavior.

## Step 5 — deterministic setup qualification

**QUALIFIED means “rules satisfied,” NOT “profitable trade.”** These are three
candidate definitions to measure later, not recommendations. There are no
confidence scores, orders, execution, authentication, sizing, entry optimization,
stops, targets, P&L, profitability backtests, AI explanations, alerts or UI.
Steps 1–4 and their source facts are unchanged. No derived data is persisted,
no schema is added, and no Step 6 functionality is implemented.

### Inputs, API and replay contract

The pure API consumes `QualificationFrame` objects, each containing an existing
Step 4 `PatternLiquiditySnapshot` and optionally a tuple of existing Step 3
`HigherTimeframeContext` objects. It uses the Step 3 contiguous-segment structure
already inside the Step 4 snapshot. It does **not** calculate swings, ranges,
ATR, volume, patterns, sweeps or retests again. Price comparisons below are
qualification rules over existing closes and frozen references, not new detectors.

```python
from trading_assistant.setup_qualification import (
    QualificationFrame,
    QualificationParameters,
    QualificationService,
    enumerate_qualifications,
    qualify,
)

parameters = QualificationParameters(
    continuation_max_bars=10,
    reversal_max_bars=10,
    range_max_bars=10,
    min_relative_volume="1",
    max_atr_percent="10",
    higher_timeframes=("4h",),
    require_higher_timeframe_alignment=True,
)

# source_frames contains one real historical Step 4 snapshot per base close,
# plus same-as-of Step 3 contexts for the requested higher timeframes.
# Each frame is QualificationFrame(step4_snapshot, higher_timeframe_contexts).
latest = qualify(source_frames, as_of=closed_at, parameters=parameters)
history = enumerate_qualifications(
    source_frames, as_of=closed_at, parameters=parameters,
    known_since=report_start,  # inclusive, applied AFTER full lifecycle replay
)

# Optional convenience adapter over an existing migrated Step 2 database engine.
# This reads locally stored candles only, never an exchange or network.
service = QualificationService(engine)
snapshot = service.snapshot(
    exchange="kraken", symbol="ETH/USD", timeframe="1h",
    as_of=closed_at, parameters=parameters,
)
json_safe = snapshot.to_json_dict()
```

`closed_at` and frame `as_of` values must be timezone-aware UTC instants on
**base-timeframe candle-close boundaries**. Intrabar evaluation is intentionally
unsupported. The service enumerates every expected close from the first stored
candle through `as_of`, including missing-candle slots. It delegates historical
analysis to the existing Step 3–4 implementations, rather than implementing a
second set of detectors. `enumerate_snapshots` takes the same arguments plus
inclusive `known_since`. Both service methods also accept `pattern_parameters`
and `structure_parameters` for the existing upstream engines.

Pure replay requires strictly chronological, unique frames for one
exchange/symbol/base timeframe, ending exactly at `as_of`. It never extrapolates
from stale/latest context. Start at or before every seed confirmation you want
to examine: **old events in the first frame's catalog do not create retrospective
candidates**. Use full history for complete enumeration. Skipped frames terminate
existing candidates; they do not fabricate intermediate transitions. New events
confirmed at the current close can still start new candidates.

### Exact family definitions

A seed is a confirmed Step 4 event whose `known_at` equals the current frame's
`as_of`. It creates one deterministic candidate per family/seed event. A seed
alone can only create WATCH: `later_evaluation` requires a strictly later close.
There is no arbitrary minimum-factor score; **every required rule must pass**.

| Family | WATCH seed and direction | Family-specific REQUIRED confirmation |
| --- | --- | --- |
| `breakout_retest_continuation` | `Breakout`; same direction as breakout | `held_retest`: a later Step 4 `Retest(state="held")` referencing exactly the seed breakout ID. An observed-only retest is pending. |
| `failed_breakout_sweep_reversal` | `FailedBreakout` at a non-range reference, opposite its breakout; or non-range `Sweep`, bearish for `above`, bullish for `below` | `reversal_breakout`: a later confirmed Step 4 breakout in the reversal direction at a **different reference ID**. No breakout or only an earlier/same-time/same-reference one leaves this pending. |
| `range_rejection_reversal` | Failed breakout or sweep at a frozen `range_high`/`range_low` reference; same reversal direction mapping | `active_range`: the current Step 3 active range must have exactly the frozen seed range's low and high. `range_followthrough`: a later close must remain inside those frozen bounds (inclusive) and move strictly farther inward than the seed close: higher for bullish, lower for bearish. |

A range-reference failure/sweep routes **only** to the range reversal family,
not also to generic liquidity reversal. Breakouts, including range breakouts,
route to continuation. Range rejection here deliberately means an already
confirmed sweep/re-entry followed by an inward close; ordinary touches without
these Step 4 events do not seed setups. Equal highs/lows alone do not seed or
qualify anything. Families can coexist and have opposing directions; the engine
does not select a trade or resolve portfolio exposure.

### Shared REQUIRED rules and OPTIONAL evidence

Rules expose `rule_id`, `required`, `outcome` (`passed`, `failed`, `pending`),
`veto`, an exact deterministic reason and typed evidence. Required unknowns
produce `pending`, not a fabricated pass or an opposing market fact.

| Rule | Exact required condition |
| --- | --- |
| `seed_event` | Confirmed event and frozen source reference as described above. |
| `later_evaluation` | Current close time strictly greater than seed `known_at`; pending at the seed close. |
| `structure` | Step 3 trend must be sufficient. Continuation requires exact directional alignment. Reversals accept either aligned or **sufficient neutral** trend; known opposite trend fails. Insufficient-swings neutral remains UNKNOWN/pending. |
| `location` | Current Step 3 `volatility.latest_close` must be **>= frozen reference band high** for bullish or **<= band low** for bearish. Missing close is pending. A close inside a nonzero-width reference band fails this gate without itself terminally invalidating the candidate. |
| `volume` | Current Step 3 volume must be available with non-null `relative_volume >= min_relative_volume`. Uses the existing latest-volume / prior-period-average measure. Unknown ratio (including unavailable baseline) is pending. |
| `volatility` | Current Step 3 volatility must be available with `0 < atr_percent_of_price <= max_atr_percent`. Unknown ATR is pending; zero/excessive ATR fails. |
| `no_failed_breakout`, `no_failed_retest` (continuation only) | Catalog must not contain a confirmed failure/failed retest of the seed breakout. A failure is also terminal, and its exact source event ID is included as opposing evidence. |
| `lifecycle` | No terminal invalidation or expiry below. |

These combine event, structure, location, participation and volatility **evidence
categories**; this does not claim statistical independence between indicators.
All volume/ATR/structure gates use the current as-of context, not an arbitrary
future maximum or an event's later outcome.

Higher timeframes are explicitly configured, independently stored Step 3
contexts; no resampling occurs in Step 5. They must be longer than the base
frame, have same-as-of analyses using the same structure parameters, and have
complete, available, nonsynthesized context to be usable. Missing, incomplete,
insufficient or unavailable context stays UNKNOWN. A supplied future/misaligned
context is rejected, not silently used.

- Default `higher_timeframes=()` produces an optional UNKNOWN
  `higher_timeframe:not_requested` rule; no alignment is inferred.
- For each configured timeframe, `higher_timeframe:<tf>` requires exact
  directional alignment when `require_higher_timeframe_alignment=True`.
  **All** configured timeframes must then align; neutral fails, UNKNOWN is pending.
- When alignment is optional, absent/UNKNOWN/neutral higher-timeframe context
  does not prevent qualification, but a **known opposite trend always vetoes**
  qualification. Optional thus does not mean “ignore known opposition.”
- Classical patterns are optional only: the latest occurrence per pattern
  geometry must be `confirmed` and known at/after the seed. Double bottom and
  inverse head-and-shoulders support bullish; double top and head-and-shoulders
  support bearish. Opposite patterns are reported as opposing but do not veto;
  no applicable confirmed pattern is UNKNOWN. Pattern evidence cannot replace
  any required gate, create a candidate, or qualify one by itself.

### State transitions, invalidation and expiry

- No seed: **NO_SETUP**, empty `setups`, reason
  `no_seed_confirmed_in_replayed_frames`. Quiet markets are valid results.
- Seed close: **WATCH** (unless already terminally invalidated by source facts).
- Later close, every required rule passed and no veto: **QUALIFIED**.
- Missing/failed required context or a higher-timeframe veto: **WATCH**.
  WATCH may persist; QUALIFIED may downgrade to WATCH and requalify later.
- A terminal rule: **NO_SETUP** with `terminal_reason` and `ended_at`.
  That setup ID never revives. A new seed gets a new identity.
- Snapshot aggregate state is QUALIFIED if any candidate is qualified, otherwise
  WATCH if any is watching, otherwise NO_SETUP. This aggregation is not a trade
  preference. Terminal candidates remain in the catalog for audit.

Terminal rules are evaluated in this exact precedence order (the first matching
reason wins), for **all three families and both WATCH/QUALIFIED states**:

1. `missing_replay_frames`: an expected evaluation frame was skipped after the
   seed. Termination is recorded at the first supplied frame after the gap,
   not retroactively at an invented observation time.
2. `missing_current_candle`: current Step 3 contiguous-segment end is not the
   expected latest closed candle. No price path is inferred.
3. `source_candle_gap`: a reported Step 2–4 gap ends at/after the seed confirmation
   (the next candle's open is the seed's close). Old gaps entirely before a new
   seed do not permanently forbid new segment candidates.
4. `maximum_bars_elapsed`: `(as_of - seed.known_at) > family_max_bars * interval`.
   Seed age is zero; exactly `max_bars` is still eligible. Both unconfirmed and
   already qualified candidates expire; retests do not reset age.
5. Continuation only: `failed_breakout`, then `failed_retest`, referencing the
   exact seed breakout ID.
6. `opposite_close_through_reference`: bullish current close **< band low**, or
   bearish current close **> band high**. Equality does not invalidate.
7. Range family additionally: `close_outside_frozen_range` if the current close
   is below the frozen range low or above its high (either side).

Missing/currently different active range evidence is pending/failed qualification,
not proof the frozen range was broken. Price invalidation and expiry remain
explicit. Changes in trend, low volume or high ATR also gate qualification, not
terminally destroy the seed by themselves.

Terminal result `as_of` advances with the enclosing snapshot, while its rules
and evidence remain frozen at `ended_at`. This makes the actual invalidating
observation distinguishable from later enumeration times. A snapshot may be
`status="incomplete"` because of older source gaps yet contain a qualified
post-gap candidate; candidate lifecycle checks only its own continuation.

### Evidence, identity and anti-lookahead guarantees

`QualificationEvidence` preserves source event/reference ID (or a deterministic
content reference for Step 3 context), timeframe, observed and confirmed/available
time, category, supportive/opposing/neutral/unknown status and reason. Event
observation time is its source candle's open; confirmation uses `known_at`.
Step 3 context is recorded as available at its snapshot `as_of`, with its latest
candle open as observation time. No missing timestamp is invented. The seed
reason also records its frozen level reference ID. Optional evidence can oppose
a candidate even when all required gates pass; inspect the rule's `required`
and `veto` flags rather than counting passes.

Frozen dataclasses and tuples make snapshots, results, rules and evidence
read-only. `SetupResult` exposes `evidence`, `passed_rules`, `failed_rules` and
`pending_rules` convenience properties; JSON includes the full rules/evidence
records. JSON projections are detached mutable copies, not source data handles.

- A frame's nested event/reference confirmations and candles must be available
  by that frame's as-of time. Current/later structure must never be relabeled as
  historical structure. Source catalogs must remain append-only with unchanged
  facts for existing event IDs. Invalid inputs raise errors.
- Frames beyond requested `as_of` are excluded before source validation,
  configuration fingerprinting, seeding and rule evaluation. The service's
  repository reads are bounded by each historical instant.
- No unconfirmed swing, future pattern transition, later retest, or higher-frame
  unclosed candle can qualify an earlier result. Actual-candle prefix tests and
  SQLite future-insertion tests verify equal entire historical snapshots.
- Setup IDs are versioned SHA-256 hashes over instrument, configuration
  fingerprint, family and seed event ID. No wall clock, UUID or random score is
  involved. Configuration fingerprints include Step 5 parameters **and Step 3
  and Step 4 parameters**, under `rules_version="setup-qualification-v1"`.
  Decimal Step 5 threshold spellings are canonicalized. Source configurations
  cannot change mid-replay. Changing rules/config intentionally changes the ID
  namespace so future measurements cannot silently mix versions.

### Step 5 configuration reference

All values live in frozen `QualificationParameters`, not environment secrets.
Percent means percentage points, not a fractional ratio.

| Parameter | Default | Validation / meaning |
| --- | --- | --- |
| `continuation_max_bars` | `10` | Integer >= 1; seed lifetime for continuation. |
| `reversal_max_bars` | `10` | Integer >= 1; seed lifetime for non-range reversal. |
| `range_max_bars` | `10` | Integer >= 1; seed lifetime for range reversal. |
| `min_relative_volume` | `1` | Finite decimal > 0; inclusive minimum. |
| `max_atr_percent` | `10` | Finite decimal > 0; inclusive ATR percentage maximum. |
| `higher_timeframes` | `()` | Unique immutable tuple of fixed-duration timeframes, canonically duration-sorted, each longer than base. Missing data is allowed and reported. |
| `require_higher_timeframe_alignment` | `False` | Boolean; `True` requires at least one configured higher timeframe. |

Do not interpret these uncalibrated defaults as validated strategy parameters.
The small `FAMILIES` registry keeps seed routing, direction and expiry definitions
separate from shared gates and replay. A future family must supply documented
seed/confirmation/lifecycle rules and tests; no dynamic strategy discovery or
additional families are silently enabled.

### Limitations and verification

- This is a correctness-first replay, not a high-throughput scanner. The service
  recomputes historical **upstream** snapshots through their public APIs at each
  close; it can be expensive for long histories. Pure replay accepts already
  computed source frames. No derived cache or persistence is introduced.
- Reproducibility assumes unchanged historical input facts and parameters.
  Backfilling/correcting old missing candles changes inputs and may change a
  rerun; adding only candles after historical `as_of` cannot. Snapshots already
  returned are immutable, not retroactively edited.
- Scope is OHLCV-derived evidence. A liquidity sweep is potential liquidity
  evidence, not proof of orders or intent. Exact range-bound matching is
  conservative; source rolling-window changes can demote a candidate to WATCH.
  Different frozen references can produce overlapping candidates; there is no
  profitability claim, deduplication into trades, or portfolio decision.
- Historical enumeration returns every evaluated close, not only transitions.
  Filtering after warm-up preserves IDs, lifecycle and expiry. No journal,
  statistics or trade-planning layer is implemented.

Offline tests cover empty/quiet markets, both continuation directions, each
required gate, all family confirmations and expiries, terminal invalidation,
WATCH persistence/downgrade, HTF optional/required/opposing/unknown contexts,
pattern-only rejection, gaps, stable IDs/JSON/snapshots, source immutability,
configuration effects, chronological enumeration and future-data invariance.
Run the complete suite with `python -m pytest -q`. The Step 5 files can be checked
with Ruff (`python -m pip install ruff`) using:

```bash
ruff check src/trading_assistant/setup_qualification tests/test_setup_qualification*.py
ruff format --check src/trading_assistant/setup_qualification tests/test_setup_qualification*.py
ruff check --isolated --select E4,E7,E9,F src tests
python -m compileall -q src tests
git diff --check
```

## Step 6 — deterministic trade planning (proposed plans from qualified setups, evidence only)

Step 6 is the first planning layer in the pipeline and the last before statistics: it converts a *currently QUALIFIED* Step 5 candidate into a transparent, deterministic, read-only proposed trade plan — or into an explicit refusal. The whole project follows the same separation of concerns: *Software calculates → rules qualify → statistics validate → AI explains → Bailey decides → everything gets recorded.* Step 6 owns only the deterministic derivation of entry, invalidation, stop, targets and unit-neutral R metrics from already-recorded evidence; every later layer (statistics, AI narration, UI, journaling, decision support, execution) remains unimplemented and out of scope.

**PLANNABLE is not a recommendation.** A plan is a proposal whose every number traces to a frozen upstream fact; it asserts nothing about profitability, likelihood, or suitability, and it places nothing. There is no order construction, order submission, broker/exchange client, credential, secret, wallet/API-key handling, position or balance state, leverage, margin, funding, fee/slippage modelling, partial-fill or bracket-order support, trailing stop, P&L or account calculation anywhere in this layer.

### Inputs, API and consumption contract

`trade_planning.plan_trade(...)` is the entire public planning surface (`trading_assistant.trade_planning.PlanningParameters`, `EntryMode`, `StopBufferMode`, `PlanState`, `TradePlanResult`, `PlannedLevel`, `PlannedTarget`, `PlanningRuleResult`, `PLANNING_RULES_VERSION`, `BASE_RULES`, `INVALID_CODES` complete it):

```python
plan_trade(
    snapshot: QualificationSnapshot,          # the Step 5 aggregate result
    frame: QualificationFrame,                # the exact frame the snapshot consumed
    setup_id: str,                            # the setup to plan
    parameters: PlanningParameters | None = None,   # None uses the defaults
) -> TradePlanResult
```

The planner consumes Step 5 output and the upstream evidence it already references. It does not rediscover or recompute any Step 2–5 fact: no candle aggregation, no swing/trend/range/level/ATR derivation, no pattern detection, no qualification-rule reimplementation. Re-verification is read-only state inspection against the passed objects only — the seed/confirmation event ids and bands already recorded by Step 5, and for continuation setups the still-`HELD`/`CONFIRMED` retest state. The only arithmetic performed is planning-specific (buffer offsets, directional distances, R ratios) and is exactly documented below. There is no separate planning `as_of` argument: the planning cutoff is always the snapshot's own `as_of`, and the supplied frame must be the one evaluated at exactly that instant (`frame_snapshot_asof_mismatch` otherwise), which is what makes “only data ≤ as_of” a structural property rather than a filter to remember. Every numerical input must already be known at that cutoff (each consumed event's `confirmed_at <= as_of` and its candle timestamp `< as_of`) or the plan is refused with `INVALID`/`future_evidence_used`.

The current actionable setup is the setup object inside the supplied, as-of-frozen snapshot; a QUALIFIED setup from an older snapshot is *historical evidence*, never implicitly actionable. Replaying qualification over a growing series and planning each snapshot at its own cutoff reproduces each historical plan exactly (identity and all numbers), because plans consume only data known at that cutoff. A qualified setup that later expires or invalidates is refused with `terminal_source_setup`; a QUALIFIED setup whose recorded `as_of` no longer matches the snapshot cutoff is refused with `stale_qualification`. There is no silent reuse of old QUALIFIED objects and no configurable “max plan age”: freshness is exactly Step 5's terminal/expiry state plus the as_of equality checks.

### Plan states (exact)

- `PLANNABLE` — a complete proposal was derived: entry, invalidation, protective stop, at least one target, positive `risk_per_unit`, and every planning rule passed.
- `NO_PLAN` — not planable *from the available evidence*: the setup is absent or not currently QUALIFIED (`setup_not_in_snapshot`, `setup_not_qualified`), terminal (`terminal_source_setup`), stale (`stale_qualification`), or a required upstream input is missing/UNKNOWN (`seed_event_missing`, `confirmation_event_missing`, `current_close_unavailable`, `missing_plan_close`, `missing_confirmation_level`, `missing_reference_band`, `missing_atr_for_stop_buffer`, `no_valid_target_available`). Missing values are never invented and gaps are never inferred: the result lists every missing input path and marks dependent rules `PENDING`.
- `INVALID` — the setup's or evidence's own data contradicts the contract or safety boundary: identity/instrument/as_of mismatches (`instrument_mismatch`, `frame_snapshot_asof_mismatch`, `reference_id_mismatch`, `family_mismatch`, `direction_mismatch`, `setup_identity_mismatch`, `as_of_beyond_snapshot_horizon`), evidence known only after the cutoff (`future_evidence_used`), failed family re-verification (`frozen_range_evidence_missing`, `active_range_mismatch`, `range_followthrough_failed`), unusable numbers (`entry_level_not_usable`, `invalid_reference_band`, `entry_not_on_trade_side`, `range_entry_outside_frozen_bounds`), or plan-level safety breaches (`stop_non_positive`, `stop_not_beyond_entry`, `non_positive_risk`, `minimum_r_multiple_not_met`).

QUALIFIED never implies PLANNABLE: the refusal codes and their exact rule reasons are recorded even when nothing could be planned, and the first failed rule's code is reported as `state_detail`. `INVALID` outranks `NO_PLAN`; the evaluation never crashes on bad inputs.

### The exact planning rules (`PLANNING_RULES_VERSION = "trade-planning-v1"`)

Fourteen named rules run in a fixed order — a fifteenth `minimum_r_multiple` record appears only when that knob is configured — and every outcome (passed/failed/pending with an exact reason string) is part of the result, so a decision can be audited later without re-running anything.

1. `source_inputs_consistent` — the snapshot and frame agree on exchange/symbol/timeframe and on the single planning `as_of`, and the seed and confirmation events exist in the frame's frozen catalogs with the reference id the setup recorded.
2. `setup_usable` — the setup is QUALIFIED, still current at `as_of`, non-terminal, its family/direction agree with the seed event, and no consumed evidence post-dates `as_of`.
3. `seed_evidence_available` — the seed's frozen reference exists with a valid, non-empty band.
4. `frozen_identity_consistent` — the plan's frozen identity (setup id, family, direction, `setup_created_at = seed.confirmed_at`, `as_of`, upstream `config_fingerprint`) is exactly reproducible from the consumed objects.
5. `availability_at_as_of` — the seed/confirmation `known_at` values and reference-band timestamps are all `< as_of` (information available *at* the cutoff, never after).
6. `family_confirmation_available` — the family's required confirmation is present (a `CONFIRMED` retest or later same-direction breakout; the swept reference for reversals; the active frozen range for range-reversals).
7. `family_state_reverified` — read-only re-check: a continuation retest is still `HELD`; a reversal's confirm candle closed back beyond the band while the extreme still holds the structural side; a range-reversal's latest close is still inside the frozen range and below (above) the failed-breakout candle's close for shorts (longs).
8. `entry_level_available` — the proposed entry number exists: `PLAN_CLOSE` uses the Step 3 `volatility.latest_close` (`source_type="step3_volatility_latest_close"`, `observed_at = as_of - 1h`, `confirmed_at = as_of`); `FROZEN_CONFIRMATION` uses the confirmation's own recorded close (`step4_retest_close` / `step4_breakout_close` / `step4_sweep_reclaim_close`). Zero/negative/non-finite values are `INVALID`, never replaced.
9. `reference_band_available` — the setup's frozen reference band is an ordered pair of finite positive prices (`invalid_reference_band` if not, `missing_reference_band` gap if absent), and it is what pins the logical invalidation: the band's *lower* side for LONG plans, its *upper* side for SHORT plans.
10. `entry_on_trade_side` — a long entry must sit on or above the band's upper side and a short on or below its lower side (`entry_not_on_trade_side` otherwise); range-reversal entries must additionally lie inside the frozen range bounds (`range_entry_outside_frozen_bounds`). A refused plan is never “corrected” onto the right side.
11. `stop_buffer_inputs_available` — an `ATR` buffer requires the Step 3 `volatility.atr` already known at `as_of`; its absence is a transparent `NO_PLAN` (`missing_atr_for_stop_buffer`).
12. `stop_beyond_entry` — the protective stop lies on the risk side: LONG `stop < entry`, SHORT `stop > entry` (`stop_on_wrong_side_of_entry`; a zero-distance stop is `stop_not_beyond_entry`).
13. `positive_risk` — `risk_per_unit = quantize_derived(abs(entry - stop)) > 0` (`non_positive_risk`).
14. `target_levels_valid` — at least one target survives the level rules below; targets on the wrong side or at the entry level are dropped with exact notes in `excluded_targets`, and if nothing usable remains the plan is refused as `NO_PLAN`/`no_valid_target_available`; a configured `minimum_r_multiple` that no target meets is refused as `INVALID`/`minimum_r_multiple_not_met` reporting the met target's exact R and the threshold. Low R alone is never a rejection unless this knob is configured.
15. `minimum_r_multiple` (recorded only when configured) — keeps every selected target at or above the threshold, excluding near targets with the exact reason (`R 1.00000000 < minimum_r_multiple 1.5`). Low R alone is never a rejection unless this knob is configured.

### Invalidation, stop and target derivation (exact)

**Logical invalidation** is structural and never a buffer: for every family, a LONG plan is invalidated at the *lower* side of the setup's frozen reference band (`source_type="step4_reference_band_low"`) and a SHORT at the *upper* side (`"step4_reference_band_high"`) — the far edge of exactly the level the setup thesis rests on: the broken shelf for continuations, the swept-and-rejected level for reversals, the defended boundary for range-reversals. The invalidation level's trace carries the structural reference id, its timeframe, the reference's latest swing timestamp (`observed_at`) and its `known_at` (`confirmed_at`), with `transformation = None` — an upstream fact is never rewritten.

**The protective stop** is `invalidation -/+ buffer` (long subtracts, short adds), quantized with the shared `quantize_derived` rounding at 8 decimal places. The buffer mode is explicit configuration — `none` (stop equals invalidation, documented as "no buffer applied"), `atr` (`atr * stop_buffer_atr_multiple`, using Step 3's ATR — never a locally recomputed one), or `percentage` (`tolerance_band(level, stop_buffer_percentage)`). Arbitrary protective offsets are impossible: the mode and value are part of the configuration fingerprint, and each buffered level records the exact formula string (e.g. `invalidation - quantize_derived(atr 2 x stop_buffer_atr_multiple 1) = buffer 2`) as its transformation. The invalidation level and the stop are reported separately and may differ.

**Targets** are derived only from information known at `as_of`. Structural candidates, in fixed sort order (distance, value, source id) with duplicates collapsed and unusable levels dropped with recorded reasons: range bounds recorded on the active frozen range (only for range setups — a setup's `active_range_id` must match its seed event's range, otherwise `active_range_mismatch`), then same-side confirmed structural references from the seed event's catalogs, then optional equal-level clusters (`step4_equal_level_cluster`, `include_equal_level_clusters`). A future swing or range is never a historical target: every candidate's confirmation must pre-date the cutoff or it is dropped with an exact `confirmed after planning as_of` note in `target_exclusions`/`excluded_targets`. Structural targets are capped at `max_structural_targets` (extra *known* levels are reported as excluded, never silently dropped). When no usable structural target exists, the configured R-multiple fallbacks produce explicit `"derived_from_r_multiple"` targets (`entry ± risk * multiple`) — labelled as derived, never presented as structural. The same rule is enforced as an invariant test: any target whose source id resolves to a recorded event has that event's `confirmed_at < as_of`.

**Risk and R** are unit-neutral distances only: `risk_per_unit = quantize_derived(abs(entry - stop))`; for each target, `reward = target - entry` for longs and `entry - target` for shorts (unquantized directional distance, always positive for accepted targets), and `R = quantize_derived(reward / risk)` at 8 places (`None` only in refusals). There is no position size, notional, quantity, account balance, equity, leverage, margin, fee, funding, P&L, or any other account-dependent calculation anywhere in the layer — enforced by tests that walk every JSON key and scan the planner source for forbidden tokens (`position_size`, `notional`, `quantity`, `leverage`, `balance`, `equity`, `pnl`, `margin`, `fee`, `funding`, `order`, `execution`, `submit`, `credential`, `secret`, `passphrase`, `api_key`) outside their negations.

### Configuration and identity

`PlanningParameters` is a frozen, validated container (exact type/range checks on load; `ValueError` with stable message substrings, consistent with Steps 3–5): `entry_mode` (`plan_close` default | `frozen_confirmation`), `stop_buffer_mode` (`none` default | `atr` | `percentage`), `stop_buffer_atr_multiple` (1, `> 0`), `stop_buffer_percentage` (0.1%, `>= 0`, `<= 5`), `max_structural_targets` (2, 1–10), `include_equal_level_clusters` (true), `r_multiple_fallbacks` (`(2,)`, unique positive multiples, sorted ascending; empty disables fallbacks), `minimum_r_multiple` (None, optional `> 0`). Its deterministic `config_fingerprint` (a SHA-256 over the canonical JSON projection, version-prefixed with `trade-planning-v1`) is recorded on every plan alongside the consumed snapshot's Step 5 fingerprint, so any configuration change is visible on previously planned setups. Changing any planning input — as_of, setup state, evidence, or config — deterministically changes the plan.

`TradePlanResult` is fully immutable (frozen dataclasses like Steps 3–5; attempts to replace plan fields, targets, or rule records raise `FrozenInstanceError`, and tampering with a `to_json_dict()` projection can never write back). The source snapshot, frame, and every consumed event are returned unmodified (proven by full JSON projections). Plan identity is the canonical `sha256` fingerprint over the *plan content*: same setup + same `as_of` + same config + same evidence ⇒ the same stable plan id and identical numbers (reproducible for backtesting later); different setup, side, mode, buffer, or config ⇒ different fingerprint. The Step 5 `setup_created_at` recorded on the plan is the seed event's confirmation time; the planning cutoff is recorded separately as `as_of`. `to_json_dict()` is the complete public representation: state, reasons, every level with its full traceability record (level id, value, source id/type, timeframe, observed/confirmed timestamps, source value, transformation), targets, risk/reward/R, rule outcomes, and both fingerprints.

### Guarantees, determinism and verification

The planner is a pure function: same snapshot, frame, setup id, `as_of` and parameters ⇒ the same result object (id and every number), with no timestamps, RNG, iteration-order, or environment dependence. It never mutates Step 2–5 objects. Anti-lookahead guarantees are explicit tests, not folklore: inserting future candles that satisfy the *next* step cannot alter a historical plan at `T` (`test_inserting_future_candles_cannot_alter_a_historical_plan`), future structure cannot become a historical target (`test_future_swing_cannot_become_a_historical_target`), and a chronological replay of every close produces the exact expected state sequence including the expiry transition (`test_chronological_replay_plans_at_every_close`). UNKNOWN/missing inputs refuse transparently rather than guessing (`missing_atr_for_stop_buffer`, `current_close_unavailable`, `seed_event_missing`, `confirmation_event_missing`, `missing_reference_band`, `no_valid_target_available`), and all refusals keep their machine-readable codes plus exact reasons. Every behavior is implemented over synthetic BTC/USDT *and* symbol-generic (ETH/USD, SOL/USDC, deep-copied fixtures) data with no hardcoded symbol, timeframe, or asset logic; `market_data` is a hard runtime dependency of the package metadata. Verification:

```bash
python -m pytest tests/test_trade_planning.py
python -m pytest                      # full repo suite: all Step 6 tests included
ruff format --check src/trading_assistant/trade_planning tests/test_trade_planning.py
ruff check src/trading_assistant/trade_planning tests/test_trade_planning.py
```

**Limitations.** Parameters are uncalibrated deterministic defaults (the same disclaimer as Steps 3–5): they were chosen so every knob is explainable and auditable, not because they make plans desirable or likely to work; calibration belongs to Step 7's backtest harness, which does not exist yet. The planner assumes Step 5's 1h pipeline evidence and cannot plan from other timeframes (Step 7's multi-timeframe layering will feed later steps, not this one). Plans are level proposals for human review only: no execution price modelling (fills, liquidity, slippage), no bracket/order construction, no position management or trailing-stop behaviour, no partial fills or cancels/replaces, no news/liquidity-awareness beyond what Steps 3–5 already recorded, and no re-plan history (each plan is independent; recording decisions against plans belongs to Step 8's journal).
