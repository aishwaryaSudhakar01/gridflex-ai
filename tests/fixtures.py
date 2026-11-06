"""TEST-ONLY fixtures. Built in memory, never saved, never shown in the app.
They let tests check that every module returns the contract shapes before the real ERCOT file exists.
Once data/grid_hourly.parquet exists, tests use the real file instead (see grid_for_tests)."""
import numpy as np
import pandas as pd
from src import contracts as c

RNG = np.random.default_rng(7)


def fixture_grid() -> pd.DataFrame:
    ts = pd.date_range(c.DATA_START, c.DATA_END, freq="h", tz="UTC", inclusive="left")
    loc = ts.tz_convert(c.TZ_LOCAL)
    hod, doy = loc.hour.values, loc.dayofyear.values
    yr = loc.year.values - 2019
    temp = 20 + 10 * np.sin((doy - 110) / 365 * 2 * np.pi) + 5 * np.sin((hod - 9) / 24 * 2 * np.pi) + RNG.normal(0, 2, len(ts))
    load = (38000 + 1500 * yr + 9000 * np.sin((hod - 10) / 24 * 2 * np.pi)
            + 22000 * np.exp(-((doy - 215) / 35) ** 2) * (0.55 + 0.45 * np.clip(np.sin((hod - 11) / 24 * 2 * np.pi), 0, 1))
            + RNG.normal(0, 900, len(ts)))
    df = pd.DataFrame({
        "ts_utc": ts, "load_mw": load.round(0), "ba_forecast_mw": (load * RNG.normal(1, 0.025, len(ts))).round(0),
        "temp_c": temp.round(1), "co2_t_per_mwh": (0.38 + 0.06 * np.sin((hod - 18) / 24 * 2 * np.pi)).round(3),
        "is_4cp": False, "excluded": False})
    df.loc[(df.ts_utc >= c.URI_DROP[0]) & (df.ts_utc < c.URI_DROP[1]), "excluded"] = True
    m = loc.month.values
    for (y, mo), g in df[(m >= 6) & (m <= 9)].groupby([loc.year[(m >= 6) & (m <= 9)], m[(m >= 6) & (m <= 9)]]):
        df.loc[g.load_mw.idxmax(), "is_4cp"] = True
    return df




def grid_for_tests() -> pd.DataFrame:
    """Real grid if Builder A has written it, else the in-memory fixture."""
    return c.read("grid_hourly") if c.ready("grid_hourly") else fixture_grid()
