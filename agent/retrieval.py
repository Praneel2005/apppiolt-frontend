"""Hybrid retrieval over application metadata (pages, widgets, APIs, entities).

Features:
- BM25 lexical search with business synonym query expansion (reusing backend/retrieval.py)
- Dense semantic vector search (sentence-transformers + FAISS if available; vectorized numpy fallback)
- Reciprocal Rank Fusion (RRF with k=60) combining lexical and dense rankings
- Metadata knowledge graph expansion using networkx (directory -> page -> widget -> dataset -> API)
- Controlled by AgentConfig.use_graph_expansion (ablation A3)
"""
from __future__ import annotations

import collections
import hashlib
import logging
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import networkx as nx
import numpy as np

from backend.config import app_model
from backend.retrieval import _index as get_bm25_index, tokens
from contracts.actions import AgentConfig
from contracts.metadata import Application, Page

logger = logging.getLogger("agent.retrieval")

# Optional ML packages
try:
    from sentence_transformers import SentenceTransformer  # type: ignore
    import faiss  # type: ignore
    _HAS_ML = True
except ImportError:
    _HAS_ML = False


@dataclass
class RetrievalCandidate:
    kind: str  # "page" | "api" | "entity"
    id: str
    title: str
    snippet: str
    score: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    pages: list[RetrievalCandidate]
    apis: list[RetrievalCandidate]
    graph_expanded_ids: list[str] = field(default_factory=list)
    validation: Any | None = None


# ------------------------------------------------------------------------------
# Dense Vector Search (Fallback / Fast Numpy Vectorizer)
# ------------------------------------------------------------------------------
class SimpleDenseIndex:
    """Fast hashing-based subword n-gram vectorizer with cosine similarity when ML packages are not installed."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.doc_vectors: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self.doc_ids: list[str] = []

    def _embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        words = re.findall(r"\w+", text.lower())
        for w in words:
            # Hash character 3-grams
            for i in range(max(1, len(w) - 2)):
                ngram = w[i : i + 3]
                h = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16) % self.dim
                vec[h] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def fit(self, docs: list[dict[str, Any]]) -> None:
        self.doc_ids = [d["id"] for d in docs]
        vecs = [self._embed(d["text"]) for d in docs]
        self.doc_vectors = np.array(vecs, dtype=np.float32)

    def search(self, query: str, top_k: int = 15) -> list[tuple[str, float]]:
        if len(self.doc_vectors) == 0:
            return []
        q_vec = self._embed(query)
        scores = np.dot(self.doc_vectors, q_vec)
        top_idx = np.argsort(-scores)[:top_k]
        return [(self.doc_ids[idx], float(scores[idx])) for idx in top_idx if scores[idx] > 0]


class MLVectorIndex:
    """Dense vector index using sentence-transformers and FAISS."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model = SentenceTransformer(model_name)
        self.index = None
        self.doc_ids: list[str] = []

    def fit(self, docs: list[dict[str, Any]]) -> None:
        self.doc_ids = [d["id"] for d in docs]
        texts = [f"{d['title']}: {d['text']}" for d in docs]
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(np.array(embeddings, dtype=np.float32))

    def search(self, query: str, top_k: int = 15) -> list[tuple[str, float]]:
        if self.index is None:
            return []
        q_emb = self.model.encode([query], normalize_embeddings=True)
        scores, indices = self.index.search(np.array(q_emb, dtype=np.float32), top_k)
        results = []
        for s, idx in zip(scores[0], indices[0]):
            if idx >= 0 and idx < len(self.doc_ids):
                results.append((self.doc_ids[idx], float(s)))
        return results


# ------------------------------------------------------------------------------
# Metadata Knowledge Graph (networkx)
# ------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def build_metadata_graph() -> nx.DiGraph:
    """Builds knowledge graph: directory -> page -> widget -> dataset -> metric & API."""
    from backend.main import app as asgi_app
    from backend.routes.system import catalog_entries

    app = app_model()
    G = nx.DiGraph()

    for p in app.pages:
        G.add_node(p.directory, kind="directory", label=p.directory)
        G.add_node(p.page_id, kind="page", label=p.title, route=p.route)
        G.add_edge(p.directory, p.page_id, relation="contains_page")
        G.add_edge(p.page_id, p.directory, relation="in_directory")

        for wid in p.widgets:
            w = app.widget(wid)
            G.add_node(w.widget_id, kind="widget", label=w.title, type=w.type)
            G.add_edge(p.page_id, w.widget_id, relation="has_widget")
            G.add_edge(w.widget_id, p.page_id, relation="on_page")

            # Link widget to dataset if metric widget
            if w.dataset_id:
                G.add_node(w.dataset_id, kind="dataset", label=w.dataset_id)
                G.add_edge(w.widget_id, w.dataset_id, relation="queries_dataset")

            # Link widget to API if API-bound
            if w.source and getattr(w.source, "api_id", None):
                api_id = w.source.api_id
                G.add_node(api_id, kind="api", label=api_id)
                G.add_edge(w.widget_id, api_id, relation="bound_to_api")
                G.add_edge(p.page_id, api_id, relation="page_uses_api")

    for a in catalog_entries(asgi_app):
        api_id = a["api_id"]
        G.add_node(api_id, kind="api", label=a["title"], method=a["method"], path=a["path"])
        entity = a.get("entity")
        if entity:
            G.add_node(f"entity:{entity}", kind="entity", label=entity)
            G.add_edge(api_id, f"entity:{entity}", relation="targets_entity")

    return G


# ------------------------------------------------------------------------------
# Hybrid Retriever Engine
# ------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_vector_index():
    docs, _ = get_bm25_index()
    if _HAS_ML:
        try:
            idx = MLVectorIndex()
            idx.fit(docs)
            return idx
        except Exception as e:
            logger.warning("MLVectorIndex failed to initialize (%s); falling back to SimpleDenseIndex", e)
    idx = SimpleDenseIndex()
    idx.fit(docs)
    return idx


def hybrid_search(
    query: str,
    config: AgentConfig | None = None,
    top_pages: int = 8,
    top_apis: int = 6,
    k_rrf: int = 60,
) -> RetrievalResult:
    """Executes hybrid retrieval: BM25 + dense vectors -> RRF -> graph expansion."""
    cfg = config or AgentConfig()
    app = app_model()
    docs, bm25 = get_bm25_index()
    doc_map = {d["id"]: d for d in docs}

    # 1. Lexical BM25 ranking
    q_tokens = tokens(query, expand=True)
    bm25_scores = bm25.get_scores(q_tokens) if q_tokens else [0.0] * len(docs)
    lexical_ranked = sorted(
        ((s, d["id"]) for s, d in zip(bm25_scores, docs) if s > 0),
        key=lambda x: -x[0],
    )

    # 2. Dense semantic ranking
    vec_index = _get_vector_index()
    dense_ranked = vec_index.search(query, top_k=30)

    # 3. Reciprocal Rank Fusion (RRF)
    rrf_scores: dict[str, float] = collections.defaultdict(float)
    for rank, (_, doc_id) in enumerate(lexical_ranked[:40]):
        rrf_scores[doc_id] += 1.0 / (k_rrf + rank + 1)
    for rank, (doc_id, _) in enumerate(dense_ranked):
        rrf_scores[doc_id] += 1.0 / (k_rrf + rank + 1)

    sorted_docs = sorted(rrf_scores.items(), key=lambda x: -x[1])

    # Partition by kind
    page_candidates: list[RetrievalCandidate] = []
    api_candidates: list[RetrievalCandidate] = []

    for doc_id, score in sorted_docs:
        d = doc_map.get(doc_id)
        if not d:
            continue
        cand = RetrievalCandidate(
            kind=d["kind"],
            id=d["id"],
            title=d["title"],
            snippet=d.get("snippet", ""),
            score=round(score, 4),
            data=d,
        )
        if d["kind"] == "page":
            page_candidates.append(cand)
        elif d["kind"] == "api":
            api_candidates.append(cand)

    # 4. Graph expansion (networkx) if enabled
    expanded_ids: list[str] = []
    if cfg.use_graph_expansion:
        G = build_metadata_graph()
        # Seed from top-3 pages
        seed_pages = [p.id for p in page_candidates[:3]]
        for pid in seed_pages:
            if pid not in G:
                continue
            # Look for directly connected APIs or sibling pages in the same directory
            for neighbor in G.neighbors(pid):
                n_data = G.nodes[neighbor]
                if n_data.get("kind") == "api" and neighbor not in [a.id for a in api_candidates]:
                    d = doc_map.get(neighbor)
                    if d:
                        api_candidates.append(
                            RetrievalCandidate(
                                kind="api",
                                id=neighbor,
                                title=d["title"],
                                snippet=d.get("snippet", ""),
                                score=0.005,
                                data=d,
                            )
                        )
                        expanded_ids.append(neighbor)
                elif n_data.get("kind") == "directory":
                    # Siblings in the same directory
                    for sibling in G.neighbors(neighbor):
                        if (
                            G.nodes[sibling].get("kind") == "page"
                            and sibling not in [p.id for p in page_candidates]
                        ):
                            d = doc_map.get(sibling)
                            if d:
                                page_candidates.append(
                                    RetrievalCandidate(
                                        kind="page",
                                        id=sibling,
                                        title=d["title"],
                                        snippet=d.get("snippet", ""),
                                        score=0.005,
                                        data=d,
                                    )
                                )
                                expanded_ids.append(sibling)

    # Enrich candidates with metadata schemas
    final_pages = page_candidates[:top_pages]
    for p in final_pages:
        try:
            page_obj = app.page(p.id)
            p.data["filters"] = [
                {"id": f.filter_id, "title": f.title, "allowed_values": f.allowed_values}
                for f in page_obj.filters
            ]
            p.data["widgets"] = page_obj.widgets
            p.data["route"] = page_obj.route
        except Exception:
            pass

    final_apis = api_candidates[:top_apis]

    return RetrievalResult(
        query=query,
        pages=final_pages,
        apis=final_apis,
        graph_expanded_ids=expanded_ids,
    )
