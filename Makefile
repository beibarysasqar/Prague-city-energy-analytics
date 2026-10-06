# Load .env (if present) and export its variables to all recipes (dbt reads them via env_var()).
-include .env
export

DBT := cd dbt && uv run dbt
# sqlfluff's dbt templater runs from the repo root, so DuckDB and bronze paths must be absolute.
SQLFLUFF := DUCKDB_PATH=$(CURDIR)/data/warehouse.duckdb BRONZE_ROOT=$(CURDIR)/data/bronze uv run sqlfluff

.PHONY: install profiles extract dbt debug docs app lint format test clean

install:
	uv sync

profiles:
	@test -f dbt/profiles.yml || cp dbt/profiles.example.yml dbt/profiles.yml
	@echo "dbt/profiles.yml is ready"

extract:
	uv run python -m extract.run

# `dbt build` runs seeds, models, snapshots and tests in DAG order (a separate `dbt snapshot`
# before the build fails on a fresh database: the snapshot reads a staging model).
dbt: profiles
	$(DBT) deps
	$(DBT) source freshness
	$(DBT) build

debug: profiles
	$(DBT) deps
	$(DBT) debug

docs: profiles
	$(DBT) docs generate
	$(DBT) docs serve

app:
	uv run streamlit run app/streamlit_app.py

lint: profiles
	uv run ruff check .
	uv run ruff format --check .
	$(SQLFLUFF) lint dbt/models dbt/tests

format: profiles
	uv run ruff check --fix .
	uv run ruff format .
	$(SQLFLUFF) fix dbt/models dbt/tests

test:
	uv run pytest

clean:
	$(DBT) clean
	rm -rf .pytest_cache .ruff_cache
