"""Right now panel (live ERCOT load). OWNER: Builder C. SHOULD-SHIP (cut before M6 if behind). Target 30 min.

What it shows: ERCOT's load this hour, today's load so far, today's forecast peak, distance to the planning peak,
and what OUR modelled data center would do this hour (required cut and where each MW comes from).

Source: gridstatus library (free, no key): gridstatus.Ercot().get_load("today") and .get_load_forecast("today").
Rules that keep the app safe (test A10 needs it to run offline):
- Network calls time out after LIVE_TIMEOUT_S. On ANY error, fall back to the latest hour in grid_hourly
  (source = "snapshot") and the app labels it "Last saved hour". Never raise.
- app.py wraps the call in st.cache_data(ttl=300), so ERCOT is hit at most once every 5 minutes.
- The data center's own usage is MODELLED from the sidebar params. No public data exists for real sites. Say so on screen.
Cut allocation ranks sources by contracts.source_costs(p), the same costs the scheduler uses. Real-time never cut.
The comparison line is the 2022-2024 record (planning peak). Label it that way on screen.
"""

from __future__ import annotations
import concurrent.futures as cf
import pandas as pd
from src import contracts as c

LIVE_TIMEOUT_S = 6
LIVE_KEYS = ["source", "as_of_utc", "load_mw", "today", "forecast_peak_today_mw", "planning_peak_mw",
             "room_to_peak_mw", "facility_mw", "required_cut_mw", "dc_draw_mw", "cut_by_source_mw", "status"]
# today: DataFrame with columns ts_utc (UTC, hour-beginning), load_mw, forecast_mw (NaN where unknown)
# status: "normal" | "tight" (cut needed this hour) | "watch" (today's forecast peak needs a cut)

_POOL = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="live")


def run_with_timeout(fn, seconds: float):
    """Run fn() and return its result, or raise TimeoutError after `seconds`. The app never waits longer:
    a slow call keeps running in the background and is ignored. (A `with ThreadPoolExecutor()` block would
    wait for it, which is the bug this replaces.)"""
    return _POOL.submit(fn).result(timeout=seconds)


def allocate_cut(cut_mw: float, p: dict, soc_mwh: float | None = None) -> dict:
    """Split a required cut (grid MW) across sources, cheapest first by contracts.source_costs(p).
    Returns MW per source (keys = contracts.SOURCES) plus "shortfall"."""
    soc = c.battery_mwh(p) if soc_mwh is None else soc_mwh
    caps = {"batch": p["it_mw"] * p["share_batch"] * p["pue"], "battery": min(p["battery_mw"], soc),
            "backup": p["backup_mw"], "training": p["it_mw"] * c.training_share(p) * p["pue"], "realtime": 0.0}
    cost = c.source_costs(p)
    left, out = float(cut_mw), {s: 0.0 for s in c.SOURCES}
    for s in sorted(c.SOURCES, key=lambda k: cost[k]):
        out[s] = min(left, caps[s]); left -= out[s]
    out["shortfall"] = max(left, 0.0)
    return out


def _fetch_live() -> pd.DataFrame:
    """Return today's ERCOT hourly load + forecast as columns ts_utc, load_mw, forecast_mw.
    gridstatus 0.37: gridstatus.Ercot().get_load("today") (5-min values -> hourly means) and
    .get_load_forecast("today") (system total). Convert to UTC hour-beginning."""
    raise NotImplementedError("Builder C: implement with gridstatus (see docstring).")


def fetch_week_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """For the Plan the week tab. OWNER: Builder C. Target 30 min. Until implemented, the planner shows Aug 2025.
    Returns (history, future), all UTC hour-beginning:
      history: ts_utc, load_mw  = ERCOT actual hourly load for the last 8 days (gridstatus Ercot().get_load per day,
               5-min values averaged to hours).
      future:  ts_utc, temp_c, load_ercot_mw = the next 168 hours starting at the next full hour.
               temp_c: Open-Meteo FORECAST API (https://api.open-meteo.com/v1/forecast, hourly=temperature_2m,
               forecast_days=8, timezone=UTC) for contracts.CITIES, weighted mean. No key. requests timeout=5.
               load_ercot_mw: gridstatus Ercot().get_load_forecast("today") system total, latest publish time.
               NaN where ERCOT has no value."""
    raise NotImplementedError("Builder C: implement with gridstatus + Open-Meteo forecast API.")


def _snapshot_today(grid: pd.DataFrame) -> pd.DataFrame:
    g = grid[~grid.excluded].dropna(subset=["load_mw"])
    last = g.ts_utc.max()
    day = c.local_day(g.ts_utc).values == c.local_day(pd.Series([last])).iloc[0]
    d = g[day]
    out = pd.DataFrame({"ts_utc": d.ts_utc.values, "load_mw": d.load_mw.values, "forecast_mw": d.ba_forecast_mw.values})
    out["ts_utc"] = pd.to_datetime(out.ts_utc, utc=True)
    return out


def latest_snapshot(grid: pd.DataFrame, p: dict, allow_network: bool = True) -> dict:
    """LIVE. Never raises for network problems. Returns a dict with LIVE_KEYS."""
    source, today = "snapshot", None
    if allow_network:
        try:
            today = run_with_timeout(_fetch_live, LIVE_TIMEOUT_S)
            source = "live"
        except Exception:
            today = None
    if today is None or today.empty:
        today, source = _snapshot_today(grid), "snapshot"
    now = today.dropna(subset=["load_mw"]).iloc[-1]
    P, F = c.planning_peak(grid), c.facility_mw(p)
    cut = float(c.required_cut(now.load_mw, F, P))
    fpeak = float(today.forecast_mw.max()) if today.forecast_mw.notna().any() else float(today.load_mw.max())
    status = "tight" if cut > 0 else ("watch" if c.required_cut(fpeak, F, P) > 0 else "normal")
    return {"source": source, "as_of_utc": now.ts_utc, "load_mw": float(now.load_mw), "today": today,
            "forecast_peak_today_mw": fpeak, "planning_peak_mw": P, "room_to_peak_mw": P - float(now.load_mw),
            "facility_mw": F, "required_cut_mw": cut, "dc_draw_mw": F - cut,
            "cut_by_source_mw": allocate_cut(cut, p), "status": status}
