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
"""

import pandas as pd
from src import contracts as c

PLAN_COLS = ["ts_utc", "realtime_mw", "batch_mw", "training_mw", "batt_mw", "soc_mwh", "backup_mw", "dc_draw_mw"]


def schedule(hours: pd.DataFrame, p: dict, P: float, soc0_mwh: float, backlog0_mwh: float) -> tuple[pd.DataFrame, dict]:
    """hours: ts_utc, load_plan_mw, co2_t_per_mwh (24 or 168 rows). Objective costs: use contracts.source_costs(p).
    Returns (plan df with PLAN_COLS, state dict {"soc_mwh", "backlog_mwh", "status"})."""
    raise NotImplementedError("Builder B: implement M3 schedule (see the docstring at the top of this file).")


def schedule_week(grid: pd.DataFrame, forecast: pd.DataFrame, p: dict, start_utc) -> pd.DataFrame:
    """LIVE in app (Tab: The operation). 168 h from start_utc on actual load (contracts.filled_load), returned in
    the replay_hourly schema with case="week", split="week". < 1 s."""
    raise NotImplementedError("Builder B: implement M3 schedule_week (see the docstring at the top of this file).")

