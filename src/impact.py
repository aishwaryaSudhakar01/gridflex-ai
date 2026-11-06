"""M7 Impact model. OWNER: Builder C. Spec: PDF M7. Never cut.
Every section reports rigid AND flexible, from replay_hourly TEST split (cases "rigid" and "flexible").
grid: dc_load_at_peak_mw = dc_draw in the hour of max load_mw; hours_over_planning_peak = hours with over_limit > 0
  (rigid: grid_total > P); firm_capacity_avoided_mw = rigid peak contribution - flexible; x capacity_cost_usd_kwyr * 1000;
  headroom_unlocked_mw = new_load_mw at curtail_pct_energy <= 0.5 from headroom (flexible), 0 for rigid.
data_center: MWh = sum of IT MW columns (1 h rows); compute_lost_pct = lost / demand * 100; battery_cycles =
  sum discharge / battery_mwh; backup hours and MWh; months_earlier_online = wait_firm - wait_flex (flexible only);
  extra_compute_mwh = months_earlier * 730 * it_mw.
emissions: co2_grid_t = sum dc_draw * co2_t_per_mwh (join grid on ts_utc); co2_backup_t = backup MWh * backup_co2_t_mwh.
ratepayers: avoided_cost_usd_per_mwh = firm_capacity_avoided_usd_yr / ERCOT annual load MWh (2024, from grid);
  cp4_cut_mw = mean cut in the is_4cp hours of the test summer (state the cost-shift side effect).
headline: mw_connectable (headroom at 0.5%), reliable_mw (reliability.k_rel_mw), compute_lost_pct (flexible),
  net_co2_change_t = flexible co2_total - rigid co2_total.
Called LIVE in the app with contracts.impact_params(p): design keys are ALWAYS the built design (the replay's),
only costs, prices and the promise come from the sliders. So numbers never mix two designs. Must run in < 1 s.
"""

import pandas as pd
from src import contracts as c


def _f(x) -> float:
    """Plain float for json (0.0 instead of NaN)."""
    x = float(x)
    return 0.0 if x != x else x


def mw_at_half_pct(headroom: pd.DataFrame) -> float:
    """Most new load the grid hosts while curtailing <= 0.5% of its energy (headroom curve, M1)."""
    ok = headroom.loc[headroom.curtail_pct_energy <= 0.5, "new_load_mw"]
    return _f(ok.max()) if len(ok) else 0.0


def compute_impact(p: dict, grid: pd.DataFrame, replay: pd.DataFrame, headroom: pd.DataFrame,
                   reliability: dict) -> dict:
    """Return the impact dict (contract keys, rigid + flexible per section)."""
    P = c.planning_peak(grid)
    test = replay[replay.split == "test"]
    g = grid.set_index("ts_utc")
    co2 = g.co2_t_per_mwh
    yr = grid.ts_utc.dt.tz_convert(c.TZ_LOCAL).dt.year
    ercot_mwh_2024 = _f(grid.loc[yr == 2024, "load_mw"].sum())
    F_mw = c.facility_mw(p)
    batt_mwh = c.battery_mwh(p)
    headroom_mw = mw_at_half_pct(headroom)

    out = {"grid": {}, "data_center": {}, "emissions": {}, "ratepayers": {}}
    peak_draw = {}
    for case in ["rigid", "flexible"]:
        d = test[test.case == case]
        flex = case == "flexible"
        if d.empty:
            peak_draw[case] = 0.0
            dc_peak = hours_over = 0.0
            served = deferred = lost = demand = batt_dis = backup_h = backup_mwh = co2_grid = cp4 = 0.0
        else:
            dc_peak = _f(d.loc[d.load_mw.idxmax(), "dc_draw_mw"])
            peak_draw[case] = dc_peak
            hours_over = _f((d.over_limit_mw > 1e-6).sum() if flex else (d.grid_total_mw > P + 1e-6).sum())
            served, deferred = _f(d.compute_served_mw.sum()), _f(d.compute_deferred_mw.sum())
            lost, demand = _f(d.compute_lost_mw.sum()), _f(d.compute_demand_mw.sum())
            batt_dis = _f(d.batt_mw.clip(lower=0).sum())
            backup_h, backup_mwh = _f((d.backup_mw > 1e-6).sum()), _f(d.backup_mw.sum())
            factor = co2.reindex(d.ts_utc).to_numpy(dtype=float)
            co2_grid = _f(pd.Series(d.dc_draw_mw.to_numpy(dtype=float) * factor).sum())   # NaN factors skipped
            is4 = g.is_4cp.reindex(d.ts_utc).fillna(False).to_numpy(dtype=bool)
            cut = (F_mw - d.dc_draw_mw.to_numpy(dtype=float)).clip(min=0)
            cp4 = _f(cut[is4].mean()) if is4.any() else 0.0
        months = _f(p["wait_firm_months"] - p["wait_flex_months"]) if flex else 0.0
        out["grid"][case] = {"dc_load_at_peak_mw": dc_peak, "hours_over_planning_peak": hours_over,
                             "firm_capacity_avoided_mw": 0.0, "firm_capacity_avoided_usd_yr": 0.0,
                             "headroom_unlocked_mw": headroom_mw if flex else 0.0}
        out["data_center"][case] = {
            "compute_served_mwh": served, "compute_deferred_mwh": deferred, "compute_lost_mwh": lost,
            "compute_lost_pct": _f(lost / demand * 100) if demand > 0 else 0.0,
            "battery_cycles": _f(batt_dis / batt_mwh) if batt_mwh > 0 else 0.0,
            "backup_hours": backup_h, "backup_mwh": backup_mwh, "months_earlier_online": months,
            "extra_compute_mwh": _f(months * 730 * p["it_mw"])}
        co2_backup = _f(backup_mwh * p["backup_co2_t_mwh"])
        out["emissions"][case] = {"co2_grid_t": co2_grid, "co2_backup_t": co2_backup, "co2_total_t": co2_grid + co2_backup}
        out["ratepayers"][case] = {"avoided_cost_usd_per_mwh": 0.0, "cp4_cut_mw": cp4}

    avoided_mw = max(peak_draw["rigid"] - peak_draw["flexible"], 0.0)
    avoided_usd = avoided_mw * p["capacity_cost_usd_kwyr"] * 1000
    out["grid"]["flexible"]["firm_capacity_avoided_mw"] = _f(avoided_mw)
    out["grid"]["flexible"]["firm_capacity_avoided_usd_yr"] = _f(avoided_usd)
    out["ratepayers"]["flexible"]["avoided_cost_usd_per_mwh"] = _f(avoided_usd / ercot_mwh_2024) if ercot_mwh_2024 else 0.0
    out["headline"] = {
        "mw_connectable": headroom_mw,
        "reliable_mw": _f(reliability.get("k_rel_mw", 0.0)),
        "compute_lost_pct": out["data_center"]["flexible"]["compute_lost_pct"],
        "net_co2_change_t": _f(out["emissions"]["flexible"]["co2_total_t"] - out["emissions"]["rigid"]["co2_total_t"]),
    }
    return out


if __name__ == "__main__":
    out = compute_impact(c.DEFAULTS, c.read("grid_hourly"), c.read("replay_hourly"), c.read("headroom"),
                         c.read("reliability"))
    c.write("impact", out)