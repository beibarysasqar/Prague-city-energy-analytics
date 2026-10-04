# Load .env (if present) and export its variables to all recipes (dbt reads them via env_var()).
-include .env
export

DBT := cd dbt && uv run dbt

.PHONY: install profiles extract dbt debug docs app lint format test clean

install:
	uv sync

profiles:
	@test -f dbt/profiles.yml || cp dbt/profiles.example.yml dbt/profiles.yml
	@echo "dbt/profiles.yml is ready"

extract:
	uv run python -m extract.run

dbt: profiles
	$(DBT) deps
	$(DBT) seed
	$(DBT) snapshot
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

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run sqlfluff lint dbt/models dbt/macros dbt/snapshots dbt/tests

format:
	uv run ruff check --fix .
	uv run ruff format .
	uv run sqlfluff fix dbt/models dbt/macros dbt/snapshots dbt/tests

test:
	uv run pytest

clean:
	$(DBT) clean
	rm -rf .pytest_cache .ruff_cache
