"""Example Airflow 3 DAG for the Prague City & Energy pipeline (documented, not run here).

It mirrors `.github/workflows/daily.yml`: extract every source into bronze, then dbt (deps,
source freshness, build = seeds + models + snapshots + tests) and the profiling reports. Each
source is its own task, so a failing API only retries that source; Open-Meteo waits for the
Golemio station snapshot because it reads the station coordinates from bronze.

Deployment assumptions: the repository is checked out at `PROJECT_DIR` on the workers with `uv`
installed, and the API keys are Airflow Variables exposed as environment variables (never in
code).
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, TaskGroup

PROJECT_DIR = "/opt/airflow/prague-city-energy-analytics"
SECRETS_ENV = {
    "GOLEMIO_API_KEY": "{{ var.value.golemio_api_key }}",
    "ENTSOE_API_KEY": "{{ var.value.entsoe_api_key }}",
    "DBT_TARGET": "{{ var.value.get('dbt_target', 'dev') }}",
}
INDEPENDENT_SOURCES = ["golemio_bicycle", "golemio_districts", "entsoe", "cnb_fx"]

default_args = {
    "owner": "analytics-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
    "execution_timeout": timedelta(minutes=45),
}


def bash(task_id: str, command: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=command,
        cwd=PROJECT_DIR,
        env=SECRETS_ENV,
        append_env=True,
    )


with DAG(
    dag_id="prague_city_energy_daily",
    description="Bronze extract -> dbt silver/gold -> profiling",
    # 13:30 UTC: after the ČNB daily rates and the next-day ENTSO-E prices are published
    schedule="30 13 * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    catchup=False,  # extractors are incremental from their own state; no Airflow backfill
    max_active_runs=1,
    default_args=default_args,
    tags=["prague", "dbt", "duckdb"],
) as dag:
    with TaskGroup(group_id="extract") as extract:
        air_quality = bash(
            "golemio_air_quality",
            "uv run python -m extract.run --source golemio_air_quality",
        )
        weather = bash("open_meteo", "uv run python -m extract.run --source open_meteo")
        air_quality >> weather  # weather uses the station coordinates from the AQ snapshot
        for source in INDEPENDENT_SOURCES:
            bash(source, f"uv run python -m extract.run --source {source}")

    dbt_deps = bash("dbt_deps", "make profiles && cd dbt && uv run dbt deps")
    dbt_freshness = bash("dbt_source_freshness", "cd dbt && uv run dbt source freshness")
    dbt_build = bash("dbt_build", "cd dbt && uv run dbt build")
    profile = bash("profile_gold", "make profile")

    extract >> dbt_deps >> dbt_freshness >> dbt_build >> profile
