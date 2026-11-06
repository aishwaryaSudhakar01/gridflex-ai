"""Shared rules: every file that exists matches its schema, and the shared formulas behave. Never edit to make it pass."""
import time
import numpy as np
import pandas as pd
import pytest
from src import contracts as c
from tests.fixtures import fixture_grid


@pytest.mark.parametrize("name", list(c.SCHEMAS))
def test_existing_file_matches_contract(name):
    if not c.ready(name):
        pytest.skip(f"{name} not built yet ({c.SCHEMAS[name]['owner']})")
    c.read(name)


def test_real_grid_basics():
    if not c.ready("grid_hourly"):
        pytest.skip("grid_hourly not built yet (Builder A)")
    g = c.read("grid_hourly")
    assert g.ts_utc.is_unique and g.ts_utc.is_monotonic_increasing
    assert len(g) > 55000 and g.excluded.mean() < 0.01
    assert c.planning_peak(g) > 50000


def test_planning_peak_single_source():
    if not (c.ready("grid_hourly") and c.ready("metrics")):
        pytest.skip("needs grid_hourly and metrics")
    assert abs(c.read("metrics")["planning_peak_mw"] - c.planning_peak(c.read("grid_hourly"))) < 1e-6


def test_cut_formula():
    assert c.required_cut(100, 10, 105) == 5
    assert c.required_cut(90, 10, 105) == 0
    assert c.required_cut(200, 10, 105) == 10


def test_splits_use_texas_dates():
    ts = pd.Series(pd.to_datetime(["2025-06-01 04:00", "2025-06-01 06:00", "2025-10-01 04:00"], utc=True))
    # 04:00 UTC Jun 1 = 11 PM May 31 in Texas (out); 06:00 UTC = 1 AM Jun 1 (in); 04:00 UTC Oct 1 = Sep 30 (in)
    assert c.in_split(ts, "test").tolist() == [False, True, True]


def test_filled_load_has_no_blanks():
    g = fixture_grid().head(500).copy()
    g.loc[100:110, "load_mw"] = np.nan
    g.loc[200, "excluded"] = True
    f = c.filled_load(g)
    assert f.notna().all() and len(f) == len(g)


def test_source_costs_default_order():
    cost = c.source_costs(c.DEFAULTS)
    assert sorted(cost, key=cost.get)[:4] == ["batch", "battery", "backup", "training"]


def test_nullable_columns_only_where_allowed():
    assert c.NULLABLE == {"forecast_test": ["ba_forecast_mw", "naive_mw"]}


def test_impact_params_pin_the_design():
    p = dict(c.DEFAULTS, it_mw=2500.0, battery_mw=400.0, carbon_price_usd_t=80.0)
    q = c.impact_params(p)
    assert q["it_mw"] == c.DEFAULTS["it_mw"] and q["battery_mw"] == c.DEFAULTS["battery_mw"]
    assert q["carbon_price_usd_t"] == 80.0
    assert c.design_changed(p) and not c.design_changed(c.DEFAULTS)


def test_ask_is_95pct_of_tight_periods_not_whole_site():
    g = fixture_grid()
    F = c.facility_mw(c.DEFAULTS)
    peaks = c.event_peaks(g, c.DEFAULTS)
    assert len(peaks) > 0 and peaks.max() == F            # worst hour is always the whole site
    ask = c.ask_cut_mw(g, c.DEFAULTS)
    assert 0 < ask <= F and abs(ask - np.quantile(peaks, 0.95)) < 1e-6
