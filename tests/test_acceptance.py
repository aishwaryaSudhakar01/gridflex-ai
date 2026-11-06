"""Acceptance tests A1-A10 from the PDF. Each SKIPS until its input file is built, and must PASS on real data.
Results go into the written doc (Logistics reads them from pytest output)."""
import time
import pandas as pd
import pytest
from src import contracts as c


def need(*names):
    missing = [n for n in names if not c.ready(n)]
    if missing:
        pytest.skip(f"not built yet: {missing}")


def test_A1_headroom_order_of_magnitude():
    need("headroom")
    h = c.read("headroom")
    x = h.loc[h.curtail_pct_energy <= 0.5, "new_load_mw"].max()
    assert 2000 <= x <= 40000, f"headroom at 0.5% = {x} MW, Duke says ~10,000 for ERCOT"


def test_A2_recall():
    need("metrics")
    m = c.read("metrics")
    assert m["recall"] >= 0.90, m


def test_A3_beats_naive_and_near_ercot():
    need("metrics")
    m = c.read("metrics")
    assert m["mape_model"] < m["mape_naive"] and m["mape_model"] <= m["mape_ba"] + 1.0, m


def test_A4_cp4_days():
    need("metrics")
    m = c.read("metrics")
    assert m["cp4_days_caught"] == 4, m


def test_A5_reliable_mw_holds_out_of_sample():
    need("reliability")
    r = c.read("reliability")
    assert r["n_events_test"] > 0, "No test-summer events: apply PLANNING_MARGIN rule (CONTEXT 6.4)"
    assert r["share_events_full_test"] >= 0.95, r


def test_A6_grid_never_over_limit():
    need("replay_hourly")
    r = c.read("replay_hourly")
    assert r[(r.case == "flexible") & (r.split == "test")].over_limit_mw.max() <= 1e-6


def test_A7_inference_protected():
    need("replay_hourly")
    r = c.read("replay_hourly")
    f = r[r.case == "flexible"]
    assert (f.realtime_mw >= c.DEFAULTS["it_mw"] * c.DEFAULTS["share_realtime"] - 1e-6).all()


def test_A8_design_search():
    need("scenarios")
    s = c.read("scenarios")
    best = s[s.is_cheapest]
    assert len(best) == 1 and bool(best.feasible.iloc[0])
    default = s[(s.it_mw == c.DEFAULTS["it_mw"])]
    assert best.yearly_cost_usd.iloc[0] <= default.yearly_cost_usd.max()


def test_A9_accounting_balances():
    need("replay_hourly")
    r = c.read("replay_hourly")
    lhs = r.compute_served_mw + r.compute_deferred_mw + r.compute_lost_mw
    assert ((lhs - r.compute_demand_mw).abs() <= 0.001 * r.compute_demand_mw).all()


def test_A10_app_loads_fast():
    need(*c.SCHEMAS)
    from streamlit.testing.v1 import AppTest
    t = time.time(); at = AppTest.from_file(str(c.ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception and time.time() - t < 15
