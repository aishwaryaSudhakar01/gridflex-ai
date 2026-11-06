"""Every module function returns the contract shape. A function that isn't written yet SKIPS with its owner's name.
Once written, it MUST pass here. If you change a function and this fails, you broke someone else's build.
Inputs: the real files when they exist, otherwise in-memory fixtures (never saved)."""
import time
import pandas as pd
import pytest
from src import contracts as c
from src import grid_capacity, forecast, scheduler, replay, flex_supply, design, impact, live, week_plan
from tests.fixtures import grid_for_tests

P0 = dict(c.DEFAULTS)


def call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except NotImplementedError as e:
        pytest.skip(str(e))


def need_file(name):
    if not c.ready(name):
        pytest.skip(f"needs {name} ({c.SCHEMAS[name]['owner']})")
    return c.read(name)


def test_m1():
    g = grid_for_tests()
    c.validate("headroom", call(grid_capacity.headroom_curve, g))


def test_m1_ask_is_live_and_fast():
    g = grid_for_tests(); t = time.time(); ask = call(grid_capacity.data_center_ask, g, P0)
    assert time.time() - t < 0.5
    assert set(ask) >= {"k_ask_mw", "worst_hour_cut_mw", "events_per_year", "hours_per_year", "longest_event_h",
                        "planning_peak_mw"}
    assert abs(ask["k_ask_mw"] - c.ask_cut_mw(g, P0)) < 1e-6, "k_ask must come from contracts.ask_cut_mw"


def test_m2():
    f, m = call(forecast.run_forecast, grid_for_tests())
    c.validate("forecast_test", f); c.validate("metrics", m)
    assert set(f.split) == {"tune", "test"}


def test_m3_schedule_day():
    g = grid_for_tests()
    day = g[c.in_split(g.ts_utc, "test").to_numpy()].head(24)
    hours = pd.DataFrame({"ts_utc": day.ts_utc.values, "load_plan_mw": day.load_mw.values,
                          "co2_t_per_mwh": day.co2_t_per_mwh.values})
    hours["ts_utc"] = pd.to_datetime(hours.ts_utc, utc=True)
    plan, state = call(scheduler.schedule, hours, P0, c.planning_peak(g), c.battery_mwh(P0), 0.0)
    assert list(plan.columns) == scheduler.PLAN_COLS and len(plan) == 24
    assert set(state) >= {"soc_mwh", "backlog_mwh", "status"}


def test_m3_week_is_fast_and_shaped():
    g = grid_for_tests()
    start = pd.Timestamp(c.SPLITS["test"]["eval"][0], tz=c.TZ_LOCAL).tz_convert("UTC") + pd.Timedelta(days=40)
    f = c.read("forecast_test") if c.ready("forecast_test") else None
    t = time.time(); w = call(scheduler.schedule_week, g, f, P0, start)
    assert time.time() - t < 1.5
    c.validate("replay_hourly", w)
    assert len(w) == 168 and set(w.case) == {"week"}


def test_m4():
    g = grid_for_tests()
    f = c.read("forecast_test") if c.ready("forecast_test") else None   # None = perfect foresight
    r, e = call(replay.run_replay, g, f, P0, "test")
    c.validate("replay_hourly", r); c.validate("events", e)
    assert {"flexible", "rigid"} <= set(r.case)


def test_m5_reliability():
    r, e = need_file("replay_hourly"), need_file("events")
    g = grid_for_tests()
    rel = call(flex_supply.reliable_flex, g, r, e, P0)
    c.validate("reliability", rel)
    assert abs(rel["k_ask_mw"] - c.ask_cut_mw(g, P0)) < 1e-6, "k_ask must come from contracts.ask_cut_mw"


def test_m5_supply_curve_live_and_uses_shared_costs():
    t = time.time(); sc = call(flex_supply.supply_curve, P0); assert time.time() - t < 0.1
    c.validate("supply_curve", sc)
    cost = c.source_costs(P0)
    for _, row in sc[sc.source != "realtime"].iterrows():
        assert abs(row.cost_per_mwh - cost[row.source]) < 1e-6, "supply curve must use contracts.source_costs"


def test_m6():
    f = need_file("forecast_test")
    c.validate("scenarios", call(design.design_search, grid_for_tests(), f, P0, c.DESIGN_GRID_REDUCED))


def test_m7_live_and_fast():
    args = (grid_for_tests(), need_file("replay_hourly"), need_file("headroom"), need_file("reliability"))
    t = time.time(); out = call(impact.compute_impact, P0, *args)
    assert time.time() - t < 1.0
    c.validate("impact", out)


def test_week_ahead_prediction_shape_and_speed():
    g = grid_for_tests(); start = week_plan.peak_week_start(g)
    hist = g.loc[g.ts_utc < start, ["ts_utc", "load_mw"]].tail(192)
    fut = g.loc[(g.ts_utc >= start) & (g.ts_utc < start + pd.Timedelta(hours=168)), ["ts_utc", "temp_c"]]
    t = time.time(); out = forecast.predict_week(hist, fut, c.planning_peak(g), c.facility_mw(P0))
    assert time.time() - t < 1.0
    assert list(out.columns) == ["ts_utc", "load_model_mw", "p_stress"] and len(out) == 168
    assert out.p_stress.between(0, 1).all()


def test_week_plan_both_modes_offline():
    for mode in ("aug2025", "live"):
        t = time.time(); df, info = week_plan.plan_week(grid_for_tests(), P0, mode, allow_network=False)
        assert time.time() - t < 3.0
        c.validate_week_plan(df)
        assert info["mode_used"] == "aug2025"
        assert (df[["cut_batch_mw", "cut_battery_mw", "cut_backup_mw", "cut_training_mw"]] >= -1e-6).all().all()


def test_live_snapshot_offline_never_raises():
    t = time.time(); s = live.latest_snapshot(grid_for_tests(), P0, allow_network=False)
    assert time.time() - t < 1.0
    assert set(live.LIVE_KEYS) <= set(s) and s["source"] == "snapshot"
    assert list(s["today"].columns) == ["ts_utc", "load_mw", "forecast_mw"]


def test_live_timeout_really_stops_waiting():
    def slow():
        time.sleep(5); return 1
    t = time.time()
    with pytest.raises(Exception):
        live.run_with_timeout(slow, 0.5)
    assert time.time() - t < 1.5


def test_live_cut_allocation_follows_shared_costs():
    a = live.allocate_cut(500, P0)
    assert a["batch"] == 360 and a["battery"] == 100 and a["backup"] == 40 and a["training"] == 0
    p = dict(P0, carbon_price_usd_t=2000.0)          # backup now dearer than training ($1,150 vs $833)
    b = live.allocate_cut(500, p)
    assert b["backup"] == 0 and b["training"] == 40
