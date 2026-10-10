"""Structured reference resolver (WP2 in AGENT_V2_DESIGN.md).

Resolves pronouns and anaphora ('this order', 'these tickets', 'that region',
'the second one') from open surfaces, selected rows, and the previous frame.
Explicitly avoids false positives on phrases like 'the order of magnitude' or
'in ascending order'.
"""
from __future__ import annotations

import re
from typing import Any


GENERIC_ORDER_PATTERNS = [
    r"\border\s+of\s+magnitude\b",
    r"\bin\s+(?:ascending|descending)\s+order\b",
    r"\border\s+by\b",
    r"\baverage\s+order\s+value\b",
    r"\border\s+count\b",
    r"\bto\s+order\b",
]


def resolve_structured_references(
    query: str,
    page_ctx: dict[str, Any],
    memory_last_turn: Any | None = None,
) -> dict[str, Any]:
    """Resolves entity references from UI context and conversation history."""
    resolved: dict[str, Any] = {}
    lower = query.lower()

    # Collect visible table rows and selected items
    widgets = page_ctx.get("widgets", [])
    table_rows: list[dict[str, Any]] = []
    selected_row: dict[str, Any] | None = None

    for w in widgets:
        rows = w.get("rows", [])
        if rows:
            table_rows.extend(rows)
        if w.get("selected_row"):
            selected_row = w.get("selected_row")

    # 1. Order reference resolution
    is_generic_order = any(re.search(p, lower) for p in GENERIC_ORDER_PATTERNS)
    if not is_generic_order:
        order_match = re.search(r"\b(?:this|that|the|selected)\s+order\b", lower)
        if order_match:
            if selected_row and ("order_id" in selected_row or "order_ref" in selected_row):
                resolved["order_id"] = selected_row.get("order_id") or selected_row.get("order_ref")
            elif table_rows:
                for r in table_rows:
                    if "order_id" in r or "order_ref" in r:
                        resolved["order_id"] = r.get("order_id") or r.get("order_ref")
                        break

    # 2. Ticket reference
    if re.search(r"\b(?:this|that|the|selected)\s+ticket\b", lower):
        if selected_row and ("ticket_id" in selected_row or "code" in selected_row):
            resolved["ticket_id"] = selected_row.get("ticket_id") or selected_row.get("code")
        elif table_rows:
            for r in table_rows:
                if "ticket_id" in r or "code" in r:
                    resolved["ticket_id"] = r.get("ticket_id") or r.get("code")
                    break

    # 3. Product reference
    if re.search(r"\b(?:this|that|the|selected)\s+product\b", lower):
        if selected_row and ("product_id" in selected_row or "product_ref" in selected_row):
            resolved["product_id"] = selected_row.get("product_id") or selected_row.get("product_ref")
        elif table_rows:
            for r in table_rows:
                if "product_id" in r or "product_ref" in r:
                    resolved["product_id"] = r.get("product_id") or r.get("product_ref")
                    break

    # 4. Seller reference
    if re.search(r"\b(?:this|that|the|selected)\s+seller\b", lower):
        if selected_row and ("seller_id" in selected_row or "seller_ref" in selected_row):
            resolved["seller_id"] = selected_row.get("seller_id") or selected_row.get("seller_ref")
        elif table_rows:
            for r in table_rows:
                if "seller_id" in r or "seller_ref" in r:
                    resolved["seller_id"] = r.get("seller_id") or r.get("seller_ref")
                    break

    # 5. Indexed row reference: "the second one", "row 3", "first item"
    ordinal_map = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
    m_ordinal = re.search(r"\bthe\s+(first|second|third|fourth|fifth)\s+(?:one|row|item)\b", lower)
    if m_ordinal and table_rows:
        idx = ordinal_map.get(m_ordinal.group(1), 0)
        if idx < len(table_rows):
            r = table_rows[idx]
            for key in ("order_id", "ticket_id", "product_id", "seller_id"):
                if key in r:
                    resolved[key] = r[key]

    # 6. Plural references: "these orders", "these tickets"
    if re.search(r"\bthese\s+orders\b", lower):
        oids = [r.get("order_id") or r.get("order_ref") for r in table_rows if r.get("order_id") or r.get("order_ref")]
        if oids:
            resolved["order_ids"] = oids[:10]
    elif re.search(r"\bthese\s+tickets\b", lower):
        tids = [r.get("ticket_id") or r.get("code") for r in table_rows if r.get("ticket_id") or r.get("code")]
        if tids:
            resolved["ticket_ids"] = tids[:10]

    # 7. Carry-over from previous turn if not resolved
    if memory_last_turn and hasattr(memory_last_turn, "slots"):
        for k, v in memory_last_turn.slots.items():
            if k not in resolved and k.endswith("_id"):
                resolved[k] = v

    return resolved
