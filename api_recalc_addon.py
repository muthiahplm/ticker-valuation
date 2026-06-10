"""
ADD THIS BLOCK TO api.py  —  paste it anywhere after the existing route definitions,
before the  if __name__ == "__main__":  block at the bottom.

It exposes:
  POST  /api/recalc/run            — trigger a full recalc of all tickers (or specific ones)
  GET   /api/recalc/status         — returns the last recalc run summary
  POST  /api/recalc/run?dry_run=1  — dry-run mode, no DB writes

The dashboard "↻ Refresh DB" button (or a new "⟳ Recalc All" button) can call this.
"""

# ─── Add these imports at the top of api.py (if not already present) ───────────
# import asyncio
# from concurrent.futures import ProcessPoolExecutor
# import importlib.util, sys as _sys

# ─── Paste this block into api.py ───────────────────────────────────────────────

from typing import Optional
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Shared state — last recalc result (simple in-memory; good enough for single-instance)
_last_recalc_result: dict = {}
_recalc_running: bool = False
_recalc_executor = ThreadPoolExecutor(max_workers=1)   # only 1 recalc at a time


class RecalcRequest(BaseModel):
    tickers: Optional[list[str]] = None    # None = all tickers
    dry_run: bool = False


def _run_recalc_sync(tickers, dry_run):
    """Called in thread pool so it doesn't block FastAPI event loop."""
    # Import here to avoid circular issues; daily_recalc.py must be in same folder
    import importlib.util, sys as _sys
    spec = importlib.util.spec_from_file_location(
        "daily_recalc",
        str(__import__("pathlib").Path(__file__).parent / "daily_recalc.py")
    )
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["daily_recalc"] = mod
    spec.loader.exec_module(mod)
    return mod.run(tickers=tickers, dry_run=dry_run)


@app.post("/api/recalc/run", summary="Trigger daily recalculation for all (or specific) tickers")
async def trigger_recalc(req: RecalcRequest = RecalcRequest()):
    global _recalc_running, _last_recalc_result

    if _recalc_running:
        raise HTTPException(status_code=409, detail="Recalc already in progress — try again shortly.")

    _recalc_running = True
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _recalc_executor,
            _run_recalc_sync,
            [t.upper() for t in req.tickers] if req.tickers else None,
            req.dry_run,
        )
        _last_recalc_result = result
        return {
            "status":    "ok",
            "dry_run":   result.get("dry_run"),
            "processed": len(result.get("processed", [])),
            "skipped":   len(result.get("skipped", [])),
            "errors":    len(result.get("errors", [])),
            "tickers":   result.get("processed", []),
            "run_at":    result.get("run_at"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _recalc_running = False


@app.get("/api/recalc/status", summary="Last recalculation run summary")
def recalc_status():
    if not _last_recalc_result:
        return {"status": "never_run", "message": "No recalc has been triggered since the server started."}
    r = _last_recalc_result
    return {
        "status":    "ok",
        "run_at":    r.get("run_at"),
        "dry_run":   r.get("dry_run"),
        "processed": len(r.get("processed", [])),
        "skipped":   len(r.get("skipped", [])),
        "errors":    len(r.get("errors", [])),
        "details":   r,
    }
