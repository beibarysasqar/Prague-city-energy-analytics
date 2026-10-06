# Prague City & Energy Analytics

Portfolio project demonstrating analytics engineering (target roles: MSD, BRP, Billigence-style teams).
Goal: production-like ELT pipeline with dbt best practices, data quality tests, lineage, CI and a dashboard.
Author works on macOS in PyCharm. Communicate with me in Russian; code, comments, commit messages and docs in English.

## Business questions
1. How are electricity prices and weather related to air pollution across Prague districts?
2. Where is bicycle traffic growing?

## Stack
- Python 3.12, dependency management with **uv** (`uv add`, `uv run`; never use pip directly)
- Extraction: `requests` + `tenacity` (retries), `pandas`/`pyarrow` → Parquet
- Warehouse: DuckDB (`data/warehouse.duckdb`) for dev; Snowflake as dbt target `prod` (profile only, no real deploy required)
- Transformation: dbt-core + dbt-duckdb, `dbt_utils`
- Dashboard: Streamlit + Plotly
- Profiling: ydata-profiling (fallback: own script)
- CI/orchestration: GitHub Actions (PR CI + daily cron). Airflow DAG only as a documented example, not run.
- Lint: ruff (Python), sqlfluff (SQL, dialect duckdb)

## Data sources
All secrets are in `.env` (never commit it; `.env.example` with empty values is committed).
Before writing an extractor, check the current official docs / try one request — do not guess response formats.

| Source | Data | Auth | Notes |
|---|---|---|---|
| Golemio API (`https://api.golemio.cz`) | air quality stations + measurements, bicycle counters + detections, city districts, building energy | header `X-Access-Token: $GOLEMIO_API_KEY` | Check exact v2 endpoints in docs at api.golemio.cz/docs; respect rate limits, paginate with limit/offset |
| ENTSO-E Transparency Platform | day-ahead prices and actual total load, bidding zone CZ | `$ENTSOE_API_KEY` | Prefer `entsoe-py` (`EntsoePandasClient`). Since Oct 2025 day-ahead MTU is 15 min → aggregate to hourly in silver |
| ČNB public FX API (`https://api.cnb.cz/cnbapi/exrates/daily`) | official daily FX rates (EUR/CZK + ~30 currencies) | none | **Primary FX source** (`extract/cnb_fx.py`). No rates on weekends/holidays → API returns the last business day (`validFor`) |
| ČNB ARAD REST API (`https://www.cnb.cz/aradb/api/v1`) | ČNB time series (rates, PRIBOR, bond yields, …) | `$CNB_ARAD_API_KEY` (query param `api_key`) | Key verified, **not used yet**: ARAD has no daily official EUR/CZK fixing (only a snapshot "hedging of exports" series ending 2026-08). Keep for optional extras |
| Open-Meteo Archive API (`https://archive-api.open-meteo.com/v1/archive`) | hourly weather (temperature, wind, precipitation, humidity) | none | Use station coordinates (from the Golemio stations snapshot in bronze) |

### Golemio air quality — verified facts (2026-10)
- OpenAPI spec: `https://api.golemio.cz/docs/static/output-gateway/openapi-public.json`. Rate limit documented as 20 requests / 8 s per key, but bursts of 20 get HTTP 429 (`Retry-After: 8`) → we throttle to 15 / 8 s; `limit` max 10000 + `offset`.
- `GET /v2/airqualitystations` → GeoJSON FeatureCollection (17 stations, `coordinates=[lon, lat]`); reference list frozen at `updated_at` 2026-08-12.
- `GET /v2/airqualitystations/history?from&to&limit&offset` → array `{id, updated_at, measurement{AQ_hourly_index, components[{type, averaged_time{averaged_hours, value}}]}}`; `from`/`to` filter on `updated_at`.
- Real types differ from the docs: `AQ_hourly_index` (e.g. "1B") and `averaged_hours` are strings. All units µg/m³ (`/componenttypes`).
- `averaged_hours` switched on 2026-08-12 ~06:00 UTC: before = 3-hour running averages (17 stations), after = 1-hour values (13 stations). Never filter on it blindly — it removes a whole period.
- `updated_at` is the publication time (`HH:50` UTC since ~2026-08-12, `HH:10` before), not the measurement hour → derive the measured hour in silver. ~23 rows/station/day; some hours are missing.
- Bronze: `golemio_air_quality_stations/` (snapshot, partition = run date UTC) and `golemio_air_quality_history/` (partition = UTC day of `updated_at`). Nested objects (`measurement`, `geometry`) are stored as JSON strings; parse them in silver.
- Shared Golemio helpers (session, rate limiter, pagination, `to_raw_row`, `day_bounds`) live in `extract/golemio.py`.

### Golemio bicycle counters — verified facts (2026-10)
- `GET /v2/bicyclecounters` → GeoJSON FeatureCollection, 40 counters: `properties {id, name, route, updated_at, directions[{id, name}]}` (79 directions); some counters stale (`updated_at` 2026-07-06).
- `GET /v2/bicyclecounters/detections?from&to&limit&offset` → array `{id (direction), locations_id (counter), measured_from, measured_to, value, value_pedestrians, measurement_count}`, **5-minute** slots, UTC; `from`/`to` filter on `measured_from`. ~19.3k rows/day (34 active counters, 67 directions × 288 slots) → 2 pages; page order is stable. `value` null in ~12 % of rows; `value_pedestrians` only for some counters. `aggregate=true` (sum per direction) exists but is not used (bronze stays raw).
- **Direction `id` is not unique**: `camea-PN-VY`/`camea-PN-BR` are measured by two counters (`camea-BC_PN-VYBR`, `camea-BC_PN-VYBR2` road). Natural key = (`locations_id`, `id`, `measured_from`).
- Bronze: `golemio_bicycle_counters/` (snapshot, partition = run date UTC, `directions` as JSON string) and `golemio_bicycle_detections/` (partition = UTC day of `measured_from`, `value`/`value_pedestrians` forced to float64). Incremental runs re-load the last 3 days (late uploads).

### Golemio city districts — verified facts (2026-10)
- `GET /v2/citydistricts?limit&offset` → GeoJSON FeatureCollection, 57 districts: `properties {id (int), name, slug, updated_at}`, `geometry` = boundary **Polygon** (~1.5 MB total; whole list refreshed at once).
- Bronze: `golemio_city_districts/` (snapshot, partition = run date UTC, polygon as GeoJSON string, ~0.8 MB Parquet); no state, `--start/--end` ignored.
- Joins: AQ stations → districts by `district` = `slug` (all 13 match). Bicycle counters have no district → point-in-polygon in silver (`ST_Contains(ST_GeomFromGeoJSON(d.geometry), ST_GeomFromGeoJSON(c.geometry))`, DuckDB `spatial`; Snowflake has `ST_CONTAINS`/`TO_GEOGRAPHY`) — all 40 counters match.

### ENTSO-E — verified facts (2026-10, entsoe-py 0.8.1)
- API `https://web-api.tp.entsoe.eu/api`; the key is a **query parameter** (`securityToken`), so requests/entsoe-py exception messages and entsoe-py DEBUG logs contain it. Always call through `extract.entsoe.call_entsoe` (sanitised `EntsoeRequestError`, no exception chaining); the `entsoe` logger is pinned to WARNING.
- CZ zone `10YCZ-CEPS-----N`. Day-ahead prices (A44): EUR/MWh, PT15M (96 points per delivery day), sent as two identical TimeSeries (entsoe-py dedups). Actual load (A65/A16): MW, PT15M. No data → HTTP 200 + Acknowledgement doc → `NoMatchingDataError` → day skipped.
- entsoe-py retries only connection errors (and sleeps `retry_delay`); we set `retry_count=1, retry_delay=0` and retry 429/5xx/network errors with tenacity. Its `truncate(after=end)` is end-inclusive → we keep `[start, end)`.
- Bronze: `entsoe_day_ahead_prices/`, `entsoe_actual_load/`, partition = **delivery day in Europe/Prague** (23/25 h on DST days), rows `ts_utc, value, unit, area_code`. 15 min → hourly in silver. Prices default end = tomorrow (published ~13:00 CET).

### ČNB FX — verified facts (2026-10)
- OpenAPI: `https://api.cnb.cz/cnbapi/api-docs`. `GET /cnbapi/exrates/daily?date=YYYY-MM-DD&lang=EN` → `{"rates":[{validFor, order, country, currency, amount, currencyCode, rate}]}` (30 currencies). `rate` is CZK per `amount` units (HUF, JPY, … use `amount=100`) → normalise in silver.
- Weekends/holidays (e.g. 2026-09-28) **and future dates** return the last published table with an older `validFor` → the extractor clamps `end` to today. Today's table appears ~14:30 Prague time; earlier runs get yesterday's and the day is re-loaded next run. Invalid params → HTTP 400 JSON `{errorCode: VALIDATION_ERROR, ...}`.
- Bronze: `cnb_fx_daily/`, partition = requested calendar day, raw rows as returned. So weekend partitions repeat Friday's rates (`validFor` = Friday) — forward-fill comes from the source; silver keeps `validFor` as the rate date.
- ARAD (`/aradb/api/v1`, docs: `https://www.cnb.cz/docs/arad20/dokumentace/arad_rest_api_cs.pdf`): CSV `;`-separated, CP1250, dates `YYYYMMDD`, key in the query string (same leak risk as ENTSO-E); bad key → HTTP 400 JSON "API key not found".

### Open-Meteo — verified facts (2026-10)
- `GET /v1/archive?latitude=a,b,…&longitude=a,b,…&start_date&end_date&hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_direction_10m&timezone=UTC`. Several coordinates → a **list** in request order with no location id (station id matched by position); one coordinate → a single object. All 17 stations × 90 days ≈ 1.5 MB in one call; we chunk by 31 days.
- Per location: `latitude/longitude/elevation` of the **model grid cell** (differs from the requested point), `hourly_units` (°C, %, mm, **km/h**, °), `hourly.time` = `YYYY-MM-DDTHH:MM` in UTC. Errors → HTTP 400 `{"error": true, "reason": ...}`.
- Recent days (incl. today) are already filled from model/forecast data and get revised → incremental runs re-load the last 3 days (`resolve_window(lookback_days=3)`).
- Bronze: `open_meteo_weather_hourly/`, partition = UTC day, one row per station × hour with requested and grid coordinates; weather variables forced to float64 for a stable schema. Requires the `golemio_air_quality_stations` snapshot (run Golemio first).

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
- Repo layout: `extract/`, `dbt/` (dbt project `prague`), `app/`, `profiling/`, `orchestration/`, `.github/workflows/`, `data/` (gitignored; small static reference data lives in `dbt/seeds/`).
- Extractors: one module per source, shared helpers in `extract/common.py`; state in `data/bronze/_state.json`; CLI `uv run python -m extract.run --source <name> --start YYYY-MM-DD --end YYYY-MM-DD` (default: incremental from state, 90 days on first run). Re-running the same date must overwrite, not duplicate.
- Logging via `logging`, no prints. Type hints everywhere. Small pure functions that are unit-testable with pytest (mock HTTP).
- All timestamps stored in UTC (`*_ts_utc`); local Prague time only as derived column. This matters for DST and the gap test.
- dbt naming: `stg_<source>__<entity>` (silver), `dim_*`, `fact_*`, `mart_*` (gold). Every model has a YAML entry with description and tests.
- Gold facts are `incremental`, `incremental_strategy='delete+insert'`, with a 3-day lookback window for late-arriving data.
- Sources read bronze Parquet via dbt-duckdb `external_location`; every source has `loaded_at_field: _loaded_at` and freshness thresholds.
- Silver (`dbt/models/staging/<source>/`): staging models are materialized as **tables** (views would keep relative `read_parquet('../data/bronze/…')` paths and break outside `dbt/`). Bronze root defaults to `../data/bronze` (relative to `dbt/`), override with `BRONZE_ROOT`. The dev profile sets DuckDB `TimeZone: UTC` (date_trunc on TIMESTAMPTZ uses the session zone) and loads `json`, `spatial`.
- Silver keys/decisions: AQ measurements = (station_id, measured_hour_start_ts_utc, pollutant), measured hour = hour ending at `date_trunc('hour', updated_at)` (assumption: published 10–50 min after the hour), latest re-publication wins; bicycle detections = (counter_id, direction_id, interval_start_ts_utc); counters → districts via `st_contains_geojson` macro (adapter.dispatch duckdb/snowflake); ENTSO-E 15 min → UTC hour averages with `n_intervals`; FX = calendar_date × currency, `rate_czk_per_unit = rate / amount`, `dbt_utils.date_spine` + forward-fill. JSON parsing/unnest in staging is DuckDB-specific (Snowflake would need FLATTEN).
- Known source quirk: counter `camea-BC_PN-VYBR` (cycle path) has a `{"id": null}` direction in the catalogue but reports detections for `camea-PN-VY`/`camea-PN-BR` → silver drops the placeholder, `fact_bike_traffic_daily` takes the direction name from the sibling counter (`is_direction_inferred`).
- SCD2: `snapshots/snap_golemio__air_quality_stations.yml` (YAML snapshot syntax, check strategy, `hard_deletes: invalidate`).
- Gold (`dbt/models/marts/`): `dim_date` (2025–2027, `date_key` YYYYMMDD, Czech holidays from seed `cz_public_holidays`), `dim_district` (+ `'-1'` Unknown member), `dim_station` (SCD2 versions; first version valid from 1900-01-01 because the snapshot started late; facts join on station_id + hour within [valid_from, valid_to)), `dim_bike_counter`; facts `fact_air_quality_hourly` (station × hour × pollutant), `fact_weather_hourly` (station × hour — added to the original list, needed for Q1), `fact_energy_price_hourly` (hour; CZK via ČNB EUR rate of the Prague date, latest rate as estimate for tomorrow), `fact_bike_traffic_daily` (counter × direction × Prague date, `is_complete_day`, DST-aware `expected_slots` 276/288/300); `mart_air_vs_energy_daily` (district × Prague date). Facts: incremental `delete+insert` with `incremental_lookback()` (var `incremental_lookback_days: 3`).
- Determinism: float `avg/sum` in DuckDB is not bit-for-bit reproducible (parallel order), which made incremental results differ from full refreshes → use the `stable_avg` / `stable_sum` macros (DECIMAL aggregation) for averages that feed incremental models or marts. Verified: incremental == full refresh for all facts and the mart.
- Required tests: unique / not_null / relationships / accepted_values, plus custom generic tests in `dbt/tests/generic/`: `non_negative` (e.g. PM2.5; prices may be negative — do NOT apply to day-ahead price) and `no_time_gaps(partition_by, datepart)` (per station, hourly, on UTC) — error on energy/weather, **warn** on AQ (real source gaps, ~1.4k).
- Prefer cross-database macros (`dbt_utils`, `dbt.date_trunc` etc.) so the `prod` Snowflake target works with minimal changes.

## Commands
```bash
uv sync
uv run python -m extract.run                  # all sources, incremental
cd dbt && uv run dbt deps
uv run dbt source freshness && uv run dbt build   # build = seeds + models + snapshots + tests in DAG order
uv run dbt docs generate && uv run dbt docs serve
uv run streamlit run app/streamlit_app.py
make lint    # ruff + sqlfluff (dbt templater; needs dbt/profiles.yml, `dbt deps` and bronze data)
uv run pytest
```
A `Makefile` wraps these (`make extract`, `make dbt`, `make app`, `make lint`, `make test`).

## Development phases (do them in order, one phase per session/PR)
1. **Scaffold**: repo layout, `.gitignore`, `.env.example`, `Makefile`, dbt project init, `profiles.example.yml` (dev=duckdb, prod=snowflake via env vars).
2. **Extractors**: common helpers → Golemio air quality (stations + history) → ENTSO-E prices + load → ČNB FX → Open-Meteo weather → Golemio bicycle counters → districts. Unit tests with mocked HTTP. Done = 90 days of data in bronze, re-run is idempotent.
3. **dbt silver + snapshot**: sources with freshness, staging models, SCD2 snapshot of stations.
4. **dbt gold + tests**: star schema, incremental facts, all tests green with `dbt build`.
5. **Profiling**: HTML/Markdown reports for gold tables in `profiling/reports/`.
6. **Streamlit**: filters (period, district, pollutant), KPI cards, station map, price vs PM2.5 time series, PM2.5 vs wind scatter coloured by price, per-district correlations (with "correlation ≠ causation" note), bike traffic growth (YoY / rolling avg by district).
7. **CI**: `dbt_ci.yml` on every PR (sync → extract 7 days → dbt build → lint → pytest), secrets from GitHub Actions secrets; `daily.yml` cron; example Airflow DAG in `orchestration/`.
8. **README**: Mermaid architecture diagram, lineage screenshot, dashboard screenshot, design decisions, how to run, roadmap.

## How to work with me
- Start each phase in plan mode: propose the plan and file list, wait for my OK, then implement.
- After implementing, run the relevant commands yourself and fix errors before reporting back.
- Never print, log or commit API keys. If a key is missing, stop and tell me which env var to set.
- Keep changes small; suggest a conventional commit message at the end of each step.
- If an API response differs from what this file says, trust the real response and update this file.
