"""M6 Design search. OWNER: Builder B. SHOULD-SHIP (cut first if behind). Spec: PDF M6.
For each design in DESIGN_GRID_REDUCED (12 runs; DESIGN_GRID_FULL only if time), run replay on the TUNE summer,
  case flexible. battery_mw = battery_frac * it_mw; backup_mw = backup_frac * facility_mw.
yearly_cost_usd = battery_mw*battery_h*1000*battery_capex_usd_kwyr + compute_lost_mwh*value_compute
                  + wear + backup fuel (scale one summer to a year: x 1.0, summer is where events are; state it).
feasible = reliable_mw >= promise_mw AND hours_over_limit == 0. is_cheapest = min yearly_cost among feasible.
Test A8: confirm the cheapest design on TEST; it must cost less than the default design.
Run offline with joblib or multiprocessing; never in the app.
"""

import pandas as pd
from src import contracts as c


def design_search(grid: pd.DataFrame, forecast: pd.DataFrame, p: dict, space: dict | None = None) -> pd.DataFrame:
    """Return the scenarios schema, one row per design."""
    raise NotImplementedError("Builder B: implement M6 design_search (see the docstring at the top of this file).")


if __name__ == "__main__":
    c.write("scenarios", design_search(c.read("grid_hourly"), c.read("forecast_test"), c.DEFAULTS))
