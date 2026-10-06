# Evidence-Driven Cryptocurrency Trading Assistant

A foundation for an evidence-driven cryptocurrency analysis assistant. The intended purpose is to organize reliable market evidence and future analysis for human review.

**This is not an automated trading bot. It contains deterministic candidate setup definitions, a deterministic, read-only trade *planning* layer, a grounded, fact-locked explanation layer (Step 9) that can only re-state existing deterministic evidence, a read-only presentation dashboard (Step 10) that displays Steps 1–9 output and records Bailey's explicit journal decisions, and an isolated historical validation layer (Step 11) — but no order placement, trade execution, account functionality, autonomous AI decision-making, or automatic strategy optimisation. The UI never executes trades: ACCEPT records a journal row, nothing more. It does not make trading decisions or place/execute trades. QUALIFIED means rules satisfied, not a profitable trade or recommendation; PLANNABLE means a complete deterministic proposal was derived from a rule-qualified setup, not that a trade is profitable, advisable, or should be executed.** Numerical market facts are derived from source data and deterministic code; missing candles remain missing rather than being guessed or synthesized. The Step 3 market-structure engine is descriptive only: it reports measured structural facts (swings, trend, ranges, levels, volatility, volume) for human review and for later deterministic steps, and never emits a trade, signal, or recommendation. Step 4 adds deterministic pattern/liquidity events as evidence only, with explicit knowable timestamps. Step 5 combines those existing facts into auditable NO_SETUP, WATCH and QUALIFIED states. Step 6 converts only a *currently QUALIFIED* Step 5 candidate into a transparent, fully traceable proposed plan (entry, invalidation, stop, targets, unit-neutral R metrics) or an explicit refusal. Step 7 is the immutable decision & outcome journal: it appends what the system proposed (the exact Step 5 snapshot and Step 6 plan projections), what Bailey explicitly decided (PENDING/ACCEPTED/REJECTED/SKIPPED), and deterministic, anti-lookahead market observations of the proposed levels (entry/stop/target touches, first-touch ordering, ambiguity, gaps, MFE/MAE) that survive restarts and never rewrite history.

**Software calculates → rules qualify → statistics validate → AI explains → Bailey decides → everything gets recorded.** Step 7 records that history durably and append-only. Step 8 adds deterministic, read-only statistics over those immutable records; it validates recorded evidence but does not establish future performance or profitability. Step 9 owns **“AI explains”**: it converts the facts already established by Steps 3–8 into clear, auditable explanations through a deterministic explanation context, a fact manifest, and a deterministic local renderer, with an optional provider-independent interface for a future LLM/API whose structured output is validated against the manifest before use. Step 9 explains existing deterministic evidence; it never creates market facts, prices, statistics, setups or trade plans, and it never decides or executes anything. Step 11 adds an isolated historical replay/report layer, and Step 12 adds a **live forward paper tester**: at each confirmed base-timeframe close it re-uses the unchanged Step 2–9 pipeline, appends an immutable forward observation to its own ledger, and tracks the resulting paper plans through later closed candles — with explicit stale/missing-data verdicts, append-only outcome versions, and a read-only dashboard section that keeps LIVE FORWARD PAPER OBSERVATIONS strictly separate from HISTORICAL VALIDATION. Like every earlier step it has no order placement, exchange credentials, account access, position sizing, leverage, or autonomous decision-making.

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
├── journaling/               Immutable decision & outcome journal (Step 7)
│   ├── types.py              Frozen records, decisions, plan projection, observations
│   ├── parameters.py         Versions, canonical JSON/fingerprints, note bounds
│   ├── observation.py        Pure deterministic candle-touch observation engine
│   ├── models.py             Four append-only SQLAlchemy tables and constraints
│   ├── repository.py         Idempotent appends and version-chain reads
│   └── service.py            Journaling surface over Step 5/6 output and Step 2 candles
├── statistics/               Deterministic read-only journal analysis (Step 8)
│   ├── config.py              Versioned sample/rounding/quantile rules
│   ├── dataset.py             Immutable journal dataset and SELECT-only reader
│   ├── analysis.py             Pure cutoff-bounded metrics and grouping
│   ├── models.py               Immutable, reproducible report contracts
│   └── service.py              Read-only composition over Step 7 rows
├── ai_explanation/           Grounded explanation layer (Step 9)
│   ├── parameters.py          Versions, fingerprint helpers, grounding rules
│   ├── errors.py              Context-build errors and grounding violations
│   ├── models.py              Immutable context/manifest/provider contracts
│   ├── context.py             Copy-only canonical context builder (Steps 3-8)
│   ├── manifest.py            Deterministic fact manifest and numeric allow-set
│   ├── renderer.py            Renderer interface + deterministic local renderer
│   ├── provider.py            Provider interface + manifest grounding validation
│   └── service.py             Stateless, database-free explanation service
├── historical_validation/    Isolated chronological replay/report layer (Step 11)
│   ├── parameters.py          Versioned split, horizon, and friction scenarios
│   ├── metrics.py             Shared pure metric helpers (Step 11 + Step 12)
│   ├── models.py              Immutable report, record, regime, and metric contracts
│   └── service.py             Read-only Steps 2–7 replay and diagnostics
├── forward_testing/          Live forward paper tester (Step 12; observation only)
│   ├── parameters.py          Versions, fingerprints, runner/forward parameters
│   ├── models.py              Frozen cycle/observation/plan/outcome/heartbeat contracts
│   ├── tables.py              Five append-only SQLAlchemy tables + guard triggers
│   ├── repository.py          Idempotent appends and version-chain reads
│   ├── service.py             Closed-candle pass, catch-up, outcome updates, snapshot
│   ├── reporting.py           Forward metrics, breakdowns, live-vs-historical comparison
│   ├── runner.py              Closed-candle polling loop, backoff, clean shutdown
│   └── __main__.py            CLI: run --once / run / status
└── web/                      Read-only presentation layer (Steps 10–12)
    ├── app.py                 FastAPI factory, JSON errors, security headers
    ├── app_factory.py         Import-light factory for uvicorn
    ├── state.py               AppState: engine, services, injectable clock
    ├── freshness.py           Deterministic CURRENT/STALE/HISTORICAL/UNKNOWN rules
    ├── dashboard_service.py   Dashboard payload assembly over Steps 2–9
    ├── journal_query.py       SELECT-only filtered journal listing adapter
    ├── schemas.py             Validated decision/observation request contracts
    ├── routers/               meta / market / dashboard / journal / statistics / validation / forward / settings
    └── static/                Zero-build dashboard UI (ES modules + CSS)
        ├── index.html         App shell (semantic markup, external scripts only)
        ├── styles.css         Dark-terminal design system, responsive breakpoints
        ├── js/                format / freshness / decision / chart / views / api
        └── vendor/            Vendored lightweight-charts build + license

scripts/seed_synthetic_demo.py  Labelled synthetic demo data for local preview
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

The Step 10 dashboard (`python -m trading_assistant.web`) is served by the same install: `fastapi` and `uvicorn` are core dependencies, and `httpx` (dev extra) powers its offline API tests. The dashboard UI has no build step and no runtime Node dependency; see the Step 10 section for local usage.

The instrument remains configurable with `TRADING_ASSISTANT_SYMBOL`, `TRADING_ASSISTANT_BASE_ASSET`, and `TRADING_ASSISTANT_QUOTE_ASSET` (defaults: `BTC/USDT`, `BTC`, `USDT`). The default local database URL remains `sqlite:///data/trading_assistant.sqlite3`. The default exchange is `kraken`, configurable with `TRADING_ASSISTANT_EXCHANGE`. Initial configured timeframes are `5m`, `15m`, `1h`, `4h`, and `1d`; `TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES` and `TRADING_ASSISTANT_DEFAULT_TIMEFRAME` can be changed. Fixed-duration seconds/minutes/hours/days/weeks are supported by the timeframe parser, so the configured set can be extended without treating the initial list as exhaustive. The raw archive root is configurable with `TRADING_ASSISTANT_RAW_DATA_DIR` and defaults to `data/raw/`.

## Market-data behavior

### Downloading and incremental updates

Run `alembic upgrade head` before using the database-backed service. For an initial historical download, pass an explicit timezone-aware UTC start time. A later `update_history` call starts after the latest stored candle; without existing history, it requires an explicit start time. Pagination uses CCXT's `since` and configured page limit, advancing by the requested timeframe. Empty or truncated exchange responses are reported through gaps; pagination stalls and exchange/network errors fail clearly. Rolling-window endpoints — Kraken's public OHLC route returns only its newest 720 entries, whatever `since` is — are requested **without** a date cursor: such a cursor cannot retrieve older candles there, so the requested start bounds the local range check and gap report instead of the exchange request. Every returned row still passes the same filtering, closed-candle and gap validation, and the requested `since` is still recorded in the raw archive.

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

**Step 7 adds revision `0003_journal`**, which is strictly additive: four append-only journal tables plus their indexes and SQLite `UPDATE`/`DELETE` guard triggers, with no change to `ohlcv_candles` or any existing row. Its downgrade refuses to run while journal rows exist and otherwise drops only the (empty) journal tables; the candle archive is never dropped or rewritten.

**Step 12 adds revision `0004_forward_testing`**, also strictly additive: five forward ledger tables (`forward_cycles`, `forward_observations`, `forward_paper_plans`, `forward_paper_outcomes`, `forward_runner_heartbeats`) plus indexes and SQLite `UPDATE`/`DELETE` guard triggers. The forward ledger is a new, separate store: it never reads or writes the Step 7 journal tables and never rewrites `ohlcv_candles`. `0004_forward_testing` is the current head, so `alembic upgrade head` takes an existing Step 7/11 database to the forward schema without touching stored market data or recorded decisions. Its downgrade refuses to run while forward rows exist and otherwise drops only the (empty) forward tables.

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

Step 6 is the first planning layer in the pipeline and the last before statistics: it converts a *currently QUALIFIED* Step 5 candidate into a transparent, deterministic, read-only proposed trade plan — or into an explicit refusal. The whole project follows the same separation of concerns: *Software calculates → rules qualify → statistics validate → AI explains → Bailey decides → everything gets recorded.* Step 6 owns only the deterministic derivation of entry, invalidation, stop, targets and unit-neutral R metrics from already-recorded evidence; Step 7 records these plans and the decisions made about them without changing them. Step 8 now analyzes only the immutable Step 7 journal; Step 9 explains already-recorded evidence without ever altering or creating it; UI, decision support and execution remain unimplemented and out of scope.

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

**Limitations.** Parameters are uncalibrated deterministic defaults (the same disclaimer as Steps 3–5): they were chosen so every knob is explainable and auditable, not because they make plans desirable or likely to work. Step 8 supplies descriptive historical statistics only; calibration, backtesting and optimization remain out of scope. The planner assumes Step 5's 1h pipeline evidence and cannot plan from other timeframes (a future multi-timeframe layering would feed later steps, not this one). Plans are level proposals for human review only: no execution price modelling (fills, liquidity, slippage), no bracket/order construction, no position management or trailing-stop behaviour, no partial fills or cancels/replaces, no news/liquidity-awareness beyond what Steps 3–5 already recorded, and no re-plan history (each plan is independent; recording decisions against plans is the Step 7 journal's job, and the journal never alters a plan).

## Step 7 — immutable decision & outcome journal (append-only record of proposals, decisions and observations)

Step 7 closes the loop of the project's core principle — *Software calculates → rules qualify → statistics validate → AI explains → Bailey decides → everything gets recorded* — by recording the last item durably. It appends **immutable journal records** for the exact Step 5 snapshot and Step 6 plan a decision was made against, the explicit **human decision** (`PENDING` / `ACCEPTED` / `REJECTED` / `SKIPPED`), and deterministic, anti-lookahead **outcome observations** of the proposed levels (entry/stop/target touches, first-touch ordering, ambiguity, gaps, MFE/MAE). Nothing that is already written is ever rewritten: corrections are new append-only versions that point at what they supersede.

**The journal is a record, not a strategy, a backtester, an execution engine, or a statistics layer.** It places no orders, models no fills, slippage, fees or funding, holds no positions, balances, leverage or margin, and computes no realised monetary P&L and no aggregate performance metric (no win rate, expectancy, profit factor, Sharpe, ranking, optimisation, or profitability claim anywhere in this layer). An outcome observation is a statement about **market prices relative to a proposed plan**; it is never an executed trade. Every decision must be recorded explicitly: a journal record that was never decided reports *no decision at all*, and `PLANNABLE` never implies `ACCEPTED`.

### What is journaled (and what is not)

- `journal_snapshot(snapshot=...)` records the **whole Step 5 snapshot** (`NO_SETUP`, `WATCH`, `QUALIFIED`, or any future state) as an audit/replay observation, with no setup and no plan attached. Refusals and quiet observations are recorded too: a journal that only kept actionable candidates could not answer "what did the system see, and what did I decide about it?".
- `journal_setup(snapshot=..., setup_id=..., plan=None)` records **one setup** from that snapshot plus the *exact* snapshot it came from. The setup must actually exist inside the passed snapshot (`ValueError` otherwise); the journal never invents or re-qualifies a setup.
- `journal_plan(snapshot=..., plan=...)` records **one Step 6 result together with its setup and snapshot**, so a plan can never float free of the moment it was produced. `PLANNABLE` plans carry the full projection used for observations; `NO_PLAN`/`INVALID` refusals are recorded with their state, reasons, rule records and fingerprints, and are deliberately **not** observable (refusing is data, but there is no proposal to observe).
- Recorded traceability: journal record id, record kind, exchange/symbol/timeframe(s), setup id, setup family, setup direction, setup state, `setup_created_at`, `setup_as_of`, seed event id, structural reference id, Step 5 config fingerprint, Step 5 rules version, plan id, plan state, `planning_as_of`, Step 6 config fingerprint, Step 6 rules version, the canonical JSON projection of the exact snapshot and plan, and the **content identity** of the snapshot (`setup_snapshot_id`, a SHA-256 fingerprint — not a mutable foreign-key-only reference).
- Not journaled: candles (Step 2 owns them), derived datasets, orders, fills, exchange responses, account state, statistics, narration, or UI state. The journal only ever **reads** the append-only `ohlcv_candles` archive and never writes to it.

### Decisions (exact states and semantics)

| State | Meaning recorded verbatim |
| --- | --- |
| `PENDING` | Bailey has seen the record and has not decided yet (recorded explicitly, not implied). |
| `ACCEPTED` | Bailey decided to act on this proposal. Recorded only when that string/enum is supplied. |
| `REJECTED` | Bailey decided not to act on the proposal. |
| `SKIPPED` | Bailey did not take the proposal (e.g. missed/ignored), without a rejection judgement. |

`record_decision(*, journal_id, decision, decided_at=None, reason=None)`:
- `decision` is **required** — omitting it is a `TypeError`, so nothing can silently default to `ACCEPTED`; an unknown value is a `ValueError` listing the four states.
- `decided_at` defaults to the current UTC time when omitted; a supplied value must be timezone-aware.
- `reason` is an optional bounded user note (`MAX_NOTE_LENGTH = 2000`; stripped; blanks become `None`; NUL/C0 control characters are refused; leading/trailing whitespace is not part of the record's meaning). Notes are metadata only and can never change an observation.
- Each decision row denormalises the record's traceability (record kind, setup id, plan id, family, direction, **the setup state at decision time**, instrument, timeframes, both `as_of` values, both config fingerprints, both rules versions, the snapshot identity) so a decision remains fully auditable even in isolation — a `SKIPPED` decision on a `WATCH` candidate can never be mistaken for a trade.
- Journaling a plan does **not** create a decision. `latest_decision(...)` returns `None` until one is recorded; there is no default `ACCEPTED`, not even for `PLANNABLE`.

### Immutable decision history and corrections

Decisions are append-only. `record_decision` computes a content-derived `decision_id` and appends a new row with `sequence = previous + 1` and `supersedes_decision_id = previous.decision_id`:

- Re-recording the *identical* decision (same state, timestamp and note) is a verified no-op: the same id and exactly one row.
- Any difference — state, timestamp, or note — appends a **new** version that supersedes the previous one. The original row keeps its state, timestamp and reason exactly as first written (enforced at the database level, see below) and stays retrievable via `decision_history(...)` and `get_decision(decision_id=...)`, so a change of mind leaves a complete correction trail instead of destroying evidence.
- `latest_decision(...)` is the effective decision; `decision_history(...)` is the whole trail, oldest first.

### Outcome observations: exact candle touch semantics

`observe_outcome(journal_id=..., observed_through=..., parameters=None)` (or the pure function `observe_outcome(journal_id=..., levels=..., candles=..., observed_through=..., parameters=...)`) evaluates how stored candles behaved relative to a **proposed plan**. Terminology is deliberate and enforced by tests that walk every stored key: these are *proposed-plan outcomes* / *hypothetical plan observations*, **never executed trades**, fills, or P&L.

The window is `[plan.as_of, observed_through]`, aligned to the plan's timeframe, and **only** candles with an open time inside it are read. Observations are computed from the plan JSON stored on the journal record (`ProposedPlanLevels.from_plan` / `from_payload`), so re-observing later cannot silently use a newer plan.

Definitions (long; short is the mirror image):

- **Entry touched** — the candle *traded* the proposed entry: `low <= entry <= high`. A candle that is entirely beyond the level (for example, entirely above a long entry) is not an entry.
- **Stop touched** — `low <= stop` for a long, `high >= stop` for a short.
- **Target touched** — `high >= target` for a long, `low <= target` for a short.
- **Ordering** — touches are ordered by candle index, then by kind (entry, stop, targets in target order); touches in one candle with the *same* kind form one co-touched group (`co_touched=True`), because OHLC data cannot order them within the candle. `first_touch_order` is the group sequence.
- **Pre-entry touches** — a stop or target touched *before* the entry is recorded with `ordering="pre_entry"` and is never counted as reached. If the stop is touched before the entry and the entry is never touched, the proposal is `INVALIDATED_BEFORE_ENTRY`. A target touched pre-entry can still be reached after the entry and then counts.
- **Ambiguity** — if the entry candle also touches the stop or a target (`entry_and_exit_same_candle`), or a post-entry candle touches both the stop and at least one target (`stop_and_target_same_candle`), the status is `AMBIGUOUS`: the affected touches are `ordering="ambiguous"`, `targets_reached` stays empty in the entry+exit case, and the trajectory stops at the ambiguity. The favourable result is never selected, and probability is never estimated.
- **Gaps / missing candles** — a missing candle stops the evaluation at that point. The gaps are recorded as merged `missing_ranges` with exact counts (`missing_candle_count`), `incomplete=True`, and the evaluated window ends at the last candle actually evaluated (`evaluated_through`). If no terminal status was established before the gap the status is `INCOMPLETE_DATA` — **UNKNOWN is never converted into a win, a loss, or an "entry not reached"** (a gap never claims `ENTRY_NOT_REACHED`). A terminal event reached before a gap still stands, with `incomplete=True` recorded alongside. Missing candles are never bridged, interpolated, or inferred through.
- **Distinct states** — `ENTRY_NOT_REACHED`, `INVALIDATED_BEFORE_ENTRY`, `STOPPED`, `STOPPED_AFTER_TARGETS`, `TARGETS_REACHED`, `OPEN_AT_CUTOFF`, `AMBIGUOUS`, `INCOMPLETE_DATA`. `ENTRY_NOT_REACHED` is only reported for a fully observed window in which the entry was never touched; `OPEN_AT_CUTOFF` means the trajectory was still open at the cutoff; `AMBIGUOUS` means the OHLC data cannot order two touches; `INCOMPLETE_DATA` means the candles needed to decide are missing — none of the three is a win or a loss. Evaluation stops at the first terminal outcome (`STOPPED`, `STOPPED_AFTER_TARGETS`, `TARGETS_REACHED`, `AMBIGUOUS`, `INCOMPLETE_DATA`), which is why `evaluated_through` is recorded separately from `observed_through`: a stop candle after the final target lies outside the evaluated window and is never counted against the plan.

Every touch is also stored as an individual event row (`entry`/`stop`/`target` + target index, the level value, the candle timestamp and index, its ordering, and the co-touched flag), along with the evaluated high/low and their timestamps and the post-entry low/high, so later steps can query or re-derive facts without parsing prose or reconstructing candles.

### MFE / MAE (exact)

`mfe_price_move` and `mae_price_move` are the **maximum favourable / adverse absolute price movement relative to the proposed entry**, over the evaluated window only (never past `observed_through`, never past a gap, clamped at ≥ 0):

- long: `mfe = max(evaluated_high - entry, 0)`, `mae = max(entry - evaluated_low, 0)`;
- short: `mfe = max(entry - evaluated_low, 0)`, `mae = max(evaluated_high - entry, 0)`;
- `mfe_r = quantize_derived(mfe_price_move / risk_per_unit)` and `mae_r = quantize_derived(mae_price_move / risk_per_unit)` at 8 decimal places, using the plan's own recorded `risk_per_unit` (unit-neutral R).

Both raw price movements and R multiples are stored, so a later step can derive metrics losslessly; both are `None` when nothing was observable, and neither is a profit, loss, or performance claim.

### Identity, versioning and the anti-lookahead guarantee

- **Identity is content, not a counter.** Journal record ids, decision ids and observation ids are stable SHA-256 fingerprints over canonical JSON (`sort_keys=True`, no whitespace) of the exact inputs (`journal-v1`, `journal-decision-v1`, `journal-outcome-v1`). Same snapshot + same plan + same cutoff + same candles + same config ⇒ the same ids and the same numbers. Repeated journaling of the same source never creates a duplicate: the insert is an idempotent `ON CONFLICT DO NOTHING` plus a field-by-field verification, and a *different* value under the same identity is refused with `JournalConflict` rather than overwriting history.
- **Observations are versioned, not rewritten.** Each stored observation for a record carries `sequence` and `supersedes_outcome_id`; re-observing the identical window is a verified no-op, and observing further (or after a backfilled candle appears) appends a new version that supersedes the previous one. The earlier version remains byte-for-byte recoverable through `get_outcome(outcome_id=...)` and `outcome_history(...)` — a T2 observation never mutates T1.
- **Anti-lookahead is structural.** Only candles at or before `observed_through` are read, and a candle outside `[as_of, observed_through]` is *refused* (`ValueError`) rather than filtered, so a later candle cannot influence a historical observation even by accident. Tests prove that inserting future candles after the fact does not change a stored observation, that a backfilled missing candle creates a new version instead of silently changing history, and that a chronological replay reproduces the same observation.
- **Immutability is enforced twice**: frozen dataclasses in Python (`FrozenInstanceError` on mutation) and SQLite `BEFORE UPDATE`/`BEFORE DELETE` triggers that abort any direct `UPDATE`/`DELETE` on all four journal tables (`append-only`).

### Persistence, schema and migrations

Journal history is durable across restarts (proven by reopening a migrated database with a new engine/service and reading the same ids, payloads and decisions). Migration `0003_journal` is **additive**: four new tables plus indexes and triggers, no change to `ohlcv_candles`, no data deletion, no reset.

| Table | Holds |
| --- | --- |
| `journal_records` | One immutable journal record: kind, instrument, snapshot/setup/plan projections and traceability, `setup_snapshot_id`. |
| `journal_decisions` | Append-only decision versions: state, `decided_at`, note, `sequence`, `supersedes_decision_id`, denormalised traceability. |
| `journal_outcomes` | Append-only observation versions: status, touch flags, target/ordering/gap fields, evaluated extremes, MFE/MAE, canonical payload, `sequence`, `supersedes_outcome_id`. |
| `journal_outcome_events` | One row per recorded touch: kind, target index, level, candle timestamp/index, ordering, co-touched flag. |

Constraints and indexes back the guarantees: `CHECK` constraints for the decision vocabulary, sequence ≥ 1, plan-completeness and record-kind consistency, outcome status vocabulary, ambiguity/incomplete/entry-ordering consistency, and event kind/target-index consistency; unique `(journal_id, sequence)` per version chain; foreign keys to `journal_records` and to the previous version; indexes for record lookup by instrument/setup/as-of/snapshot identity, decisions by record/time/state/setup/plan, outcomes by status/cutoff/plan/setup, and events by ordering. The migration's `downgrade` **refuses to run while journal rows exist** (`RuntimeError: Refusing to downgrade: ...`) and only drops the empty journal tables otherwise; historical candles are never dropped. Downgrading an empty journal returns the database to `0002_ohlcv_candles` unchanged.

### API and example

The whole public surface is `trading_assistant.journaling` (`JournalService`, `JournalRepository`, `observe_outcome`, `ProposedPlanLevels`, `JournalRecord`, `DecisionRecord`, `DecisionState`, `OutcomeObservation`, `OutcomeVersion`, `OutcomeEvent`, `OutcomeStatus`, `OutcomeEventKind`, `OutcomeEventOrdering`, `OutcomeParameters`, `snapshot_identity`, `record_identity_material`, `fingerprint`, `canonical_json`, `normalize_note`, the version constants, and the errors `JournalError`/`JournalConflict`/`JournalNotFound`):

```python
from trading_assistant.database import create_database_engine
from trading_assistant.journaling import DecisionState, JournalService

service = JournalService(create_database_engine("sqlite:///data/trading_assistant.sqlite3"))

record = service.journal_plan(snapshot=snapshot, plan=plan)          # exact Step 5 + Step 6 moment
service.record_decision(                                             # explicit human decision
    journal_id=record.journal_id,
    decision=DecisionState.ACCEPTED,                                 # no default: required
    reason="manually reviewed",
)
observation = service.observe_outcome(                               # deterministic, cutoff-bounded
    journal_id=record.journal_id, observed_through=at(8)
)
print(observation.status, observation.entry_reached, observation.targets_reached,
      observation.mfe_r, observation.mae_r)

for version in service.outcome_history(journal_id=record.journal_id):
    print(version.sequence, version.supersedes_outcome_id, version.observation.status)

service.observe_outcome(journal_id=record.journal_id, observed_through=at(20))  # T2 appended
service.get_outcome(outcome_id=observation.id)                                  # T1 still exact

# Rejected/skipped proposals are observable on exactly the same terms, so a
# later comparison can include the opportunities that were not taken.
skipped = service.journal_plan(snapshot=other_snapshot, plan=other_plan)
service.record_decision(journal_id=skipped.journal_id, decision=DecisionState.SKIPPED)
service.observe_outcome(journal_id=skipped.journal_id, observed_through=at(20))
```

`JournalRepository` is the SQLite persistence layer beneath the service (records by setup or snapshot identity, decisions and outcome versions per record). The service is a thin adapter: it computes identities and projections, reads stored candles, and never replays qualification or re-plans anything.

### Guarantees, determinism and verification

The journal is deterministic and offline: no network, no exchange, no credentials, no clock beyond an explicit `decided_at` default and no randomness in any observation. It never mutates Step 2–6 objects (full JSON projections are compared before/after) and never writes to the raw or candle archives. All behavior is implemented over synthetic BTC/USDT *and* symbol-generic (`ETH/USD`, `SOL/USDC`) data with no hardcoded symbol, timeframe or asset logic, and the package adds no dependency beyond the existing SQLAlchemy/Alembic stack. Tests cover qualification/plan journaling, all four decision states, the no-default rule, immutable correction trails, snapshot+fingerprint preservation, duplicate/idempotent journaling, accepted *and* rejected/skipped outcomes, entry/stop/targets reached or not, multiple targets, first-touch ordering, same-candle stop+target and entry+exit ambiguity, gaps, `UNKNOWN` staying `UNKNOWN`, incomplete windows, long/short MFE/MAE, historical cutoffs, future-data invariance, T2-not-overwriting-T1, persistence across sessions, database constraints and triggers, the additive migration (including refuse-to-downgrade-with-rows), immutability of Step 5/6 objects, and symbol-generic behavior:

```bash
python -m pytest tests/test_journaling.py tests/test_journaling_service.py
python -m pytest                      # full repo suite: all Step 7 tests included
ruff format --check src/trading_assistant/journaling tests/test_journaling.py tests/test_journaling_service.py
ruff check src/trading_assistant/journaling tests/test_journaling.py tests/test_journaling_service.py
alembic upgrade head                  # additive: 0002_ohlcv_candles -> 0003_journal
alembic check                         # metadata and migrations agree, no new operations
```

**Limitations.** The journal records historical system proposals, human decisions and deterministic market observations; it does not prove profitability, and it does not represent exchange execution unless a future execution system supplies genuine fill data. Touch rules are candle-level OHLC approximations, not tick or order-book reconstructions: same-candle orderings are reported as ambiguous instead of guessed, pre-entry touches are recorded but never counted, and gaps stop the evaluation rather than being bridged. There is deliberately no execution modelling (no fills, sizing, slippage, commissions, fees, funding, balances, positions, leverage, liquidation, or realised monetary P&L), no aggregate statistics or performance claims, no optimisation, no AI narration, no alerts and no UI; `PENDING`, `ACCEPTED`, `REJECTED` and `SKIPPED` are human metadata and none of them is interpreted by the observation engine. Journaling does not make a proposal better: it only makes the history of what the system proposed, what was decided, and what the market subsequently did auditable, reproducible and append-only.

## Step 8 — deterministic statistics and performance analysis (read-only journal evidence)

Step 8 owns **“statistics validate”** in the project principle. It recomputes descriptive statistics from an explicit immutable Step 7 journal dataset. It does not replay Step 5 qualification, re-plan with Step 6, read current market candles, write aggregates, or modify journal rows. `StatisticsAnalyzer` is pure in-memory analysis; `JournalStatisticsService` reads only the four Step 7 journal tables with `SELECT` statements and does not run migrations or persist reports. Reports are returned as frozen values and can be serialized with canonical `to_json()`.

This is analysis of recorded proposals and market observations, **not executed trades**. Statistics describe historical observations; they do not prove that a strategy works, guarantee future results, or establish future profitability.

### API and exact report identity

```python
from datetime import UTC, datetime

from trading_assistant.database import create_database_engine
from trading_assistant.statistics import JournalStatisticsService, StatisticsConfig

service = JournalStatisticsService(
    create_database_engine("sqlite:///data/trading_assistant.sqlite3"),
    config=StatisticsConfig(),  # journal-statistics-v1; default sample floor = 30
)
report = service.analyze(
    as_of=datetime(2026, 10, 1, tzinfo=UTC),
    window_start=datetime(2026, 7, 1, tzinfo=UTC),  # optional cohort lower bound
    group_by=("setup_family", "direction", "decision_state", "symbol", "timeframe"),
)
print(report.report_id, report.data_quality.total_journal_records_considered)
for group in report.groups:
    print(group.key, group.entry_reach_rate.status, group.entry_reach_rate.percentage)
```

The lower bound and `as_of` are inclusive and timezone-aware. `window_start` filters the **setup record cohort** by `JournalRecord.setup_as_of`; `as_of` caps all source event times. `StatisticsAnalyzer.analyze(dataset, ...)` accepts a caller-supplied `JournalDataset(records, decisions, outcomes)` when a specific immutable input snapshot is required. The report includes the exact considered journal, decision and outcome identities, a source fingerprint, normalized filters, requested/effective grouping, configuration fingerprint, cutoff/window, and `report_id`. The ID is a SHA-256 identity of these inputs and report rules; same rows + same filters + same versions/configuration + same cutoffs produce the same ID and canonical JSON. No mutable aggregate truth is stored.

`total_journal_records_considered` means raw Step 7 `JournalRecord` rows that pass the report's source-time cutoff, optional cohort window and exact filters. Rows outside those bounds are not in the historical report and do not inflate its denominator. Each metric independently reports `records_considered`, `eligible_records`, `excluded_records`, exclusion reasons, `sample_size`, numerator/denominator where applicable, and its denominator definition. The top-level data-quality block exposes the considered record count, setup/plan/outcome coverage, ambiguous/incomplete counts, entry-not-reached count and exclusions. `dimension_counts` always reports raw counts by setup family, direction, symbol, exchange, timeframe, setup state, decision state, plan state, record kind and outcome status; null/not-applicable buckets remain visible.

### Counts and denominator rules

- **Setup qualification:** setup counts use individual `record_kind=SETUP` journal rows; each row is one recorded Step 5 setup observation. Repeated journal rows for the same `setup_id` remain repeated observations (the report separately exposes distinct setup IDs). `setup_qualification_rate` is `QUALIFIED SETUP rows / all SETUP rows` in that group; `WATCH` and `NO_SETUP` setup rows remain in the denominator. Aggregate `SNAPSHOT` rows are counted as journal/setup-state records but are not expanded into setup-family observations, avoiding double counting when the same snapshot and individual setups were both journaled. They are reported as an explicit exclusion from the setup-rate metric.
- **Decisions:** for each journal record, the latest decision with recorded `decided_at <= as_of` is selected. `PENDING`, `ACCEPTED`, `REJECTED` and `SKIPPED` are counted separately. `NO_DECISION` means no explicit decision row is eligible by the cutoff; it is not inferred to be pending or accepted. Accepted/rejected/skipped comparisons use the decision attached to the journal record and never relabel those opportunities as trades.
- **Plans:** plan-state counts retain `PLANNABLE`, `NO_PLAN`, and `INVALID`; `NO_PLAN_ATTACHED` and `PLAN_NOT_YET_VISIBLE` distinguish a record with no plan from a plan whose recorded planning time is after the cutoff. Only Step 7 observations attached to `PLANNABLE` proposals enter outcome-performance metrics; each journaled PLANNABLE record is one proposal observation and is not deduplicated by setup id. Missing plan/outcome rows remain explicit exclusions.
- **Outcome-state counts/rates:** each selected observation is counted under its exact Step 7 state: `ENTRY_NOT_REACHED`, `INVALIDATED_BEFORE_ENTRY`, `STOPPED`, `STOPPED_AFTER_TARGETS`, `TARGETS_REACHED`, `OPEN_AT_CUTOFF`, `AMBIGUOUS`, or `INCOMPLETE_DATA`. State percentages use all PLANNABLE records with an outcome observation visible by the cutoff as denominator. This is a status distribution, not a win/loss rate; ambiguous and incomplete statuses remain their own categories. `entry_not_reached_count` counts only the two known no-entry states (`ENTRY_NOT_REACHED` and `INVALIDATED_BEFORE_ENTRY`); an incomplete record with no observed entry is unknown, not a no-entry result. `observations_with_any_gap_count` separately reports the Step 7 gap flag, including terminal outcomes that were established before a later gap.
- **Entry touch rate:** numerator is the Step 7 recorded `entry_reached=True`; denominator is observations with a known entry-touch state. A directly recorded entry touch remains an entry touch even if a later same-candle ambiguity or data gap makes the plan outcome unknown. `ENTRY_NOT_REACHED` and `INVALIDATED_BEFORE_ENTRY` are known non-touches. Ambiguous/incomplete observations without an observed entry touch are excluded, not counted as misses. This metric says nothing about an order or fill.
- **Stop/target touch rates:** stop rate is the proposed stop-level touch count after an **ordered entry**, divided by clean observations with an ordered entry. Target `Tn` is its own statistic: ordered post-entry reaches of that target divided by clean, non-ambiguous, non-`INCOMPLETE_DATA` observations that proposed `Tn` and had an ordered entry. A pre-entry touch is not a reached target. T2 is never merged into T1; arbitrary target counts are supported. Each target reports its own proposed-record count, numerator, denominator, exclusions and rate. Ambiguous/incomplete outcomes are excluded from stop/target hit-rate denominators rather than treated as misses.
- **Rejected/skipped comparisons:** `REJECTED`, `SKIPPED`, `ACCEPTED`, `PENDING` and `NO_DECISION` rows can each be grouped and compared when they have valid PLANNABLE Step 7 observations. Their sample is still a set of hypothetical plan observations, not executed trades.

### Hypothetical proposed-plan R definition

`hypothetical_proposed_plan_outcome_r` is explicitly a **proposed-level, observational/hypothetical R distribution**, not realized R, return, account P&L or expectancy:

- For a clean `STOPPED` outcome with an ordered entry, calculate `(directional move from the stored proposed entry to the stored proposed stop) / stored risk_per_unit`. It is ordinarily about −1R, but the calculation uses the recorded prices rather than inserting a fill assumption.
- For a clean `TARGETS_REACHED` outcome, calculate directional R for the ordered target levels Step 7 records as reached and use the largest reached target R. Entry, target levels and risk all come from the immutable observation.
- `STOPPED_AFTER_TARGETS` has no single proposed-plan outcome R because Step 6/7 defines no partial-exit quantity or exit policy; it is explicitly excluded rather than assigned a convenient result. `ENTRY_NOT_REACHED`, `INVALIDATED_BEFORE_ENTRY`, `OPEN_AT_CUTOFF`, `AMBIGUOUS`, `INCOMPLETE_DATA`, missing observations and non-terminal outcomes are also excluded with reasons.

The resulting values support raw distributions and, when sample size permits, average, median, minimum, maximum and sign counts. Even this hypothetical metric assumes only the stated proposed-level arithmetic; it assumes no execution price, fill, partial fill, fees, slippage or order handling and makes no profitability claim.

### MFE/MAE definitions

The four distribution metrics use the stored Step 7 `mfe_price_move`, `mae_price_move`, `mfe_r` and `mae_r` fields exactly; Step 8 does not re-read OHLCV or recalculate the trajectory. Step 7 defines these as proposed-entry-relative favourable/adverse extremes over its evaluated window, from plan `as_of` through the terminal candle or observation cutoff, clamped at zero. That window can include candles **before** the proposed entry was touched, so these are not realized-trade excursions. Price-move and R forms are summarized separately. `AMBIGUOUS` and `INCOMPLETE_DATA` statuses are excluded from these distribution summaries; a terminal outcome established before a later gap remains usable, and the gap flag is still exposed in data quality.

### Sample-size, Decimal and quantile rules

`StatisticsConfig` is the central frozen, fingerprinted configuration. Version `journal-statistics-v1` defaults to `minimum_sample_size=30`, eight decimal places, `ROUND_HALF_EVEN`, and Q25/Q50/Q75. The threshold is a fixed reporting floor, not a confidence interval, significance test, probability guarantee or automatic tuning rule. It is configurable only by an explicit config whose fingerprint/version changes the report identity.

Every rate uses its own stated denominator; its raw numerator, denominator and exclusions are preserved at every sample size. If that denominator is below the configured minimum, status is `INSUFFICIENT_DATA` and percentage is `None`. Distribution summaries likewise preserve sample count, sign counts and exact value-frequency distribution below threshold, but average/median/min/max/quantiles are `None`/empty and status is `INSUFFICIENT_DATA`. At the exact minimum the descriptive values become available (`SUFFICIENT_DATA`). No confidence is fabricated and counts are never hidden to make a small sample look conclusive.

All derived arithmetic uses `Decimal` with the configured fixed precision and rounding. The median is the middle sorted value for odd `n`; for even `n` it is the arithmetic mean of the two middle values. Configured quantiles use the **nearest-rank** rule, rank `ceil(p × n)` (one-based), with no interpolation. This means Q50 and the even-sample median can differ by design. Quantiles and all rounded summary values use the central eight-decimal half-even rule by default; raw decimal frequency counts remain exact.

### Version separation, periods and historical cutoffs

By default the analyzer groups result metrics separately by recorded journal rules version; Step 5 qualification rules/config fingerprint; Step 6 planning rules/config fingerprint; applicable decision rules version; and Step 7 outcome-observation rules/config fingerprint. These are appended to `effective_group_by` even if omitted from the requested dimensions. If incompatible non-null versions are present, `mixed_versions=True`, the per-version groups remain available, and `overall` is `None` rather than silently pooling them. A caller may pass `allow_mixed_versions=True` to explicitly request a mixed aggregate; the report then marks `MIXED_VERSIONS_EXPLICITLY_ALLOWED` and includes every `VersionProfile` so the mixture is visible. Statistics-rule/config version is part of every report fingerprint.

For a cutoff `T`, source journal records with `setup_as_of > T`, decisions with `decided_at > T`, and outcome versions with `observed_through > T` are excluded. Of eligible outcome versions for a record, the greatest `observed_through` is used; if multiple rows have the same horizon, the earliest immutable sequence is selected so a later same-horizon replay does not silently replace that value. Report identities use only the exact selected rows, so adding records or versions with later event timestamps does not change a report at historical `T`. Grouping by `period_day`, `period_week` or `period_month` uses the record's UTC `setup_as_of`. `rolling_reports(cutoffs=..., lookback=...)` recomputes explicit, fixed-width inclusive cohort windows without lookahead.

**Cutoff limitation:** Step 7 persists event timestamps and append-only sequence, but it does not store a separate database `recorded_at` timestamp for each decision/outcome version. Step 8 can enforce the event-time cutoffs above and prevents later-window observations from leaking into earlier cutoffs; it cannot prove when a row carrying a backdated `decided_at`/`observed_through` value was physically appended. Historical cutoff statements therefore mean *recorded event-time evidence at or before T*, not a claim about database ingestion/availability time. The exact immutable input IDs and source fingerprint make the dataset used by each report auditable.

### Step 8 limits

Step 8 does not place orders, infer fills, create trades/positions, read balances/leverage, calculate monetary realized P&L, model fees/slippage, explain results with AI, alert, rank strategies, optimize parameters, tune thresholds, fit models, or prove profitability. It does not rewrite Step 7 rows or OHLCV data and adds no database migration or persisted aggregate. `INSUFFICIENT_DATA` means the configured descriptive sample floor was not reached; even `SUFFICIENT_DATA` is not evidence of future profitability. Statistics describe only the historical observations actually recorded in the immutable journal.

```bash
python -m pytest tests/test_statistics.py
python -m pytest                         # complete Steps 1–8 test suite
ruff format --check src/trading_assistant/statistics tests/test_statistics.py
ruff check --isolated --select E4,E7,E9,F src/trading_assistant/statistics tests/test_statistics.py
```

## Step 9 — grounded AI explanation layer (explains deterministic evidence, never creates it)

Step 9 owns **“AI explains”** in the project principle: *Software calculates → rules qualify → statistics validate → AI explains → Bailey decides → everything gets recorded.* It converts facts already established by Steps 3–8 into clear, useful trading explanations for a human. **The AI/explanation layer is never a source of numerical market truth.** The deterministic engine remains authoritative: the explanation layer calculates nothing new, infers nothing through a gap, decides nothing, accepts nothing and executes nothing. It may explain market structure, pattern/liquidity evidence, setup qualification, why a setup is `NO_SETUP`/`WATCH`/`QUALIFIED`, the Step 6 proposal or refusal, Step 8 historical statistics, limitations and uncertainty, and what would invalidate the recorded thesis. It must not invent setups, prices, indicators, statistics, sample sizes, signals, targets or trade plans.

### Deterministic engine vs AI boundary

| Concern | Owner | Step 9 behavior |
| --- | --- | --- |
| Market facts, levels, R, statistics | Steps 2–8 deterministic code | copied verbatim, never recomputed |
| Setup/plan states | Steps 5–6 | re-stated exactly; never upgraded or downgraded |
| Narrative wording | Step 9 renderer/provider | grounded to the fact manifest |
| Numerical values in rendered output | deterministic software | inserted from the manifest, never re-typed by a provider |
| The decision | Bailey (external) | never taken, implied or recorded by Step 9 |

### Deterministic explanation context

`ExplanationService.build_context(snapshot=..., setup_id=..., frame=..., plan=..., journal_record=..., latest_decision=..., latest_outcome=..., statistics_report=..., statistics_config=...)` consumes existing Step 3–8 objects and produces one frozen, canonical `ExplanationContext`:

- **Copy-only payload.** Only the Step 5 `QualificationSnapshot` is required; every other input is optional and copied verbatim (Step 3/4 structure and events from the same-`as_of` frame, Step 6 `TradePlanResult`, Step 7 record/decision/outcome, Step 8 report plus config). The builder never mutates inputs and never keeps live references to them.
- **Canonical schema.** Payload sections: `instrument`, `as_of`, `setup_focus`, `qualification` (every setup with rules, vetoes and evidence references), `market_structure` (trend, active range, zones, volatility, volume, completeness, higher timeframes), `pattern_evidence` (Step 4 events), `plan`, `journal` (record, decision, outcome, quarantined untrusted notes), `statistics`, and explicit `limitations` for absent inputs. Serialization uses the same canonical projection as Steps 3–8 (UTC ISO-8601 datetimes, exact decimal strings), so the context fingerprint is stable.
- **UNKNOWN stays UNKNOWN.** Missing values are `None`/absent and are labelled UNKNOWN; nothing is inferred. A `NO_PLAN` refusal keeps `stop.value = null` rather than receiving a guessed number.
- **Consistency guards.** Mismatched instruments, wrong setup ids, plans not at the snapshot `as_of`, dangling decision/outcome links, future-dated journal records or statistics reports (cutoff invariance) raise `ContextBuildError` instead of being interpreted.
- **Identity.** `context.fingerprint` is a version-prefixed SHA-256 over the canonical payload (`ai-explanation-v1` / schema `ai-explanation-context-v1`). Same inputs ⇒ same fingerprint.

### Fact-manifest grounding

`build_fact_manifest(context)` flattens the payload into individually addressable facts with stable canonical-path IDs (`ctx.plan.entry.value`, `ctx.statistics.report.data_quality.ambiguous_count`, …), exact canonical values, kinds (`decimal`, `integer`, `text`, `boolean`, `null`) and a deterministic numeric token allow-set derived only from manifest values. External explanations are verified against this manifest:

- every factual claim must reference existing manifest fact IDs; unknown IDs fail;
- `UNKNOWN` facts cannot be cited as evidence and known facts cannot be relabelled unknown;
- structured `setup_state`/`plan_state` fields must equal the recorded states;
- plan/statistics prose is rejected when the context has no plan/statistics;
- provider prose is scanned for numeric tokens that do not appear anywhere in the manifest (defence in depth on top of the structured contract);
- forbidden language (guarantees, “will win”, advice/recommendations, execution claims, autonomous-decision claims) is rejected;
- deterministic software inserts authoritative values into final rendered claims — a provider never re-types a number.

This is structural validation of a structured response contract, not free-text mathematical extraction.

### Provider-independent architecture and the local renderer

`ExplanationRenderer` is the rendering interface; `LocalTemplateRenderer` (`local-template-renderer` / `ai-explanation-local-v1`) is the deterministic offline reference implementation and the safety baseline. It renders ten numbered sections — WHAT THE ENGINE SEES, WHY IT MATTERS, EVIDENCE FOR, EVIDENCE AGAINST, UNKNOWN / MISSING INFORMATION, CURRENT SETUP STATE, PROPOSED PLAN, HISTORICAL EVIDENCE, INVALIDATION / WHAT WOULD CHANGE THE VIEW, LIMITATIONS — using only exact manifest spellings:

- `NO_SETUP` is stated plainly; nothing interesting is manufactured.
- `WATCH` states exactly what is present and which rules must still resolve.
- `QUALIFIED` + `NO_PLAN`/`INVALID` explains that qualification is not an actionable plan and states the deterministic refusal reasons/missing inputs.
- `PLANNABLE` renders entry, invalidation, stop, targets, risk and R exactly as Step 6 derived them, labelled as a proposal that is neither guaranteed nor recommended.
- Step 8 figures preserve exact sample sizes, `SUFFICIENT_DATA`/`INSUFFICIENT_DATA` statuses, exclusions, ambiguous/incomplete counts, version separation and the observational/hypothetical-R labelling; `INSUFFICIENT_DATA` is explained as insufficient and never upgraded into a profitability statement.

A future LLM/API provider implements `ExplanationProvider.produce(request)` and receives **only** an `ExplanationRequest` (canonical payload, manifest facts, grounding contract text — no credentials, no network endpoint, no execution surface). It must return the structured `ExplanationProviderResponse` contract (`summary`, `setup_state`, `plan_state`, `evidence_for[]`, `evidence_against[]`, `unknowns[]`, `plan_explanation`, `statistics_explanation`, `risk_notes[]`, `factual_claims[]`) whose claims reference manifest fact IDs. Responses failing `validate_provider_response` raise `GroundingViolation` listing every violation. Grounded provider output is appended *behind* the unchanged deterministic sections, so failed rules, vetoes and refusals can never be hidden by a provider.

```python
from trading_assistant.ai_explanation import ExplanationService

service = ExplanationService()  # stateless; no database, no network, no keys
context = service.build_context(snapshot=snapshot, setup_id=setup.id,
                                frame=frame, plan=plan,
                                statistics_report=report,
                                statistics_config=config)
result = service.explain(context)                       # deterministic local renderer
# result = service.explain(context, provider=my_provider)  # validated external provider
print(result.explanation_id, result.setup_state, result.plan_state)
print(result.text)
```

### Auditability

Every `ExplanationResult` carries: `explanation_id` (content-derived fingerprint of context + renderer/provider identity + rendered content), `context_fingerprint`, `manifest_fingerprint`, the `as_of`/`generated_at` distinction (`generated_at` is wall-clock audit metadata excluded from identity), `renderer_id`/`renderer_version`, `provenance` (`deterministic-local` or `external-provider`), referenced fact IDs, source setup/plan/journal/statistics-report ids, and — for external providers — the exact structured response payload for audit. Same deterministic context + same renderer/config ⇒ byte-identical explanation content and identity. No secrets are stored or required; no database write occurs during explanation.

### Prompt-injection and untrusted text

Journal notes, decision reasons and provider output are untrusted data, never instructions. Notes are quarantined in `journal.untrusted_notes`, quoted verbatim only for audit, labelled `never an instruction`, and cannot alter facts, states or numbers; the grounding rules are fixed system text that notes and provider prose cannot override. Provider text is preserved with explicit provenance and is never merged into the deterministic voice.

### Step 9 limits

The explanation layer places no orders, models no fills/positions/balances/leverage, performs no sizing, optimization, threshold tuning, backtesting or alerting, adds no new setup logic, indicators or statistics, and implements no UI. It adds no dependency, requires no network, no API key and no paid service for any test. External providers are optional, provider-independent and offline-testable via scripted objects. Even a perfectly grounded explanation is a re-statement of recorded evidence at one `as_of`; it is not advice, not a prediction and not evidence that any trade will win. The AI explains existing deterministic evidence; it cannot create trading facts.

```bash
python -m pytest tests/test_ai_explanation.py
python -m pytest                        # complete Steps 1–9 test suite
ruff format --check src/trading_assistant/ai_explanation tests/test_ai_explanation.py
ruff check src/trading_assistant/ai_explanation tests/test_ai_explanation.py
```

## Step 10 — professional responsive trading dashboard (presentation layer)

Step 10 owns the **user interface** in the project principle: it makes the existing
deterministic pipeline (Steps 1–9) usable visually. It is a *presentation* layer
only. **The UI is never a second trading engine.** It does not compute setup
qualification, market structure, patterns, plan levels, statistics, or outcomes; it
does not fabricate prices or AI facts; and it never places, modifies, or cancels an
order. Steps 1–9 remain authoritative, and the dashboard reuses their exact outputs.

**Software calculates → rules qualify → statistics validate → AI explains → Bailey
decides → everything gets recorded.** Step 10 presents that pipeline and exposes the
one state-changing action that has existed since Step 7: appending an explicit
`ACCEPTED` / `REJECTED` / `SKIPPED` decision to the immutable journal. **The UI does
not execute trades.** "Accept" means "record Bailey's decision in the journal," never
"place an order."

### Architecture chosen and why

The smallest maintainable stack that integrates with the existing Python application:

* **Backend:** FastAPI serving a typed JSON API plus the static UI from a single
  process. FastAPI was chosen because it is the minimal, widely-understood Python web
  layer that gives request validation (Pydantic), clean dependency injection of the
  existing `AppState` (engine + Steps 2–9 services + injectable clock), and structured
  JSON error handling with no extra framework. It adds no new database tables and no
  new migrations.
* **Frontend:** a zero-build vanilla ES-module single-page app (`web/static/js`) with a
  hand-written dark design system (`styles.css`). No bundler, no node dependency at
  runtime, no framework lock-in — the files the server ships are the files reviewed.
* **Charting:** TradingView `lightweight-charts` v4.2.0, **vendored** into
  `web/static/vendor/` with its license, so the app needs no CDN and no network to
  render. The chart consumes only backend-served candles/overlays — the browser never
  fetches market data independently.
* **Data source:** the chart, dashboard, journal and statistics all read the same
  SQLite tables the engine uses, through the existing Step 2–9 services. There is a
  single source of truth.

```text
Browser (ES-module SPA)
   │  fetch /api/*  (JSON only; no inline script; CSP default-src 'self')
   ▼
FastAPI routers ──► AppState (engine + Steps 2–11 services + clock)
   │                     │
   │                     ├─ CandleRepository        (Step 2 closed candles)
   │                     ├─ QualificationService    (Step 5 state)
   │                     ├─ plan_trade              (Step 6 plan/refusal)
   │                     ├─ JournalService          (Step 7 decisions/observations)
   │                     ├─ JournalStatisticsService(Step 8 report)
   │                     ├─ HistoricalValidationService (Step 11 isolated replay)
   │                     └─ ExplanationService      (Step 9 grounded narrative)
   ▼
Static UI assets (index.html, styles.css, js/, vendor/lightweight-charts)
```

### Running the dashboard locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head                     # create/migrate the SQLite schema
python scripts/seed_synthetic_demo.py    # OPTIONAL labelled synthetic preview data
python -m trading_assistant.web          # http://127.0.0.1:8040
# options: --host 0.0.0.0 --port 8040
```

Without seed data the dashboard is honest about emptiness: it shows **NO TRADE**, an
`UNKNOWN` freshness badge, and "No stored candles / No journal entries" empty states
rather than fabricated demo candles. To work with real data, download candles first
(Step 2 `MarketDataService.update_history`).

### Desktop and mobile behaviour

Five primary areas are reached from a **sidebar on desktop** and a **bottom tab bar on
mobile** (thumb-reachable, one-handed): **Dashboard, Journal, Statistics, Validation, Settings.**
The active section is highlighted with `aria-current="page"`.

* **Desktop** lays out cards in a single column under a sticky top bar that always shows
  symbol, latest stored price and the freshness badge. The candlestick chart is ~420px
  with overlay toggle chips (zones, range, equal levels, plan levels, swings).
* **Mobile (≤860px)** is designed intentionally, not shrunk: the top bar compacts, cards
  stack, the chart drops to ~320px, decision buttons become full-width ≥44px touch
  targets, tables become cards, and the nav becomes a fixed bottom bar with
  `safe-area-inset` padding. Status is never conveyed by colour alone — every badge pairs
  a dot with text (`QUALIFIED`, `WATCH`, `NO TRADE`, `CURRENT`, `STALE`, …).

The web manifest and meta tags make the app **installable to a home screen** (PWA-ready
presentation), but Step 10 deliberately ships **no service worker and no push
infrastructure** — that deployment concern is deferred.

### Backend / frontend boundary

The API returns authoritative structured data with explicit `UNKNOWN`/`null` states and
never substitutes zeros. The frontend may *format* values for display (thousands
separators, UTC rendering) but preserves the raw authoritative spelling and never
re-derives a number.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/meta` | Identity, exchange, supported timeframes, rule versions, `execution_disabled: true` |
| `GET /api/dashboard` | One authoritative payload: market, freshness, qualification, plan, overlays, journal, explanation |
| `GET /api/market/candles` | Stored closed candles for the chart (never re-fetched from an exchange) |
| `GET /api/market/structure` | Step 3 overlays (zones, range, swings, trend) for one timeframe |
| `GET /api/journal/records` | Filtered, paged immutable journal listing |
| `GET /api/journal/records/{id}` | One immutable historical record + decision/outcome history |
| `POST /api/dashboard/decisions` | Journal the exact proposal at `as_of` and append one decision |
| `POST /api/journal/records/{id}/decisions` | Append/correct a decision on an existing record |
| `POST /api/journal/records/{id}/observations` | Deterministic Step 7 outcome observation at a cutoff |
| `GET /api/statistics` (+ `/rolling`) | Step 8 report JSON (counts, rates, distributions) |
| `GET /api/validation` | Step 11 isolated historical validation report; read-only, no journal writes |
| `GET /api/settings` | Configured defaults + explicitly unavailable controls |

The only mutating routes are the three journal/decision endpoints above; there is no
order, position, balance, leverage, transfer, or credential endpoint anywhere.

### Data freshness states

Every current-market screen reports a deterministic freshness computed on the backend
from `as_of`, the timeframe's candle-close boundary, and the latest stored candle:

* **CURRENT** — `as_of` is the newest boundary and the latest expected closed candle is
  stored. This is the *only* state that may read as up-to-date; the word "LIVE" is never
  used.
* **STALE** — the requested boundary is current but stored candles stop before it;
  `staleness_intervals` counts the gap.
* **HISTORICAL** — the screen evaluates a past instant (including the default view when
  data stops well before the wall clock). Never labelled live.
* **UNKNOWN** — no stored candles (or an unusable series) for that instrument/timeframe.

The dashboard's default `as_of` snaps to the newest boundary that has data, so a stale
series renders as a clearly-labelled **HISTORICAL** view instead of forcing the engine to
replay thousands of empty future frames.

### Decision semantics

Decisions use the existing Step 7 model unchanged:

* There is **no default** — a proposal is never assumed accepted; `decision` is required.
* Controls are **enabled only when a complete `PLANNABLE` proposal exists**; otherwise
  they are disabled with the exact deterministic reason (no plan, `NO_PLAN`, `INVALID`,
  or no qualified setup).
* A confirmation dialog shows exactly which setup/plan snapshot (`as_of`, symbol,
  timeframe, entry/stop/targets, setup id) the decision applies to before anything is
  recorded.
* Submitting an **identical** decision again is suppressed (`duplicate: true`, nothing
  appended); a **different** decision appends a new row that supersedes the prior one,
  preserving the full append-only correction trail.
* Double-submission is guarded client-side (button locks) and server-side (duplicate
  suppression).

### Journal and statistics pages

* **Journal** is searchable/filterable (symbol, family, direction, setup/plan state,
  decision, outcome, date range) and paged. Opening a record shows the **immutable
  historical snapshot** — the exact stored Step 5/Step 6 projections — plus decision
  history (with supersession notes) and outcome-observation versions. Historical rows
  never re-render against today's market.
* **Statistics** renders Step 8 visually with the sample size and
  `SUFFICIENT_DATA`/`INSUFFICIENT_DATA` banner impossible to miss. Every rate shows its
  exact numerator/denominator; ambiguous, incomplete, and entry-not-reached counts are
  surfaced; MFE/MAE and hypothetical R distributions are labelled **observational /
  hypothetical**, never realized profit, and there is no fake equity curve.

### Security boundaries and limitations

* **No secrets** are committed or exposed; no exchange credentials, balances, leverage,
  or order-execution surface exists in the bundle or the API.
* **Content-Security-Policy** is `default-src 'self'` with no inline script/style/eval;
  all scripts and styles are external files.
* **Untrusted text** (journal notes, decision reasons, explanation prose) is rendered via
  `textContent` only — never `innerHTML` — and is preserved verbatim, so it cannot inject
  markup. There is no `eval`, no `document.write`, and no `Function(...)` in the shipped JS.
* State-changing endpoints validate input with Pydantic enums and reject missing/invalid
  decisions with `422`.
* Errors are JSON-only (no HTML error pages leaking internals).

**Required before any internet-facing deployment (not part of Step 10):** real
authentication and an identity layer in front of the app, plus transport security. Step 10
deliberately does **not** build a fake authentication system for appearance's sake; it
flags `authentication_required_before_public_deployment: true` in `/api/meta` instead. The
app is intended as a **private** application.

### Step 10 limits

The dashboard adds no trading logic, no new setup rules, no indicators, no statistics, no
execution, no sizing, no optimization, and no parameter tuning. It re-uses deterministic
outputs; where data is missing it shows an empty/UNKNOWN state rather than inventing
values. Presentation preferences (preferred symbol/timeframe, overlay toggles, explanation
detail) are stored **in the browser only** and never alter engine rules. Real-money
execution, exchange authentication, balances, positions, leverage, and auto-trading remain
out of scope for all steps.

```bash
python -m pytest                        # complete Steps 1–11 Python suite (495 tests)
node --test tests/frontend/*.test.mjs   # frontend logic tests (23 tests)
ruff format --check src/trading_assistant/historical_validation src/trading_assistant/web tests/test_historical_validation.py tests/test_web_*.py tests/web_fixtures.py tests/test_web_frontend_contract.py
ruff check src/trading_assistant/historical_validation src/trading_assistant/web tests/test_historical_validation.py tests/test_web_*.py tests/web_fixtures.py tests/test_web_frontend_contract.py
```

## Step 11 — historical torture test / out-of-sample validation

Step 11 is an **isolated, deterministic validation layer**, not a strategy
optimizer and not an addition to Bailey's decision journal. It replays the
stored Step 2 candle archive chronologically through the existing Step 3 market
structure, Step 4 pattern/liquidity, Step 5 qualification, and Step 6 planning
logic. For each base-candle close it records a derived `SNAPSHOT` or `SETUP`
artifact in the returned report, including every `NO_SETUP`, `WATCH`, and
`QUALIFIED` state encountered. A `QUALIFIED` candidate is passed to the
unchanged Step 6 planner; both a complete `PLANNABLE` proposal and any explicit
`NO_PLAN`/`INVALID` refusal are preserved exactly in the derived record.

These report records are **not** Step 7 journal rows. The implementation has no
write path, no migration, no aggregate table, and no interaction with Bailey's
real decisions or historical journal records. It reads only stored candles using
explicit end bounds, constructs report values in memory, and returns canonical
JSON. Raw market data is never rewritten or removed.

### Replay methodology and anti-lookahead guarantees

A base-timeframe candle becomes available only at its close boundary. At each
boundary the validator builds a fresh Step 4 snapshot with that exact `as_of`,
then replays Step 5 frames in chronological order. Existing Step 3/4/5 timing
checks remain authoritative: confirmed swings retain their confirmation time,
patterns/liquidity retain their `known_at`, and higher-timeframe candles are
independently bounded by their own close time. A higher-timeframe row is never
resampled from lower data and is never used while forming.

The validation service additionally freezes the source frame before a run and
uses explicit `end_time` bounds for every base/higher-timeframe retrieval and
outcome window. Adding a candle after a historical decision timestamp cannot
change that earlier setup or Step 6 plan. Step 7's pure `observe_outcome`
semantics are used unchanged: same-candle entry/exit and stop/target order is
`AMBIGUOUS`, gaps become `INCOMPLETE_DATA` unless an earlier terminal event is
already proven, no level outside an OHLC range is claimed as touched, and an
untouched entry remains `ENTRY_NOT_REACHED`. Ambiguous, incomplete, open, and
not-observed-at-split-boundary outcomes remain separate; they are never silently
converted to wins or losses.

### Development and out-of-sample protocol

`ValidationConfig` accepts an explicit `ChronologicalSplit` with strictly
non-overlapping decision-boundary periods:

```python
from datetime import UTC, datetime
from trading_assistant.historical_validation import (
    ChronologicalSplit, HistoricalValidationService, ValidationConfig,
)

report = HistoricalValidationService(engine).validate(
    exchange="kraken",
    symbol="BTC/USDT",
    timeframe="1h",
    config=ValidationConfig(
        split=ChronologicalSplit(
            development_start=datetime(2024, 1, 1, tzinfo=UTC),
            development_end=datetime(2024, 9, 30, tzinfo=UTC),
            out_of_sample_start=datetime(2024, 10, 1, tzinfo=UTC),
            out_of_sample_end=datetime(2024, 12, 31, tzinfo=UTC),
        ),
        observation_horizon_candles=20,
    ),
)
```

`development_end` must be strictly earlier than `out_of_sample_start`; all
explicit boundaries must be aligned base-timeframe candle-close boundaries. A plan
from the development period is observed only through the last *development*
candle; it cannot use out-of-sample candles to produce a development result.
The same boundary rule applies to out-of-sample plans. When no split is passed,
the service derives a documented chronological 70/30 decision-boundary split
from source timestamps alone (never outcomes) and records its exact resolved
ranges. A one-boundary dataset honestly reports no out-of-sample period. The
architecture can run the same immutable configuration over successive explicit
periods for future walk-forward analysis without changing any strategy rule.

### Friction scenarios and report interpretation

`FrictionAssumptions` are explicit, versioned basis-point inputs:
`fee_bps` (charged on both proposed entry and terminal endpoint),
`entry_slippage_bps`, and `exit_slippage_bps` (both adverse). The report shows:

* **raw observational hypothetical R** — only clean terminal proposed-level
  observations: a proved stop or all proposed targets reached, normalized by the
  proposed per-unit risk;
* **friction-adjusted hypothetical R** — the same endpoints after the declared
  two-sided fee/slippage scenario, still normalized by the original proposed
  risk.

Neither quantity is realised P&L or profit. There is no position sizing,
leverage, balance, account, fill, order, partial-exit, equity curve, or money
management model. `STOPPED_AFTER_TARGETS` is excluded from R because Step 7 has
no partial-exit policy. Every rate gives its numerator and denominator; samples
below the configured reporting floor retain counts but withhold percentage and
descriptive summaries.

Each report includes source candle/date ranges and counts, dataset/config/report
fingerprints, rule/config identities, exact split boundaries, candidate/setup
and plan/refusal counts, completed/unresolved/ambiguous/incomplete counts,
entry/target/stop rates, raw and friction R distributions, and a development vs
out-of-sample comparison. It also provides diagnostic-only breakdowns by setup
family, long/short direction, timeframe, Step 3 trend, causal expanding-median
volatility label, and calendar period. The volatility label is descriptive and
never feeds Steps 3–6.

Warnings make small samples, ambiguity/incompleteness, plans without a
within-cohort observation, positive R concentrated in one family or calendar
period, configured-friction sensitivity, and development-to-out-of-sample raw-R
deterioration visible. Warnings do not modify or tune the strategy.

### Validation UI and API

The Step 10 navigation has a read-only **Validation** page. It calls
`GET /api/validation` and is explicitly headed **HISTORICAL VALIDATION — NOT
LIVE PERFORMANCE**. It displays dataset/fingerprint and split information,
development and out-of-sample samples, raw and friction-adjusted hypothetical
metrics, ambiguity/incomplete totals, descriptive breakdowns, and warnings. It
has no decision, execution, account, balance, leverage, or optimisation control.

The endpoint accepts optional explicit split timestamps plus
`observation_horizon_candles`, `minimum_sample_size`, `fee_bps`,
`entry_slippage_bps`, and `exit_slippage_bps`. All four timestamp fields are
required together when selecting an explicit split. No upstream strategy
threshold is accepted by the web API.

### Limitations

Validation can only test the stored, closed OHLCV history and the deterministic
rules already present. It cannot establish intrabar ordering, fills, latency,
liquidity, changing exchange fees, funding, market impact, data availability at
the original wall-clock retrieval time, or a causal economic edge. It does not
optimize thresholds or search parameters. Missing history, few setups, a weak
out-of-sample result, or a failed strategy are reported honestly.

**Historical performance does not establish future profitability.**

## Step 12 — live forward paper testing (closed-candle observations, no orders)

Step 12 turns the existing system into a **live forward paper tester**: at every
confirmed base-timeframe close it re-uses the unchanged Step 2–9 pipeline, records
an immutable forward observation of what the deterministic rules concluded, and
then follows the resulting paper plans through later closed candles. It is an
observation and record-keeping layer. It has no order path, no exchange
credentials, no account/balance/position access, no leverage or sizing, and no
automatic strategy change.

**PAPER OBSERVATION — NO REAL ORDER. LIVE FORWARD VALIDATION — NOT REAL
PERFORMANCE.**

### Architecture and data flow

```text
public OHLCV (kraken, no API key, closed candles only)
        │  Step 2 MarketDataService  (dedupe / paginate / gap report / raw archive)
        ▼
ohlcv_candles (Step 2 storage, unchanged, never rewritten)
        │  runner decides a close boundary has passed
        ▼
forward_testing.ForwardTestService.run_once()
        ├── Step 3 market structure      (unchanged)
        ├── Step 4 pattern & liquidity   (unchanged)
        ├── Step 5 setup qualification   (unchanged)
        ├── Step 6 trade planning        (unchanged, only for QUALIFIED setups)
        ├── Step 9 explanation           (unchanged, facts only)
        ├── append forward cycle + observations (immutable)
        └── update earlier paper-plan outcomes with newly closed candles
        ▼
forward ledger tables  ->  read-only dashboard API/UI + `status` CLI
        │
        └── compared side by side with Step 11 historical validation (never merged)
```

The forward ledger is **separate from** raw Step 2 market data, Bailey's Step 7
decision journal, and the Step 11 validation report. Step 12 never writes to
`journal_records`/`journal_decisions`/`journal_outcomes`/`journal_outcome_events`,
never modifies a stored candle, and never rewrites an earlier forward row.

### Closed-candle policy and market data

* Only candles whose **full interval has closed** are ever analysed. The runner
  never inspects or concludes from the candle still forming.
* Data is **public only** (Kraken public OHLCV through the existing Step 2
  adapter). No API key, secret, or private endpoint is required or read.
* Candles come through the existing Step 2 service and storage: duplicate
  de-duplication, closed-candle filtering, raw-response archiving, and gap
  reporting are inherited unchanged. No new fetch, parsing, or storage path was
  added.
* **Bootstrap is bounded and explicit in the ledger.** With no stored history
  the runner downloads the newest `bootstrap_candles` (default 240) closed
  candles ending at the latest fully closed candle — the deepest default that
  keeps the first correctness-first Step 5 replay bounded, and never fewer than
  the configured `minimum_history_candles` precondition. A `--backfill-start`
  instant is aligned up to a candle open; an unaligned instant is never allowed
  to fail the whole pass, and nothing before the requested instant is fetched.
  The downloaded range and counts (including `excluded_open_count` for the
  still-forming candle) are recorded on the pass heartbeat.
* UTC datetimes and exact `Decimal` prices are preserved end to end.
* Network/rate-limit failures are retried conservatively (default: 3 attempts
  with backoff). If the refresh still fails, the pass processes only candles that
  are **already stored**, records the error on the heartbeat and status, and says
  so; a missing or unavailable candle is **never fabricated**.
* Staleness is explicit: the status/ledger compare the latest stored closed
  candle with the expected latest close and report a data-health verdict
  (`CURRENT`, `STALE`, `INCOMPLETE`, `HISTORICAL`, `UNKNOWN`) with a detail
  sentence and a staleness count. Stale or incomplete data cannot silently
  produce a fresh conclusion — a close with no stored candle is recorded as an
  explicit `MISSING_CANDLE` cycle with no snapshot, no observation and no plan,
  and a close whose analysed window has a gap has Step 6 planning withheld.
* A close whose data was incomplete or stale is **retryable**: once the candle
  actually arrives, that same boundary is re-analysed and the recovered
  conclusion is appended as its own row. A boundary that already has a complete
  conclusion is never recomputed from changed data.

### One decision per close, restart-safe recording

Every closed candle produces at most one forward cycle, and cycles are written in
chronological order. Identity is deterministic — exchange, symbol, timeframe,
close boundary, candle open time, cycle status, terminal snapshot/plan content
and the strategy/config fingerprints — so:

* the same candle with the same strategy version and configuration always yields
  the same logical result;
* re-running the runner (restart, crash, or a second process) re-inserts nothing:
  existing rows are verified against the deterministic projection, and a mismatch
  raises an explicit `ForwardConflict` instead of overwriting history;
* duplicates in the downloaded candle stream cannot create duplicate decisions;
* a candle added later cannot change a decision already recorded at an earlier
  close.

After downtime the runner **catches up chronologically**: it inspects the last
recorded boundary, finds the missed closes, and processes them in order up to a
documented cap (default 720 closes per pass; the remainder is reported as
`pending_catch_up_boundaries`). Gaps are never silently skipped, and unresolved
paper plans keep being tracked across restarts.

### Recorded forward observation

Each observation is immutable and carries:

* deterministic observation id, exchange, symbol, timeframe, close boundary
  (`as_of`), the analysed candle's open time and the recording instant;
* strategy/qualification/planning rules versions and configuration fingerprints;
* the source-data fingerprint and Step 3/4 snapshot references;
* the Step 5 state, setup id, family, direction, and the evidence/vetoes that
  produced it;
* the Step 6 plan id/state and, in a frozen paper plan, entry, stop,
  invalidation, targets, risk per unit and R multiples;
* the Step 9 explanation fingerprint;
* data-health/freshness verdict and detail;
* the current paper outcome and its version chain, ambiguity/incomplete flags;
* the friction assumptions and version in force when the plan was recorded.

Nothing about a past belief is silently rewritten. Outcome evolution is
**append-only**: a later observation of the same paper plan is a new version
(`forward_paper_outcomes`), and version numbers never retreat — a previously
observed window is only re-checked when it was recorded as incomplete.

### Paper-trade semantics

A paper observation is created **only** when the unchanged Step 6 result is
`PLANNABLE`. `NO_SETUP`, `WATCH`, and `QUALIFIED`-but-`NO_PLAN` closes are
recorded with their evidence but never produce a paper plan.

Paper tracking is observational and level-based, using the existing Step 7
outcome semantics over subsequent **closed** candles only:

* a paper plan is resolved only when the proposed levels are actually reached;
* the proposed entry is **not** assumed to have been filled — an untouched entry
  ends as `ENTRY_NOT_REACHED`, and a plan invalidated before entry is recorded as
  such rather than counted as a trade;
* if OHLC data cannot prove the ordering of entry and exit (or stop and target)
  inside the same candle, the outcome is `AMBIGUOUS`; the favourable
  interpretation is never chosen;
* missing candles inside a window make the outcome `INCOMPLETE_DATA` rather than
  guessed, and an unproven terminal level stays
  `TERMINAL_LEVEL_NOT_ESTABLISHED`;
* all eight existing `OutcomeStatus` values are preserved and shown, including
  `STOPPED_AFTER_TARGETS` (excluded from R because Step 7 has no partial-exit
  policy) and `OPEN_AT_CUTOFF`.

A paper observation is never described as an executed trade, a fill, a position
or realised P&L.

### Friction semantics

Friction reuses the existing, versioned Step 11 `FrictionAssumptions`
(`fee_bps` charged on both sides, plus adverse `entry_slippage_bps` and
`exit_slippage_bps`). Reports keep two clearly separate quantities:

* **raw observational R** — hypothetical R from clean, proved proposed-level
  endpoints only, normalized by the proposed per-unit risk;
* **friction-adjusted hypothetical R** — the same endpoints after the declared
  two-sided cost scenario.

Neither is realised P&L or profit. There is no position sizing, capital
allocation, leverage, margin, liquidation, fill, or order model anywhere in
Step 12. The friction version/fingerprint in force is stored on every paper plan,
and the dashboard reuses the recorded assumptions when all stored plans agree on
them so a different cost model cannot silently change an old observation's
numbers.

### Live vs historical: never merged

The Step 12 comparison endpoint places the **HISTORICAL VALIDATION** numbers from
the unchanged Step 11 replay next to the **LIVE FORWARD PAPER OBSERVATIONS** from
the ledger. Setup / `WATCH` / `QUALIFIED` / `PLANNABLE` counts, entry-reached and
not-reached, target-hit and stop rates, ambiguous/incomplete/unresolved counts,
raw and friction-adjusted R, and breakdowns by family, direction, timeframe,
trend and volatility context are reported per side, each with its own
denominators and its own label. The two sides are never combined into one
performance number, and no live result is ever used to adjust a
strategy parameter.

The forward report always shows its sample size and denominators, and it
withholds percentages/descriptive summaries below the configured reporting floor
while still showing the raw counts. Ambiguous, incomplete, unresolved and
open observations are always visible; they are never hidden or reclassified.
Unfavourable or empty results are reported honestly, including the warnings that
the sample is too small to conclude anything.

**Paper trading and historical performance do not establish future
profitability.**

### Version separation

Strategy/qualification/planning versions and configuration fingerprints are
stored on every cycle and observation. If the ledger contains more than one
incompatible fingerprint cohort, the report refuses to blend them: results are
shown per version cohort, unfavourable versions stay separate, and a combined
figure is either explicitly unavailable or carries a warning naming the
difference. Old forward decisions are never retroactively recomputed under new
logic.

### Running the forward tester

Everything runs locally; there is no Docker, cloud service, queue, or scheduler.

```bash
alembic upgrade head                                   # additive: 0003_journal -> 0004_forward_testing

# process the closes that are already pending, then exit (safe first run)
python -m trading_assistant.forward_testing run --once

# continuous closed-candle polling until Ctrl-C / SIGTERM
python -m trading_assistant.forward_testing run

# inspect market-data health, runner status and recorded sample sizes
python -m trading_assistant.forward_testing status
```

Useful options: `--timeframe`, `--symbol`, `--interval-seconds` (poll cadence,
default 60), `--no-refresh` (process only already-stored closed candles),
`--start-at <ISO-8601 UTC boundary>` (begin the ledger at a chosen close),
`--backfill-start <ISO-8601 UTC instant>` (where the initial public download
starts when no history is stored; an instant that is not a candle open is moved
**up** to the next candle open, never earlier),
`--bootstrap-candles <N>` (newest closed candles the runner seeds itself with
when no history is stored and no `--backfill-start` is given, default 240),
`--horizon-candles`, `--minimum-sample-size`,
`--max-catch-up-candles`, `--fetch-max-attempts`,
`--retry-backoff-seconds`, `--stop-after-errors`, and the three friction flags
`--fee-bps`, `--entry-slippage-bps`, `--exit-slippage-bps`.

**First run on an empty database.** The runner seeds its own history: when no
candle is stored for the instrument/timeframe and no `--backfill-start` was
given, it downloads the newest `--bootstrap-candles` (default 240) *closed*
candles from the public endpoint before processing the latest close, so
`run --once` works on a fresh database instead of reporting `NO_DATA`. This
changes only how far back the *download* starts — every downloaded row still
passes the unchanged Step 2 validation, gaps stay explicit, and the
still-forming candle is still excluded before anything is stored. On a
rolling-window endpoint (Kraken public OHLC) the requested start bounds that
local validation rather than the exchange request: the endpoint is asked for its
newest 720 entries without a date cursor, so the oldest part of a longer window
is reported as an explicit gap instead of failing the whole pass. Once history
exists, each pass requests only the newly closed candles after the latest stored
one. Deeper history can be requested explicitly with `--backfill-start`; note
that the first pass replays the whole stored history through the unchanged
Step 5 qualification engine, so a deep backfill makes that first pass
correspondingly slower.

The runner logs each pass (status, cycles, observations, paper plans, outcome
versions, pending catch-up, data health), retries public fetches conservatively,
stops cleanly on SIGINT/SIGTERM after finishing the current pass, and writes a
`STOPPED` heartbeat. Restarting it is always safe: it continues from the last
recorded boundary and never duplicates a decision or a paper plan.

Two processes must be running for a complete live view:

1. the **forward runner** (`python -m trading_assistant.forward_testing run`) —
   the only component that fetches candles and writes the forward ledger;
2. the **dashboard** (`python -m trading_assistant.web`, `http://127.0.0.1:8040`)
   — read-only presentation.

If the runner is not running, the dashboard still loads, says the runner has not
reported, and shows the last recorded state with explicit staleness — it never
invents live data.

### Dashboard

Step 10 navigation adds a **Live / Paper** view (`GET /api/forward`), and the
dashboard home shows a compact live/paper card fed by the same endpoint. The view
is headed **LIVE MARKET DATA** and **PAPER OBSERVATION — NO REAL ORDER**, with
**LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE** on the forward statistics. It
shows:

* market-data health, latest closed candle, expected latest close,
  staleness/intervals detail, and the closed-candle policy;
* runner status and last successful processing time;
* the current setup/planning state and the current paper plan (or its absence);
* unresolved paper observations, latest forward observations with their outcome
  versions, forward sample size, and forward statistics with denominators;
* a user-triggered **historical vs forward** comparison with both sides labelled
  and both denominators, plus warnings and limitations.

The Step 12 endpoints are strictly read-only: they never fetch candles, never run
a forward pass, never write to the ledger, and accept no strategy thresholds.
Stale or broken data is visually obvious (tone-coded status banner) and no fake
or placeholder live data is ever rendered; an empty ledger renders an explicit
empty state.

### Safety boundaries and limitations

Step 12 contains **no** order submission, no exchange authentication, no private
API, no deposit/withdrawal/wallet, no balance or account access, no positions, no
leverage/margin/liquidation controls, no position sizing or capital allocation,
no hidden execution path, and no `eval`/dynamic execution. Step 9 remains
explanation-only: it can explain recorded state, evidence, plans, statistics and
limitations, but it cannot invent facts, alter levels or outcomes, submit
anything, choose size, claim guaranteed profitability, or accept a setup —
missing information stays UNKNOWN. Step 10's web security posture is unchanged
(strict CSP, no inline script, JSON error envelopes), and the dashboard remains
private-by-design.

Like Step 11, forward testing cannot establish intrabar ordering, real fills,
latency, liquidity, future fees, funding, or market impact, and it only observes
the stored public history plus the deterministic rules already present. Missing
candles, small samples, stale feeds, gaps, ambiguous bars, and poor live results
are all reported as such.

**Deployment note:** the dashboard and runner are intended for local use. If they
are exposed beyond localhost, add real authentication and a secured deployment
(reverse proxy/TLS); neither application ships authentication, because it
deliberately ships no secrets or credentials.

**Paper trading and historical performance do not establish future
profitability.**

## Operations runbook (forward-first daily operation)

This section is the operator-facing consolidation of the audit: every
configuration surface, the exact health semantics, the refresh/caching
contract, the smoke procedure with its results, and the honest list of what
the system cannot tell you.

### Configuration inventory and tuning audit

There are exactly two configuration surfaces. Neither contains a
performance-fitted trading threshold, and the repository contains no
optimizer, grid search, or hyperparameter fitting of any kind (verified by
source audit).

**1. Runtime settings** (`src/trading_assistant/config.py`, prefix
`TRADING_ASSISTANT_`, optional `.env`): identity and plumbing only.

| Variable | Default | Meaning |
|---|---|---|
| `TRADING_ASSISTANT_SYMBOL` | `BTC/USDT` | Instrument |
| `TRADING_ASSISTANT_EXCHANGE` | `kraken` | Public-data source id |
| `TRADING_ASSISTANT_DATABASE_URL` | `sqlite:///data/trading_assistant.sqlite3` | App database |
| `TRADING_ASSISTANT_DEFAULT_TIMEFRAME` | `1h` | Base timeframe |
| `TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES` | `5m,15m,1h,4h,1d` | Accepted timeframes |
| `TRADING_ASSISTANT_RAW_DATA_DIR` | `data/raw` | Raw exchange payloads |
| `TRADING_ASSISTANT_MARKET_DATA_PAGE_LIMIT` | `720` | Download page size |
| `TRADING_ASSISTANT_MARKET_DATA_MAX_PAGES` | `10000` | Download page cap |
| `TRADING_ASSISTANT_LOG_LEVEL` | `INFO` | Logging verbosity |

**2. Frozen step parameters** (dataclass defaults in each
`parameters.py`, each guarded by a `RULES_VERSION` string that is fingerprinted
into journal records and forward cycles, so results from different rules are
never mixed):

- Step 3: swing windows 2/2 strict; trend swing_count 2; range lookback 120,
  2 touches/side, 1% tolerance, 10% max width, 20-candle min span;
  zones from 40 swings, 1% tolerance, max 12; ATR(14); volume SMA(20).
- Step 4: breakout/retest/equal-level tolerances 0.1–0.5%; confirmation 1
  candle; failure/retest windows 10 candles; pattern depth/prominence 1%.
- Step 5: setup lifetimes 10 bars per family; min relative volume 1.0;
  max ATR 10% of price; HTF alignment optional and off by default.
- Step 6: entry at plan close; no stop buffer; up to 2 structural targets
  plus equal levels; 2R fallback target.
- Step 8: minimum sample 30; 8 decimal places; quartiles.
- Step 11: 30% out-of-sample fraction; 20-candle observation horizon;
  minimum sample 20; zero-fee/slippage default friction (explicitly labelled
  hypothetical when changed).
- Step 12: 20-candle horizon; 720-candle catch-up cap; 60s poll;
  3 fetch attempts; stop after 10 errors; 240 bootstrap candles.

Tuning-audit verdict: every default is a structural or measurement constant
(windows, tolerances, sample floors). Changing one changes the version
fingerprint and therefore starts a separated evidence cohort; nothing in the
pipeline can silently re-fit history.

### Dashboard payload reference: market_state, scenario, explanation

`GET /api/dashboard` carries three descriptive sections beyond the trading
state. All three are pure projections of deterministic Step 3–6 objects at
the current boundary — no scores, no predictions:

- `market_state`: trend (direction, reason, sufficiency, frame-over-frame
  transition and momentum), volatility (ATR and ATR% with
  expanding/contracting/unchanged/unknown), volume (relative volume with
  strengthening/weakening/unchanged/unknown), range (active flag, detected
  band, formed/broken/held/redefined/absent transition), nearest zones and
  equal levels, event-catalog counts with latest breakouts/sweeps/retests,
  fresh-at-this-close breakout attempts/acceptances/rejections/sweeps, and
  higher-timeframe contexts verbatim. Unavailable inputs stay visibly
  unavailable (`available: false` or explicit UNKNOWN reasons).
- `scenario`: `doing_now` (one deterministic paragraph), `bot_seeing`
  (aggregate state plus every live setup's rules, age, and expiry),
  `strengthen_bullish`/`strengthen_bearish` (pending required rules of
  developing setups plus what each family needs to start), `waiting_for`
  (merged pending evidence with reasons and setup counts), `invalidate`
  (per-setup expiry, invalidation/lifecycle evidence, and the exact Step 6
  entry/stop/invalidation of the selected setup).
- `explanation`: the Step 9 grounded local-renderer output (headline,
  fact-cited sections, limitations, renderer provenance). The dashboard
  renders it verbatim as a collapsible card; it can only repeat backend
  facts (see Step 9).

### Freshness, SYSTEM OK, and runner states (exact)

Data freshness (`src/trading_assistant/web/freshness.py`) compares the latest
stored candle open against the expected latest closed boundary from the
server clock: CURRENT (stored equals expected), STALE (stored stops early,
with a staleness count), HISTORICAL (the requested instant is old),
UNKNOWN (no stored candles). The header pill shows SYSTEM OK only when all
of these hold: dashboard freshness CURRENT, market window complete, forward
`data_health` CURRENT, zero pending catch-up boundaries, and a runner
heartbeat with status STARTED, PROCESSED, or IDLE and no recorded error.
Anything else — never-run, stale data, a recorded runner error, a missing
payload, a single pending boundary — is SYSTEM WARNING.

The System-details card distinguishes three runner presences: `unavailable`
(no forward status payload — every runner field UNKNOWN), `never run`
(backend reports `runner: null`), and `reported` (state, detail,
server-computed heartbeat age, last error, all verbatim). Pending catch-up
is always a counted label ("N closed candles not yet processed").

### Interface caching (no query hacks, no service worker)

The HTML shell (`/`) and every file under `/static/` are served with
`Cache-Control: no-cache`: the browser revalidates each file by ETag
(answered `304` when unchanged) before reuse, so a refresh always renders
the latest interface while unchanged files cost one conditional round-trip.
There are no `?v=` parameters, no service worker, and no build step; API
responses are dynamic JSON and carry no cache headers. Covered by
`tests/test_web_cache.py`, including a 304 round-trip test.

### Daily operation

1. Refresh market data (public Kraken candles only), then run the forward
   tester continuously (`python -m trading_assistant.forward_testing run`)
   or once per close (`... run --once`). Each confirmed closed candle is
   recorded exactly once; restarts are safe and idempotent.
2. Serve the dashboard (`python -m trading_assistant.web --host 0.0.0.0
   --port 8040`) and check the header: symbol/timeframe, last completed
   candle, close, and the SYSTEM pill. Expand System details on any warning.
3. Never delete or rewrite database history. Schema changes go through the
   `migrations/versions/` chain (`0001`→`0004`); to repair a database,
   migrate a fresh file and copy rows over — never hand-edit alembic state.

### Smoke procedure, results, and visual-test status

Procedure (2026-10-06, sandbox without usable network, so exchange download
was unavailable): migrated a scratch database, inserted 300 deterministic
synthetic hourly candles ending at the latest closed boundary (clearly
labelled synthetic — no real market data was available), ran the real
`forward_testing run --once` CLI (1 close processed, 7 observations,
5 paper plans), served the real web app, and exercised every route.

Results: `GET /` 200 with `no-cache`; static assets 200 with ETag and
`no-cache`; `/api/meta`, `/api/dashboard` (303KB, QUALIFIED/PLANNABLE,
CURRENT, 300 candles, full `market_state`/`scenario`/`explanation`),
`/api/forward`, `/api/market/candles`, `/api/settings`,
`/api/journal/records` (honest empty), `/api/statistics` all 200;
`/api/validation` 200 (66MB replay over the synthetic history in ~94s —
operators should expect this endpoint to be slow and heavy on large
histories). The smoke run caught one real integration defect (frontend
cards written against misremembered payload shapes — fixed and re-verified
by rendering the actual smoke responses through the real dashboard renderer
with zero object leaks).

Visual-test status: **no browser visual testing was performed — no browser
is available in this environment.** Rendering is verified headlessly: 62
zero-dependency node tests (including full-dashboard MockNode renders),
Python contract tests over the shipped bundle, and the real-bytes render
check above. Before trusting any visual change, open the dashboard in a real
browser at desktop and 360px widths and confirm the chart draws.

### Information gaps (what the system cannot tell you)

- No live or intrabar price: only confirmed closed candles are ever
  analysed, by design.
- No win rate, expectancy, drawdown, or realised P&L: only denominated
  outcome observations and raw/friction-adjusted observational R, by design.
- A fresh forward ledger starts empty: the N≥30 reporting floor needs
  weeks of runner operation before combined figures appear.
- Paper observations are OHLC touch records, not fills: no order book,
  latency, liquidity, or market-impact modelling.
- One instrument/timeframe at a time (BTC/USDT 1h); higher-timeframe
  contexts are optional and off unless requested.
- `/api/validation` replays are expensive (tens of MB, tens of seconds on
  hundreds of candles) and are fetched only when the dashboard's
  Historical-validation disclosure is expanded.

### Security posture (re-confirmed)

No secrets, credentials, tokens, or private API paths exist anywhere in
`src/` (audited). The exchange surface is public market data only; the
application never places, modifies, or cancels orders and never reads
balances, positions, or accounts. The web layer keeps strict CSP, no inline
scripts, text-only rendering (`innerHTML` is banned and contract-tested),
and JSON error envelopes. The app remains local-first: expose beyond
localhost only behind real authentication and TLS, per the Step 12
deployment note.
