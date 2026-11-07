"""M0 Data pipeline. OWNER: Builder A. Spec: PDF M0 + CONTEXT.md section 6.
Reads data/raw/ (from scripts/fetch_raw.py). Writes data/grid_hourly.parquet via contracts.write("grid_hourly", df).

Steps (as implemented):
1. Demand: every data/raw/EIA930_BALANCE_*.csv (thousands=",", low_memory=False), ERCOT rows ("ERCO").
   Columns picked with find_col(): find_col(cols, "utc", "end of hour"), find_col(cols, "demand", "(mw)",
   prefer="adjusted") excluding "forecast", find_col(cols, "demand forecast").
   ts_utc = UTC end-of-hour time MINUS 1 HOUR (contract is hour-beginning). Duplicate hours: keep the last.
2. CO2: co2_t_per_mwh = sum(gen_fuel * EMISSION_FACTORS_T_MWH) / total net generation, per hour.
   Fuel columns via find_col(cols, "net generation", "coal" / "natural gas" / "petroleum"); negative gen -> 0.
   Hours with no valid total generation are time-interpolated (estimate, stated as an assumption).
3. Weather: data/raw/weather_<city>.csv (time is UTC, hour-beginning), weighted mean with CITIES weights -> temp_c
   (weights renormalised over the cities present that hour). Gaps time-interpolated.
4. Clean: excluded=True for URI_DROP (Texas dates, inclusive), zeros, and outliers: |load - centred 24h rolling median|
   > 25% AND a one-hour spike (>10% from both neighbours, same direction; without it the rule dropped ~1,000 real hours).
   Zeros and outliers are blanked, then gaps of <= 3 hours are filled by linear interpolation; longer gaps stay NaN
   and excluded=True. (Outlier and zero hours stay excluded even when filled.)
5. is_4cp: for each Jun-Sep month (Central), True on the single hour with the highest load_mw (excluded ignored).
6. Range DATA_START to DATA_END. summary() checks rows, duplicates, % excluded, 4CP dates vs CP4_2025.
7. summary() prints P = contracts.planning_peak(df) and test-summer hours with required_cut > 0 at default F
   (rule 6.4: if < 10, set PLANNING_MARGIN = 0.97 in contracts.py, commit, tell the team).
"""
from __future__ import annotations
import glob
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src import contracts as c

OUTLIER_FRAC = 0.25
SPIKE_FRAC = 0.10   # real swings ramp smoothly; a glitch jumps >10% from BOTH neighbours (same direction)
MAX_FILL_H = 3
FUELS = {"coal": "coal", "gas": "natural gas", "oil": "petroleum"}


def find_col(columns, *must, prefer=None):
    """Pick a column whose name contains every word in must (case-insensitive). If several match, prefer the one
    containing `prefer` (e.g. "adjusted"). EIA renames columns between years; never hard-code exact names."""
    m = [x for x in columns if all(w.lower() in x.lower() for w in must)]
    if prefer:
        p = [x for x in m if prefer.lower() in x.lower()]
        m = p or m
    if not m:
        raise KeyError(f"no column containing {must}; columns are {list(columns)}")
    return sorted(m, key=len)[0] if not prefer else m[0]


def raw_files_present() -> bool:
    return any(c.RAW.glob("EIA930_BALANCE_*.csv"))


# ---------------------------------------------------------------- 1-2. EIA-930
def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _read_eia() -> pd.DataFrame:
    files = sorted(glob.glob(str(c.RAW / "EIA930_BALANCE_*.csv")))
    if not files:
        raise FileNotFoundError(f"No EIA930_BALANCE_*.csv in {c.RAW}. Run python scripts/fetch_raw.py first.")
    parts = []
    for f in files:
        df = pd.read_csv(f, thousands=",", low_memory=False)
        cols = [str(x) for x in df.columns]
        df.columns = cols
        df = df[df[find_col(cols, "balancing authority")].astype(str).str.strip() == "ERCO"]
        if df.empty:
            continue
        no_fcst = [x for x in cols if "forecast" not in x.lower()]
        t_end = find_col(cols, "utc", "end of hour")
        demand = find_col(no_fcst, "demand", "(mw)", prefer="adjusted")
        fcst = find_col(cols, "demand forecast")
        total = _num(df[find_col(cols, "net generation", "(mw)")]).clip(lower=0)
        co2 = pd.Series(0.0, index=df.index)
        for fuel, word in FUELS.items():
            co2 = co2 + _num(df[find_col(cols, "net generation", word)]).clip(lower=0).fillna(0) * c.EMISSION_FACTORS_T_MWH[fuel]
        parts.append(pd.DataFrame({
            "ts_utc": pd.to_datetime(df[t_end], format="mixed", utc=True) - pd.Timedelta(hours=1),
            "load_mw": _num(df[demand]),
            "ba_forecast_mw": _num(df[fcst]),
            "co2_t_per_mwh": (co2 / total.where(total > 0)).to_numpy(),
        }))
    eia = pd.concat(parts, ignore_index=True)
    eia["ts_utc"] = eia.ts_utc.dt.floor("h")
    return eia.drop_duplicates("ts_utc", keep="last").set_index("ts_utc").sort_index()


# ---------------------------------------------------------------- 3. weather
def _norm(x: str) -> str:
    return re.sub(r"[^a-z0-9]", "", x.lower())


def _read_weather_file(f: str) -> pd.Series:
    lines = Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()
    skip = next((i for i, ln in enumerate(lines) if ln.lower().startswith(("time", "date"))), 0)
    df = pd.read_csv(f, skiprows=skip)
    cols = [str(x) for x in df.columns]
    df.columns = cols
    tcol = next((x for x in cols if x.lower().startswith(("time", "date"))), cols[0])
    vcol = find_col(cols, "temperature")
    ts = pd.to_datetime(df[tcol], utc=True).dt.floor("h")
    return pd.Series(pd.to_numeric(df[vcol], errors="coerce").to_numpy(), index=ts).groupby(level=0).mean()


def _read_weather(index: pd.DatetimeIndex) -> pd.Series:
    files = sorted(glob.glob(str(c.RAW / "weather_*.csv")))
    num = pd.Series(0.0, index=index)
    den = pd.Series(0.0, index=index)
    found = []
    for city, (_, _, w) in c.CITIES.items():
        match = [f for f in files if _norm(city) in _norm(Path(f).stem)]
        if not match:
            continue
        s = pd.concat([_read_weather_file(f) for f in match]).groupby(level=0).mean().reindex(index)
        num += s.fillna(0) * w
        den += s.notna() * w
        found.append(city)
    if not found:
        raise FileNotFoundError(f"No weather_<city>.csv for {list(c.CITIES)} in {c.RAW}. Files: {files}")
    missing = set(c.CITIES) - set(found)
    if missing:
        print(f"[M0] WARNING: no weather file for {sorted(missing)}; weights renormalised over {found}")
    return (num / den.where(den > 0)).rename("temp_c")


# ---------------------------------------------------------------- 4. cleaning
def _fill_short_gaps(s: pd.Series, max_len: int) -> pd.Series:
    """Linear interpolation only inside gaps of <= max_len consecutive missing hours."""
    miss = s.isna()
    run_id = (miss != miss.shift()).cumsum()
    run_len = miss.groupby(run_id).transform("sum")
    filled = s.interpolate(method="linear", limit_area="inside")
    return s.where(~(miss & (run_len <= max_len)), filled)


def _clean_load(raw: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Returns (load_mw, bad). bad = zero, or >25% away from the centred 24h rolling median AND a one-hour spike
    (>10% away from both neighbours, same direction). The spike test stops real hot afternoons being dropped."""
    med = raw.where(raw > 0).rolling(24, center=True, min_periods=12).median()
    far = (raw - med).abs() > OUTLIER_FRAC * med
    d_prev, d_next = raw - raw.shift(1), raw - raw.shift(-1)
    spike = (np.sign(d_prev) == np.sign(d_next)) & (np.minimum(d_prev.abs(), d_next.abs()) > SPIKE_FRAC * raw)
    bad = (raw <= 0) | (far & spike)
    bad = bad.fillna(False).astype(bool)
    load = _fill_short_gaps(raw.mask(bad), MAX_FILL_H)
    return load, bad


# ---------------------------------------------------------------- 5. 4CP
def _mark_4cp(g: pd.DataFrame) -> pd.Series:
    local = g.ts_utc.dt.tz_convert(c.TZ_LOCAL)
    ok = ~g.excluded & g.load_mw.notna() & local.dt.month.isin([6, 7, 8, 9])
    peak_idx = g[ok].groupby([local[ok].dt.year, local[ok].dt.month])["load_mw"].idxmax()
    flag = pd.Series(False, index=g.index)
    flag.loc[peak_idx.to_numpy()] = True
    return flag


# ---------------------------------------------------------------- main
def build_grid_hourly() -> pd.DataFrame:
    idx = pd.date_range(c.DATA_START, c.DATA_END, freq="h", inclusive="left", tz="UTC")
    eia = _read_eia().reindex(idx)
    load, bad = _clean_load(eia.load_mw)
    fc = eia.ba_forecast_mw
    g = pd.DataFrame({
        "load_mw": load,
        "ba_forecast_mw": fc.where(fc > 0),
        "temp_c": _read_weather(idx).interpolate(method="time", limit_direction="both"),
        "co2_t_per_mwh": eia.co2_t_per_mwh.interpolate(method="time", limit_direction="both"),
    }, index=idx).rename_axis("ts_utc").reset_index()

    day = pd.to_datetime(c.local_day(g.ts_utc)).to_numpy()
    uri = (day >= np.datetime64(c.URI_DROP[0])) & (day <= np.datetime64(c.URI_DROP[1]))
    g["excluded"] = uri | bad.to_numpy() | g.load_mw.isna().to_numpy()
    g["is_4cp"] = _mark_4cp(g)

    cols = list(c.SCHEMAS["grid_hourly"]["cols"])
    g = g[cols].astype({k: float for k in ["load_mw", "ba_forecast_mw", "temp_c", "co2_t_per_mwh"]})
    g = g.astype({"is_4cp": bool, "excluded": bool})
    return c.validate("grid_hourly", g)


def summary(grid: pd.DataFrame) -> None:
    """Step 6-7 checks and the numbers to post in team chat."""
    P = c.planning_peak(grid)
    F = c.facility_mw(c.DEFAULTS)
    test = c.in_split(grid.ts_utc, "test").to_numpy() & ~grid.excluded.to_numpy()
    n = int((c.required_cut(grid.load_mw, F, P)[test] > 0).sum())
    pct_ex = 100 * grid.excluded.mean()
    print(f"[M0] rows {len(grid):,} (expect ~59,000); duplicate hours {int(grid.ts_utc.duplicated().sum())}; "
          f"excluded {int(grid.excluded.sum()):,} = {pct_ex:.2f}% (expect < 1%, Uri alone is 288 h)")
    print(f"[M0] P = {P:,.0f} MW (PLANNING_MARGIN {c.PLANNING_MARGIN}); F = {F:,.0f} MW")
    print(f"[M0] test-summer (Jun-Sep 2025) hours with a required cut: {n}")
    if n < 10 and c.PLANNING_MARGIN == 1.0:
        print("[M0] RULE 6.4: fewer than 10 -> set PLANNING_MARGIN = 0.97 in contracts.py, commit, tell the team.")
    d = c.local_day(grid.ts_utc[grid.is_4cp])
    ours = sorted(str(x) for x in d if str(x).startswith("2025"))
    print(f"[M0] 2025 is_4cp dates: {ours}  vs CP4_2025 {c.CP4_2025}  matched "
          f"{len(set(ours) & set(c.CP4_2025))}/4")


if __name__ == "__main__":
    g = build_grid_hourly()
    summary(g)
