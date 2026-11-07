"""M1 Grid capacity. OWNER: Builder A. Spec: PDF M1.
headroom_curve: for X in 0..25,000 MW step 250, on 2022-2024 rows (not excluded), cut_t = required_cut(L_t, X, P).
  curtail_hours = hours with cut > 0 per YEAR (divide by 3); curtail_pct_energy = 100 * sum(cut) / (X * hours);
  avg_event_hours over find_events runs; max_cut_mw = max cut; n_events per YEAR.
  Test A1: X at curtail_pct_energy <= 0.5% should be the same order of magnitude as Duke's ~10 GW.
data_center_ask: same math at F = facility_mw(p). Called LIVE by app.py, so keep it under 0.5 s.
  k_ask_mw = contracts.ask_cut_mw(grid, p): the cut covering 95% of tight periods (audit fix 5). Do NOT use the
  largest single cut: at the record hour that is always the whole site. Report that as worst_hour_cut_mw.

Notes (as implemented):
- Years follow contracts.planning_peak / event_peaks (calendar years of ts_utc), so every number uses the same rows.
- hours = number of usable 2022-2024 hours (not excluded, load present). Excluded hours split events, so a run
  never bridges a data gap.
- X = 0 has no load to curtail: every column is 0.
- headroom_curve uses the RECORD peak (planning_peak / PLANNING_MARGIN). Rule 6.4 lowers P only so the 2025 test
  summer has stress hours; at P x 0.97, 2022-2024 already exceeded P in 128 hours, so every new load would be
  curtailed > 0.5% and headroom would read 0 (fails Test A1). Headroom asks "how much fits under the grid's real
  record", so it uses the record. data_center_ask keeps planning_peak (with margin), matching the replay and
  contracts.ask_cut_mw.
"""

import numpy as np
import pandas as pd
from src import contracts as c

X_MAX_MW, X_STEP_MW = 25_000, 250
DUKE_PCT = 0.5          # Test A1: Duke Nicholas Institute curtailment rate
DUKE_ERCOT_MW = 10_000  # Duke's ERCOT estimate at that rate


def _peak_years(grid: pd.DataFrame) -> pd.DataFrame:
    """2022-2024 rows, not excluded, load present (same rows as contracts.event_peaks)."""
    g = grid[~grid.excluded].dropna(subset=["load_mw"])
    yr = g.ts_utc.dt.year
    return g[(yr >= c.PEAK_YEARS[0]) & (yr <= c.PEAK_YEARS[1])].reset_index(drop=True)


def record_peak(grid: pd.DataFrame) -> float:
    """Highest 2022-2024 hourly load, without the rule 6.4 margin. Used for the headroom curve only."""
    return c.planning_peak(grid) / c.PLANNING_MARGIN


def _n_years() -> int:
    return c.PEAK_YEARS[1] - c.PEAK_YEARS[0] + 1


def _stats(load: np.ndarray, ts: pd.Series, x_mw: float, P: float) -> dict:
    """Curtailment stats for a new load of x_mw on these hours."""
    years = _n_years()
    if x_mw <= 0 or len(load) == 0:
        return {"new_load_mw": float(x_mw), "curtail_hours": 0.0, "curtail_pct_energy": 0.0,
                "avg_event_hours": 0.0, "max_cut_mw": 0.0, "n_events": 0.0, "_runs": []}
    cut = c.required_cut(load, x_mw, P)
    runs = c.find_events(cut, ts)
    lengths = [b - a + 1 for a, b in runs]
    return {
        "new_load_mw": float(x_mw),
        "curtail_hours": float((cut > 0).sum()) / years,
        "curtail_pct_energy": 100.0 * float(cut.sum()) / (x_mw * len(load)),
        "avg_event_hours": float(np.mean(lengths)) if lengths else 0.0,
        "max_cut_mw": float(cut.max()),
        "n_events": float(len(runs)) / years,
        "_runs": lengths,
    }


def headroom_curve(grid: pd.DataFrame) -> pd.DataFrame:
    """Return the headroom schema."""
    g = _peak_years(grid)
    P = record_peak(grid)
    load, ts = g.load_mw.to_numpy(dtype=float), g.ts_utc
    rows = []
    for x in range(0, X_MAX_MW + 1, X_STEP_MW):
        s = _stats(load, ts, float(x), P)
        s.pop("_runs")
        rows.append(s)
    out = pd.DataFrame(rows)[list(c.SCHEMAS["headroom"]["cols"])].astype(float)
    return c.validate("headroom", out)


def headroom_at(curve: pd.DataFrame, pct: float = DUKE_PCT) -> float:
    """Largest new load (MW) whose curtailment stays at or below pct % of its energy (Test A1)."""
    ok = curve[(curve.new_load_mw > 0) & (curve.curtail_pct_energy <= pct)]
    return float(ok.new_load_mw.max()) if len(ok) else 0.0


def data_center_ask(grid: pd.DataFrame, p: dict) -> dict:
    """What the grid asks of this data center (live, < 0.5 s). Keys: k_ask_mw, events_per_year, hours_per_year,
    longest_event_h, planning_peak_mw, worst_hour_cut_mw."""
    g = _peak_years(grid)
    P = c.planning_peak(grid)
    s = _stats(g.load_mw.to_numpy(dtype=float), g.ts_utc, c.facility_mw(p), P)
    return {
        "k_ask_mw": c.ask_cut_mw(grid, p),
        "events_per_year": s["n_events"],
        "hours_per_year": s["curtail_hours"],
        "longest_event_h": float(max(s["_runs"])) if s["_runs"] else 0.0,
        "planning_peak_mw": P,
        "worst_hour_cut_mw": s["max_cut_mw"],
    }


def summary(grid: pd.DataFrame, curve: pd.DataFrame) -> None:
    """A1 check and the numbers to post in team chat."""
    x = headroom_at(curve)
    ratio = x / DUKE_ERCOT_MW if DUKE_ERCOT_MW else float("nan")
    verdict = "PASS (same order of magnitude)" if 0.1 <= ratio <= 10 else "CHECK: not the same order of magnitude"
    print(f"[M1] headroom curve measured against the record peak {record_peak(grid):,.0f} MW")
    print(f"[M1] headroom at <= {DUKE_PCT}% curtailment: {x:,.0f} MW vs Duke ~{DUKE_ERCOT_MW:,} MW -> {verdict}")
    for pct in (0.25, 1.0, 5.0):
        print(f"[M1] headroom at <= {pct}%: {headroom_at(curve, pct):,.0f} MW")
    a = data_center_ask(grid, c.DEFAULTS)
    print(f"[M1] default data center ({c.facility_mw(c.DEFAULTS):,.0f} MW at the meter): ask K = {a['k_ask_mw']:,.0f} MW, "
          f"{a['events_per_year']:.1f} events/yr, {a['hours_per_year']:.1f} h/yr, longest {a['longest_event_h']:.0f} h, "
          f"worst hour {a['worst_hour_cut_mw']:,.0f} MW, P = {a['planning_peak_mw']:,.0f} MW")


if __name__ == "__main__":
    grid = c.read("grid_hourly")
    summary(grid, headroom_curve(grid))
