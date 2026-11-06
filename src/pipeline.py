"""M0 Data pipeline. OWNER: Builder A. Spec: PDF M0 + CONTEXT.md section 6.
Reads data/raw/ (from scripts/fetch_raw.py). Writes data/grid_hourly.parquet via contracts.write("grid_hourly", df).

Steps:
1. Demand: read every data/raw/EIA930_BALANCE_*.csv (thousands=",", low_memory=False). Keep the ERCOT rows
   (balancing authority code "ERCO"). Column names change slightly between years, so ALWAYS pick them with
   find_col() below, never hard-code: e.g. find_col(cols, "utc", "end of hour"), find_col(cols, "demand", "(mw)",
   prefer="adjusted") excluding "forecast", find_col(cols, "demand forecast").
   First thing at 1:00: print the column list of one file and post it in team chat.
   ts_utc = the UTC end-of-hour time MINUS 1 HOUR (contract is hour-beginning).
2. CO2: co2_t_per_mwh = sum(gen_fuel * EMISSION_FACTORS_T_MWH) / total net generation, per hour.
   Fuel columns contain "net generation" and "coal" / "natural gas" / "petroleum" (use find_col). Negative gen -> 0.
3. Weather: read data/raw/weather_<city>.csv (time is UTC, hour-beginning), weighted mean with CITIES weights -> temp_c.
4. Clean: drop duplicate hours; set excluded=True for URI_DROP, zeros, and |load - rolling 24h median| > 25%.
   Fill gaps of <= 3 hours by linear interpolation; longer gaps stay NaN and excluded=True.
5. is_4cp: for each Jun-Sep month (Central), True on the single hour with the highest load_mw.
6. Range DATA_START to DATA_END. Check: ~59,000 rows, no duplicate ts_utc, < 1% excluded.
   Then check the 4CP rows: post the 2025 is_4cp dates (Texas time) next to contracts.CP4_2025. Any mismatch,
   post it; the team keeps CP4_2025 (ERCOT's official 15-minute peaks) and the doc notes the difference.
7. Print and post in chat: P = contracts.planning_peak(df) and the count of test-summer hours with required_cut > 0
   at default F. If that count < 10, set PLANNING_MARGIN = 0.97 in contracts.py (CONTEXT 6.4), commit, tell the team.
Fallback if eia.gov is down: PUDL's archived raw EIA-930 on Zenodo (https://zenodo.org/records/19031168), same files.
Done when: `python build.py --only m0` writes data/grid_hourly.parquet and `python -m pytest` is green.
"""

import pandas as pd
from src import contracts as c


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


def build_grid_hourly() -> pd.DataFrame:
    """Return the clean hourly ERCOT table in the grid_hourly schema."""
    raise NotImplementedError("Builder A: implement M0 build_grid_hourly (see the docstring at the top of this file).")


if __name__ == "__main__":
    df = build_grid_hourly()
    c.write("grid_hourly", df)
    print(len(df), "rows. P =", c.planning_peak(df))
