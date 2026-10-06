# Orchestration

The pipeline is scheduled with **GitHub Actions** (`.github/workflows/daily.yml`). This folder holds an
**example Airflow 3 DAG** showing how the same pipeline maps to Airflow. It is documented, not deployed or
run as part of this project.

| GitHub Actions (`daily.yml`) | Airflow (`airflow/dags/prague_city_energy.py`) |
|---|---|
| one step `extract.run` (all sources in sequence) | task group `extract`: one task per source, retried individually; `open_meteo` waits for `golemio_air_quality` (station coordinates), the other four run in parallel |
| `make dbt` | `dbt_deps` → `dbt_source_freshness` → `dbt_build` (seeds, models, snapshots, tests) |
| `make profile` | `profile_gold` |
| cron `30 13 * * *` | `schedule="30 13 * * *"`, `catchup=False`, `max_active_runs=1` |
| repository secrets | Airflow Variables `golemio_api_key`, `entsoe_api_key` (+ optional `dbt_target`) passed as env vars |

Why `catchup=False`: the extractors are incremental from their own state (`data/bronze/_state.json`)
and re-load a lookback window, so Airflow backfills are not needed; a historical reload is a manual run
with `--start`/`--end`.

Assumptions for a real deployment: the repository is checked out at `PROJECT_DIR` on the workers, `uv`
is installed, and `data/` sits on persistent storage shared by the tasks (DuckDB is a single file).

The DAG was validated by parsing it with Apache Airflow 3.3.2 (`DagBag`, no import errors, 10 tasks with
the dependencies above). Airflow is intentionally not a project dependency.
