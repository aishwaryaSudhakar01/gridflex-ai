"""M1 Grid capacity. OWNER: Builder A. Spec: PDF M1.
headroom_curve: for X in 0..25,000 MW step 250, on 2022-2024 rows (not excluded), cut_t = required_cut(L_t, X, P).
  curtail_hours = hours with cut > 0 per YEAR (divide by 3); curtail_pct_energy = 100 * sum(cut) / (X * hours);
  avg_event_hours over find_events runs; max_cut_mw = max cut; n_events per YEAR.
  Test A1: X at curtail_pct_energy <= 0.5% should be the same order of magnitude as Duke's ~10 GW.
data_center_ask: same math at F = facility_mw(p). Called LIVE by app.py, so keep it under 0.5 s.
  k_ask_mw = contracts.ask_cut_mw(grid, p): the cut covering 95% of tight periods (audit fix 5). Do NOT use the
  largest single cut: at the record hour that is always the whole site. Report that as worst_hour_cut_mw.
"""

import pandas as pd
from src import contracts as c


def headroom_curve(grid: pd.DataFrame) -> pd.DataFrame:
    """Return the headroom schema."""
    raise NotImplementedError("Builder A: implement M1 headroom_curve (see the docstring at the top of this file).")


def data_center_ask(grid: pd.DataFrame, p: dict) -> dict:
    """Keys: k_ask_mw, worst_hour_cut_mw, events_per_year, hours_per_year, longest_event_h, planning_peak_mw.
    k_ask_mw = contracts.ask_cut_mw(grid, p). LIVE, < 0.5 s."""
    raise NotImplementedError("Builder A: implement M1 data_center_ask (see the docstring at the top of this file).")


if __name__ == "__main__":
    c.write("headroom", headroom_curve(c.read("grid_hourly")))
