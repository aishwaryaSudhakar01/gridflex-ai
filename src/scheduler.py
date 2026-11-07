"""M3 Workload scheduler (LP). OWNER: Builder B. Spec: PDF M3 + CONTEXT.md section 7.
Solver: scipy.optimize.linprog(method="highs"). No PuLP.
Per hour t, IT MW variables: training p_t, batch b_t; battery charge ch_t, discharge d_t (grid MW); soc s_t; backup u_t.
Real-time inference is fixed at it_mw * share_realtime, never cut.
Objective: sum v*(pbar - p_t) + w*(ch_t + d_t) + f*u_t + lambda*(e_t*g_t + e_b*u_t),  g_t = facility draw.
Constraints:
  grid:     (rt + b_t + p_t)*pue + ch_t - d_t - u_t <= limit_t   where limit_t = contracts.grid_limit(load_plan_t, p, P)
  GPUs:     rt + b_t + p_t <= it_mw / gpu_util ;  p_t <= pbar
  batch:    each Central day's batch energy (share_batch * it_mw * 24) is served by the end of that day + 24 h
            (simplification allowed: within the horizon, carry any unserved backlog_out to the next call)
  battery:  s_t = s_{t-1} + eff*ch_t - d_t ; 0 <= s_t <= battery_mwh ; ch_t, d_t <= battery_mw ; s_end >= battery_end_soc*battery_mwh
  backup:   0 <= u_t <= backup_mw
load_plan_t = forecast load (model_mw) + cut_buffer_mw in replay; actual load when perfect foresight.
Must solve 24 h in < 50 ms and 168 h in < 1 s.

IMPLEMENTATION NOTES (Builder B decisions, one line each)
- Costs come ONLY from contracts.source_costs(p): battery cost is charged per MWh discharged (it already contains wear
  and round-trip losses); backup cost already contains carbon_price * backup_co2, so the carbon term below is applied
  to the grid draw only (no double counting); training cost per IT MW lost = source_costs["training"] * pue (= v).
- Batch deadline: arrivals of Central day D are due at the end of day D+1. Opening backlog is due by the end of the
  first Central day of the horizon. Batch can never run ahead of its arrival (no negative backlog).
- Never infeasible: an "over_limit" slack (priced far above every real source) absorbs a grid limit that no source can
  meet. The replay reports it as over_limit_mw. Same for the battery end-of-horizon charge target (soft, small price).
- Tie-breakers (tiny): serve batch as early as possible, so plans are stable and not random between equal-cost hours.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

from src import contracts as c

PLAN_COLS = ["ts_utc", "realtime_mw", "batch_mw", "training_mw", "batt_mw", "soc_mwh", "backup_mw", "dc_draw_mw"]

_END_BACKLOG_USD = 5.0     # $/IT MWh of batch still unserved at horizon end: small, so serving free beats nothing
_EARLY_USD = 1e-3          # $/IT MWh per hour of delay: prefer serving batch earlier among equal-cost choices


def _utc_index(ts) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(pd.Series(ts), utc=True))


def _as_ns(idx: pd.DatetimeIndex) -> np.ndarray:
    """tz-aware UTC index -> plain datetime64[ns] array (safe to compare, any pandas version)."""
    return idx.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")


def _batch_required(ts: pd.DatetimeIndex, bt0: float, backlog0: float) -> np.ndarray:
    """Cumulative IT MWh of batch that MUST have run by the end of each hour (deadline rule in the docstring)."""
    T = len(ts)
    days = pd.to_datetime(pd.Series(c.local_day(ts))).reset_index(drop=True)          # naive Central dates
    deadline = (days + pd.Timedelta(days=2)).dt.tz_localize(c.TZ_LOCAL).dt.tz_convert("UTC")
    first_end = (days.iloc[0] + pd.Timedelta(days=1)).tz_localize(c.TZ_LOCAL).tz_convert("UTC")
    ends = _as_ns(ts + pd.Timedelta(hours=1))
    dl = _as_ns(pd.DatetimeIndex(deadline))
    due_new = bt0 * (dl[None, :] <= ends[:, None]).sum(axis=1)
    due_old = backlog0 * (ends >= np.datetime64(first_end.tz_convert("UTC").tz_localize(None), "ns"))
    return due_old + due_new


def _solve(hours: pd.DataFrame, p: dict, P: float, soc0: float, backlog0: float, with_deadlines: bool):
    T = len(hours)
    pue, it = p["pue"], p["it_mw"]
    rt = it * p["share_realtime"]
    bt0 = it * p["share_batch"]
    pbar = it * c.training_share(p)
    cap = c.battery_mwh(p)
    eff = p["battery_eff"]
    gpu_cap = it / p["gpu_util"]
    costs = c.source_costs(p)
    lam = p["carbon_price_usd_t"]

    load_plan = hours["load_plan_mw"].to_numpy(dtype=float)
    co2 = hours["co2_t_per_mwh"].to_numpy(dtype=float)
    co2 = np.where(np.isnan(co2), np.nanmean(co2) if np.isfinite(co2).any() else 0.0, co2)
    limit = np.asarray(c.grid_limit(load_plan, p, P), dtype=float)
    ts = _utc_index(hours["ts_utc"])

    # variable blocks, each T long: p, b, ch, d, s, u, o   + 1 slack for the battery end target
    ip, ib, ich, idd, isoc, iu, io = (np.arange(T) + k * T for k in range(7))
    ie = 7 * T
    n = 7 * T + 1
    big = 100.0 * max(1000.0, *[v for v in costs.values() if np.isfinite(v)])

    cost = np.zeros(n)
    cost[ip] = -costs["training"] * pue + lam * co2 * pue          # v*(pbar-p): constant dropped
    hr = np.arange(T)
    cost[ib] = _EARLY_USD * hr - _END_BACKLOG_USD + lam * co2 * pue
    cost[ich] = lam * co2
    cost[idd] = costs["battery"] - lam * co2
    cost[iu] = costs["backup"] - lam * co2
    cost[io] = big
    cost[ie] = max(costs["battery"], 1.0)

    A, b = [], []
    # grid limit, with over-limit slack
    G = np.zeros((T, n)); G[hr, ip] = pue; G[hr, ib] = pue; G[hr, ich] = 1; G[hr, idd] = -1; G[hr, iu] = -1; G[hr, io] = -1
    A.append(G); b.append(limit - rt * pue)
    # installed GPUs
    U = np.zeros((T, n)); U[hr, ip] = 1; U[hr, ib] = 1
    A.append(U); b.append(np.full(T, gpu_cap - rt))
    # batch cannot run ahead of its arrival
    tri = np.tril(np.ones((T, T)))
    C = np.zeros((T, n)); C[:, ib] = tri
    A.append(C); b.append(backlog0 + bt0 * (hr + 1))
    # batch deadlines
    if with_deadlines:
        req = _batch_required(ts, bt0, backlog0)
        rows = np.flatnonzero(req > 1e-9)
        if len(rows):
            D = np.zeros((len(rows), n)); D[:, ib] = -tri[rows]
            A.append(D); b.append(-req[rows])
    # battery end target (soft)
    E = np.zeros((1, n)); E[0, isoc[-1]] = -1; E[0, ie] = -1
    A.append(E); b.append(np.array([-min(p["battery_end_soc"] * cap, cap)]))

    # battery balance: s_t - s_{t-1} - eff*ch_t + d_t = 0  (s_{-1} = soc0)
    Q = np.zeros((T, n)); Q[hr, isoc] = 1; Q[hr, ich] = -eff; Q[hr, idd] = 1
    Q[hr[1:], isoc[:-1]] = -1
    beq = np.zeros(T); beq[0] = soc0

    lo = np.zeros(n)
    hi = np.full(n, np.inf)
    hi[ip] = pbar
    hi[ich] = p["battery_mw"]; hi[idd] = p["battery_mw"]
    hi[isoc] = cap
    hi[iu] = p["backup_mw"]
    bounds = np.column_stack([lo, hi])

    res = linprog(cost, A_ub=sparse.csr_matrix(np.vstack(A)), b_ub=np.concatenate(b),
                  A_eq=sparse.csr_matrix(Q), b_eq=beq, bounds=bounds, method="highs")
    return res, dict(T=T, rt=rt, bt0=bt0, pue=pue, ip=ip, ib=ib, ich=ich, idd=idd, isoc=isoc, iu=iu, io=io, ts=ts)


def schedule(hours: pd.DataFrame, p: dict, P: float, soc0_mwh: float, backlog0_mwh: float) -> tuple[pd.DataFrame, dict]:
    """hours: ts_utc, load_plan_mw, co2_t_per_mwh (24 or 168 rows). Objective costs: use contracts.source_costs(p).
    Returns (plan df with PLAN_COLS, state dict {"soc_mwh", "backlog_mwh", "status"})."""
    hours = hours.reset_index(drop=True)
    cap = c.battery_mwh(p)
    soc0 = float(np.clip(soc0_mwh, 0.0, cap))
    backlog0 = max(float(backlog0_mwh), 0.0)

    res, m = _solve(hours, p, P, soc0, backlog0, with_deadlines=True)
    status = "optimal"
    if res.status != 0:                       # deadlines impossible to meet (e.g. huge backlog): relax and say so
        res, m = _solve(hours, p, P, soc0, backlog0, with_deadlines=False)
        status = "relaxed"
    if res.status != 0:
        raise RuntimeError(f"scheduler LP failed: {res.message}")

    x = np.where(np.abs(res.x) < 1e-9, 0.0, res.x)
    T, rt, pue = m["T"], m["rt"], m["pue"]
    tr, ba, ch, d, soc, u, over = (x[m[k]] for k in ("ip", "ib", "ich", "idd", "isoc", "iu", "io"))
    plan = pd.DataFrame({
        "ts_utc": m["ts"], "realtime_mw": rt, "batch_mw": ba, "training_mw": tr,
        "batt_mw": d - ch, "soc_mwh": soc, "backup_mw": u,
        "dc_draw_mw": (rt + ba + tr) * pue + ch - d - u,
    })[PLAN_COLS]
    backlog_out = max(backlog0 + m["bt0"] * T - float(ba.sum()), 0.0)
    state = {"soc_mwh": float(soc[-1]), "backlog_mwh": backlog_out, "status": status,
             "over_limit_mwh": float(over.sum())}
    return plan, state


def replay_rows(plan: pd.DataFrame, load_mw, load_plan_mw, p: dict, P: float, case: str, split: str) -> pd.DataFrame:
    """Plan -> replay_hourly schema (helper shared with replay.py). IT MW for workloads, grid MW for the rest.
    Accounting per hour (IT MW): demand = served + deferred + lost, with
    served = realtime + batch + training, deferred = batch due - batch run, lost = pbar - training."""
    F = c.facility_mw(p)
    load = np.asarray(load_mw, dtype=float)
    plan_load = np.asarray(load_plan_mw, dtype=float)
    pbar = p["it_mw"] * c.training_share(p)
    bt0 = p["it_mw"] * p["share_batch"]
    draw = plan.dc_draw_mw.to_numpy(dtype=float)
    total = load + draw
    return pd.DataFrame({
        "ts_utc": pd.DatetimeIndex(plan.ts_utc),
        "case": case, "split": split, "load_mw": load,
        "required_cut_mw": c.required_cut(load, F, P),
        "planned_cut_mw": c.required_cut(plan_load, F, P),
        "realtime_mw": plan.realtime_mw.to_numpy(dtype=float), "batch_mw": plan.batch_mw.to_numpy(dtype=float),
        "training_mw": plan.training_mw.to_numpy(dtype=float), "batt_mw": plan.batt_mw.to_numpy(dtype=float),
        "soc_mwh": plan.soc_mwh.to_numpy(dtype=float), "backup_mw": plan.backup_mw.to_numpy(dtype=float),
        "dc_draw_mw": draw, "grid_total_mw": total, "over_limit_mw": np.maximum(total - P, 0.0),
        "compute_demand_mw": float(p["it_mw"]),
        "compute_served_mw": (plan.realtime_mw + plan.batch_mw + plan.training_mw).to_numpy(dtype=float),
        "compute_deferred_mw": bt0 - plan.batch_mw.to_numpy(dtype=float),
        "compute_lost_mw": pbar - plan.training_mw.to_numpy(dtype=float),
    })


def schedule_week(grid: pd.DataFrame, forecast: pd.DataFrame, p: dict, start_utc) -> pd.DataFrame:
    """LIVE in app (Tab: The operation). 168 h from start_utc on actual load (contracts.filled_load), returned in
    the replay_hourly schema with case="week", split="week". < 1 s.
    Starts from a full battery and no batch backlog (same as week_plan). `forecast` is not used: plans on actual load."""
    start = pd.Timestamp(start_utc)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    full = pd.DataFrame({"ts_utc": pd.to_datetime(grid.ts_utc, utc=True).to_numpy(),
                         "load_mw": c.filled_load(grid).to_numpy(), "co2_t_per_mwh": grid.co2_t_per_mwh.to_numpy()})
    full["ts_utc"] = pd.to_datetime(full.ts_utc, utc=True)
    wk = full[(full.ts_utc >= start) & (full.ts_utc < start + pd.Timedelta(hours=168))].reset_index(drop=True)
    if wk.empty:
        raise ValueError("schedule_week: no grid hours at or after start_utc")
    wk["co2_t_per_mwh"] = wk.co2_t_per_mwh.fillna(full.co2_t_per_mwh.mean()).fillna(0.0)
    P = c.planning_peak(grid)
    hours = pd.DataFrame({"ts_utc": wk.ts_utc, "load_plan_mw": wk.load_mw, "co2_t_per_mwh": wk.co2_t_per_mwh})
    plan, _ = schedule(hours, p, P, c.battery_mwh(p), 0.0)
    return replay_rows(plan, wk.load_mw, wk.load_mw, p, P, "week", "week")
