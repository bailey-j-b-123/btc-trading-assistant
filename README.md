# Evidence-Driven Cryptocurrency Trading Assistant

A foundation for an evidence-driven cryptocurrency analysis assistant. The intended purpose is to organize reliable market evidence and future analysis for human review.

**This is not an automated trading bot. It contains no trading strategies, chart-pattern trading signals, setup qualification, trade planning engine, backtesting, alerts, trade execution, AI/LLM features, or user interface. It does not make trading decisions or place/execute trades.** Numerical market facts are derived from source data and deterministic code; missing candles remain missing rather than being guessed or synthesized. The Step 3 market-structure engine is descriptive only: it reports measured structural facts (swings, trend, ranges, levels, volatility, volume) for human review and for later deterministic steps, and never emits a trade, signal, or recommendation.

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
├── pattern_liquidity/        Reserved; no detector implemented
├── setup_qualification/      Reserved; no qualification logic implemented
├── trade_planning/           Reserved; no planning logic implemented
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
