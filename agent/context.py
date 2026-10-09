"""Session context builder, conversation memory, and reference resolution.

Maintains:
- Sliding dialogue history (~6 turns; older summarized)
- Visible page context (active filters, dates, visible widget rows)
- Pronoun and anaphora resolution ("this order", "these tickets", "same for SP", "that region")
- Delimits data as untrusted to prevent prompt injection
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    user_message: str
    intent: str = "navigate"
    plan_summary: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionMemory:
    session_id: str
    turns: list[ConversationTurn] = field(default_factory=list)
    max_turns: int = 6
    summary_of_older_turns: str = ""

    def add_turn(self, turn: ConversationTurn) -> None:
        self.turns.append(turn)
        if len(self.turns) > self.max_turns:
            evicted = self.turns.pop(0)
            if self.summary_of_older_turns:
                self.summary_of_older_turns += f"; Earlier: user asked '{evicted.user_message}' -> {evicted.intent}"
            else:
                self.summary_of_older_turns = f"Earlier: user asked '{evicted.user_message}' -> {evicted.intent}"

    def last_turn(self) -> ConversationTurn | None:
        return self.turns[-1] if self.turns else None


# In-memory registry of session memories
SESSION_MEMORIES: dict[str, SessionMemory] = {}


def get_memory(session_id: str) -> SessionMemory:
    if session_id not in SESSION_MEMORIES:
        SESSION_MEMORIES[session_id] = SessionMemory(session_id=session_id)
    return SESSION_MEMORIES[session_id]


def resolve_references(
    query: str,
    page_ctx: dict[str, Any],
    memory: SessionMemory,
) -> dict[str, Any]:
    """Resolves 'this order', 'these tickets', 'same for SP', etc. from context and memory."""
    resolved: dict[str, Any] = {}
    q_lower = query.lower()

    # 1. Inspect visible table widgets on current page
    widgets = page_ctx.get("widgets", [])
    table_rows: list[dict[str, Any]] = []
    for w in widgets:
        rows = w.get("rows", [])
        if rows:
            table_rows.extend(rows)

    # 2. Singular pronoun: "this order", "this ticket", "this product", "this seller"
    if "this order" in q_lower or "the order" in q_lower:
        # Check selected widget / selected row
        for r in table_rows:
            if "order_id" in r or "order_ref" in r:
                resolved["order_id"] = r.get("order_id") or r.get("order_ref")
                break
    elif "this ticket" in q_lower or "the ticket" in q_lower:
        for r in table_rows:
            if "ticket_id" in r or "code" in r:
                resolved["ticket_id"] = r.get("ticket_id") or r.get("code")
                break
    elif "this product" in q_lower or "the product" in q_lower:
        for r in table_rows:
            if "product_id" in r or "product_ref" in r:
                resolved["product_id"] = r.get("product_id") or r.get("product_ref")
                break
    elif "this seller" in q_lower or "the seller" in q_lower:
        for r in table_rows:
            if "seller_id" in r or "seller_ref" in r:
                resolved["seller_id"] = r.get("seller_id") or r.get("seller_ref")
                break

    # 3. Plural pronoun: "these orders", "these products", "these tickets"
    if "these orders" in q_lower:
        oids = [r.get("order_id") or r.get("order_ref") for r in table_rows if r.get("order_id") or r.get("order_ref")]
        if oids:
            resolved["order_ids"] = oids[:10]
    elif "these tickets" in q_lower:
        tids = [r.get("ticket_id") or r.get("code") for r in table_rows if r.get("ticket_id") or r.get("code")]
        if tids:
            resolved["ticket_ids"] = tids[:10]
    elif "these products" in q_lower:
        pids = [r.get("product_id") or r.get("product_ref") for r in table_rows if r.get("product_id") or r.get("product_ref")]
        if pids:
            resolved["product_ids"] = pids[:10]

    # 4. Elliptical follow-up: "same for <X>", "now for <X>", "compare with <X>"
    last = memory.last_turn()
    if last and last.slots:
        match_same = re.search(r"(?:same\s+for|now\s+for|and\s+for)\s+([A-Za-z0-9_\s]+)", q_lower)
        if match_same:
            target = match_same.group(1).strip().upper()
            # Inherit previous slots
            resolved.update(last.slots)
            # Override target dimension/state
            if len(target) == 2:  # Likely Brazilian state code (SP, RJ, etc.)
                resolved["customer_state"] = target
                resolved["filters"] = {**resolved.get("filters", {}), "customer_state": target}
            else:
                resolved["target_value"] = target

    return resolved


def build_prompt_context(
    page_ctx: dict[str, Any],
    memory: SessionMemory,
    resolved_refs: dict[str, Any],
) -> str:
    """Builds structured prompt context with explicit untrusted boundaries for data."""
    lines: list[str] = []

    # Current screen state
    page = page_ctx.get("page", {})
    lines.append(f"CURRENT PAGE: {page.get('title', 'Unknown')} (id: {page.get('page_id', 'unknown')}, route: {page.get('route', '/')})")
    if page.get("agent_context"):
        lines.append(f"PAGE CONTEXT: {page.get('agent_context')}")

    active_filters = page_ctx.get("active_filters", {})
    if active_filters:
        lines.append(f"ACTIVE FILTERS: {json.dumps(active_filters, ensure_ascii=False)}")
    date_range = page_ctx.get("date_range", {})
    if date_range:
        lines.append(f"ACTIVE DATE RANGE: {json.dumps(date_range, ensure_ascii=False)}")

    # Resolved entity references
    if resolved_refs:
        lines.append(f"RESOLVED PRONOUN REFERENCES: {json.dumps(resolved_refs, ensure_ascii=False)}")

    # Dialogue history
    if memory.summary_of_older_turns:
        lines.append(f"EARLIER CONVERSATION: {memory.summary_of_older_turns}")
    if memory.turns:
        lines.append("RECENT TURNS:")
        for i, t in enumerate(memory.turns[-memory.max_turns :]):
            lines.append(f"  User turn {i+1}: '{t.user_message}' -> Intent: {t.intent}")

    # Visible widget rows (Delimited as untrusted data)
    widgets = page_ctx.get("widgets", [])
    if widgets:
        lines.append("\n=== UNTRUSTED APPLICATION DATA (VISIBLE ON SCREEN) ===")
        for w in widgets:
            w_title = w.get("title", "Widget")
            w_rows = w.get("rows", [])[:5]  # first 5 rows
            if w_rows:
                lines.append(f"Widget '{w_title}' ({w.get('widget_id', '')}):")
                for r in w_rows:
                    # Sanitize row to prevent instruction injection
                    clean_row = {k: v for k, v in r.items() if not isinstance(v, (dict, list))}
                    lines.append(f"  - {json.dumps(clean_row, ensure_ascii=False)}")
        lines.append("=== END UNTRUSTED DATA ===\n")

    return "\n".join(lines)
