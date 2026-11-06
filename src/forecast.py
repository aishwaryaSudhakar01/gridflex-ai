"""M2 Stress forecaster. OWNER: Builder A. Spec: PDF M2.
Task: at end of Central day D, forecast hourly load for D+1 with data known by then.
Features: hour, day of week, US holiday, sin/cos day of year; temp_c, CDH max(T-20,0), HDH max(10-T,0),
  day max temp, 3-day mean temp; lag 24h, lag 168h, previous day peak. Actual temps stand in for weather forecasts (stated limit).
Model: LinearRegression on lags + degree-hours, then HistGradientBoostingRegressor on its residuals.
Fit twice: train on rows where contracts.before_train_end(ts, s) is True, predict rows where contracts.in_split(ts, s)
is True. Never train on rows you predict. Rows with excluded=True are dropped from training and scoring.
Stress: is_stress = actual load > P - F (default params). p_stress = 1 - Phi(((P - F) - model_mw) / sigma_hour),
  sigma_hour = residual std per hour of day on the TUNE summer. tau = highest value with recall >= 0.90 on tune.
  stress_flag = p_stress >= tau. Apply the same tau on test.
Benchmarks: ba_forecast_mw (ERCOT's own) and naive_mw = load 168 h earlier. Both may be blank in some hours
(contracts.NULLABLE); score each forecast on its non-blank hours only.
metrics["planning_peak_mw"] MUST be exactly contracts.planning_peak(grid) (a test checks it).
metrics on TEST: MAPE all hours and top 5% load hours for all three; recall, precision; cp4_days_caught =
  CP4_2025 dates with any stress_flag hour that day (Central).
Output: forecast_test (both splits, split column = "tune"/"test") and metrics.

WEEK-AHEAD MODEL (for the Plan the week tab), same file, same owner. Target 30 min, after the day-ahead model.
- Same model design, minus the features you can't know 2-7 days out: drop lag 24 h and previous day peak.
  Keep calendar, temperature features (from the weather FORECAST at run time), and lag 168 h
  (for any hour in the next 7 days, the load 168 h earlier is already known).
- train_week_model(grid): fit on everything before SPLITS["test"]["train_end"]; return the fitted object
  (one sklearn Pipeline or a small dict of models). build.py saves it with joblib to contracts.WEEK_MODEL.
- metrics["mape_week_model"]: MAPE of predict_week on the test summer, run as rolling 7-day windows.
- predict_week must work with model=None (loads WEEK_MODEL if it exists, else falls back to lag 168, the stub).
- p_stress uses the same formula as the day-ahead model with sigma per hour measured for the week model.
"""

import numpy as np
import pandas as pd
from src import contracts as c


def run_forecast(grid: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return (forecast_test df, metrics dict)."""
    raise NotImplementedError("Builder A: implement M2 run_forecast (see the docstring at the top of this file).")


def train_week_model(grid: pd.DataFrame):
    """Return the fitted week-ahead model, or None (predict_week then uses the baseline)."""
    return None


def predict_week(history: pd.DataFrame, future: pd.DataFrame, P: float, F_mw: float, model=None) -> pd.DataFrame:
    """history: ts_utc, load_mw (at least the 168 h before future starts).
    future: ts_utc, temp_c (exactly 168 hourly rows).
    Returns ts_utc, load_model_mw, p_stress (168 rows). LIVE in app: < 1 s.
    Until Builder A's model exists this uses a real, simple BASELINE: the load at the same hour last week."""
    if model is None and c.WEEK_MODEL.exists():
        try:
            import joblib
            model = joblib.load(c.WEEK_MODEL)
        except Exception:
            model = None          # wrong library version or corrupt file: fall back to the baseline, never crash
    # Builder A: if model is not None, predict with it here and return.
    h = history.set_index("ts_utc").load_mw
    lag = h.reindex(future.ts_utc - pd.Timedelta(hours=168)).to_numpy(dtype=float)
    lag = np.where(np.isnan(lag), np.nanmean(h.to_numpy(dtype=float)[-168:]), lag)
    sigma = 0.04 * lag                                   # baseline error spread ~4%; A replaces with measured sigma
    from scipy.stats import norm
    p = 1 - norm.cdf(((P - F_mw) - lag) / sigma)
    out = pd.DataFrame({"ts_utc": future.ts_utc.values, "load_model_mw": lag, "p_stress": np.round(p, 3)})
    out["ts_utc"] = pd.to_datetime(out.ts_utc, utc=True)
    return out


if __name__ == "__main__":
    f, m = run_forecast(c.read("grid_hourly"))
    c.write("forecast_test", f); c.write("metrics", m)
