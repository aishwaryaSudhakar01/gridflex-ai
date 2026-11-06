"""Plan the week: forecast the next 168 hours and choose how the data center runs each hour.
Glue only, already implemented. It calls: live.fetch_week_inputs (C), forecast.predict_week (A),
scheduler.schedule (B). When those become real, this file needs NO changes. OWNER: Builder C (don't change signatures).

Modes:
- "live": the coming 7 days (live ERCOT history + Open-Meteo weather forecast + ERCOT 7-day forecast).
- "aug2025": the real peak week of summer 2025 from the saved snapshot, planned as if it were the coming week.
  Uses actual temperatures as the weather forecast (stated limit). Always works offline.
If live inputs fail for any reason, the planner returns the aug2025 plan with info["note"] explaining why.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from src import contracts as c, forecast, scheduler, live


def peak_week_start(grid: pd.DataFrame) -> pd.Timestamp:
    """Central midnight 3 days before the highest-load hour of the test summer (UTC)."""
    g = grid[c.in_split(grid.ts_utc, "test").to_numpy() & ~grid.excluded.to_numpy()]
    t = g.loc[g.load_mw.idxmax(), "ts_utc"].tz_convert(c.TZ_LOCAL).normalize() - pd.Timedelta(days=3)
    return t.tz_convert("UTC")


def _inputs_aug2025(grid: pd.DataFrame):
    start = peak_week_start(grid)
    full = grid.assign(load_mw=c.filled_load(grid))
    hist = full.loc[full.ts_utc < start, ["ts_utc", "load_mw"]].tail(24 * 8).reset_index(drop=True)
    wk = grid[(grid.ts_utc >= start) & (grid.ts_utc < start + pd.Timedelta(hours=168))]
    fut = pd.DataFrame({"ts_utc": wk.ts_utc.values, "temp_c": wk.temp_c.values,
                        "load_ercot_mw": wk.ba_forecast_mw.values, "co2_t_per_mwh": wk.co2_t_per_mwh.values})
    fut["ts_utc"] = pd.to_datetime(fut.ts_utc, utc=True)
    fut["temp_c"] = fut.temp_c.interpolate(limit_direction="both")
    fut["co2_t_per_mwh"] = fut.co2_t_per_mwh.fillna(grid.co2_t_per_mwh.mean())
    return hist, fut


def _inputs_live(grid: pd.DataFrame):
    hist, fut = live.run_with_timeout(live.fetch_week_inputs, live.LIVE_TIMEOUT_S * 2)
    if len(fut) < 168:
        raise ValueError("live forecast shorter than 168 hours")
    fut = fut.head(168).copy()
    g = grid.dropna(subset=["co2_t_per_mwh"])
    by_hour = g.groupby(g.ts_utc.dt.hour).co2_t_per_mwh.mean()     # typical carbon intensity by hour of day
    fut["co2_t_per_mwh"] = fut.ts_utc.dt.hour.map(by_hour).fillna(g.co2_t_per_mwh.mean()).values
    return hist, fut


def plan_week(grid: pd.DataFrame, p: dict, mode: str = "aug2025", allow_network: bool = True) -> tuple[pd.DataFrame, dict]:
    """Return (week plan df with contracts.WEEK_PLAN_COLS, info dict: mode_used, note, start_utc). Never raises
    for live problems. LIVE in app (cache 1 h): must run in < 2 s once real."""
    note, used = "", mode
    if mode == "live":
        try:
            if not allow_network:
                raise RuntimeError("network disabled")
            hist, fut = _inputs_live(grid)
        except Exception as e:
            used = "aug2025"
            note = f"Live forecast unavailable ({type(e).__name__}). Showing the real peak week of Aug 2025 instead."
    if used == "aug2025":
        hist, fut = _inputs_aug2025(grid)

    P, F = c.planning_peak(grid), c.facility_mw(p)
    pred = forecast.predict_week(hist, fut[["ts_utc", "temp_c"]], P, F)
    load_plan = pred.load_model_mw.to_numpy() + p["cut_buffer_mw"]
    hours = pd.DataFrame({"ts_utc": fut.ts_utc.values, "load_plan_mw": load_plan,
                          "co2_t_per_mwh": fut.co2_t_per_mwh.values})
    hours["ts_utc"] = pd.to_datetime(hours.ts_utc, utc=True)
    try:
        plan, _ = scheduler.schedule(hours, p, P, c.battery_mwh(p), 0.0)
    except NotImplementedError:      # Builder B's scheduler not in yet: show a rigid data center, say so
        rt0, bt0, tr0 = p["it_mw"] * p["share_realtime"], p["it_mw"] * p["share_batch"], p["it_mw"] * c.training_share(p)
        plan = pd.DataFrame({"ts_utc": hours.ts_utc, "realtime_mw": rt0, "batch_mw": bt0, "training_mw": tr0,
                             "batt_mw": 0.0, "soc_mwh": c.battery_mwh(p), "backup_mw": 0.0, "dc_draw_mw": c.facility_mw(p)})
        note = (note + " " if note else "") + "Scheduler not connected yet (Builder B): showing the data center at full power."

    rt, bt, tr = p["it_mw"] * p["share_realtime"], p["it_mw"] * p["share_batch"], p["it_mw"] * c.training_share(p)
    df = pd.DataFrame({
        "ts_utc": hours.ts_utc, "mode": used, "load_model_mw": pred.load_model_mw.to_numpy(),
        "load_ercot_mw": fut.load_ercot_mw.to_numpy(dtype=float), "temp_c": fut.temp_c.to_numpy(dtype=float),
        "p_stress": pred.p_stress.to_numpy(), "planned_cut_mw": c.required_cut(load_plan, F, P),
        "realtime_mw": plan.realtime_mw.to_numpy(), "batch_mw": plan.batch_mw.to_numpy(),
        "training_mw": plan.training_mw.to_numpy(), "batt_mw": plan.batt_mw.to_numpy(),
        "soc_mwh": plan.soc_mwh.to_numpy(), "backup_mw": plan.backup_mw.to_numpy(),
        "dc_draw_mw": plan.dc_draw_mw.to_numpy(),
        "cut_batch_mw": np.maximum(bt - plan.batch_mw.to_numpy(), 0) * p["pue"],
        "cut_battery_mw": np.maximum(plan.batt_mw.to_numpy(), 0),
        "cut_backup_mw": plan.backup_mw.to_numpy(),
        "cut_training_mw": np.maximum(tr - plan.training_mw.to_numpy(), 0) * p["pue"],
    })
    return c.validate_week_plan(df), {"mode_used": used, "note": note, "start_utc": df.ts_utc.iloc[0]}
