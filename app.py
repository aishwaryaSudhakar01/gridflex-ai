"""GridFlex AI Streamlit app (M8). OWNER: Builder C. Spec: PDF M8 + CONTEXT.md section 9 + mockups/gridflex-app-mockup.html.

Rules that keep integration safe:
- Read handoff files ONLY with contracts.read(). Never open data/ or out/ files directly.
- Every tab body runs inside section(): a missing file shows "Waiting for <owner>", an unwritten function shows
  "Waiting for <builder>", and any other error shows a red box IN THAT TAB. The rest of the app keeps working.
- Heavy work never runs here. Live calls: grid_capacity.data_center_ask, scheduler.schedule_week,
  flex_supply.supply_curve, impact.compute_impact, live.latest_snapshot, week_plan.plan_week. All cached.
- The planning peak always comes from contracts.planning_peak(grid). Never from metrics.json.
- Convert ts_utc to Texas time only for display.
- tests/test_app.py boots this file headless; it must pass before every push.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from src import contracts as c
from src import grid_capacity, scheduler, flex_supply, impact, live, week_plan

st.set_page_config(page_title="GridFlex AI", layout="wide")


# ---------------------------------------------------------------- safety net + loaders
class Waiting(Exception):
    pass


def need(*names):
    missing = [n for n in names if not c.ready(n)]
    if missing:
        raise Waiting("Waiting for " + ", ".join(f"{n} ({c.SCHEMAS[n]['owner']})" for n in missing) + ".")


def section(fn):
    """Run one tab or block. Never lets an error escape and blank the whole app."""
    try:
        fn()
    except Waiting as e:
        st.info(str(e))
    except NotImplementedError as e:
        st.info(f"Waiting: {e}")
    except Exception as e:  # noqa: BLE001  (show the error where it happened, keep the app alive)
        st.error(f"This section hit an error: {type(e).__name__}: {e}")


@st.cache_data
def load(name):
    return c.read(name)


@st.cache_data
def peak():
    return c.planning_peak(load("grid_hourly"))


@st.cache_data
def live_ask(p_items):
    return grid_capacity.data_center_ask(load("grid_hourly"), dict(p_items))


@st.cache_data
def live_supply(p_items):
    return flex_supply.supply_curve(dict(p_items))


@st.cache_data
def live_impact(p_items):
    """Design sliders never reach here: impact_params pins them to the built design (audit fix 6)."""
    return impact.compute_impact(c.impact_params(dict(p_items)), load("grid_hourly"), load("replay_hourly"),
                                 load("headroom"), load("reliability"))


def built_design_note():
    d = c.DEFAULTS
    st.caption(f"Results for the built design: {d['it_mw']:,.0f} MW IT, battery {d['battery_mw']:,.0f} MW x "
               f"{d['battery_h']:.0f} h, backup {d['backup_mw']:,.0f} MW. Cost sliders update these numbers; "
               "design sliders change Plan the week, the peak-week view and the supply curve.")


@st.cache_data
def live_week(p_items, start_utc):
    return scheduler.schedule_week(load("grid_hourly"), load("forecast_test"), dict(p_items), start_utc)


@st.cache_data(ttl=300)
def live_now(p_items):
    return live.latest_snapshot(load("grid_hourly"), dict(p_items))


@st.cache_data(ttl=3600)
def live_week_plan(p_items, mode):
    return week_plan.plan_week(load("grid_hourly"), dict(p_items), mode)


def local(ts):
    return ts.dt.tz_convert(c.TZ_LOCAL)


# ---------------------------------------------------------------- sidebar (built from contracts.SLIDERS)
for k, v in c.DEFAULTS.items():
    st.session_state.setdefault(k, v)

with st.sidebar:
    st.header("Data center")
    st.selectbox("Region", ["ERCOT (Texas)"], disabled=True)
    for k in ["it_mw", "pue", "share_realtime", "share_batch", "battery_mw", "battery_h", "backup_mw"]:
        lo, hi, step = c.SLIDERS[k]
        st.slider(k, lo, hi, step=step, key=k)
    st.caption(f"Training share: {c.training_share(st.session_state):.0%}")
    st.header("Promise")
    for k in ["promise_mw", "reliability_target"]:
        lo, hi, step = c.SLIDERS[k]
        st.slider(k, lo, hi, step=step, key=k)
    with st.expander("Costs and impact"):
        for k in ["value_compute_usd_mwh", "battery_wear_usd_mwh", "battery_capex_usd_kwyr", "backup_fuel_usd_mwh",
                  "carbon_price_usd_t", "capacity_cost_usd_kwyr", "wait_firm_months", "wait_flex_months"]:
            lo, hi, step = c.SLIDERS[k]
            st.slider(k, lo, hi, step=step, key=k)
    if st.button("Reset to defaults"):
        for k, v in c.DEFAULTS.items():
            st.session_state[k] = v
        st.rerun()

p = {k: st.session_state[k] for k in c.DEFAULTS}
err = c.params_ok(p)
if err:
    st.error(err)
    st.stop()
P_ITEMS = tuple(sorted(p.items()))

# ---------------------------------------------------------------- header
st.title("GridFlex AI")
st.caption("The grid isn't full. It's full for a few dozen hours a year. We forecast those hours, schedule an AI "
           "data center through them, and measure what that does for the grid, the data center and emissions.")
not_built = [n for n in c.SCHEMAS if not c.ready(n)]
if not_built:
    st.warning("Still being built: " + ", ".join(not_built))


def headline():
    need("grid_hourly", "replay_hourly", "headroom", "reliability")
    h = live_impact(P_ITEMS)["headline"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("MW connectable without new capacity", f"{h['mw_connectable']:,.0f}")
    m2.metric("Reliable MW delivered", f"{h['reliable_mw']:,.0f}")
    m3.metric("Compute lost", f"{h['compute_lost_pct']:.2f}%")
    m4.metric("Net CO2 change", f"{h['net_co2_change_t']:+,.0f} t")
    if c.design_changed(p):
        built_design_note()


section(headline)
t0, t1, t2, t3, t4, t5 = st.tabs(["Plan the week", "The grid", "The forecast", "The operation", "The design",
                                  "The impact"])


# ---------------------------------------------------------------- Tab 0: Plan the week
def right_now():
    need("grid_hourly")
    now = live_now(P_ITEMS)
    when = now["as_of_utc"].tz_convert(c.TZ_LOCAL).strftime("%b %d, %I %p CT")
    label = "Live from ERCOT" if now["source"] == "live" else "Last saved hour (live feed not connected or unavailable)"
    st.subheader(f"Right now: {when}")
    st.caption(label + ". The data center is modelled from the sidebar; no real site publishes its usage.")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("ERCOT load this hour", f"{now['load_mw']:,.0f} MW")
    r2.metric("Today's forecast peak", f"{now['forecast_peak_today_mw']:,.0f} MW")
    r3.metric("Room below the 2022-2024 record", f"{now['room_to_peak_mw']:,.0f} MW")
    if now["required_cut_mw"] > 0:
        srcs = ", ".join(f"{k} {v:,.0f} MW" for k, v in now["cut_by_source_mw"].items() if v > 0 and k != "shortfall")
        r4.metric("Our data center", f"Cutting {now['required_cut_mw']:,.0f} MW", help=srcs)
    else:
        r4.metric("Our data center", f"Full power, {now['facility_mw']:,.0f} MW",
                  help="Watch: today's forecast peak would need a cut" if now["status"] == "watch" else None)
    td = now["today"]
    fig = go.Figure()
    fig.add_scatter(x=local(td.ts_utc), y=td.load_mw, name="Load so far", line=dict(width=2))
    fig.add_scatter(x=local(td.ts_utc), y=td.forecast_mw, name="ERCOT forecast", line=dict(dash="dot"))
    fig.add_hline(y=now["planning_peak_mw"] - now["facility_mw"], line_dash="dash", line_color="red",
                  annotation_text="Cut starts here (2022-2024 record minus our data center)")
    fig.update_layout(height=220, margin=dict(t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)


def plan_the_week():
    need("grid_hourly")
    st.subheader("Plan the week")
    mode_label = st.radio("Week", ["Real peak week, Aug 2025", "Coming week (live)"], horizontal=True, key="week_mode")
    mode = "live" if mode_label.startswith("Coming") else "aug2025"
    wp, info = live_week_plan(P_ITEMS, mode)
    if info["note"]:
        st.info(info["note"])
    wp = wp.assign(local_ts=local(wp.ts_utc))
    days = list(dict.fromkeys(wp.local_ts.dt.date))
    d1, d2 = st.columns([2, 3])
    day = d1.selectbox("Day", days, format_func=lambda d: d.strftime("%a %b %d"), key="week_day")
    hour = d2.slider("Hour (Texas time)", 0, 23, 17, key="week_hour")
    sel = wp[(wp.local_ts.dt.date == day) & (wp.local_ts.dt.hour == hour)]
    if sel.empty:
        st.warning("No forecast for that hour.")
    else:
        r = sel.iloc[0]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Forecast load (our model)", f"{r.load_model_mw:,.0f} MW")
        k2.metric("ERCOT's forecast", "n/a" if pd.isna(r.load_ercot_mw) else f"{r.load_ercot_mw:,.0f} MW")
        k3.metric("Chance the grid is tight", f"{r.p_stress:.0%}")
        k4.metric("Cut planned", f"{r.planned_cut_mw:,.0f} MW")
        st.markdown("**What the model runs this hour**")
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Real-time inference", f"{r.realtime_mw * p['pue']:,.0f} MW", "never cut", delta_color="off")
        s2.metric("Batch", f"{r.batch_mw * p['pue']:,.0f} MW",
                  f"-{r.cut_batch_mw:,.0f} paused" if r.cut_batch_mw > 0 else None)
        s3.metric("Training", f"{r.training_mw * p['pue']:,.0f} MW",
                  f"-{r.cut_training_mw:,.0f} slowed" if r.cut_training_mw > 0 else None)
        state = "discharging" if r.batt_mw > 0 else "charging" if r.batt_mw < 0 else "idle"
        s4.metric("Battery", f"{state} {abs(r.batt_mw):,.0f} MW", f"{r.soc_mwh:,.0f} MWh stored", delta_color="off")
        s5.metric("Backup", f"{r.backup_mw:,.0f} MW")
        st.caption(f"Grid draw this hour: {r.dc_draw_mw:,.0f} MW of a normal {c.facility_mw(p):,.0f} MW.")
    line = peak() - c.facility_mw(p)
    fig = go.Figure()
    fig.add_scatter(x=wp.local_ts, y=wp.load_model_mw, name="Our forecast")
    fig.add_scatter(x=wp.local_ts, y=wp.load_ercot_mw, name="ERCOT forecast", line=dict(dash="dot"))
    fig.add_hline(y=line, line_dash="dash", line_color="red", annotation_text="Cut starts here")
    if not sel.empty:
        fig.add_vline(x=sel.local_ts.iloc[0], line_color="black", opacity=0.4)
    fig.update_layout(title="Load forecast for the week", height=300, margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)
    fig = go.Figure()
    for col, name in [("cut_batch_mw", "Batch paused"), ("cut_battery_mw", "Battery"), ("cut_backup_mw", "Backup"),
                      ("cut_training_mw", "Training slowed")]:
        fig.add_bar(x=wp.local_ts, y=wp[col], name=name)
    fig.add_scatter(x=wp.local_ts, y=wp.planned_cut_mw, name="Cut needed", line=dict(color="red"))
    fig.update_layout(barmode="stack", title="Where each MW of cut comes from, hour by hour", height=280,
                      margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)


with t0:
    section(right_now)
    section(plan_the_week)


# ---------------------------------------------------------------- Tab 1: The grid (M1)
def tab_grid():
    need("grid_hourly", "headroom")
    hr = load("headroom")
    fig = go.Figure(go.Scatter(x=hr.new_load_mw, y=hr.curtail_pct_energy, mode="lines", name="Energy curtailed"))
    fig.add_hline(y=0.5, line_dash="dash", annotation_text="0.5% (Duke benchmark)")
    fig.update_layout(title="New load the grid can host vs energy it must curtail", xaxis_title="New load (MW)",
                      yaxis_title="% of its energy curtailed", height=380)
    st.plotly_chart(fig, use_container_width=True)
    ask = live_ask(P_ITEMS)
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Events per year", f"{ask['events_per_year']:.0f}")
    a2.metric("Hours per year", f"{ask['hours_per_year']:.0f}")
    a3.metric("Longest event", f"{ask['longest_event_h']:.0f} h")
    a4.metric("Cut the grid asks for", f"{ask['k_ask_mw']:,.0f} MW",
              help=f"Covers {p['reliability_target']:.0%} of tight periods. In the single worst hour the whole site "
                   f"({ask['worst_hour_cut_mw']:,.0f} MW) must drop, because load sat exactly at the record.")
    # TODO C: required-cut histogram for this data center (PDF Tab 1).


with t1:
    section(tab_grid)


# ---------------------------------------------------------------- Tab 2: The forecast (M2)
def tab_forecast():
    need("grid_hourly", "forecast_test", "metrics")
    fc = load("forecast_test")
    fc = fc[fc.split == "test"]
    met = load("metrics")
    fig = go.Figure()
    fig.add_scatter(x=local(fc.ts_utc), y=fc.actual_mw, name="Actual", line=dict(width=1))
    fig.add_scatter(x=local(fc.ts_utc), y=fc.model_mw, name="Our forecast", line=dict(width=1))
    fig.add_scatter(x=local(fc.ts_utc), y=fc.ba_forecast_mw, name="ERCOT forecast", line=dict(width=1, dash="dot"))
    fig.add_hline(y=peak() - c.facility_mw(c.DEFAULTS), line_dash="dash", annotation_text="Stress line (default design)")
    for d in c.CP4_2025:
        fig.add_vline(x=d, line_color="red", opacity=0.3)
    fig.update_layout(title="Summer 2025, forecast a day ahead (red lines: 4CP peak days)", height=380)
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Stress hours and the line are for the default 1,000 MW design the forecast was scored on; "
               "sidebar changes don't move them.")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Recall on stress hours", f"{met['recall']:.0%}")
    s2.metric("Precision", f"{met['precision']:.0%}")
    s3.metric("MAPE: ours / ERCOT / naive", f"{met['mape_model']:.1f} / {met['mape_ba']:.1f} / {met['mape_naive']:.1f}")
    s4.metric("4CP days caught", f"{met['cp4_days_caught']} of {met['cp4_days_total']}")


with t2:
    section(tab_forecast)


# ---------------------------------------------------------------- Tab 3: The operation (M3, M4, M5)
def tab_operation():
    need("grid_hourly", "replay_hourly", "events", "reliability")
    rp = load("replay_hourly")
    test = rp[rp.split == "test"]
    flex = test[test.case == "flexible"]
    peak_ts = flex.loc[flex.load_mw.idxmax(), "ts_utc"]
    week = live_week(P_ITEMS, (peak_ts - pd.Timedelta(days=3)).floor("D"))
    fig = go.Figure()
    for col, name in [("realtime_mw", "Real-time inference"), ("batch_mw", "Batch"), ("training_mw", "Training")]:
        fig.add_bar(x=local(week.ts_utc), y=week[col] * p["pue"], name=name)
    fig.add_bar(x=local(week.ts_utc), y=-week.batt_mw, name="Battery (charge +, discharge -)")
    fig.add_bar(x=local(week.ts_utc), y=-week.backup_mw, name="Backup")
    fig.add_scatter(x=local(week.ts_utc), y=week.dc_draw_mw, name="Grid draw", line=dict(color="black"))
    fig.update_layout(barmode="relative", title="Peak week: workloads under the grid limit", height=400)
    st.plotly_chart(fig, use_container_width=True)
    fig = go.Figure()
    for case in ["rigid", "flexible"]:
        d = test[test.case == case]
        fig.add_scatter(x=local(d.ts_utc), y=d.grid_total_mw, name=f"ERCOT load, {case} data center", line=dict(width=1))
    fig.add_hline(y=peak(), line_dash="dash", annotation_text="Planning peak (2022-2024 record)")
    fig.update_layout(title="Total grid load, rigid vs flexible", height=340)
    st.plotly_chart(fig, use_container_width=True)
    ev = load("events")
    evf = ev[(ev.case == "flexible") & (ev.split == "test")]
    fig = go.Figure(go.Bar(x=evf.event_id, y=evf.delivered_mw, name="Delivered"))
    fig.add_hline(y=p["promise_mw"], line_dash="dash", annotation_text="Promise")
    fig.update_layout(title="Delivered MW per real event vs the promise", height=300)
    st.plotly_chart(fig, use_container_width=True)
    rel = load("reliability")
    st.info(f"Reliable flex: {rel['k_rel_mw']:,.0f} MW in {p['reliability_target']:.0%} of events. "
            f"Promise {p['promise_mw']:,.0f} MW {'holds' if rel['k_rel_mw'] >= p['promise_mw'] else 'does NOT hold'}.")


with t3:
    section(tab_operation)


# ---------------------------------------------------------------- Tab 4: The design (M5, M6)
def supply():
    sc = live_supply(P_ITEMS)
    sc = sc[sc.mw > 0].sort_values("cost_per_mwh")
    fig = go.Figure(go.Bar(x=sc.source, y=sc.mw, text=[f"${v:,.0f}/MWh" for v in sc.cost_per_mwh]))
    fig.update_layout(title="Supply curve: MW of flex by source, cheapest first", height=340)
    st.plotly_chart(fig, use_container_width=True)


def frontier():
    need("scenarios")
    sn = load("scenarios")
    fig = go.Figure(go.Scatter(x=sn.facility_mw, y=sn.compute_lost_pct, mode="markers",
                               marker=dict(color=sn.feasible.map({True: "green", False: "grey"}), size=10),
                               text=[f"Battery {b:.0f} MW" for b in sn.battery_mw], name="Designs"))
    best = sn[sn.is_cheapest]
    fig.add_scatter(x=best.facility_mw, y=best.compute_lost_pct, mode="markers", name="Cheapest feasible",
                    marker=dict(symbol="star", size=18, color="gold"))
    fig.update_layout(title="Design frontier: size vs compute lost", xaxis_title="Facility MW",
                      yaxis_title="Compute lost %", height=380)
    st.plotly_chart(fig, use_container_width=True)


with t4:
    section(supply)
    section(frontier)


# ---------------------------------------------------------------- Tab 5: The impact (M7)
def tab_impact():
    need("grid_hourly", "replay_hourly", "headroom", "reliability")
    imp = live_impact(P_ITEMS)
    cols = st.columns(4)
    for col, sec in zip(cols, ["grid", "data_center", "emissions", "ratepayers"]):
        with col:
            st.subheader(sec.replace("_", " ").title())
            for k in c.IMPACT_KEYS[sec]:
                st.write(f"**{k}**: rigid {imp[sec]['rigid'][k]:,.2f} | flexible {imp[sec]['flexible'][k]:,.2f}")
    built_design_note()
    with st.expander("Business case"):
        st.write("Flexing pays when its cost is below the value of getting power sooner. Deal pricing is next.")


with t5:
    section(tab_impact)

st.divider()
st.caption("Data: EIA-930 (ERCOT hourly demand, forecast, fuel mix), Open-Meteo (temperature). Data center and cost "
           "inputs are assumptions. Grid-wide view only (no substation limits); average not marginal emissions; "
           "actual temperatures stand in for weather forecasts.")
