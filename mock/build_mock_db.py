"""Build mock/olist_mock.sqlite from the Postgres fact tables (run by the backend team, not by the frontend pair).

    python -m mock.build_mock_db

Keeps only the columns the semantic layer uses; order_id becomes an integer surrogate (COUNT(DISTINCT) still works).
"""
import os
import sqlite3
from pathlib import Path

import psycopg

OUT = Path(__file__).resolve().parent / "olist_mock.sqlite"
TABLES = {
    "fact_order_items": ["order_id", "price", "freight_value", "order_status", "is_valid_sale", "order_date", "order_month",
                         "customer_state", "customer_region", "seller_state", "product_category", "delivery_days", "is_late"],
    "fact_orders": ["order_status", "is_valid_sale", "order_date", "order_month", "customer_state", "customer_region",
                    "delivery_days", "is_late"],
    "fact_payments": ["payment_type", "payment_installments", "payment_value", "order_date", "order_month",
                      "customer_state", "customer_region"],
    "fact_reviews": ["review_score", "order_date", "order_month", "customer_state", "customer_region"],
}


def main():
    url = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
    OUT.unlink(missing_ok=True)
    lite = sqlite3.connect(OUT)
    with psycopg.connect(url) as pg:
        for table, cols in TABLES.items():
            sel = ", ".join(
                "dense_rank() OVER (ORDER BY order_id) AS order_id" if c == "order_id"
                else f"{c}::text AS {c}" if c in ("order_date", "order_month")
                else f"CASE WHEN {c} THEN 1 ELSE 0 END AS {c}" if c == "is_valid_sale"
                else c
                for c in cols)
            rows = pg.execute(f"SELECT {sel} FROM {table}").fetchall()
            lite.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
            lite.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})",
                             [tuple(float(v) if hasattr(v, 'as_integer_ratio') and not isinstance(v, (int, float)) else v
                                    for v in r) for r in rows])
            lite.execute(f"CREATE INDEX {table}_date ON {table} (order_date)")
            print(f"{table:18s} {len(rows):>8,} rows")
    lite.commit()
    lite.execute("VACUUM")
    lite.close()
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
