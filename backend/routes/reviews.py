"""Reviews (real Olist data) and seller replies (synthetic, writable)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from backend.catalog import endpoint
from backend.common import (ApiError, Body, Paging, Where, check_enum, check_enum_list, date_range, order_by,
                            page, resolve_ref, source)
from backend.config import sim_now
from backend.db import ro
from backend.writes import Changes, WriteOpts, run_write

router = APIRouter()


class ReviewsQuery(Paging):
    score_max: int | None = Field(None, ge=1, le=5, description="Score at most (1-5)")
    score_min: int | None = Field(None, ge=1, le=5, description="Score at least (1-5)")
    customer_state: str | None = Field(None, description="Customer state codes, comma-separated")
    seller_id: str | None = Field(None, description="Reviews of orders with items from this seller")
    product_category: str | None = Field(None, description="Reviews of orders containing this category")
    has_comment: bool | None = Field(None, description="true = the customer wrote a comment")
    has_reply: bool | None = Field(None, description="true = the seller already replied")
    date_from: date | None = Field(None, description="Review created on or after")
    date_to: date | None = Field(None, description="Review created on or before")
    preset: str | None = Field(None, description="Relative review-date range, e.g. last_30_days")
    sort: str | None = Field(None, description="created_at (default -created_at) or score")


@endpoint(router, "GET", "/api/reviews", api_id="reviews.search", entity="review",
          title="Search customer reviews",
          description="Customer reviews with score, comment, order, customer state and whether the seller replied. "
                      "Filter by score, state, seller, category, comment, reply status or review date.",
          data_layers=["original", "synthetic"],
          returns="items[] of reviews with order_ref, score, comment, reply; total",
          examples=["1-star reviews without a reply", "negative reviews in SP last month",
                    "what are customers complaining about"])
def reviews_search(q: Annotated[ReviewsQuery, Query()]):
    w = Where()
    with ro() as conn:
        if q.score_max:
            w.add("r.review_score <= %(smax)s", smax=q.score_max)
        if q.score_min:
            w.add("r.review_score >= %(smin)s", smin=q.score_min)
        if states := check_enum_list("customer_state", q.customer_state, "customer_state"):
            w.add("s.customer_state = ANY(%(states)s)", states=states)
        if q.seller_id:
            w.add("%(seller)s = ANY(s.seller_ids)",
                  seller=resolve_ref(conn, "raw_sellers", "seller_id", q.seller_id, "seller"))
        if q.product_category:
            w.add("%(cat)s = ANY(s.categories)", cat=check_enum("product_category", q.product_category))
        if q.has_comment is not None:
            w.add("(r.review_comment_message IS NOT NULL) = %(hc)s", hc=q.has_comment)
        if q.has_reply is not None:
            w.add(("" if q.has_reply else "NOT ") + "EXISTS (SELECT 1 FROM ops_review_replies x "
                  "WHERE x.review_id = r.review_id AND x.order_id = r.order_id)")
        f, t = date_range(q.date_from, q.date_to, q.preset)
        if f:
            w.add("r.review_creation_date >= %(f)s", f=f)
        if t:
            w.add("r.review_creation_date < %(t)s::date + 1", t=t)
        frm = "FROM raw_reviews r JOIN core_order_summary s USING (order_id)"
        total = conn.execute(f"SELECT count(*) AS n {frm}{w.sql()}", w.params).fetchone()["n"]
        rows = conn.execute(f"""
            SELECT r.review_id, r.order_id, s.order_ref, r.review_score, r.review_comment_title,
                   r.review_comment_message, r.review_creation_date AS created_at, s.customer_state, s.categories,
                   (SELECT json_build_object('code', x.code, 'reply_text', x.reply_text, 'created_at', x.created_at)
                    FROM ops_review_replies x WHERE x.review_id = r.review_id AND x.order_id = r.order_id
                    ORDER BY x.id DESC LIMIT 1) AS reply
            {frm}{w.sql()}{order_by(q.sort, {'created_at': 'r.review_creation_date', 'score': 'r.review_score'},
                                    '-created_at')}
            LIMIT %(limit)s OFFSET %(offset)s""", {**w.params, "limit": q.limit, "offset": q.offset}).fetchall()
    return page(rows, total, q.limit, q.offset,
                source(["raw_reviews", "core_order_summary", "ops_review_replies"], ["original", "synthetic"],
                       "reviews are real Olist data; replies are synthetic"))


class ReplyCreate(Body):
    reply_text: str = Field(..., min_length=2, max_length=2000, description="The seller's public reply")
    order_id: str | None = Field(None, description="Needed only when the review_id belongs to several orders")


@endpoint(router, "POST", "/api/reviews/{review_id}/reply", api_id="reviews.reply", entity="review",
          title="Reply to a review",
          description="Post the seller's public reply to a customer review. One reply per review; refused if "
                      "the review already has one.",
          data_layers=["synthetic"], returns="the new reply (code RPL-...)",
          examples=["reply to this review apologising for the delay", "answer the 1-star review on order E481F51C"])
def reviews_reply(review_id: str, body: ReplyCreate, opts: WriteOpts = Depends()):
    def apply(conn, ch: Changes):
        rid = review_id.strip().lower()
        params = {"r": rid}
        cond = "review_id = %(r)s"
        if body.order_id:
            params["o"] = resolve_ref(conn, "raw_orders", "order_id", body.order_id, "order")
            cond += " AND order_id = %(o)s"
        found = conn.execute(f"SELECT review_id, order_id FROM raw_reviews WHERE {cond}", params).fetchall()
        if not found:
            raise ApiError(404, "review_not_found", f"No review '{review_id}'.")
        if len(found) > 1:
            raise ApiError(409, "ambiguous_review", "This review_id belongs to several orders; pass order_id.",
                           candidates=[x["order_id"] for x in found])
        rev = found[0]
        dup = conn.execute("SELECT code FROM ops_review_replies WHERE review_id = %(r)s AND order_id = %(o)s",
                           {"r": rev["review_id"], "o": rev["order_id"]}).fetchone()
        if dup:
            raise ApiError(409, "already_replied", f"This review already has reply {dup['code']}.", existing=dup["code"])
        return ch.insert("ops_review_replies", {"review_id": rev["review_id"], "order_id": rev["order_id"],
                                                "reply_text": body.reply_text, "created_at": sim_now(),
                                                "created_by": opts.actor})

    return run_write("reviews.reply", opts, {"review_id": review_id, **body.model_dump()}, apply)
