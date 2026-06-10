"""
daily_recalc.py  —  PLM Ticker Watcher Nightly Recalculator
=============================================================
Fetches the latest saved assumptions for every ticker in the
database, pulls a fresh live price via yfinance, re-runs DCF
and Growth model maths, then inserts a new dated run so the
dashboard always shows today's verdict.

Usage:
    python daily_recalc.py              # runs all tickers
    python daily_recalc.py TSLA AAPL    # runs specific tickers only
    python daily_recalc.py --dry-run    # shows what would happen, no DB writes

Schedule (Windows Task Scheduler):
    Action:  python C:\\path\\to\\daily_recalc.py
    Trigger: Daily at 07:00 AM
    Start in: C:\\path\\to\\project (so it finds .env)

Logs are written to: daily_recalc.log  (same folder as this script)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import pyodbc
from dotenv import load_dotenv

# ── Logging ────────────────────────────────────────────────────
log_path = Path(__file__).parent / "daily_recalc.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("recalc")

# ── Env / DB ────────────────────────────────────────────────────
load_dotenv()

DB_SERVER   = os.getenv("DB_SERVER",   "localhost")
DB_NAME     = os.getenv("DB_NAME",     "dcf_db")
DB_USER     = os.getenv("DB_USER",     "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


def _conn_str() -> str:
    driver = "ODBC Driver 17 for SQL Server"
    base   = f"DRIVER={{{driver}}};SERVER={DB_SERVER};DATABASE={DB_NAME};"
    if DB_USER and DB_PASSWORD:
        return base + f"UID={DB_USER};PWD={DB_PASSWORD};"
    return base + "Trusted_Connection=yes;"


def get_conn() -> pyodbc.Connection:
    return pyodbc.connect(_conn_str(), autocommit=False)


# ── Price fetch ─────────────────────────────────────────────────
def fetch_price(ticker: str) -> float | None:
    """Try bare symbol, then .NS (NSE), then .BO (BSE) via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed — run: pip install yfinance")
        return None

    candidates = [ticker, ticker + ".NS", ticker + ".BO"]
    for sym in candidates:
        try:
            info = yf.Ticker(sym).fast_info
            px   = getattr(info, "last_price", None)
            if px and float(px) > 0:
                log.info(f"  price {sym} → {float(px):.4f}")
                return float(px)
        except Exception as exc:
            log.debug(f"  yfinance {sym} failed: {exc}")

    log.warning(f"  could not fetch price for {ticker} — skipping")
    return None


# ── Maths helpers ───────────────────────────────────────────────
def calc_dcf(row: dict, live_price: float) -> dict | None:
    """
    Re-run DCF with saved assumptions + live_price.
    Returns a dict of calculated outputs, or None if inputs are unusable.
    """
    try:
        R0  = float(row["base_revenue"]   or 0)
        SO  = float(row["shares_outstanding"] or 0)
        ND  = float(row["net_debt"]        or 0)
        W   = float(row["wacc"]            or 0)
        g12 = float(row["growth_yr1_2"]    or 0)
        g35 = float(row["growth_yr3_5"]    or 0)
        tg  = float(row["terminal_growth"] or 0)
        em1 = float(row["ebitda_margin_yr1"] or 0)
        em5 = float(row["ebitda_margin_yr5"] or 0)
        dna = float(row["dna_pct_rev"]     or 0)
        cp  = float(row["capex_pct_rev"]   or 0)
        nwc = float(row["nwc_pct_rev"]     or 0)
        tax = float(row["tax_rate"]        or 0)
    except (TypeError, ValueError):
        return None

    # Skip if any critical input is zero / missing
    if not (R0 and SO and W and W > tg):
        return None

    years       = [1, 2, 3, 4, 5]
    growth_rates = [g12, g12, g35, g35, g35]
    revs = [R0]
    for g in growth_rates:
        revs.append(revs[-1] * (1 + g))
    proj_revs = revs[1:]

    margins  = [em1 + (em5 - em1) * (i / 4) for i in range(5)]
    ebitda   = [r * m for r, m in zip(proj_revs, margins)]
    dna_arr  = [r * dna for r in proj_revs]
    ebit     = [e - d for e, d in zip(ebitda, dna_arr)]
    nopat    = [e * (1 - tax) for e in ebit]
    capex_arr = [r * cp  for r in proj_revs]
    nwc_arr   = [r * nwc for r in proj_revs]
    fcf       = [n + d - cx - nw for n, d, cx, nw in zip(nopat, dna_arr, capex_arr, nwc_arr)]
    disc      = [1 / (1 + W) ** i for i in years]

    pv_fcf     = [f * d for f, d in zip(fcf, disc)]
    sum_pv     = sum(pv_fcf)
    tv         = fcf[-1] * (1 + tg) / (W - tg)
    pv_tv      = tv * disc[-1]
    ev         = sum_pv + pv_tv
    eq         = ev - ND
    iv         = eq / SO
    upside_pct = (iv - live_price) / live_price * 100 if live_price else 0

    return {
        "calc_type":       "DCF",
        "intrinsic_value": round(iv, 4),
        "current_price":   round(live_price, 4),
        "upside_pct":      round(upside_pct, 4),
        "mos_10_price":    round(iv * 0.90, 4),
        "mos_20_price":    round(iv * 0.80, 4),
        "mos_25_price":    round(iv * 0.75, 4),
        "mos_30_price":    round(iv * 0.70, 4),
        "pv_fcf_sum":      round(sum_pv, 4),
        "pv_terminal_value": round(pv_tv, 4),
        "enterprise_value":  round(ev, 4),
        "equity_value":      round(eq, 4),
        "blended_fair_value": None,  # filled later if both models run
    }


def calc_growth(row: dict, live_price: float) -> dict | None:
    """
    Re-run Growth Model with saved assumptions + live_price.
    Returns a dict of calculated outputs, or None if inputs are unusable.
    """
    try:
        eps0   = float(row["base_eps"]         or 0)
        gr15   = float(row["eps_growth_yr1_5"] or 0)
        gr610  = float(row["eps_growth_yr6_10"] or 0)
        term_pe = float(row["terminal_pe"]      or 0)
        rr     = float(row["required_return"]   or 0)
        div    = float(row["dividend_per_share"] or 0)
    except (TypeError, ValueError):
        return None

    if not (eps0 > 0 and term_pe > 0 and rr > 0):
        return None

    eps_arr = [eps0]
    for i in range(1, 11):
        g = gr15 if i <= 5 else gr610
        eps_arr.append(eps_arr[-1] * (1 + g))
    proj_eps = eps_arr[1:]

    disc_factors = [1 / (1 + rr) ** i for i in range(1, 11)]
    pv_divs      = [div * df for df in disc_factors]
    term_price   = proj_eps[-1] * term_pe
    pv_term      = term_price * disc_factors[-1]
    sum_pv_divs  = sum(pv_divs)
    iv           = sum_pv_divs + pv_term

    if iv <= 0:
        return None

    upside_pct = (iv - live_price) / live_price * 100 if live_price else 0

    return {
        "calc_type":        "GROWTH",
        "intrinsic_value":  round(iv, 4),
        "current_price":    round(live_price, 4),
        "upside_pct":       round(upside_pct, 4),
        "mos_10_price":     round(iv * 0.90, 4),
        "mos_20_price":     round(iv * 0.80, 4),
        "mos_25_price":     round(iv * 0.75, 4),
        "mos_30_price":     round(iv * 0.70, 4),
        "eps_year10":       round(proj_eps[-1], 4),
        "terminal_price":   round(term_price, 4),
        "pv_terminal_price": round(pv_term, 4),
        "pv_dividends_sum": round(sum_pv_divs, 4),
        "blended_fair_value": None,
    }


def verdict(upside_pct: float) -> str:
    if upside_pct >= 30:  return "STRONG BUY"
    if upside_pct >= 10:  return "BUY"
    if upside_pct >= -10: return "HOLD"
    if upside_pct >= -25: return "SELL"
    return "STRONG SELL"


# ── DB helpers ──────────────────────────────────────────────────
def get_latest_inputs(cur: pyodbc.Cursor, tickers: list[str] | None) -> list[dict]:
    """
    For each ticker return the single most-recent TickerInput row.
    If tickers list is provided, filter to those only.
    """
    ticker_filter = ""
    params: list = []
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        ticker_filter = f"AND ti.ticker IN ({placeholders})"
        params.extend(tickers)

    sql = f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY generated_at DESC) AS rn
            FROM dbo.TickerInput
            WHERE 1=1 {ticker_filter}
        )
        SELECT * FROM ranked WHERE rn = 1
    """
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def insert_input(cur: pyodbc.Cursor, row: dict, live_price: float, generated_at: datetime) -> int:
    """Clone the input row with today's live price and timestamp."""
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
        row["ticker"], row["company_name"], row["exchange"], row["currency_unit"],
        live_price,          # ← updated to today's live price
        row["shares_outstanding"], row["net_debt"], row["wacc"],
        row["base_revenue"], row["base_year"],
        row["growth_yr1_2"], row["growth_yr3_5"], row["terminal_growth"],
        row["ebitda_margin_yr1"], row["ebitda_margin_yr5"],
        row["dna_pct_rev"], row["capex_pct_rev"], row["nwc_pct_rev"], row["tax_rate"],
        row["base_eps"], row["eps_growth_yr1_5"], row["eps_growth_yr6_10"],
        row["terminal_pe"], row["required_return"], row["dividend_per_share"],
        row["historical_data"],
        generated_at,
        "daily_recalc",      # ← marks this as an auto-run
        f"Auto-recalc from daily_recalc.py — assumptions from run #{row['id']}",
    )
    return cur.fetchval()


def insert_calc(cur: pyodbc.Cursor, input_id: int, calc: dict) -> int:
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
        input_id, calc["calc_type"],
        calc["intrinsic_value"], calc["current_price"], calc["upside_pct"],
        calc["mos_10_price"], calc["mos_20_price"], calc["mos_25_price"], calc["mos_30_price"],
        calc.get("pv_fcf_sum"), calc.get("pv_terminal_value"),
        calc.get("enterprise_value"), calc.get("equity_value"),
        calc.get("eps_year10"), calc.get("terminal_price"),
        calc.get("pv_terminal_price"), calc.get("pv_dividends_sum"),
        calc.get("blended_fair_value"),
    )
    return cur.fetchval()


# ── Main ────────────────────────────────────────────────────────
def run(tickers: list[str] | None = None, dry_run: bool = False) -> dict:
    log.info("=" * 60)
    log.info(f"PLM Ticker Watcher — Daily Recalc  {'(DRY RUN) ' if dry_run else ''}started")
    log.info(f"DB: {DB_SERVER} / {DB_NAME}")

    results = {
        "run_at":    datetime.utcnow().isoformat(),
        "dry_run":   dry_run,
        "processed": [],
        "skipped":   [],
        "errors":    [],
    }

    try:
        conn = get_conn()
    except Exception as e:
        log.error(f"DB connection failed: {e}")
        results["errors"].append({"ticker": "DB", "error": str(e)})
        return results

    try:
        cur = conn.cursor()
        rows = get_latest_inputs(cur, [t.upper() for t in tickers] if tickers else None)
        log.info(f"Found {len(rows)} ticker(s) to process")

        generated_at = datetime.utcnow()

        for row in rows:
            ticker = row["ticker"]
            log.info(f"\n── {ticker} ──────────────────────")

            # 1. Fetch live price
            live_price = fetch_price(ticker)
            if live_price is None:
                log.warning(f"  {ticker}: no price — skipped")
                results["skipped"].append({"ticker": ticker, "reason": "price unavailable"})
                continue

            # 2. Run DCF
            dcf  = calc_dcf(row, live_price)
            grw  = calc_growth(row, live_price)

            if dcf is None and grw is None:
                log.warning(f"  {ticker}: neither DCF nor Growth model could run — check assumptions")
                results["skipped"].append({"ticker": ticker, "reason": "insufficient assumptions for any model"})
                continue

            # 3. Fill blended fair value
            if dcf and grw:
                blended = (dcf["intrinsic_value"] + grw["intrinsic_value"]) / 2
                dcf["blended_fair_value"] = round(blended, 4)
                grw["blended_fair_value"] = round(blended, 4)
            elif dcf:
                dcf["blended_fair_value"] = dcf["intrinsic_value"]
            elif grw:
                grw["blended_fair_value"] = grw["intrinsic_value"]

            # 4. Log what we got
            if dcf:
                v = verdict(dcf["upside_pct"])
                log.info(f"  DCF IV={dcf['intrinsic_value']:.2f}  upside={dcf['upside_pct']:+.1f}%  → {v}")
            if grw:
                v = verdict(grw["upside_pct"])
                log.info(f"  GRW IV={grw['intrinsic_value']:.2f}  upside={grw['upside_pct']:+.1f}%  → {v}")

            if dry_run:
                results["processed"].append({
                    "ticker": ticker,
                    "live_price": live_price,
                    "dcf_iv": dcf["intrinsic_value"] if dcf else None,
                    "dcf_upside": dcf["upside_pct"] if dcf else None,
                    "grw_iv": grw["intrinsic_value"] if grw else None,
                    "grw_upside": grw["upside_pct"] if grw else None,
                })
                continue

            # 5. Write to DB
            try:
                new_input_id = insert_input(cur, row, live_price, generated_at)
                calc_ids = []
                if dcf:
                    calc_ids.append(insert_calc(cur, new_input_id, dcf))
                if grw:
                    calc_ids.append(insert_calc(cur, new_input_id, grw))
                conn.commit()
                log.info(f"  {ticker}: saved → input_id={new_input_id}  calc_ids={calc_ids}")
                results["processed"].append({
                    "ticker": ticker,
                    "input_id": new_input_id,
                    "live_price": live_price,
                    "dcf_iv": dcf["intrinsic_value"] if dcf else None,
                    "dcf_upside": dcf["upside_pct"] if dcf else None,
                    "grw_iv": grw["intrinsic_value"] if grw else None,
                    "grw_upside": grw["upside_pct"] if grw else None,
                })
            except Exception as db_err:
                conn.rollback()
                log.error(f"  {ticker}: DB write failed — {db_err}")
                results["errors"].append({"ticker": ticker, "error": str(db_err)})

    finally:
        conn.close()

    # ── Summary ──
    log.info("\n" + "=" * 60)
    log.info(f"Done.  processed={len(results['processed'])}  "
             f"skipped={len(results['skipped'])}  errors={len(results['errors'])}")
    if results["processed"]:
        log.info("Results:")
        for r in results["processed"]:
            dcf_str = f"DCF {r['dcf_iv']:.2f} ({r['dcf_upside']:+.1f}%)" if r.get("dcf_iv") else "DCF —"
            grw_str = f"GRW {r['grw_iv']:.2f} ({r['grw_upside']:+.1f}%)" if r.get("grw_iv") else "GRW —"
            v_str   = verdict(r["dcf_upside"] or r.get("grw_upside") or 0)
            log.info(f"  {r['ticker']:<14}  price={r['live_price']:.2f}  {dcf_str}  {grw_str}  [{v_str}]")
    if results["skipped"]:
        log.info("Skipped: " + ", ".join(f"{s['ticker']}({s['reason']})" for s in results["skipped"]))
    if results["errors"]:
        log.warning("Errors:  " + ", ".join(f"{e['ticker']}({e['error'][:60]})" for e in results["errors"]))

    return results


# ── CLI entry point ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PLM Ticker Watcher — daily recalculation job"
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Optional: specific ticker(s) to run. If omitted, all DB tickers are processed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch prices and compute IVs but do NOT write to DB.",
    )
    args = parser.parse_args()
    run(
        tickers  = [t.upper() for t in args.tickers] if args.tickers else None,
        dry_run  = args.dry_run,
    )
