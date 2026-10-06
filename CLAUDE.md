# Prague City & Energy Analytics

Portfolio project demonstrating analytics engineering (target roles: MSD, BRP, Billigence-style teams).
Goal: production-like ELT pipeline with dbt best practices, data quality tests, lineage, CI and a dashboard.
Author works on macOS in PyCharm. Communicate with me in Russian; code, comments, commit messages and docs in English.

## Business questions
1. How are electricity prices and weather related to air pollution across Prague districts?
2. Where is bicycle traffic growing?

## Status
Phases 1–7 done (one branch per phase, stacked: `feat/phase-2-extractors` … `feat/phase-7-ci`; only phase 2 is
pushed, `main` holds the scaffold). Next: phase 8 (README).

## Stack
- Python 3.12, dependency management with **uv** (`uv add`, `uv run`; never use pip directly)
- Extraction: `requests` + `tenacity` (retries), `entsoe-py`, `pandas`/`pyarrow` → Parquet
- Warehouse: DuckDB (`data/warehouse.duckdb`) for dev; Snowflake as dbt target `prod` (profile + `snowflake` extra only, no real deploy)
- Transformation: dbt-core + dbt-duckdb, `dbt_utils`
- Dashboard: Streamlit + Plotly
- Profiling: ydata-profiling 4.18 in a self-contained PEP 723 script (`uv run --script`, own pinned env with
  pandas < 3 and `setuptools<81` for `pkg_resources`); the project env stays on pandas 3. `ydata_profiling` is
  deprecated upstream in favour of `fg-data-profiling` (same authors, also pandas < 3) — candidate for a later switch.
- CI/orchestration: GitHub Actions (PR CI + daily cron). Airflow DAG only as a documented example, not run.
- Lint: ruff (Python), sqlfluff with the dbt templater (SQL, dialect duckdb), actionlint (workflows)

## Data sources
All secrets are in `.env` (never commit it; `.env.example` with empty values is committed).
Before writing an extractor, check the current official docs / try one request — do not guess response formats.

| Source | Data | Auth | Module |
|---|---|---|---|
| Golemio API v2 (`https://api.golemio.cz`) | air quality stations + measurements, bicycle counters + detections, city districts | header `X-Access-Token: $GOLEMIO_API_KEY` | `extract/golemio_*.py` |
| ENTSO-E Transparency Platform | day-ahead prices and actual total load, bidding zone CZ | `$ENTSOE_API_KEY` | `extract/entsoe.py` |
| ČNB public FX API (`https://api.cnb.cz/cnbapi/exrates/daily`) | official daily FX rates (EUR/CZK + ~30 currencies) | none | `extract/cnb_fx.py` |
| Open-Meteo Archive API (`https://archive-api.open-meteo.com/v1/archive`) | hourly weather at the AQ station coordinates | none | `extract/open_meteo.py` |

### Golemio air quality — verified facts (2026-10)
- OpenAPI spec: `https://api.golemio.cz/docs/static/output-gateway/openapi-public.json`. Rate limit documented as 20 requests / 8 s per key, but bursts of 20 get HTTP 429 (`Retry-After: 8`) → we throttle to 15 / 8 s; `limit` max 10000 + `offset`.
- `GET /v2/airqualitystations` → GeoJSON FeatureCollection (17 stations, `coordinates=[lon, lat]`); reference list frozen at `updated_at` 2026-08-12.
- `GET /v2/airqualitystations/history?from&to&limit&offset` → array `{id, updated_at, measurement{AQ_hourly_index, components[{type, averaged_time{averaged_hours, value}}]}}`; `from`/`to` filter on `updated_at`.
- Real types differ from the docs: `AQ_hourly_index` (e.g. "1B") and `averaged_hours` are strings. All units µg/m³ (`/componenttypes`).
- `averaged_hours` switched on 2026-08-12 ~06:00 UTC: before = 3-hour running averages (17 stations), after = 1-hour values (13 stations). Never filter on it blindly — it removes a whole period.
- `updated_at` is the publication time (`HH:50` UTC since ~2026-08-12, `HH:10` before, occasional `HH:40` re-publications), not the measurement hour → derive the measured hour in silver. ~23 rows/station/day; some hours are missing.
- Bronze: `golemio_air_quality_stations/` (snapshot, partition = run date UTC) and `golemio_air_quality_history/` (partition = UTC day of `updated_at`). Nested objects (`measurement`, `geometry`) are stored as JSON strings.

### Golemio bicycle counters — verified facts (2026-10)
- `GET /v2/bicyclecounters` → GeoJSON FeatureCollection, 40 counters: `properties {id, name, route, updated_at, directions[{id, name}]}` (79 directions); some counters stale (`updated_at` 2026-07-06).
- `GET /v2/bicyclecounters/detections?from&to&limit&offset` → array `{id (direction), locations_id (counter), measured_from, measured_to, value, value_pedestrians, measurement_count}`, **5-minute** slots, UTC; `from`/`to` filter on `measured_from`. ~19.3k rows/day (34 active counters, 67 directions × 288 slots) → 2 pages; page order is stable. `value` null in ~12 % of rows; `value_pedestrians` only for some counters. `aggregate=true` exists but is not used (bronze stays raw).
- **Direction `id` is not unique**: `camea-PN-VY`/`camea-PN-BR` are measured by two counters (`camea-BC_PN-VYBR` cycle path, `camea-BC_PN-VYBR2` road). Natural key = (`locations_id`, `id`, `measured_from`). The catalogue lists `{"id": null}` as the direction of `camea-BC_PN-VYBR` although it reports detections.
- Bronze: `golemio_bicycle_counters/` (snapshot, `directions` as JSON string) and `golemio_bicycle_detections/` (partition = UTC day of `measured_from`, counts forced to float64). Incremental runs re-load the last 3 days (late uploads).

### Golemio city districts — verified facts (2026-10)
- `GET /v2/citydistricts?limit&offset` → GeoJSON FeatureCollection, 57 districts: `properties {id (int), name, slug, updated_at}`, `geometry` = boundary **Polygon** (~1.5 MB; whole list refreshed at once).
- Bronze: `golemio_city_districts/` (snapshot, polygon as GeoJSON string); no state, `--start/--end` ignored.
- Joins: AQ stations → districts by `district` = `slug` (all 13 match); bicycle counters have no district → point-in-polygon in silver (all 40 match).

### ENTSO-E — verified facts (2026-10, entsoe-py 0.8.1)
- API `https://web-api.tp.entsoe.eu/api`; the key is a **query parameter** (`securityToken`), so requests/entsoe-py exception messages and entsoe-py DEBUG logs contain it. Always call through `extract.entsoe.call_entsoe` (sanitised `EntsoeRequestError`, no exception chaining); the `entsoe` logger is pinned to WARNING.
- CZ zone `10YCZ-CEPS-----N`. Day-ahead prices (A44): EUR/MWh, PT15M since Oct 2025 (96 points per delivery day), sent as two identical TimeSeries (entsoe-py dedups); prices can be negative. Actual load (A65/A16): MW, PT15M. No data → HTTP 200 + Acknowledgement doc → `NoMatchingDataError` → day skipped.
- entsoe-py retries only connection errors (and sleeps `retry_delay`); we set `retry_count=1, retry_delay=0` and retry 429/5xx/network errors with tenacity. Its `truncate(after=end)` is end-inclusive → we keep `[start, end)`.
- Bronze: `entsoe_day_ahead_prices/`, `entsoe_actual_load/`, partition = **delivery day in Europe/Prague** (23/25 h on DST days), rows `ts_utc, value, unit, area_code`. Prices default end = tomorrow (published ~13:00 CET).

### ČNB FX — verified facts (2026-10)
- OpenAPI: `https://api.cnb.cz/cnbapi/api-docs`. `GET /cnbapi/exrates/daily?date=YYYY-MM-DD&lang=EN` → `{"rates":[{validFor, order, country, currency, amount, currencyCode, rate}]}` (30 currencies). `rate` is CZK per `amount` units (HUF, JPY, … use `amount=100`) → normalise in silver.
- Weekends/holidays (e.g. 2026-09-28) **and future dates** return the last published table with an older `validFor` → the extractor clamps `end` to today. Today's table appears ~14:30 Prague time. Invalid params → HTTP 400 JSON `{errorCode: VALIDATION_ERROR, ...}`.
- Bronze: `cnb_fx_daily/`, partition = requested calendar day, raw rows; weekend partitions repeat Friday's rates (`validFor` = Friday).
- Not used: the ČNB ARAD time-series API (`/aradb/api/v1`, key in the query string) has no daily official EUR/CZK fixing (only a snapshot "hedging of exports" series ending 2026-08), so the public API is the FX source.

### Open-Meteo — verified facts (2026-10)
- `GET /v1/archive?latitude=a,b,…&longitude=a,b,…&start_date&end_date&hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_direction_10m&timezone=UTC`. Several coordinates → a **list** in request order with no location id (station id matched by position); one coordinate → a single object. We chunk by 31 days.
- Per location: `latitude/longitude/elevation` of the **model grid cell** (differs from the requested point), `hourly_units` (°C, %, mm, **km/h**, °), `hourly.time` = `YYYY-MM-DDTHH:MM` in UTC. Errors → HTTP 400 `{"error": true, "reason": ...}`.
- Recent days (incl. today) come from model/forecast data and get revised → incremental runs re-load the last 3 days.
- Bronze: `open_meteo_weather_hourly/`, partition = UTC day, one row per station × hour; weather variables forced to float64. Requires the `golemio_air_quality_stations` snapshot (run Golemio first).

## Architecture
```
Python extractors (incremental by date, retries, idempotent)
   → bronze: data/bronze/<source>_<entity>/load_date=YYYY-MM-DD/part.parquet  (raw + _loaded_at UTC)
   → dbt silver: typing, UTC timestamps, dedup (qualify row_number), 15min→hourly, FX forward-fill
   → dbt snapshot: SCD2 of station dimension (strategy=check)
   → dbt gold (star schema):
       fact_air_quality_hourly, fact_weather_hourly, fact_energy_price_hourly, fact_bike_traffic_daily,
       dim_station (SCD2), dim_district, dim_date, dim_bike_counter, mart_air_vs_energy_daily
   → Streamlit app + profiling reports
```

## Conventions
### General
- Repo layout: `extract/`, `dbt/` (dbt project `prague`), `app/`, `profiling/`, `orchestration/`, `.github/workflows/`, `tests/`, `data/` (gitignored; static reference data lives in `dbt/seeds/`).
- Logging via `logging`, no prints. Type hints everywhere. Small pure functions that are unit-testable with pytest (mock HTTP).
- All timestamps stored in UTC (`*_ts_utc`); local Prague time only as derived column. This matters for DST and the gap test.
- Direct imports must be declared dependencies (e.g. `duckdb`, `beautifulsoup4`), not rely on transitive ones.

### Extract (`extract/`)
- One module per source; shared helpers in `extract/common.py` (HTTP with retries, pagination, `write_bronze`, state) and `extract/golemio.py` (Golemio session, rate limiter, raw rows).
- State in `data/bronze/_state.json`; CLI `uv run python -m extract.run --source <name> [--start YYYY-MM-DD | --days N] [--end YYYY-MM-DD]` (default: incremental from state, 90 days on first run). Re-running the same date overwrites the partition, never duplicates.

### dbt (`dbt/`)
- Naming: `stg_<source>__<entity>` (silver), `dim_*`, `fact_*`, `mart_*` (gold). Every model has a YAML entry with description and tests.
- Sources read bronze Parquet via dbt-duckdb `external_location` (bronze root `../data/bronze` relative to `dbt/`, override `BRONZE_ROOT`); every source has `loaded_at_field: _loaded_at` and freshness thresholds. `make dbt` reports stale sources but still builds (one stale API must not block the others).
- Silver is materialized as **tables** (views would keep relative `read_parquet()` paths and break outside `dbt/`). The dev profile sets DuckDB `TimeZone: UTC` (date_trunc on TIMESTAMPTZ uses the session zone) and loads `json`, `spatial`.
- Silver keys/decisions: AQ measurements = (station_id, measured_hour_start_ts_utc, pollutant), measured hour = hour ending at `date_trunc('hour', updated_at)` (assumption: published 10–50 min after the hour), latest re-publication wins; bicycle detections = (counter_id, direction_id, interval_start_ts_utc), the null catalogue direction is dropped; counters → districts via the `st_contains_geojson` macro (adapter.dispatch duckdb/snowflake); ENTSO-E 15 min → UTC hour averages with `n_intervals`; FX = calendar_date × currency, `rate_czk_per_unit = rate / amount`, `dbt_utils.date_spine` + forward-fill. JSON parsing/unnest in staging is DuckDB-specific (Snowflake would need FLATTEN).
- SCD2: `snapshots/snap_golemio__air_quality_stations.yml` (YAML snapshot syntax, check strategy, `hard_deletes: invalidate`).
- Gold: `dim_date` (2025–2027, `date_key` YYYYMMDD, Czech holidays from seed `cz_public_holidays`), `dim_district` (+ `'-1'` Unknown member), `dim_station` (SCD2 versions; first version valid from 1900-01-01 because the snapshot started late; facts join on station_id + hour within [valid_from, valid_to)), `dim_bike_counter`; `fact_air_quality_hourly` (station × hour × pollutant), `fact_weather_hourly` (station × hour — added to the original list, needed for Q1), `fact_energy_price_hourly` (hour; CZK via the ČNB EUR rate of the Prague date, latest rate as estimate for tomorrow), `fact_bike_traffic_daily` (counter × direction × Prague date, `is_complete_day`, DST-aware `expected_slots` 276/288/300, `is_direction_inferred` for the cycle-path counter); `mart_air_vs_energy_daily` (district × Prague date).
- Gold facts are `incremental`, `incremental_strategy='delete+insert'`, with a 3-day lookback (`incremental_lookback()`, var `incremental_lookback_days`).
- Determinism: float `avg/sum` in DuckDB is not bit-for-bit reproducible (parallel order) → use `stable_avg` / `stable_sum` (DECIMAL aggregation) for aggregates that feed incremental models or marts. Verified: incremental == full refresh.
- Required tests: unique / not_null / relationships / accepted_values, plus custom generic tests in `dbt/tests/generic/`: `non_negative` (e.g. PM2.5; prices may be negative — never on day-ahead price) and `no_time_gaps(partition_by, datepart)` (per station, hourly, on UTC) — error on energy/weather, **warn** on AQ (real source gaps, ~1.4k).
- Prefer cross-database macros (`dbt_utils`, `dbt.date_trunc`, `extract(...)`) so the `prod` Snowflake target works with minimal changes.

### App (`app/`)
- `streamlit_app.py` (layout only) + `data.py` (read-only gold queries, pure transforms, unit-tested) + `charts.py` (Plotly builders) + `theme.py` (light/dark palette from the dataviz reference palette; red diverging arm lightness-matched in OKLCH). Reads `data/warehouse.duckdb` read-only (`DUCKDB_PATH` override, relative to `dbt/`).
- Pollutant filter = PM10 (default, 13 stations) / NO₂ / PM2.5 (4 stations only); price vs pollution = two stacked charts on a shared time axis (never a dual axis); correlations = Pearson r of district-day means, ≥ 14 days, with a "correlation ≠ causation" note; bike growth = last 28 vs first 28 complete days (YoY once ≥ 1 year of data exists) plus a per-counter table, because single counters dominate swings (e.g. Smetanovo nábřeží ×13 since mid-August).

### CI and orchestration
- `.github/workflows/dbt_ci.yml` on every PR: `uv sync --locked` → `extract.run --days 7` → `make dbt` → `make lint` → `make test`, dbt artifacts uploaded.
- `.github/workflows/daily.yml` cron 13:30 UTC: restores `data/` (bronze, `_state.json`, warehouse incl. SCD2 snapshot) from the Actions cache → incremental extract → `make dbt` → `make profile` → saves the cache, uploads reports + warehouse.
- Repository secrets: `GOLEMIO_API_KEY`, `ENTSOE_API_KEY` (Settings → Secrets and variables → Actions).
- `orchestration/airflow/dags/prague_city_energy.py`: example Airflow 3 DAG (one task per source, dbt deps → freshness → build, profiling); parsed with Airflow 3.3.2, never run — Airflow is not a project dependency.
- `profiling/profile_gold.py` pins the same DuckDB version as the project (a test enforces it): it reads the project's warehouse file.

## Commands
```bash
uv sync
uv run python -m extract.run                  # all sources, incremental
uv run python -m extract.run --days 7         # last 7 days regardless of state (CI)
cd dbt && uv run dbt deps
uv run dbt source freshness && uv run dbt build   # build = seeds + models + snapshots + tests in DAG order
uv run dbt docs generate && uv run dbt docs serve
uv run streamlit run app/streamlit_app.py
uv run pytest
```
`Makefile` targets: `install`, `extract`, `dbt` (deps → freshness → build), `debug`, `docs`, `profile` (HTML per gold
table + `index.md` → `profiling/reports/`, not committed), `app`, `lint` (ruff + sqlfluff + actionlint; needs
`dbt/profiles.yml`, `dbt deps` and bronze data), `format`, `test`, `clean`.

## Development phases (do them in order, one phase per session/PR)
1. ✅ **Scaffold**: repo layout, `.gitignore`, `.env.example`, `Makefile`, dbt project init, `profiles.example.yml` (dev=duckdb, prod=snowflake via env vars).
2. ✅ **Extractors**: common helpers → Golemio air quality (stations + history) → ENTSO-E prices + load → ČNB FX → Open-Meteo weather → Golemio bicycle counters → districts. Unit tests with mocked HTTP. Done = 90 days of data in bronze, re-run is idempotent.
3. ✅ **dbt silver + snapshot**: sources with freshness, staging models, SCD2 snapshot of stations.
4. ✅ **dbt gold + tests**: star schema, incremental facts, all tests green with `dbt build`.
5. ✅ **Profiling**: HTML/Markdown reports for gold tables in `profiling/reports/`.
6. ✅ **Streamlit**: filters (period, district, pollutant), KPI cards, station map, price vs pollutant time series, pollutant vs wind scatter coloured by price, per-district correlations (with "correlation ≠ causation" note), bike traffic growth (rolling avg by district; YoY once a year of data exists).
7. ✅ **CI**: `dbt_ci.yml` on every PR (sync → extract 7 days → dbt build → lint → pytest), secrets from GitHub Actions secrets; `daily.yml` cron; example Airflow DAG in `orchestration/`.
8. **README**: Mermaid architecture diagram, lineage screenshot, dashboard screenshot, design decisions, how to run, roadmap.

## How to work with me
- Start each phase in plan mode: propose the plan and file list, wait for my OK, then implement.
- After implementing, run the relevant commands yourself and fix errors before reporting back.
- Never print, log or commit API keys. If a key is missing, stop and tell me which env var to set.
- Keep changes small; suggest a conventional commit message at the end of each step.
- If an API response differs from what this file says, trust the real response and update this file.
