"""Semantic layer checks (card T5). Skipped when the Olist database is not reachable."""
import os

import pytest

psycopg = pytest.importorskip("psycopg")

URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/appdb")


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(URL, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database not reachable: {e}")
    if not c.execute("SELECT to_regclass('fact_order_items')").fetchone()[0]:
        pytest.skip("Olist not loaded")
    yield c
    c.close()


@pytest.fixture(scope="module")
def datasets(conn):
    from generator.semantic_layer import build_datasets

    return build_datasets(conn)


def test_every_metric_runs_and_is_numeric(conn, datasets):
    for ds in datasets:
        for m in ds.metrics:
            v = conn.execute(f"SELECT {m.sql} FROM {ds.table}").fetchone()[0]
            assert v is not None and float(v) >= 0, (ds.dataset_id, m.metric_id)


def test_revenue_matches_independent_sql(conn, datasets):
    rev = next(m for d in datasets for m in d.metrics if m.metric_id == "revenue")
    sem = conn.execute(f"SELECT round(({rev.sql})::numeric, 2) FROM fact_order_items").fetchone()[0]
    hand = conn.execute("""
        SELECT round(sum(i.price)::numeric, 2) FROM raw_order_items i JOIN raw_orders o USING (order_id)
        WHERE o.order_status NOT IN ('canceled', 'unavailable')""").fetchone()[0]
    assert sem == hand


def test_enum_values_complete_and_from_db(conn, datasets):
    items = next(d for d in datasets if d.dataset_id == "ds_order_items")
    assert len(items.field("customer_state").values) == 27
    assert items.field("customer_region").values == ["Central-West", "North", "Northeast", "South", "Southeast"]
    n_cat = conn.execute("SELECT count(DISTINCT product_category) FROM fact_order_items").fetchone()[0]
    assert len(items.field("product_category").values) == n_cat


def test_metric_ids_unique_and_additivity(datasets):
    ids = [m.metric_id for d in datasets for m in d.metrics]
    assert len(ids) == len(set(ids))
    by = {m.metric_id: m for d in datasets for m in d.metrics}
    assert by["revenue"].additive and by["orders"].additive
    assert not by["aov"].additive and not by["late_rate"].additive


def test_metric_sql_parses_with_sqlglot(datasets):
    sqlglot = pytest.importorskip("sqlglot")
    for d in datasets:
        for m in d.metrics:
            sqlglot.parse_one(f"SELECT {m.sql} FROM {d.table}", read="postgres")
