"""Retrieval over the application's metadata: pages (with widgets and filters), catalogue APIs, entities.

BM25 over the descriptions written for retrieval, with query expansion over business synonyms. The
agent layer adds dense embeddings and graph expansion on top (hybrid retrieval); this service is the
lexical, deterministic part and is useful on its own (GET /api/search).
"""
from __future__ import annotations

import re
from functools import lru_cache

from rank_bm25 import BM25Okapi

from backend.catalog import ENTITIES
from backend.config import app_model

SYNONYM_GROUPS = [
    {"late", "delayed", "delay", "overdue", "slow", "sla", "breach", "breaches"},
    {"revenue", "sales", "turnover", "gmv", "income"},
    {"inventory", "stock", "stocks", "stockout", "warehouse"},
    {"ticket", "complaint", "case", "support", "issue"},
    {"seller", "merchant", "vendor", "shop"},
    {"order", "purchase"},
    {"review", "rating", "feedback", "stars", "satisfaction"},
    {"promotion", "discount", "campaign", "offer", "coupon"},
    {"restock", "reorder", "replenish", "replenishment", "resupply"},
    {"target", "goal", "quota", "plan", "attainment"},
    {"forecast", "prediction", "projection", "outlook", "predicted"},
    {"lead", "funnel", "acquisition", "channel", "conversion"},
    {"customer", "buyer", "client"},
    {"price", "pricing", "cost", "margin"},
    {"delivery", "shipping", "shipment", "freight", "logistics"},
    {"state", "region", "location", "geography"},
]
_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "by", "and", "or", "is", "are", "was", "were", "with", "my",
         "me", "i", "show", "give", "get", "what", "which", "how", "many", "much", "do", "does", "all", "this", "that",
         "from", "at", "it", "be", "can", "you", "please", "last", "next"}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str, expand: bool = False) -> list[str]:
    ws = [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")) if w not in _STOP]
    if not expand:
        return ws
    out = list(ws)
    for w in ws:
        for g in SYNONYM_GROUPS:
            stems = {_stem(x) for x in g}
            if w in stems:
                out += sorted(stems - {w})
    return out


@lru_cache(maxsize=1)
def _index():
    from backend.main import app as asgi_app
    from backend.routes.system import catalog_entries

    app = app_model()
    docs: list[dict] = []
    for p in app.pages:
        ws = [app.widget(w) for w in p.widgets]
        text = " ".join([p.title] * 2 + [p.description, p.agent_context, " ".join(p.keywords),
                                         " ".join(w.title + " " + w.description for w in ws),
                                         " ".join(f.title for f in p.filters), p.directory.replace("_", " ")])
        docs.append({"kind": "page", "id": p.page_id, "code": p.page_code, "title": p.title, "route": p.route,
                     "page_kind": p.kind, "snippet": p.description[:160], "text": text})
    for a in catalog_entries(asgi_app):
        text = " ".join([a["title"]] * 2 + [a["description"], a["search_text"], a["entity"], " ".join(a["examples"])])
        docs.append({"kind": "api", "id": a["api_id"], "title": a["title"], "method": a["method"], "path": a["path"],
                     "api_kind": a["kind"], "snippet": a["description"][:160], "text": text})
    for name, e in ENTITIES.items():
        docs.append({"kind": "entity", "id": name, "title": name.replace("_", " ").title(),
                     "snippet": e["description"], "text": f"{name} {e['description']} {e['id']}"})
    bm = BM25Okapi([tokens(d["text"]) for d in docs])
    return docs, bm


def search(query: str, kinds: tuple[str, ...] = ("page", "api", "entity"), k: int = 5) -> list[dict]:
    docs, bm = _index()
    q = tokens(query, expand=True)
    if not q:
        return []
    scores = bm.get_scores(q)
    ranked = sorted(((s, d) for s, d in zip(scores, docs) if d["kind"] in kinds and s > 0), key=lambda x: -x[0])[:k]
    top = ranked[0][0] if ranked else 1.0
    return [{**{kk: v for kk, v in d.items() if kk != "text"}, "score": round(float(s / top), 3)} for s, d in ranked]
