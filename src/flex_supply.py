"""M5 Reliable flex + supply curve. OWNER: Builder B. Spec: PDF M5.
reliable_flex: for each TUNE event (case flexible), small LP = largest constant cut held for the whole event, starting
  from the replay's real soc and batch backlog at event start. k_rel_mw = 5th percentile (1 - reliability_target).
  k_ask_mw = contracts.ask_cut_mw(grid, p) (cut covering 95% of tight periods; audit fix 5).
  promise_holds = k_rel_mw >= promise_mw. Also report whether k_rel_mw >= k_ask_mw in the doc.
  share_events_full_test = share of TEST events delivering k_rel_mw in full (test A5 needs >= 0.95).
  max_facility_mw_holding = largest it_mw*pue (search 250 MW steps) where k_rel >= ask_cut_mw at that size.
  mw_by_source = mean MW by source across tune events, keys = contracts.SOURCES.
supply_curve: LIVE in app. Rows in contracts.SOURCES order; cost per MWh from params (see PDF M5 table);
  battery cost = wear + (1/eff - 1) * mean grid price proxy ($40/MWh); realtime row has mw = 0.
"""

import pandas as pd
from src import contracts as c


def reliable_flex(grid: pd.DataFrame, replay: pd.DataFrame, events: pd.DataFrame, p: dict) -> dict:
    """Return the reliability dict (contract keys)."""
    raise NotImplementedError("Builder B: implement M5 reliable_flex (see the docstring at the top of this file).")


def supply_curve(p: dict) -> pd.DataFrame:
    """LIVE. Return the supply_curve schema for params p, < 50 ms. cost_per_mwh MUST come from contracts.source_costs(p)."""
    raise NotImplementedError("Builder B: implement M5 supply_curve (see the docstring at the top of this file).")


if __name__ == "__main__":
    g, r, e = c.read("grid_hourly"), c.read("replay_hourly"), c.read("events")
    c.write("reliability", reliable_flex(g, r, e, c.DEFAULTS))
    c.write("supply_curve", supply_curve(c.DEFAULTS))
