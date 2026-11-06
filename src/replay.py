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
"""

import pandas as pd
from src import contracts as c


def run_replay(grid: pd.DataFrame, forecast: pd.DataFrame | None, p: dict, split: str = "test",
               cases=("flexible", "rigid")) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (replay_hourly df, events df) for the split and cases."""
    raise NotImplementedError("Builder B: implement M4 run_replay (see the docstring at the top of this file).")


if __name__ == "__main__":
    g = c.read("grid_hourly")
    f = c.read("forecast_test") if c.ready("forecast_test") else None
    parts = [run_replay(g, f, c.DEFAULTS, s) for s in ("tune", "test")]
    c.write("replay_hourly", pd.concat([x[0] for x in parts], ignore_index=True))
    c.write("events", pd.concat([x[1] for x in parts], ignore_index=True))
