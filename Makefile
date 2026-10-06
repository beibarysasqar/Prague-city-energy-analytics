# Load .env (if present) and export its variables to all recipes (dbt reads them via env_var()).
-include .env
export

DBT := cd dbt && uv run dbt
# sqlfluff's dbt templater runs from the repo root, so DuckDB and bronze paths must be absolute.
SQLFLUFF := DUCKDB_PATH=$(CURDIR)/data/warehouse.duckdb BRONZE_ROOT=$(CURDIR)/data/bronze uv run sqlfluff

.PHONY: install profiles extract dbt debug docs profile app lint format test clean

install:
	uv sync

profiles:
	@test -f dbt/profiles.yml || cp dbt/profiles.example.yml dbt/profiles.yml
	@echo "dbt/profiles.yml is ready"

extract:
	uv run python -m extract.run

# `dbt build` runs seeds, models, snapshots and tests in DAG order (a separate `dbt snapshot`
# before the build fails on a fresh database: the snapshot reads a staging model).
# Source freshness is reported but does not block the build: one stale API must not stop the
# refresh of every other source (the failure stays visible in the log / CI annotation).
dbt: profiles
	$(DBT) deps
	$(DBT) source freshness || echo "::warning::dbt source freshness failed - stale sources above; continuing with dbt build"
	$(DBT) build

debug: profiles
	$(DBT) deps
	$(DBT) debug

docs: profiles
	$(DBT) docs generate
	$(DBT) docs serve

# ydata-profiling needs pandas < 3, so the script runs in its own env (PEP 723 metadata).
profile:
	uv run --script profiling/profile_gold.py

app:
	uv run streamlit run app/streamlit_app.py

lint: profiles
	uv run ruff check .
	uv run ruff format --check .
	$(SQLFLUFF) lint dbt/models dbt/tests
	uv run --with actionlint-py actionlint .github/workflows/*.yml

format: profiles
	uv run ruff check --fix .
	uv run ruff format .
	$(SQLFLUFF) fix dbt/models dbt/tests

test:
	uv run pytest

clean:
	$(DBT) clean
	rm -rf .pytest_cache .ruff_cache
