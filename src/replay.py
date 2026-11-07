"""M4 Rolling replay. OWNER: Builder B. Spec: PDF M4. The proof engine. Never cut.
Hours: the split's summer = rows where contracts.in_split(ts, split) is True (Texas dates Jun 1 to Sep 30).
Load: use contracts.filled_load(grid) so there are never blanks. Hours with excluded=True never count as events.
For each Texas day D in that summer:
 1. Plan D+1 with scheduler.schedule on load_plan = model_mw + cut_buffer_mw (case "flexible")
    or actual load (case "perfect").
    forecast=None (Builder A's forecast not built yet): plan "flexible" on actual load too. build.py prints a
    PERFECT FORESIGHT warning; rerun `python build.py --from m4` after A's m2 lands. Never wait for A to start.
 2. Execute against actual required_cut c_t = required_cut(load, F, P). If plan draw > F - c_t, close the gap in
    this fixed order: batch shift, battery (within soc/power), backup (to backup_mw), training throttle.
    Remaining gap = over_limit_mw (grid_total - P, floored at 0).
 3. Carry soc and batch backlog into D+1.
Cases: "rigid" (draws F every hour), "flexible", "perfect", "noise" (temp_c +/- 2 C, stretch).
Accounting (test A9, every hour): compute_demand = served + deferred + lost (+/- 0.1%), all IT MW.
  demand = it_mw; served = realtime + batch + training run; deferred = batch due - batch run (negative when catching up);
  lost = pbar - training run.
events: one row per run of required_cut > 0 per (case, split). asked = max c_t; shortfall = max hourly (c_t - delivered_t)+;
  delivered = asked - shortfall; mw_* = mean grid-MW contribution per source over event hours;
  flagged_day_ahead = any forecast stress_flag in the event hours.

IMPLEMENTATION NOTES (Builder B decisions, one line each)
- "Day D" = a Texas calendar day. Its plan is made from the day-ahead forecast (the evening before) and executed on actual load.
  The forecast file holds one day-ahead prediction per hour, so each day uses only its own 24 forecast rows.
- Execution uses the REALISED battery level (not the planned one). If earlier hours drained it, a planned discharge is
  trimmed to what is left, and the missing MW is closed by backup, then training (same fixed order as the spec).
- Batch is served oldest-first. Batch that is still unserved when its deadline passes (end of the day after it arrived)
  is counted as LOST compute in the last hour of that day (moved from "deferred" to "lost", so test A9 still balances),
  and a warning is printed. This keeps a missed deadline from looking free.
- "rigid" draws F every hour with the battery idle and full; "perfect" always plans on actual load.
- The grid itself can exceed P in an hour (load alone above the planning peak). That shows up as over_limit_mw and no
  schedule can remove it; see CONTEXT.md 6.4 (PLANNING_MARGIN).
- Never writes files itself except in __main__ (through contracts.write). Importing this module has no side effects.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from src import contracts as c
from src import scheduler

_EPS = 1e-9
_CASES = ("flexible", "rigid", "perfect")


def _summer_frame(grid: pd.DataFrame, forecast: pd.DataFrame | None, split: str) -> pd.DataFrame:
    """One row per summer hour: ts_utc, load_mw (filled), co2, excluded, model_mw, stress_flag, day."""
    full_load = c.filled_load(grid)
    mask = c.in_split(grid.ts_utc, split).to_numpy()
    df = grid.loc[mask, ["ts_utc", "co2_t_per_mwh", "excluded"]].copy()
    df["load_mw"] = full_load.loc[df.index].to_numpy(dtype=float)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df = df.sort_values("ts_utc").reset_index(drop=True)
    if df.empty:
        raise ValueError(f"run_replay: no grid hours fall inside the '{split}' summer")
    df["excluded"] = df["excluded"].astype(bool)
    df["co2_t_per_mwh"] = df["co2_t_per_mwh"].fillna(grid["co2_t_per_mwh"].mean()).fillna(0.0)
    df["model_mw"], df["stress_flag"] = df["load_mw"], False
    if forecast is not None:
        f = forecast[forecast["split"] == split][["ts_utc", "model_mw", "stress_flag"]].copy()
        f["ts_utc"] = pd.to_datetime(f["ts_utc"], utc=True)
        df = df.drop(columns=["model_mw", "stress_flag"]).merge(f, on="ts_utc", how="left")
        df["model_mw"] = df["model_mw"].fillna(df["load_mw"])          # no forecast for an hour: plan on actual
        df["stress_flag"] = df["stress_flag"].fillna(False).astype(bool)
    df["day"] = c.local_day(df["ts_utc"]).to_numpy()
    return df


def _rigid_plan(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = len(df)
    return pd.DataFrame({
        "ts_utc": df["ts_utc"], "realtime_mw": p["it_mw"] * p["share_realtime"],
        "batch_mw": p["it_mw"] * p["share_batch"], "training_mw": p["it_mw"] * c.training_share(p),
        "batt_mw": 0.0, "soc_mwh": c.battery_mwh(p), "backup_mw": 0.0, "dc_draw_mw": np.full(n, c.facility_mw(p))})


def _run_plan_and_execute(df: pd.DataFrame, plan_load: np.ndarray, p: dict, P: float):
    """Plan each Texas day with the scheduler, execute it on actual load. Returns (executed plan df, extra_lost array)."""
    pue, it = p["pue"], p["it_mw"]
    bt0, pbar, cap, eff = it * p["share_batch"], it * c.training_share(p), c.battery_mwh(p), p["battery_eff"]
    actual = df["load_mw"].to_numpy(dtype=float)
    limit_actual = np.asarray(c.grid_limit(actual, p, P), dtype=float)
    cols = {k: np.zeros(len(df)) for k in ("realtime_mw", "batch_mw", "training_mw", "batt_mw", "soc_mwh", "backup_mw", "dc_draw_mw")}
    extra_lost = np.zeros(len(df))
    soc, backlog = cap, 0.0                                    # start of summer: full battery, nothing owed
    for _, idx in df.groupby("day", sort=True).indices.items():
        idx = np.sort(idx)
        hours = pd.DataFrame({"ts_utc": df["ts_utc"].iloc[idx].to_numpy(), "load_plan_mw": plan_load[idx],
                              "co2_t_per_mwh": df["co2_t_per_mwh"].iloc[idx].to_numpy()})
        hours["ts_utc"] = pd.to_datetime(hours["ts_utc"], utc=True)
        plan, _ = scheduler.schedule(hours, p, P, soc, backlog)
        backlog_in, served_day = backlog, 0.0
        for k, t in enumerate(idx):
            rt, b, tr = float(plan.realtime_mw.iloc[k]), float(plan.batch_mw.iloc[k]), float(plan.training_mw.iloc[k])
            u, net = float(plan.backup_mw.iloc[k]), float(plan.batt_mw.iloc[k])      # net > 0 = discharging
            max_dis, max_chg = min(p["battery_mw"], soc), min(p["battery_mw"], max((cap - soc) / eff, 0.0))
            net = float(np.clip(net, -max_chg, max_dis))                             # realised battery limits
            gap = (rt + b + tr) * pue - net - u - limit_actual[t]
            if gap > _EPS:
                x = min(b, gap / pue); b -= x; gap -= x * pue                        # 1. batch shift
                x = min(gap, max_dis - net); net += x; gap -= x                      # 2. battery
                x = min(gap, p["backup_mw"] - u); u += x; gap -= x                   # 3. backup
                x = min(tr, max(gap, 0.0) / pue); tr -= x; gap -= x * pue           # 4. training throttle
            soc = soc + eff * max(-net, 0.0) - max(net, 0.0)
            soc = float(np.clip(soc, 0.0, cap))
            served_day += b
            cols["realtime_mw"][t], cols["batch_mw"][t], cols["training_mw"][t] = rt, b, tr
            cols["batt_mw"][t], cols["soc_mwh"][t], cols["backup_mw"][t] = net, soc, u
            cols["dc_draw_mw"][t] = (rt + b + tr) * pue - net - u
        old_left = max(backlog_in - served_day, 0.0)           # oldest-first: this much missed its deadline
        backlog = max(backlog_in + bt0 * len(idx) - served_day - old_left, 0.0)
        extra_lost[idx[-1]] += old_left
    out = pd.DataFrame({"ts_utc": df["ts_utc"], **cols})
    return out[scheduler.PLAN_COLS], extra_lost


def _events(rows: pd.DataFrame, meta: pd.DataFrame, p: dict, case: str, split: str) -> pd.DataFrame:
    F, pue = c.facility_mw(p), p["pue"]
    bt0, pbar = p["it_mw"] * p["share_batch"], p["it_mw"] * c.training_share(p)
    cut = rows["required_cut_mw"].to_numpy(dtype=float)
    cut_for_events = np.where(meta["excluded"].to_numpy(), 0.0, cut)           # filled/excluded hours never count
    short = np.maximum(rows["dc_draw_mw"].to_numpy() - (F - cut), 0.0)         # (c_t - delivered_t)+, delivered = F - draw
    src = {"mw_batch": np.maximum(bt0 - rows["batch_mw"].to_numpy(), 0.0) * pue,
           "mw_battery": np.maximum(rows["batt_mw"].to_numpy(), 0.0),
           "mw_backup": rows["backup_mw"].to_numpy(dtype=float),
           "mw_training": np.maximum(pbar - rows["training_mw"].to_numpy(), 0.0) * pue}
    flag = meta["stress_flag"].to_numpy(dtype=bool)
    recs = []
    for n, (a, b) in enumerate(c.find_events(cut_for_events, rows["ts_utc"]), start=1):
        asked, shortfall = float(cut_for_events[a:b + 1].max()), float(short[a:b + 1].max())
        recs.append({"event_id": f"{case}-{split}-{n:03d}", "case": case, "split": split,
                     "start_utc": rows["ts_utc"].iloc[a], "hours": int(b - a + 1), "asked_mw": asked,
                     "delivered_mw": asked - shortfall, **{k: float(v[a:b + 1].mean()) for k, v in src.items()},
                     "shortfall_mw": shortfall, "flagged_day_ahead": bool(flag[a:b + 1].any())})
    ev = pd.DataFrame(recs, columns=list(c.SCHEMAS["events"]["cols"]))
    ev["start_utc"] = pd.to_datetime(ev["start_utc"], utc=True)
    return ev


def run_replay(grid: pd.DataFrame, forecast: pd.DataFrame | None, p: dict, split: str = "test",
               cases=("flexible", "rigid")) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (replay_hourly df, events df) for the split and cases."""
    bad = [k for k in cases if k not in _CASES]
    if bad:
        raise NotImplementedError(f"run_replay: case(s) {bad} not built (stretch). Allowed: {list(_CASES)}")
    P = c.planning_peak(grid)
    df = _summer_frame(grid, forecast, split)
    load = df["load_mw"].to_numpy(dtype=float)
    rows_out, events_out = [], []
    for case in cases:
        if case == "rigid":
            plan, plan_load, extra = _rigid_plan(df, p), load, np.zeros(len(df))
        else:
            use_forecast = case == "flexible" and forecast is not None
            plan_load = df["model_mw"].to_numpy(dtype=float) + p["cut_buffer_mw"] if use_forecast else load
            plan, extra = _run_plan_and_execute(df, plan_load, p, P)
        rows = scheduler.replay_rows(plan, load, plan_load, p, P, case, split)
        rows["compute_deferred_mw"] = rows["compute_deferred_mw"] - extra     # overdue batch: deferred -> lost
        rows["compute_lost_mw"] = rows["compute_lost_mw"] + extra
        if extra.sum() > 1e-6:
            warnings.warn(f"replay[{case}/{split}]: {extra.sum():,.0f} MWh of batch missed its deadline and is counted as lost")
        rows_out.append(rows)
        events_out.append(_events(rows, df, p, case, split))
    return pd.concat(rows_out, ignore_index=True), pd.concat(events_out, ignore_index=True)


if __name__ == "__main__":
    g = c.read("grid_hourly")
    f = c.read("forecast_test") if c.ready("forecast_test") else None
    parts = [run_replay(g, f, c.DEFAULTS, s) for s in ("tune", "test")]
    c.write("replay_hourly", pd.concat([x[0] for x in parts], ignore_index=True))
    c.write("events", pd.concat([x[1] for x in parts], ignore_index=True))
