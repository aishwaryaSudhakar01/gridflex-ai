"""Offline build. OWNER: Builder A. Runs M0 -> M7 in order; each step writes its real files through contracts.write().
A step whose code isn't written yet prints WAITING and is skipped. Nothing fake is ever written.
  python build.py              # everything (Builder A only: needs data/raw/)
  python build.py --from m4    # start at a module (m0 m1 m2 m4 m5 m6 m7)
  python build.py --only m4    # what B and C use for their own step
Final run at 4:30 PM by Builder A, then commit data/grid_hourly.parquet and out/ (the app only reads these)."""
import argparse, time
import pandas as pd
from src import contracts as c
from src import pipeline, grid_capacity, forecast, replay, flex_supply, design, impact

STEPS = ["m0", "m1", "m2", "m4", "m5", "m6", "m7"]
NEEDS = {"m1": ["grid_hourly"], "m2": ["grid_hourly"], "m4": ["grid_hourly"],
         "m5": ["grid_hourly", "replay_hourly", "events"], "m6": ["grid_hourly", "forecast_test"],
         "m7": ["grid_hourly", "replay_hourly", "headroom", "reliability"]}


def run(step: str):
    p = c.DEFAULTS
    if step == "m0":
        if not pipeline.raw_files_present():
            msg = "kept the existing data/grid_hourly.parquet" if c.ready("grid_hourly") else "no grid data yet"
            print(f"m0 SKIPPED: data/raw/ is empty on this laptop ({msg}). Only Builder A runs m0."); return False
        c.write("grid_hourly", pipeline.build_grid_hourly())
    elif step == "m1":
        c.write("headroom", grid_capacity.headroom_curve(c.read("grid_hourly")))
    elif step == "m2":
        g = c.read("grid_hourly")
        f, m = forecast.run_forecast(g)
        c.write("forecast_test", f); c.write("metrics", m)
        wm = forecast.train_week_model(g)
        if wm is not None:
            import joblib
            joblib.dump(wm, c.WEEK_MODEL)
    elif step == "m4":
        g = c.read("grid_hourly")
        f = c.read("forecast_test") if c.ready("forecast_test") else None
        if f is None:
            print("m4 running with PERFECT FORESIGHT (no forecast yet). Rerun after Builder A's m2.")
        parts = [replay.run_replay(g, f, p, s) for s in ("tune", "test")]
        c.write("replay_hourly", pd.concat([x[0] for x in parts], ignore_index=True))
        c.write("events", pd.concat([x[1] for x in parts], ignore_index=True))
    elif step == "m5":
        g, r, e = c.read("grid_hourly"), c.read("replay_hourly"), c.read("events")
        c.write("reliability", flex_supply.reliable_flex(g, r, e, p))
        c.write("supply_curve", flex_supply.supply_curve(p))
    elif step == "m6":
        c.write("scenarios", design.design_search(c.read("grid_hourly"), c.read("forecast_test"), p))
    elif step == "m7":
        out = impact.compute_impact(p, c.read("grid_hourly"), c.read("replay_hourly"), c.read("headroom"),
                                    c.read("reliability"))
        c.write("impact", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="m0", choices=STEPS)
    ap.add_argument("--only", choices=STEPS)
    a = ap.parse_args()
    steps = [a.only] if a.only else STEPS[STEPS.index(a.start):]
    for s in steps:
        missing = [n for n in NEEDS.get(s, []) if not c.ready(n)]
        if missing:
            print(f"{s} WAITING for {missing}"); continue
        t = time.time()
        try:
            if run(s) is not False:
                print(f"{s} done in {time.time() - t:.1f}s")
        except NotImplementedError as e:
            print(f"{s} WAITING: {e}")
    todo = [f"{n} ({c.SCHEMAS[n]['owner']})" for n in c.SCHEMAS if not c.ready(n)]
    print("NOT BUILT YET:", todo or "none. Everything is real.")
