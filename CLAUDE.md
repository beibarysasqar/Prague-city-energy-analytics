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
| ČNB ARAD REST API | EUR/CZK and other series | `$CNB_ARAD_API_KEY` | Example call generated via ARAD API Builder will be provided by me |
| ČNB public FX API (`https://api.cnb.cz/cnbapi/exrates/daily`) | daily FX rates | none | Fallback if ARAD is not ready. No rates on weekends/holidays → forward-fill |
| Open-Meteo Archive API | hourly weather (temperature, wind, precipitation, humidity) | none | Use station coordinates |

## Architecture
```
Python extractors (incremental by date, retries, idempotent)
   → bronze: data/bronze/<source>/load_date=YYYY-MM-DD/part.parquet  (raw + _loaded_at UTC)
   → dbt silver: typing, UTC timestamps, dedup (qualify row_number), 15min→hourly, FX forward-fill
   → dbt snapshot: SCD2 of station dimension (strategy=check)
   → dbt gold (star schema):
       fact_air_quality_hourly, fact_energy_price_hourly, fact_bike_traffic_daily,
       dim_station, dim_district, dim_date, (mart_air_vs_energy_daily)
   → Streamlit app + profiling reports
```

## Conventions
- Repo layout: `extract/`, `dbt/` (dbt project `prague`), `app/`, `profiling/`, `orchestration/`, `.github/workflows/`, `data/` (gitignored except small seeds).
- Extractors: one module per source, shared helpers in `extract/common.py`; state in `data/bronze/_state.json`; CLI `uv run python -m extract.run --source <name> --start YYYY-MM-DD --end YYYY-MM-DD` (default: incremental from state, 90 days on first run). Re-running the same date must overwrite, not duplicate.
- Logging via `logging`, no prints. Type hints everywhere. Small pure functions that are unit-testable with pytest (mock HTTP).
- All timestamps stored in UTC (`*_ts_utc`); local Prague time only as derived column. This matters for DST and the gap test.
- dbt naming: `stg_<source>__<entity>` (silver), `dim_*`, `fact_*`, `mart_*` (gold). Every model has a YAML entry with description and tests.
- Gold facts are `incremental`, `incremental_strategy='delete+insert'`, with a 3-day lookback window for late-arriving data.
- Sources read bronze Parquet via dbt-duckdb `external_location`; every source has `loaded_at_field: _loaded_at` and freshness thresholds.
- Required tests: unique / not_null / relationships / accepted_values, plus custom generic tests `non_negative` (e.g. PM2.5, prices may be negative! — do NOT apply to day-ahead price) and `no_time_gaps` (per station, hourly, on UTC).
- Prefer cross-database macros (`dbt_utils`, `dbt.date_trunc` etc.) so the `prod` Snowflake target works with minimal changes.

## Commands
```bash
uv sync
uv run python -m extract.run                  # all sources, incremental
cd dbt && uv run dbt deps && uv run dbt seed && uv run dbt snapshot
uv run dbt source freshness && uv run dbt build
uv run dbt docs generate && uv run dbt docs serve
uv run streamlit run app/streamlit_app.py
uv run ruff check . && uv run sqlfluff lint dbt/models --dialect duckdb
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
