"""M7 Impact model. OWNER: Builder C. Spec: PDF M7. Never cut.
Every section reports rigid AND flexible, from replay_hourly TEST split (cases "rigid" and "flexible").
grid: dc_load_at_peak_mw = dc_draw in the hour of max load_mw; hours_over_planning_peak = hours with over_limit > 0
  (rigid: grid_total > P); firm_capacity_avoided_mw = rigid peak contribution - flexible; x capacity_cost_usd_kwyr * 1000;
  headroom_unlocked_mw = new_load_mw at curtail_pct_energy <= 0.5 from headroom (flexible), 0 for rigid.
data_center: MWh = sum of IT MW columns (1 h rows); compute_lost_pct = lost / demand * 100; battery_cycles =
  sum discharge / battery_mwh; backup hours and MWh; months_earlier_online = wait_firm - wait_flex (flexible only);
  extra_compute_mwh = months_earlier * 730 * it_mw.
emissions: co2_grid_t = sum dc_draw * co2_t_per_mwh (join grid on ts_utc); co2_backup_t = backup MWh * backup_co2_t_mwh.
ratepayers: avoided_cost_usd_per_mwh = firm_capacity_avoided_usd_yr / ERCOT annual load MWh (2024, from grid);
  cp4_cut_mw = mean cut in the is_4cp hours of the test summer (state the cost-shift side effect).
headline: mw_connectable (headroom at 0.5%), reliable_mw (reliability.k_rel_mw), compute_lost_pct (flexible),
  net_co2_change_t = flexible co2_total - rigid co2_total.
Called LIVE in the app with contracts.impact_params(p): design keys are ALWAYS the built design (the replay's),
only costs, prices and the promise come from the sliders. So numbers never mix two designs. Must run in < 1 s.
"""

import pandas as pd
from src import contracts as c


def compute_impact(p: dict, grid: pd.DataFrame, replay: pd.DataFrame, headroom: pd.DataFrame,
                   reliability: dict) -> dict:
    """Return the impact dict (contract keys, rigid + flexible per section)."""
    raise NotImplementedError("Builder C: implement M7 compute_impact (see the docstring at the top of this file).")


if __name__ == "__main__":
    out = compute_impact(c.DEFAULTS, c.read("grid_hourly"), c.read("replay_hourly"), c.read("headroom"),
                         c.read("reliability"))
    c.write("impact", out)
