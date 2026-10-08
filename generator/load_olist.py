"""Load the Olist CSVs into Postgres (raw_* tables) and build the fact tables.

    python generator/load_olist.py            # reset raw tables, load CSVs, build facts
    python generator/load_olist.py --facts-only

Reads DATABASE_URL from the environment or .env (default: local docker compose DB).
The geolocation CSV is not loaded (not used by the semantic layer).
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "olist"
SQL = ROOT / "sql"

TABLES = [
    ("raw_customers", "olist_customers_dataset.csv"),
    ("raw_orders", "olist_orders_dataset.csv"),
    ("raw_order_items", "olist_order_items_dataset.csv"),
    ("raw_payments", "olist_order_payments_dataset.csv"),
    ("raw_reviews", "olist_order_reviews_dataset.csv"),
    ("raw_products", "olist_products_dataset.csv"),
    ("raw_sellers", "olist_sellers_dataset.csv"),
    ("raw_category_translation", "product_category_name_translation.csv"),
]


def load_env() -> None:
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def run_sql(conn: psycopg.Connection, path: Path) -> None:
    conn.execute(path.read_text())


def copy_csv(conn: psycopg.Connection, table: str, csv_name: str) -> None:
    path = DATA / csv_name
    # utf-8-sig: product_category_name_translation.csv starts with a byte-order mark
    header = path.open(encoding="utf-8-sig").readline().strip()
    cols = ", ".join(h.strip().strip('"') for h in header.split(","))
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
            with path.open("rb") as fh:
                while chunk := fh.read(1 << 20):
                    cp.write(chunk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts-only", action="store_true")
    args = ap.parse_args()
    load_env()
    url = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
    t0 = time.time()
    with psycopg.connect(url) as conn:
        if not args.facts_only:
            run_sql(conn, SQL / "05_raw_schema.sql")
            for table, csv_name in TABLES:
                copy_csv(conn, table, csv_name)
            run_sql(conn, SQL / "06_category_fixes.sql")
        run_sql(conn, SQL / "10_facts.sql")
        conn.commit()
        for table in [t for t, _ in TABLES] + ["fact_order_items", "fact_orders", "fact_payments", "fact_reviews"]:
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"{table:28s} {n:>9,}")
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
