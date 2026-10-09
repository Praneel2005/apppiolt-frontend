"""Checks on the loaded Olist database (cards T3-T4). Skipped when Postgres is not reachable."""
import csv
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "olist"
URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")
URL_RO = os.environ.get("DATABASE_URL_RO", "postgresql://app_ro:app_ro@localhost:5432/appdb")

RAW = {
    "raw_customers": "olist_customers_dataset.csv",
    "raw_orders": "olist_orders_dataset.csv",
    "raw_order_items": "olist_order_items_dataset.csv",
    "raw_payments": "olist_order_payments_dataset.csv",
    "raw_reviews": "olist_order_reviews_dataset.csv",
    "raw_products": "olist_products_dataset.csv",
    "raw_sellers": "olist_sellers_dataset.csv",
}


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(URL, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    if not c.execute("SELECT to_regclass('fact_order_items')").fetchone()[0]:
        pytest.skip("Olist not loaded (run generator/load_olist.py)")
    yield c
    c.close()


def q(conn, sql):
    return conn.execute(sql).fetchone()[0]


def csv_rows(name):
    with (DATA / name).open(encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.reader(fh)) - 1


@pytest.mark.parametrize("table", list(RAW))
def test_raw_counts_match_csv(conn, table):
    if not (DATA / RAW[table]).exists():
        pytest.skip("CSV not present")
    assert q(conn, f"SELECT count(*) FROM {table}") == csv_rows(RAW[table])


def test_fact_items_no_fanout_or_loss(conn):
    assert q(conn, "SELECT count(*) FROM fact_order_items") == q(conn, "SELECT count(*) FROM raw_order_items")


def test_revenue_preserved(conn):
    fact = q(conn, "SELECT round(sum(price)::numeric, 2) FROM fact_order_items")
    raw = q(conn, "SELECT round(sum(price)::numeric, 2) FROM raw_order_items")
    assert fact == raw


def test_valid_sale_revenue_matches_independent_sql(conn):
    fact = q(conn, "SELECT round(sum(price) FILTER (WHERE is_valid_sale)::numeric, 2) FROM fact_order_items")
    hand = q(conn, """
        SELECT round(sum(i.price)::numeric, 2) FROM raw_order_items i JOIN raw_orders o USING (order_id)
        WHERE o.order_status NOT IN ('canceled', 'unavailable')""")
    assert fact == hand


def test_every_state_maps_to_a_region(conn):
    assert q(conn, "SELECT count(*) FROM fact_order_items WHERE customer_region = 'Unknown'") == 0
    assert q(conn, "SELECT count(DISTINCT customer_region) FROM fact_order_items") == 5


def test_late_and_delivery_null_iff_undelivered(conn):
    assert q(conn, """
        SELECT count(*) FROM fact_orders f JOIN raw_orders o USING (order_id)
        WHERE (o.order_delivered_customer_date IS NULL) <> (f.delivery_days IS NULL)
           OR (o.order_delivered_customer_date IS NULL) <> (f.is_late IS NULL)""") == 0


def test_all_categories_translated_or_unknown(conn):
    # every non-null category must have an English name in core_category_map (Olist's 71 rows + our 2);
    # the original raw_category_translation table itself stays untouched
    assert q(conn, """
        SELECT count(*) FROM raw_products p LEFT JOIN core_category_map t USING (product_category_name)
        WHERE p.product_category_name IS NOT NULL AND t.product_category_name_english IS NULL""") == 0
    assert q(conn, "SELECT count(*) FROM raw_category_translation") == 71


def test_read_only_role():
    try:
        ro = psycopg.connect(URL_RO, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"read-only role not reachable: {e}")
    with ro:
        assert ro.execute("SELECT count(*) FROM fact_order_items").fetchone()[0] > 0
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            ro.execute("INSERT INTO fact_reviews (review_id) VALUES ('x')")
