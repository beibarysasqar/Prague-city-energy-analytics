# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#     "ydata-profiling==4.18.4",
#     "duckdb==1.5.6",
#     "pandas>=2.2,<3",
#     "setuptools<81",  # ydata-profiling 4.18 still imports pkg_resources
# ]
# ///
"""Profile the gold tables with ydata-profiling: one HTML report per table + a Markdown index.

ydata-profiling needs pandas < 3 / numpy < 2.4 while the project uses pandas 3, so this script is
self-contained (PEP 723 metadata above) and runs in its own uv-managed environment:

    uv run --script profiling/profile_gold.py            # or: make profile
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "warehouse.duckdb"
DEFAULT_OUT = PROJECT_ROOT / "profiling" / "reports"
SCHEMA = "gold"
DEFAULT_SAMPLE_ROWS = 50_000
MINIMAL_ABOVE_ROWS = 10_000
SAMPLE_SEED = 42
TOP_MISSING_COLUMNS = 5

logger = logging.getLogger("profile_gold")


@dataclass
class TableSummary:
    table: str
    total_rows: int
    profiled_rows: int
    n_columns: int
    duplicate_rows: int
    missing_pct: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def sampled(self) -> bool:
        return self.profiled_rows < self.total_rows


# --------------------------------------------------------------------------- pure helpers


def sample_query(schema: str, table: str, total_rows: int, sample_rows: int) -> str:
    """SELECT for the table, with a reproducible reservoir sample when it is larger than allowed."""
    query = f'select * from "{schema}"."{table}"'
    if total_rows > sample_rows:
        query += f" using sample reservoir({sample_rows} rows) repeatable ({SAMPLE_SEED})"
    return query


def missing_percentages(df: pd.DataFrame) -> dict[str, float]:
    """Share of nulls per column in percent, only columns that have nulls, highest first."""
    pct = (df.isna().mean() * 100).round(2)
    return {col: float(v) for col, v in pct.sort_values(ascending=False).items() if v > 0}


def markdown_index(summaries: Sequence[TableSummary], generated_at: str) -> str:
    """Markdown overview of all profiled tables with links to the HTML reports."""
    lines = [
        "# Gold layer profiling",
        "",
        f"Generated {generated_at} with ydata-profiling. Large tables are profiled on a "
        "reproducible sample (reservoir, seed 42); counts below refer to the profiled rows.",
        "",
        "| Table | Rows | Profiled | Columns | Duplicate rows | Columns with nulls (top) "
        "| Report |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for s in summaries:
        top = sorted(s.missing_pct.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_MISSING_COLUMNS]
        nulls = ", ".join(f"`{col}` {pct:g} %" for col, pct in top) or "—"
        profiled = f"{s.profiled_rows:,} (sample)" if s.sampled else f"{s.profiled_rows:,}"
        lines.append(
            f"| `{s.table}` | {s.total_rows:,} | {profiled} | {s.n_columns} | "
            f"{s.duplicate_rows:,} | {nulls} | [{s.table}.html]({s.table}.html) |"
        )
    return "\n".join(lines) + "\n"


def to_profile_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Timezone-aware timestamps → naive UTC (ydata-profiling handles naive datetimes best)."""
    for col in df.select_dtypes(include=["datetimetz"]).columns:
        df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
    return df


# --------------------------------------------------------------------------- profiling


def list_tables(con: duckdb.DuckDBPyConnection, schema: str) -> list[str]:
    rows = con.execute(
        "select table_name from information_schema.tables where table_schema = ? order by 1",
        [schema],
    ).fetchall()
    return [r[0] for r in rows]


def profile_table(
    con: duckdb.DuckDBPyConnection, table: str, out_dir: Path, sample_rows: int
) -> TableSummary:
    from ydata_profiling import ProfileReport  # heavy import, only inside the script env

    started = time.monotonic()
    total = con.execute(f'select count(*) from "{SCHEMA}"."{table}"').fetchone()[0]
    df = to_profile_frame(con.execute(sample_query(SCHEMA, table, total, sample_rows)).df())
    minimal = len(df) > MINIMAL_ABOVE_ROWS
    logger.info("Profiling %s (%d of %d rows, minimal=%s)", table, len(df), total, minimal)

    report = ProfileReport(
        df,
        title=f"{SCHEMA}.{table}",
        minimal=minimal,
        explorative=False,
        progress_bar=False,
        correlations={
            "auto": {"calculate": False},
            "pearson": {"calculate": not minimal},
            "spearman": {"calculate": not minimal},
        },
        interactions={"continuous": False},
    )
    report.to_file(out_dir / f"{table}.html")

    return TableSummary(
        table=table,
        total_rows=total,
        profiled_rows=len(df),
        n_columns=df.shape[1],
        duplicate_rows=int(df.duplicated().sum()),
        missing_pct=missing_percentages(df),
        seconds=round(time.monotonic() - started, 1),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile gold tables with ydata-profiling.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--tables", nargs="*", help="default: all tables in the gold schema")
    parser.add_argument("--sample-rows", type=int, default=DEFAULT_SAMPLE_ROWS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("matplotlib").setLevel(logging.WARNING)  # font-cache noise
    # ydata 4.18 has one tqdm bar that ignores `progress_bar=False` (summary_pandas.py);
    # tqdm reads this at import time, and ydata is imported lazily after this point.
    os.environ.setdefault("TQDM_DISABLE", "1")
    args = parse_args(argv)
    if not args.db.exists():
        logger.error("Warehouse %s not found; run `make dbt` first", args.db)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(args.db), read_only=True) as con:
        con.execute("set timezone = 'UTC'")
        tables = args.tables or list_tables(con, SCHEMA)
        summaries = [profile_table(con, t, args.out, args.sample_rows) for t in tables]

    generated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    (args.out / "index.md").write_text(markdown_index(summaries, generated_at), encoding="utf-8")
    for s in summaries:
        logger.info(
            "%-28s rows=%-7d dup=%-4d %.1fs", s.table, s.total_rows, s.duplicate_rows, s.seconds
        )
    logger.info("Wrote %d reports + index.md to %s", len(summaries), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
