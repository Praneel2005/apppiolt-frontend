"""AppPilot backend. Run from the repo root:

    uvicorn backend.main:app --port 8000 --reload

Interactive docs: http://localhost:8000/docs   API catalogue: http://localhost:8000/api/catalog
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import db
from backend.common import ApiError
from backend.config import CORS_ORIGINS, as_of_date, sim_now
from backend.routes import (funnel, metrics, orders, plan, products, promotions, reviews, sellers, session, summaries,
                            system, tickets)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pools()
    yield
    db.close_pools()


app = FastAPI(title="AppPilot - Olist Seller Operations API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

for r in (metrics, plan, funnel, summaries, orders, products, sellers, reviews, tickets, promotions, system, session):
    app.include_router(r.router)


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, e: ApiError):
    return JSONResponse(e.body(), status_code=e.status)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, e: RequestValidationError):
    issues = [{"where": ".".join(str(x) for x in err["loc"]), "problem": err["msg"], "type": err["type"]}
              for err in e.errors()]
    return JSONResponse({"error": {"code": "invalid_request", "message": "The request is not valid.",
                                   "issues": issues}}, status_code=422)


@app.get("/api/health", tags=["metadata"])
def health():
    return {"status": "ok", "as_of_date": as_of_date().isoformat(), "sim_now": sim_now().isoformat()}
