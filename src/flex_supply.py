"""M5 Reliable flex + supply curve. OWNER: Builder B. Spec: PDF M5.
reliable_flex: for each TUNE event (case flexible), small LP = largest constant cut held for the whole event, starting
  from the replay's real soc and batch backlog at event start. k_rel_mw = 5th percentile (1 - reliability_target).
  k_ask_mw = contracts.ask_cut_mw(grid, p) (cut covering 95% of tight periods; audit fix 5).
  promise_holds = k_rel_mw >= promise_mw. Also report whether k_rel_mw >= k_ask_mw in the doc.
  share_events_full_test = share of TEST events delivering k_rel_mw in full (test A5 needs >= 0.95).
  max_facility_mw_holding = largest it_mw*pue (search 250 MW steps) where k_rel >= ask_cut_mw at that size.
  mw_by_source = mean MW by source across tune events, keys = contracts.SOURCES.
supply_curve: LIVE in app. Rows in contracts.SOURCES order; cost per MWh from params (see PDF M5 table);
  battery cost = wear + (1/eff - 1) * mean grid price proxy ($40/MWh); realtime row has mw = 0.

IMPLEMENTATION NOTES (Builder B decisions, one line each)
- The "small LP" is solved in closed form (same answer, no solver needed, instant). For an event of H hours the constant
  cut K is the sum of four pools, capped at the facility draw F (real-time inference is never cut):
    batch    = min(batch MW, (idle-GPU catch-up capacity in 24 h - batch backlog at event start) / H) * PUE
    battery  = min(battery MW, soc at event start / H)         (no charging during the event)
    backup   = backup MW
    training = training MW * PUE                                (throttle to zero)
  Spreading a shared pool evenly over the H hours is optimal for a constant cut. Verified against linprog in testing.
- Start state comes from the replay itself: soc at the end of the hour before the event, and the batch backlog
  (running total of compute_deferred_mw) at that moment, so back-to-back heat-wave days count against it.
- "Test events deliver k_rel in full" = the event's own capability (computed from the TEST replay's real start state) is
  >= k_rel_mw. This is the out-of-sample check for A5. It is reported honestly, pass or fail.
- max_facility_mw_holding re-sizes only the IT load (it_mw = F / PUE); battery and backup stay at their absolute MW, and the
  start states come from the default-design replay. Stated simplification.
- No events: k_rel_mw = F (nothing was ever asked), counts are 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import contracts as c

_STEP_MW = 250.0
_MAX_FACILITY_MW = 4000.0


def _capability(hours: float, soc0_mwh: float, backlog0_mwh: float, p: dict) -> float:
    """Largest constant cut (grid MW) the site can hold for `hours` hours from this start state."""
    pue, it = p["pue"], p["it_mw"]
    bt0, pbar = it * p["share_batch"], it * c.training_share(p)
    catch_up_mwh = max(it / p["gpu_util"] - it, 0.0) * 24.0           # IT MWh idle GPUs can win back within 24 h
    defer_mw = min(bt0, max(catch_up_mwh - max(backlog0_mwh, 0.0), 0.0) / hours)
    batt_mw = min(p["battery_mw"], max(soc0_mwh, 0.0) / hours)
    total = defer_mw * pue + batt_mw + p["backup_mw"] + pbar * pue
    return float(min(c.facility_mw(p), total))


def _event_states(replay: pd.DataFrame, events: pd.DataFrame, split: str, p: dict) -> list[tuple[float, float, float]]:
    """(hours, soc at start, backlog at start) for each flexible event of the split, from the replay's real path."""
    rows = replay[(replay["case"] == "flexible") & (replay["split"] == split)].sort_values("ts_utc").reset_index(drop=True)
    ev = events[(events["case"] == "flexible") & (events["split"] == split)]
    if rows.empty or ev.empty:
        return []
    cum_deferred = rows["compute_deferred_mw"].cumsum().to_numpy()
    soc_end = rows["soc_mwh"].to_numpy()
    ts = pd.DatetimeIndex(rows["ts_utc"])
    out = []
    for _, e in ev.iterrows():
        i = int(ts.searchsorted(pd.Timestamp(e["start_utc"])))
        soc0 = float(soc_end[i - 1]) if i > 0 else c.battery_mwh(p)
        backlog0 = float(cum_deferred[i - 1]) if i > 0 else 0.0
        out.append((float(e["hours"]), soc0, max(backlog0, 0.0)))
    return out


def _quantile_low(values: list[float], target: float) -> float:
    return float(np.quantile(values, 1.0 - target, method="lower"))


def reliable_flex(grid: pd.DataFrame, replay: pd.DataFrame, events: pd.DataFrame, p: dict) -> dict:
    """Return the reliability dict (contract keys)."""
    F, target = c.facility_mw(p), p["reliability_target"]
    tune = _event_states(replay, events, "tune", p)
    test = _event_states(replay, events, "test", p)
    caps_tune = [_capability(*s, p) for s in tune]
    caps_test = [_capability(*s, p) for s in test]
    k_rel = _quantile_low(caps_tune, target) if caps_tune else float(F)
    k_ask = float(c.ask_cut_mw(grid, p))

    def share(caps):
        return float(np.mean([k >= k_rel - 1e-9 for k in caps])) if caps else 0.0

    best = 0.0                                                  # largest facility size where the promise still covers the ask
    for size in np.arange(_STEP_MW, _MAX_FACILITY_MW + 1.0, _STEP_MW):
        p2 = {**p, "it_mw": float(size) / p["pue"]}
        caps2 = [_capability(*s, p2) for s in tune]
        if caps2 and _quantile_low(caps2, target) >= c.ask_cut_mw(grid, p2) - 1e-9:
            best = float(size)

    ev_t = events[(events["case"] == "flexible") & (events["split"] == "tune")]
    src = {"batch": "mw_batch", "battery": "mw_battery", "backup": "mw_backup", "training": "mw_training"}
    mw_by_source = {s: (float(ev_t[col].mean()) if len(ev_t) else 0.0) for s, col in src.items()}
    mw_by_source["realtime"] = 0.0
    return {
        "k_rel_mw": float(k_rel), "k_ask_mw": k_ask, "promise_mw": float(p["promise_mw"]),
        "promise_holds": bool(k_rel >= p["promise_mw"]), "k_rel_covers_ask": bool(k_rel >= k_ask),
        "share_events_full_tune": share(caps_tune), "share_events_full_test": share(caps_test),
        "n_events_tune": int(len(tune)), "n_events_test": int(len(test)),
        "max_facility_mw_holding": best, "mw_by_source": {s: mw_by_source[s] for s in c.SOURCES},
    }


def supply_curve(p: dict) -> pd.DataFrame:
    """LIVE. Return the supply_curve schema for params p, < 50 ms. cost_per_mwh MUST come from contracts.source_costs(p)."""
    cost, pue, it = c.source_costs(p), p["pue"], p["it_mw"]
    bt0, pbar = it * p["share_batch"], it * c.training_share(p)
    batch_mwh = min(bt0 * 24.0, max(it / p["gpu_util"] - it, 0.0) * 24.0) * pue      # idle-GPU catch-up limit per day
    data = {"batch": (bt0 * pue, batch_mwh), "battery": (p["battery_mw"], c.battery_mwh(p)),
            "backup": (p["backup_mw"], float("inf")), "training": (pbar * pue, float("inf")),
            "realtime": (0.0, 0.0)}
    return pd.DataFrame([{"source": s, "mw": float(data[s][0]), "cost_per_mwh": float(cost[s]),
                          "energy_limit_mwh": float(data[s][1])} for s in c.SOURCES],
                        columns=list(c.SCHEMAS["supply_curve"]["cols"]))


if __name__ == "__main__":
    g, r, e = c.read("grid_hourly"), c.read("replay_hourly"), c.read("events")
    c.write("reliability", reliable_flex(g, r, e, c.DEFAULTS))
    c.write("supply_curve", supply_curve(c.DEFAULTS))
