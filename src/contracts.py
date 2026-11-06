"""GridFlex AI shared contract. THE single source of truth for paths, columns, units, params and shared formulas.

FROZEN. Changing anything here needs all three builders to agree in team chat; Builder A commits it.
Every module reads and writes ONLY through read() and write() below. Never type a path or column by hand.

Conventions (no exceptions):
- Time: every timestamp is tz-aware UTC, column ts_utc (or *_utc), HOUR-BEGINNING. Convert to America/Chicago only in app.py.
- "Day" (forecast day, batch deadline, battery end-of-day) = a calendar day in America/Chicago (Texas time).
- Summers, splits and train cut-offs are Texas calendar dates: use in_split() and before_train_end(), never raw UTC compares.
- No fake data anywhere in data/ or out/. A file exists only when its owner's real code wrote it. The app shows
  "waiting for <owner>" for anything missing. Tests use in-memory fixtures (tests/fixtures.py) that are never saved.
- Power: every *_mw is MW at the GRID METER (includes cooling via PUE). Workload columns
  (realtime_mw, batch_mw, training_mw, compute_*_mw) are IT MW. Multiply IT MW by PUE to get grid MW.
- Energy: *_mwh. Money: *_usd. Emissions: *_t (tonnes CO2).
- Signs: batt_mw > 0 = discharging (reduces grid draw), < 0 = charging.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT, RAW = ROOT / "data", ROOT / "out", ROOT / "data" / "raw"
TZ_LOCAL = "America/Chicago"

# ---------------------------------------------------------------- periods and splits (spec: Data splits)
DATA_START, DATA_END = "2019-01-01", "2025-10-01"          # UTC, end exclusive (covers every Texas hour Jan 2019 to Sep 2025)
URI_DROP = ("2021-02-10", "2021-02-21")                    # Winter Storm Uri, marked excluded
PEAK_YEARS = (2022, 2024)                                   # planning peak P = max load in these years
SPLITS = {  # Texas calendar dates. eval = [start, end) ; train on Texas days before train_end
    "tune": {"train_end": "2024-06-01", "eval": ("2024-06-01", "2024-10-01")},
    "test": {"train_end": "2025-06-01", "eval": ("2025-06-01", "2025-10-01")},
}
CP4_2025 = ["2025-06-19", "2025-07-30", "2025-08-18", "2025-09-04"]   # Central dates, test A4
CITIES = {  # name: (lat, lon, weight)
    "DFW": (32.90, -97.04, 0.39), "Houston": (29.76, -95.37, 0.36),
    "San Antonio": (29.42, -98.49, 0.13), "Austin": (30.27, -97.74, 0.12),
}

# Pre-decided rule, no debate (CONTEXT.md section 6.4). Builder A sets it at 1:45 PM if needed.
PLANNING_MARGIN = 1.00   # P = PLANNING_MARGIN * max load 2022-2024. Allowed values: 1.00 or 0.97.

# ---------------------------------------------------------------- sidebar defaults (spec: Data center setup + Cost)
DEFAULTS = {
    "it_mw": 1000.0, "pue": 1.2,
    "share_realtime": 0.20, "share_batch": 0.30,            # training share = 1 - realtime - batch
    "gpu_util": 0.90,              # installed GPUs = it_mw / gpu_util; the spare capacity is where batch catches up
    "battery_mw": 100.0, "battery_h": 4.0, "battery_eff": 0.90, "battery_end_soc": 0.50,
    "backup_mw": 150.0,
    "promise_mw": 100.0, "reliability_target": 0.95,
    "cut_buffer_mw": 50.0,         # safety buffer added to the forecast load when planning
    "value_compute_usd_mwh": 1000.0, "battery_wear_usd_mwh": 20.0,
    "battery_capex_usd_kwyr": 40.0, "backup_fuel_usd_mwh": 150.0,
    "backup_co2_t_mwh": 0.5, "carbon_price_usd_t": 0.0,
    "capacity_cost_usd_kwyr": 120.0, "wait_firm_months": 60, "wait_flex_months": 12,
}
SLIDERS = {  # name: (min, max, step). app.py builds every slider from this; key= is the param name
    "it_mw": (250.0, 3000.0, 50.0), "pue": (1.05, 1.6, 0.05),
    "share_realtime": (0.0, 0.6, 0.05), "share_batch": (0.0, 0.6, 0.05),
    "battery_mw": (0.0, 600.0, 25.0), "battery_h": (0.0, 8.0, 1.0), "backup_mw": (0.0, 600.0, 25.0),
    "promise_mw": (0.0, 1500.0, 25.0), "reliability_target": (0.80, 0.99, 0.01),
    "value_compute_usd_mwh": (100.0, 5000.0, 100.0), "battery_wear_usd_mwh": (0.0, 100.0, 5.0),
    "battery_capex_usd_kwyr": (10.0, 120.0, 5.0), "backup_fuel_usd_mwh": (50.0, 500.0, 10.0),
    "carbon_price_usd_t": (0.0, 200.0, 5.0), "capacity_cost_usd_kwyr": (30.0, 300.0, 10.0),
    "wait_firm_months": (12, 96, 6), "wait_flex_months": (0, 48, 6),
}
# Design params change what the replay would do, so the stored results only hold for the built design (DEFAULTS).
# Everything else is a cost, price or promise and can be applied live to the stored replay. (Audit fix 6)
DESIGN_KEYS = ["it_mw", "pue", "share_realtime", "share_batch", "gpu_util", "battery_mw", "battery_h",
               "battery_eff", "battery_end_soc", "backup_mw", "cut_buffer_mw"]

def impact_params(p: dict) -> dict:
    """Params for impact and headline numbers: design keys fixed at the built design, costs from the sidebar."""
    return {**DEFAULTS, **{k: v for k, v in p.items() if k not in DESIGN_KEYS}}

def design_changed(p: dict) -> bool:
    return any(abs(float(p[k]) - float(DEFAULTS[k])) > 1e-9 for k in DESIGN_KEYS)

EMISSION_FACTORS_T_MWH = {"coal": 1.00, "gas": 0.43, "oil": 0.80}   # stated assumption; every other fuel = 0
DESIGN_GRID_FULL = {"it_mw": [500, 1000, 1500, 2000, 2500, 3000], "battery_frac": [0, .06, .12, .18, .24, .30],
                    "battery_h": [0, 2, 4, 6, 8], "backup_frac": [0, .10, .20]}
DESIGN_GRID_REDUCED = {"it_mw": [500, 1000, 2000], "battery_frac": [0, .10, .20, .30],
                       "battery_h": [4], "backup_frac": [0.125]}   # should-ship version: 12 designs

# ---------------------------------------------------------------- file schemas
F, I, S, B, T = "float", "int", "str", "bool", "ts"
SCHEMAS = {
    "grid_hourly": {"path": DATA / "grid_hourly.parquet", "owner": "Builder A (M0)", "cols": {
        "ts_utc": T, "load_mw": F, "ba_forecast_mw": F, "temp_c": F, "co2_t_per_mwh": F, "is_4cp": B, "excluded": B}},
    "headroom": {"path": OUT / "headroom.csv", "owner": "Builder A (M1)", "cols": {
        "new_load_mw": F, "curtail_hours": F, "curtail_pct_energy": F, "avg_event_hours": F,
        "max_cut_mw": F, "n_events": F}},
    "forecast_test": {"path": OUT / "forecast_test.csv", "owner": "Builder A (M2)", "cols": {
        "ts_utc": T, "split": S, "actual_mw": F, "model_mw": F, "ba_forecast_mw": F, "naive_mw": F,
        "p_stress": F, "stress_flag": B, "is_stress": B}},
    "metrics": {"path": OUT / "metrics.json", "owner": "Builder A (M2)", "keys": [
        "planning_peak_mw", "tau", "recall", "precision", "mape_model", "mape_ba", "mape_naive",
        "mape_top5_model", "mape_top5_ba", "mape_top5_naive", "cp4_days_caught", "cp4_days_total",
        "n_stress_hours_test", "n_stress_hours_tune", "mape_week_model"]},
    "replay_hourly": {"path": OUT / "replay_hourly.csv", "owner": "Builder B (M4)", "cols": {
        "ts_utc": T, "case": S, "split": S, "load_mw": F, "required_cut_mw": F, "planned_cut_mw": F,
        "realtime_mw": F, "batch_mw": F, "training_mw": F, "batt_mw": F, "soc_mwh": F, "backup_mw": F,
        "dc_draw_mw": F, "grid_total_mw": F, "over_limit_mw": F, "compute_demand_mw": F,
        "compute_served_mw": F, "compute_deferred_mw": F, "compute_lost_mw": F}},
    "events": {"path": OUT / "events.csv", "owner": "Builder B (M4)", "cols": {
        "event_id": S, "case": S, "split": S, "start_utc": T, "hours": I, "asked_mw": F, "delivered_mw": F,
        "mw_batch": F, "mw_battery": F, "mw_backup": F, "mw_training": F, "shortfall_mw": F,
        "flagged_day_ahead": B}},
    "reliability": {"path": OUT / "reliability.json", "owner": "Builder B (M5)", "keys": [
        "k_rel_mw", "k_ask_mw", "promise_mw", "promise_holds", "share_events_full_tune",
        "share_events_full_test", "n_events_tune", "n_events_test", "max_facility_mw_holding", "mw_by_source"]},
    "supply_curve": {"path": OUT / "supply_curve.csv", "owner": "Builder B (M5)", "cols": {
        "source": S, "mw": F, "cost_per_mwh": F, "energy_limit_mwh": F}},
    "scenarios": {"path": OUT / "scenarios.parquet", "owner": "Builder B (M6)", "cols": {
        "it_mw": F, "facility_mw": F, "battery_mw": F, "battery_h": F, "backup_mw": F, "reliable_mw": F,
        "compute_lost_pct": F, "backup_mwh": F, "co2_t": F, "yearly_cost_usd": F, "hours_over_limit": F,
        "feasible": B, "is_cheapest": B}},
    "impact": {"path": OUT / "impact.json", "owner": "Builder C (M7)", "keys": [
        "headline", "grid", "data_center", "emissions", "ratepayers"]},
}
# ---------------------------------------------------------------- week planner (not files: live outputs, validated in tests)
WEEK_MODEL = OUT / "week_model.joblib"      # Builder A saves the trained week-ahead model here (joblib.dump)
WEEK_MODES = ["live", "aug2025"]             # live = coming 7 days; aug2025 = the real peak week of summer 2025
WEEK_PLAN_COLS = ["ts_utc", "mode", "load_model_mw", "load_ercot_mw", "temp_c", "p_stress", "planned_cut_mw",
                  "realtime_mw", "batch_mw", "training_mw", "batt_mw", "soc_mwh", "backup_mw", "dc_draw_mw",
                  "cut_batch_mw", "cut_battery_mw", "cut_backup_mw", "cut_training_mw"]
# load_*_mw, *_cut_*, batt, backup, dc_draw = grid MW. realtime/batch/training = IT MW. cut_X_mw = MW at the meter
# that source removes vs running at full power (>= 0). planned_cut = sum of cut_X_mw needed by the forecast.

def validate_week_plan(df: pd.DataFrame) -> pd.DataFrame:
    if list(df.columns) != WEEK_PLAN_COLS:
        raise ContractError(f"week_plan: columns must be exactly {WEEK_PLAN_COLS}, got {list(df.columns)}")
    if str(df.ts_utc.dt.tz) != "UTC":
        raise ContractError("week_plan.ts_utc must be UTC")
    if not set(df["mode"]).issubset(WEEK_MODES):
        raise ContractError(f"week_plan.mode: allowed {WEEK_MODES}")
    if len(df) != 168:
        raise ContractError("week_plan must have exactly 168 hourly rows")
    return df

NULLABLE = {  # the only result columns allowed to have blanks (scores skip blank hours)
    "forecast_test": ["ba_forecast_mw", "naive_mw"],
}

CASES = ["flexible", "rigid", "perfect", "noise", "week"]       # allowed values of the case column
SPLIT_VALUES = ["tune", "test", "week"]
SOURCES = ["batch", "battery", "backup", "training", "realtime"]  # supply_curve.source values, cheapest first by default
IMPACT_KEYS = {
    "headline": ["mw_connectable", "reliable_mw", "compute_lost_pct", "net_co2_change_t"],
    "grid": ["dc_load_at_peak_mw", "hours_over_planning_peak", "firm_capacity_avoided_mw",
             "firm_capacity_avoided_usd_yr", "headroom_unlocked_mw"],
    "data_center": ["compute_served_mwh", "compute_deferred_mwh", "compute_lost_mwh", "compute_lost_pct",
                    "battery_cycles", "backup_hours", "backup_mwh", "months_earlier_online", "extra_compute_mwh"],
    "emissions": ["co2_grid_t", "co2_backup_t", "co2_total_t"],
    "ratepayers": ["avoided_cost_usd_per_mwh", "cp4_cut_mw"],
}

# ---------------------------------------------------------------- shared formulas (spec: Shared definitions)
def training_share(p: dict) -> float:
    return 1.0 - p["share_realtime"] - p["share_batch"]

def facility_mw(p: dict) -> float:
    """F: normal facility draw at the meter = IT MW x PUE. A rigid data center draws F every hour."""
    return p["it_mw"] * p["pue"]

def facility_max_mw(p: dict) -> float:
    """Most the site can ever draw: installed GPUs (it_mw / gpu_util) x PUE. Only reached during batch catch-up."""
    return p["it_mw"] / p["gpu_util"] * p["pue"]

def battery_mwh(p: dict) -> float:
    return p["battery_mw"] * p["battery_h"]

def planning_peak(grid: pd.DataFrame) -> float:
    """P: highest hourly load in 2022-2024 (excluded rows ignored), times PLANNING_MARGIN."""
    g = grid[~grid.excluded]
    yr = g.ts_utc.dt.year
    return float(g.loc[(yr >= PEAK_YEARS[0]) & (yr <= PEAK_YEARS[1]), "load_mw"].max()) * PLANNING_MARGIN

def required_cut(load_mw, F_mw: float, P: float):
    """c_t = min(max(L_t + F - P, 0), F). Works on floats, arrays and Series."""
    return np.minimum(np.maximum(np.asarray(load_mw, dtype=float) + F_mw - P, 0.0), F_mw)

def grid_limit(load_mw, p: dict, P: float):
    """Most the facility may draw this hour = clip(P - L_t, 0, F_max). Equals F - c_t in every stress hour,
    and allows catch-up up to F_max when the grid has room. Use load forecast + cut_buffer_mw when planning."""
    return np.clip(P - np.asarray(load_mw, dtype=float), 0.0, facility_max_mw(p))

def find_events(cut_mw, ts) -> list[tuple[int, int]]:
    """Runs of consecutive hours with cut > 0. Returns (first_row, last_row) positional pairs, inclusive."""
    cut = np.asarray(cut_mw, dtype=float)
    t = pd.to_datetime(pd.Series(ts), utc=True).reset_index(drop=True)
    pos = np.flatnonzero(cut > 1e-9)
    if len(pos) == 0:
        return []
    runs, start = [], pos[0]
    for a, b in zip(pos[:-1], pos[1:]):
        if b != a + 1 or (t[b] - t[a]) != pd.Timedelta(hours=1):
            runs.append((int(start), int(a))); start = b
    runs.append((int(start), int(pos[-1])))
    return runs

def event_peaks(grid: pd.DataFrame, p: dict) -> np.ndarray:
    """Largest required cut in each tight period (event) of 2022-2024, for this data center's size."""
    g = grid[~grid.excluded].dropna(subset=["load_mw"])
    yr = g.ts_utc.dt.year
    g = g[(yr >= PEAK_YEARS[0]) & (yr <= PEAK_YEARS[1])].reset_index(drop=True)
    cut = required_cut(g.load_mw, facility_mw(p), planning_peak(grid))
    return np.array([cut[a:b + 1].max() for a, b in find_events(cut, g.ts_utc)])

def ask_cut_mw(grid: pd.DataFrame, p: dict) -> float:
    """K_ask, the cut the grid asks this data center for: big enough to cover reliability_target (default 95%)
    of tight periods in 2022-2024. Decided in audit fix 5. The single worst hour is always the whole site
    (load sat exactly at the record), so it is reported separately as worst_hour_cut_mw, never used as the ask.
    Builder A (data_center_ask) and Builder B (reliable_flex) MUST both use this function."""
    peaks = event_peaks(grid, p)
    return float(np.quantile(peaks, p["reliability_target"])) if len(peaks) else 0.0

def local_day(ts_utc) -> pd.Series:
    """Central calendar date for each UTC timestamp."""
    return pd.to_datetime(pd.Series(ts_utc), utc=True).dt.tz_convert(TZ_LOCAL).dt.date

def in_split(ts_utc, split: str) -> pd.Series:
    """True for hours whose TEXAS date is inside a split's evaluation summer (Jun 1 to Sep 30)."""
    a, b = SPLITS[split]["eval"]
    d = pd.Series(pd.to_datetime(local_day(ts_utc)))
    return ((d >= pd.Timestamp(a)) & (d < pd.Timestamp(b))).set_axis(range(len(d)))

def before_train_end(ts_utc, split: str) -> pd.Series:
    """True for hours whose Texas date is before the split's training cut-off. Train only on these rows."""
    d = pd.Series(pd.to_datetime(local_day(ts_utc)))
    return (d < pd.Timestamp(SPLITS[split]["train_end"])).set_axis(range(len(d)))

def filled_load(grid: pd.DataFrame) -> pd.Series:
    """Load with excluded or blank hours filled by time interpolation. The replay and planner use this so
    they never see blanks. Filled hours are never counted as events (check grid.excluded)."""
    s = grid.load_mw.where(~grid.excluded).set_axis(pd.DatetimeIndex(grid.ts_utc))
    return pd.Series(s.interpolate(method="time", limit_direction="both").to_numpy(), index=grid.index)

def source_costs(p: dict) -> dict:
    """$ per MWh at the grid meter for each source of flex. ONE definition used by the scheduler objective,
    the supply curve and the Right now panel, so every screen ranks sources the same way."""
    return {"batch": 0.0,
            "battery": p["battery_wear_usd_mwh"] + (1 / p["battery_eff"] - 1) * 40.0,   # wear + losses at ~$40/MWh
            "backup": p["backup_fuel_usd_mwh"] + p["carbon_price_usd_t"] * p["backup_co2_t_mwh"],
            "training": p["value_compute_usd_mwh"] / p["pue"],                          # $/IT MWh -> $/grid MWh
            "realtime": float("inf")}

# ---------------------------------------------------------------- read / write / validate
class ContractError(ValueError):
    pass

def _cast(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    for c, t in cols.items():
        if t == T:
            df[c] = pd.to_datetime(df[c], utc=True)
        elif t == B:
            df[c] = df[c].astype(str).str.lower().isin(["true", "1"]) if df[c].dtype == object else df[c].astype(bool)
        elif t == F:
            df[c] = df[c].astype(float)
        elif t == I:
            df[c] = df[c].astype(int)
        elif t == S:
            df[c] = df[c].astype(str)
    return df

def validate(name: str, obj):
    """Raise ContractError if obj doesn't match SCHEMAS[name]. read() and write() both call this."""
    spec = SCHEMAS[name]
    if "cols" in spec:
        if not isinstance(obj, pd.DataFrame):
            raise ContractError(f"{name}: expected a DataFrame")
        if list(obj.columns) != list(spec["cols"]):
            raise ContractError(f"{name}: columns must be exactly {list(spec['cols'])}, got {list(obj.columns)}")
        for c, t in spec["cols"].items():
            if t == T and str(getattr(obj[c].dt, "tz", None)) != "UTC":
                raise ContractError(f"{name}.{c}: must be tz-aware UTC")
            if t == F and obj[c].isna().any() and name != "grid_hourly" and c not in NULLABLE.get(name, []):
                raise ContractError(f"{name}.{c}: NaN not allowed")
        if "case" in obj and not set(obj["case"]).issubset(CASES):
            raise ContractError(f"{name}.case: allowed values {CASES}")
        if "split" in obj and not set(obj["split"]).issubset(SPLIT_VALUES):
            raise ContractError(f"{name}.split: allowed values {SPLIT_VALUES}")
        if name == "supply_curve" and not set(obj["source"]).issubset(SOURCES):
            raise ContractError(f"supply_curve.source: allowed values {SOURCES}")
    else:
        if not isinstance(obj, dict):
            raise ContractError(f"{name}: expected a dict")
        missing = [k for k in spec["keys"] if k not in obj]
        if missing:
            raise ContractError(f"{name}: missing keys {missing}")
        if name == "impact":
            for sec, keys in IMPACT_KEYS.items():
                parts = [obj[sec]] if sec == "headline" else [obj[sec].get("rigid", {}), obj[sec].get("flexible", {})]
                for part in parts:
                    miss = [k for k in keys if k not in part]
                    if miss:
                        raise ContractError(f"impact.{sec}: missing {miss} (sections need rigid and flexible)")
    return obj

def write(name: str, obj):
    """The ONLY way to save a handoff file. Validates, then saves. Only real code calls this."""
    spec = SCHEMAS[name]; path = spec["path"]
    validate(name, obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "cols" in spec:
        if path.suffix == ".parquet":
            obj.to_parquet(path, index=False)
        else:
            out = obj.copy()
            for c, t in spec["cols"].items():
                if t == T:
                    out[c] = out[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            out.to_csv(path, index=False)
    else:
        path.write_text(json.dumps(obj, indent=2, default=float))
    return path

def read(name: str):
    """The ONLY way to load a handoff file. Returns typed, validated data with UTC timestamps parsed."""
    spec = SCHEMAS[name]; path = spec["path"]
    if "cols" in spec:
        df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        return validate(name, _cast(df, spec["cols"]))
    return validate(name, json.loads(path.read_text()))

def ready(name: str) -> bool:
    """True once the owner's real code has written this file."""
    return SCHEMAS[name]["path"].exists()

def params_ok(p: dict) -> str | None:
    """Return an error message if sidebar params are impossible, else None. app.py shows it and stops."""
    if training_share(p) < -1e-9:
        return "Real-time + batch shares exceed 100% of IT load. Lower one of them."
    if p["battery_mw"] > 0 and p["battery_h"] == 0:
        return "Battery has power but 0 hours of storage. Set hours above 0 or power to 0."
    return None
