"""
DCF Valuation API  —  FastAPI + pyodbc (SQL Server)
----------------------------------------------------
Endpoints:
  POST   /api/valuations/save              Save full valuation in one call
  GET    /api/valuations/{ticker}          Latest valuation for a ticker
  GET    /api/valuations/{ticker}/history  All runs for a ticker
  GET    /api/valuations/detail/{id}       Full detail incl. comments
  DELETE /api/valuations/{id}              Delete a run (cascades)

Install:
  pip install -r requirements.txt

Run:
  uvicorn api:app --reload --port 8000

.env file:
  DB_SERVER=localhost
  DB_NAME=dcf_db
  DB_USER=sa
  DB_PASSWORD=YourPassword
  # Leave DB_USER / DB_PASSWORD blank to use Windows Auth instead
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

import httpx
import pyodbc
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

load_dotenv()

# ── Connection string builder ──────────────────────────────────
DB_SERVER   = os.getenv("DB_SERVER",   "localhost")
DB_NAME     = os.getenv("DB_NAME",     "dcf_db")
DB_USER     = os.getenv("DB_USER",     "")        # blank = Windows Auth
DB_PASSWORD    = os.getenv("DB_PASSWORD", "")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY", "")

def _conn_str() -> str:
    driver = "ODBC Driver 17 for SQL Server"
    base   = f"DRIVER={{{driver}}};SERVER={DB_SERVER};DATABASE={DB_NAME};"
    if DB_USER and DB_PASSWORD:
        return base + f"UID={DB_USER};PWD={DB_PASSWORD};"
    return base + "Trusted_Connection=yes;"   # Windows Auth

def get_conn() -> pyodbc.Connection:
    """Return a fresh pyodbc connection (use inside with-block)."""
    return pyodbc.connect(_conn_str(), autocommit=False)


# ── App setup ──────────────────────────────────────────────────
app = FastAPI(
    title="DCF Valuation API",
    description="Save and retrieve DCF / Growth model valuations — SQL Server backend",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# ── Pydantic models ────────────────────────────────────────────

class HistoricalRow(BaseModel):
    yr:     str
    rev:    float
    margin: float


class TickerInputPayload(BaseModel):
    ticker:              str
    company_name:        Optional[str]               = None
    exchange:            Optional[str]               = None
    currency_unit:       Optional[str]               = None
    stock_price:         float
    shares_outstanding:  Optional[float]             = None
    net_debt:            Optional[float]             = None
    wacc:                Optional[float]             = None
    base_revenue:        Optional[float]             = None
    base_year:           Optional[int]               = None
    growth_yr1_2:        Optional[float]             = None
    growth_yr3_5:        Optional[float]             = None
    terminal_growth:     Optional[float]             = None
    ebitda_margin_yr1:   Optional[float]             = None
    ebitda_margin_yr5:   Optional[float]             = None
    dna_pct_rev:         Optional[float]             = None
    capex_pct_rev:       Optional[float]             = None
    nwc_pct_rev:         Optional[float]             = None
    tax_rate:            Optional[float]             = None
    base_eps:            Optional[float]             = None
    eps_growth_yr1_5:    Optional[float]             = None
    eps_growth_yr6_10:   Optional[float]             = None
    terminal_pe:         Optional[float]             = None
    required_return:     Optional[float]             = None
    dividend_per_share:  Optional[float]             = None
    historical_data:     Optional[list[HistoricalRow]] = None
    generated_at:        Optional[datetime]          = None
    created_by:          Optional[str]               = None
    notes:               Optional[str]               = None

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.strip().upper()


class CalcPayload(BaseModel):
    calc_type:           Literal["DCF", "GROWTH"]
    intrinsic_value:     float
    current_price:       float
    upside_pct:          Optional[float] = None
    mos_10_price:        Optional[float] = None
    mos_20_price:        Optional[float] = None
    mos_25_price:        Optional[float] = None
    mos_30_price:        Optional[float] = None
    pv_fcf_sum:          Optional[float] = None
    pv_terminal_value:   Optional[float] = None
    enterprise_value:    Optional[float] = None
    equity_value:        Optional[float] = None
    eps_year10:          Optional[float] = None
    terminal_price:      Optional[float] = None
    pv_terminal_price:   Optional[float] = None
    pv_dividends_sum:    Optional[float] = None
    blended_fair_value:  Optional[float] = None


class CommentPayload(BaseModel):
    comment_type: Literal[
        "RATIONALE","RISK_HIGH","RISK_MED","RISK_LOW",
        "STRESS_BULL","STRESS_BEAR","BALANCE","ANALYST"
    ]
    title:       Optional[str] = None
    body:        str
    sort_order:  int           = 0
    source:      Literal["AI","MANUAL","IMPORTED"] = "AI"


class SaveValuationRequest(BaseModel):
    input:        TickerInputPayload
    calculations: list[CalcPayload]    = Field(default_factory=list)
    comments:     list[CommentPayload] = Field(default_factory=list)


class SaveValuationResponse(BaseModel):
    input_id:        int
    calculation_ids: list[int]
    comment_ids:     list[int]
    ticker:          str
    generated_at:    datetime
    message:         str


# ── Helpers ────────────────────────────────────────────────────

def _mos(iv: float, pct: float) -> float:
    return round(iv * (1 - pct), 4)

def _upside(iv: float, price: float) -> float:
    return round((iv - price) / price * 100, 4) if price else 0.0

def _row_to_dict(cursor, row) -> dict:
    cols = [col[0] for col in cursor.description]
    return dict(zip(cols, row))

def _rows_to_list(cursor, rows) -> list[dict]:
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


# ── Routes ─────────────────────────────────────────────────────

@app.post(
    "/api/valuations/save",
    response_model=SaveValuationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save full valuation — input + calculations + comments",
)
def save_valuation(req: SaveValuationRequest):
    inp = req.input

    # Auto-fill MOS / upside if not provided
    for c in req.calculations:
        if c.upside_pct  is None: c.upside_pct  = _upside(c.intrinsic_value, c.current_price)
        if c.mos_10_price is None: c.mos_10_price = _mos(c.intrinsic_value, 0.10)
        if c.mos_20_price is None: c.mos_20_price = _mos(c.intrinsic_value, 0.20)
        if c.mos_25_price is None: c.mos_25_price = _mos(c.intrinsic_value, 0.25)
        if c.mos_30_price is None: c.mos_30_price = _mos(c.intrinsic_value, 0.30)

    hist_json = (
        json.dumps([r.model_dump() for r in inp.historical_data])
        if inp.historical_data else None
    )
    gen_at = inp.generated_at or datetime.utcnow()

    try:
        with get_conn() as conn:
            cur = conn.cursor()

            # ── 1. Insert TickerInput ──
            cur.execute("""
                INSERT INTO dbo.TickerInput (
                    ticker, company_name, exchange, currency_unit,
                    stock_price, shares_outstanding, net_debt, wacc,
                    base_revenue, base_year,
                    growth_yr1_2, growth_yr3_5, terminal_growth,
                    ebitda_margin_yr1, ebitda_margin_yr5,
                    dna_pct_rev, capex_pct_rev, nwc_pct_rev, tax_rate,
                    base_eps, eps_growth_yr1_5, eps_growth_yr6_10,
                    terminal_pe, required_return, dividend_per_share,
                    historical_data, generated_at, created_by, notes
                )
                OUTPUT INSERTED.id
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
                inp.ticker, inp.company_name, inp.exchange, inp.currency_unit,
                inp.stock_price, inp.shares_outstanding, inp.net_debt, inp.wacc,
                inp.base_revenue, inp.base_year,
                inp.growth_yr1_2, inp.growth_yr3_5, inp.terminal_growth,
                inp.ebitda_margin_yr1, inp.ebitda_margin_yr5,
                inp.dna_pct_rev, inp.capex_pct_rev, inp.nwc_pct_rev, inp.tax_rate,
                inp.base_eps, inp.eps_growth_yr1_5, inp.eps_growth_yr6_10,
                inp.terminal_pe, inp.required_return, inp.dividend_per_share,
                hist_json, gen_at, inp.created_by, inp.notes,
            )
            input_id: int = cur.fetchval()

            # ── 2. Insert TickerCalculation rows ──
            calc_ids: list[int] = []
            for c in req.calculations:
                cur.execute("""
                    INSERT INTO dbo.TickerCalculation (
                        input_id, calc_type,
                        intrinsic_value, current_price, upside_pct,
                        mos_10_price, mos_20_price, mos_25_price, mos_30_price,
                        pv_fcf_sum, pv_terminal_value, enterprise_value, equity_value,
                        eps_year10, terminal_price, pv_terminal_price, pv_dividends_sum,
                        blended_fair_value
                    )
                    OUTPUT INSERTED.id
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                    input_id, c.calc_type,
                    c.intrinsic_value, c.current_price, c.upside_pct,
                    c.mos_10_price, c.mos_20_price, c.mos_25_price, c.mos_30_price,
                    c.pv_fcf_sum, c.pv_terminal_value, c.enterprise_value, c.equity_value,
                    c.eps_year10, c.terminal_price, c.pv_terminal_price, c.pv_dividends_sum,
                    c.blended_fair_value,
                )
                calc_ids.append(cur.fetchval())

            # ── 3. Insert TickerKeyComments rows ──
            comment_ids: list[int] = []
            for i, c in enumerate(req.comments):
                cur.execute("""
                    INSERT INTO dbo.TickerKeyComments (
                        input_id, comment_type, title, body, sort_order, source
                    )
                    OUTPUT INSERTED.id
                    VALUES (?,?,?,?,?,?)
                """,
                    input_id, c.comment_type, c.title, c.body,
                    c.sort_order if c.sort_order else i, c.source,
                )
                comment_ids.append(cur.fetchval())

            conn.commit()

    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return SaveValuationResponse(
        input_id=input_id,
        calculation_ids=calc_ids,
        comment_ids=comment_ids,
        ticker=inp.ticker,
        generated_at=gen_at,
        message=f"Saved {inp.ticker}: {len(calc_ids)} calculation(s), {len(comment_ids)} comment(s).",
    )


@app.get(
    "/api/valuations/{ticker}",
    summary="Latest valuation summary for a ticker",
)
def get_latest(ticker: str):
    ticker = ticker.strip().upper()
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM dbo.v_latest_valuations WHERE ticker = ?", ticker)
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"No valuation found for {ticker}")
            return _row_to_dict(cur, row)
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/valuations/{ticker}/history",
    summary="All valuation runs for a ticker, newest first",
)
def get_history(ticker: str, limit: int = 20):
    ticker = ticker.strip().upper()
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT TOP (?)
                    ti.id AS input_id,
                    ti.ticker, ti.company_name, ti.stock_price,
                    ti.currency_unit, ti.generated_at,
                    tc.calc_type, tc.intrinsic_value,
                    tc.upside_pct, tc.verdict
                FROM dbo.TickerInput ti
                LEFT JOIN dbo.TickerCalculation tc ON tc.input_id = ti.id
                WHERE ti.ticker = ?
                ORDER BY ti.generated_at DESC, tc.calc_type
            """, limit, ticker)
            return _rows_to_list(cur, cur.fetchall())
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/valuations/detail/{input_id}",
    summary="Full detail — input + calculations + comments",
)
def get_detail(input_id: int):
    try:
        with get_conn() as conn:
            cur = conn.cursor()

            cur.execute("SELECT * FROM dbo.TickerInput WHERE id = ?", input_id)
            inp_row = cur.fetchone()
            if not inp_row:
                raise HTTPException(status_code=404, detail=f"input_id {input_id} not found")
            inp = _row_to_dict(cur, inp_row)

            cur.execute(
                "SELECT * FROM dbo.TickerCalculation WHERE input_id = ? ORDER BY calc_type",
                input_id
            )
            calcs = _rows_to_list(cur, cur.fetchall())

            cur.execute("""
                SELECT * FROM dbo.TickerKeyComments
                WHERE input_id = ?
                ORDER BY comment_type, sort_order
            """, input_id)
            comments = _rows_to_list(cur, cur.fetchall())

        return {"input": inp, "calculations": calcs, "comments": comments}
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.delete(
    "/api/valuations/ticker/{ticker}",
    status_code=status.HTTP_200_OK,
    summary="Delete ALL runs for a ticker (cascades to calcs + comments)",
)
def delete_ticker(ticker: str):
    ticker = ticker.strip().upper()
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM dbo.TickerInput WHERE ticker = ?", ticker
            )
            count = cur.fetchval()
            if not count:
                raise HTTPException(status_code=404, detail=f"No valuations found for {ticker}")
            cur.execute(
                "DELETE FROM dbo.TickerInput WHERE ticker = ?", ticker
            )
            conn.commit()
        return {"ticker": ticker, "deleted_runs": count, "message": f"Deleted all {count} run(s) for {ticker}"}
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete(
    "/api/valuations/{input_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a valuation run and all its data (cascade)",
)
def delete_valuation(input_id: int):
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM dbo.TickerInput WHERE id = ?", input_id)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"input_id {input_id} not found")
            conn.commit()
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.get(
    "/api/valuations",
    summary="All tickers saved — grouped with latest summary per ticker",
)
def get_all_tickers():
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            # All distinct tickers with their latest price + both model IVs
            cur.execute("""
                WITH ranked AS (
                    SELECT
                        ti.id, ti.ticker, ti.company_name, ti.exchange,
                        ti.currency_unit, ti.stock_price, ti.generated_at,
                        ROW_NUMBER() OVER (PARTITION BY ti.ticker ORDER BY ti.generated_at DESC) AS rn
                    FROM dbo.TickerInput ti
                )
                SELECT
                    r.ticker, r.company_name, r.exchange, r.currency_unit,
                    r.stock_price AS latest_price, r.generated_at AS last_updated,
                    dcf.intrinsic_value AS dcf_iv, dcf.upside_pct AS dcf_upside, dcf.verdict AS dcf_verdict,
                    grw.intrinsic_value AS growth_iv, grw.upside_pct AS growth_upside, grw.verdict AS growth_verdict,
                    (SELECT COUNT(*) FROM dbo.TickerInput WHERE ticker = r.ticker) AS total_runs
                FROM ranked r
                LEFT JOIN dbo.TickerCalculation dcf
                    ON dcf.input_id = r.id AND dcf.calc_type = 'DCF'
                LEFT JOIN dbo.TickerCalculation grw
                    ON grw.input_id = r.id AND grw.calc_type = 'GROWTH'
                WHERE r.rn = 1
                ORDER BY r.ticker
            """)
            return _rows_to_list(cur, cur.fetchall())
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/valuations/{ticker}/runs",
    summary="All runs for a ticker — each run with its DCF + Growth results",
)
def get_ticker_runs(ticker: str):
    ticker = ticker.strip().upper()
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    ti.id AS input_id,
                    ti.ticker, ti.company_name, ti.stock_price,
                    ti.currency_unit, ti.generated_at, ti.created_by,
                    dcf.intrinsic_value  AS dcf_iv,
                    dcf.upside_pct       AS dcf_upside,
                    dcf.verdict          AS dcf_verdict,
                    dcf.mos_20_price     AS dcf_mos20,
                    dcf.mos_30_price     AS dcf_mos30,
                    grw.intrinsic_value  AS growth_iv,
                    grw.upside_pct       AS growth_upside,
                    grw.verdict          AS growth_verdict,
                    grw.mos_20_price     AS growth_mos20,
                    grw.mos_30_price     AS growth_mos30,
                    CASE
                        WHEN dcf.intrinsic_value IS NOT NULL AND grw.intrinsic_value IS NOT NULL
                        THEN ROUND((dcf.intrinsic_value + grw.intrinsic_value) / 2, 4)
                        ELSE COALESCE(dcf.intrinsic_value, grw.intrinsic_value)
                    END AS blended_iv
                FROM dbo.TickerInput ti
                LEFT JOIN dbo.TickerCalculation dcf
                    ON dcf.input_id = ti.id AND dcf.calc_type = 'DCF'
                LEFT JOIN dbo.TickerCalculation grw
                    ON grw.input_id = ti.id AND grw.calc_type = 'GROWTH'
                WHERE ti.ticker = ?
                ORDER BY ti.generated_at DESC
            """, ticker)
            return _rows_to_list(cur, cur.fetchall())
    except pyodbc.Error as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def root():
    return {"status": "DCF Valuation API is running", "docs": "/docs", "health": "/api/health"}


# ── Anthropic proxy ────────────────────────────────────────────
# The browser cannot call api.anthropic.com directly (CORS).
# The HTML calls this endpoint instead; FastAPI forwards the
# request server-side and streams the response back.

class AnthropicProxyRequest(BaseModel):
    model:     str            = "claude-sonnet-4-5"
    max_tokens: int           = 1500
    messages:  list[dict]
    tools:     list[dict]     = Field(default_factory=list)


@app.post("/api/ai/messages", summary="Proxy to Anthropic API (avoids browser CORS)")
async def anthropic_proxy(req: AnthropicProxyRequest):
    if not ANTHROPIC_KEY:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not set in .env — add it and restart the server"
        )

    has_web_search = any(
        t.get("type") == "web_search_20250305" for t in req.tools
    )

    payload = {
        "model":      req.model,
        "max_tokens": min(req.max_tokens, 1024),   # hard cap — never exceed 1024 output tokens
        "messages":   req.messages,
    }

    if has_web_search:
        # Limit web search results to reduce input tokens
        payload["tools"] = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 2,               # max 2 searches per call
            }
        ]
        # Truncate the user message to 1500 chars to keep input tokens low
        if payload["messages"] and payload["messages"][-1].get("role") == "user":
            content = payload["messages"][-1].get("content", "")
            if isinstance(content, str) and len(content) > 1500:
                payload["messages"][-1]["content"] = content[:1500]
    elif req.tools:
        payload["tools"] = req.tools

    async with httpx.AsyncClient(timeout=90) as client:
        try:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta":    "web-search-2025-03-05",
                    "content-type":      "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Could not reach Anthropic: {str(e)}")

@app.get("/api/health", summary="Health check")
def health():
    try:
        with get_conn() as conn:
            conn.cursor().execute("SELECT 1")
        return {"status": "ok", "database": DB_NAME, "server": DB_SERVER}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)