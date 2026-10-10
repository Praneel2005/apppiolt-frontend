"""Analysis engine for AppPilot (WP7 in AGENT_V2_DESIGN.md).

Deterministic computational layer for analytics:
- compare_slices: compare any two slices (different filters/dimensions)
- scan_drivers: evaluate metric decomposition across all dimensions, rank by explained share
- check_confounders: check related metrics (cancellations, late deliveries, reviews)
- reconcile: drivers sum to total change; flags unexplained gap explicitly
- check_period_comparability: flag partial periods or unequal durations
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from backend.routes.metrics import MetricCompare, MetricQuery, metrics_compare, metrics_query


def check_period_comparability(
    from1: str,
    to1: str,
    from2: str,
    to2: str,
    as_of: str = "2018-08-31",
) -> dict[str, Any]:
    """Detects partial periods, unequal lengths, and data window boundaries."""
    d1_start, d1_end = date.fromisoformat(from1), date.fromisoformat(to1)
    d2_start, d2_end = date.fromisoformat(from2), date.fromisoformat(to2)
    as_of_date = date.fromisoformat(as_of)

    len1 = (d1_end - d1_start).days + 1
    len2 = (d2_end - d2_start).days + 1

    warnings = []
    is_comparable = True

    if len1 != len2:
        warnings.append(
            f"Unequal period lengths: {len1} days ({from1} to {to1}) vs {len2} days ({from2} to {to2})."
        )
        is_comparable = False

    if d1_end > as_of_date or d2_end > as_of_date:
        warnings.append(
            f"Period extends past the governed data window (as of {as_of})."
        )

    return {
        "is_comparable": is_comparable,
        "len1_days": len1,
        "len2_days": len2,
        "warnings": warnings,
    }


def reconcile_drivers(
    drivers: list[dict[str, Any]],
    total_delta: float,
) -> dict[str, Any]:
    """Validates that driver contributions sum to total change, calculating unexplained gap."""
    if not drivers:
        return {
            "sum_drivers": 0.0,
            "total_delta": total_delta,
            "unexplained_delta": total_delta,
            "unexplained_pct": 100.0 if total_delta else 0.0,
            "reconciled": False,
        }

    sum_delta = sum(d.get("delta", 0.0) for d in drivers)
    unexplained = total_delta - sum_delta
    unexplained_pct = round(100.0 * (unexplained / total_delta), 1) if total_delta else 0.0

    return {
        "sum_drivers": round(sum_delta, 4),
        "total_delta": round(total_delta, 4),
        "unexplained_delta": round(unexplained, 4),
        "unexplained_pct": unexplained_pct,
        "reconciled": abs(unexplained) < 1e-4,
    }


def compare_slices(
    metric: str,
    slice1_filters: dict[str, list[str]],
    slice2_filters: dict[str, list[str]],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict[str, Any]:
    """Compares two distinct filter slices for the same metric."""
    q1 = MetricQuery(
        metric=metric,
        filters=slice1_filters,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )
    q2 = MetricQuery(
        metric=metric,
        filters=slice2_filters,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )

    res1 = metrics_query(q1)
    res2 = metrics_query(q2)

    val1 = res1.get("total", 0.0) or 0.0
    val2 = res2.get("total", 0.0) or 0.0

    delta = val1 - val2
    pct_change = round(100.0 * (delta / val2), 2) if val2 else None

    return {
        "metric": metric,
        "slice1": {"filters": slice1_filters, "value": val1},
        "slice2": {"filters": slice2_filters, "value": val2},
        "delta": round(delta, 4),
        "pct_change": pct_change,
        "ratio": round(val1 / val2, 4) if val2 else None,
    }


def scan_drivers(
    metric: str,
    date_from: str,
    date_to: str,
    dimensions: list[str] = ("customer_state", "customer_region", "product_category"),
    limit: int = 5,
) -> dict[str, Any]:
    """Scans all available dimensions to find the strongest drivers of metric change."""
    results = {}
    best_dim = None
    max_explained = 0.0

    for dim in dimensions:
        try:
            body = MetricCompare(
                metric=metric,
                group_by=dim,
                date_from=date.fromisoformat(date_from),
                date_to=date.fromisoformat(date_to),
                limit=limit,
            )
            res = metrics_compare(body)
            drivers = res.get("drivers", [])
            tot_delta = res.get("delta") or 0.0
            reconciliation = reconcile_drivers(drivers, tot_delta)

            results[dim] = {
                "drivers": drivers,
                "reconciliation": reconciliation,
            }
            if drivers:
                explained_share = 100.0 - abs(reconciliation["unexplained_pct"])
                if explained_share > max_explained:
                    max_explained = explained_share
                    best_dim = dim
        except Exception:
            continue

    return {
        "metric": metric,
        "best_dimension": best_dim,
        "dimensions_scanned": list(results.keys()),
        "breakdowns": results,
    }
