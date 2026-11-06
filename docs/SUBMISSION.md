# GridFlex AI: forecasting the grid's tight hours and running an AI data center through them

Team: ______ · Theme 03, Time to power · Live app: ______ · Code: ______

> Owner: Aish. Every `{key}` comes from a file in `out/`. Copy numbers from the LIVE app at default settings so the doc matches the demo exactly. Delete these notes before submitting. Target length: 2 to 3 pages.

## 1. Problem and user
A data center takes months to build but years to connect, because grids are planned for their single worst hour. Our user is a data center developer negotiating a flexible connection with ERCOT, and ERCOT checking whether the promise is real. They need three answers: when is the grid actually tight, how would the site run through those hours, and does the promise hold.

## 2. What we built
One app on real ERCOT data that forecasts tight hours a day ahead, optimizes how a 1,000 MW AI data center runs each hour, replays two real summers to prove it, and reports the impact.
[Screenshot: header + Tab 3 peak week]

## 3. Data and method
- Data: EIA-930 hourly ERCOT demand, ERCOT's own day-ahead forecast and fuel mix (2019 to Sep 2025); Open-Meteo hourly temperature for Dallas, Houston, San Antonio, Austin. Frozen snapshot in the repo.
- Planning peak P = {metrics.planning_peak_mw} MW (record load 2022 to 2024{ ", with a 3% reserve margin" if PLANNING_MARGIN = 0.97 }).
- Forecast: linear model on lags and degree-hours plus gradient-boosted trees on its errors. Trained to May 2025, tested on Jun to Sep 2025, never seen.
- Optimization: a linear program each day chooses batch timing, battery, backup and training power at least cost, keeping real-time AI whole.
- Proof: day-by-day replay of summer 2024 (tuning) and summer 2025 (test), forecast-driven{ / perfect foresight if B4 was cut }.

## 4. Results (summer 2025, held out)
| Question | Result |
|---|---|
| Grid headroom at 0.5% energy curtailed | {headline.mw_connectable} MW (Duke: ~10,000) |
| Hours this data center must cut per year | {ask.hours_per_year} in {ask.events_per_year} events |
| Cut the grid asks for (covers 95% of tight periods) | {ask.k_ask_mw} MW (worst single hour: the whole site, {ask.worst_hour_cut_mw} MW) |
| Stress hours caught a day ahead | recall {metrics.recall}, precision {metrics.precision} |
| Forecast error (MAPE): ours / ERCOT / naive | {mape_model} / {mape_ba} / {mape_naive} |
| Week-ahead forecast error (MAPE) | {mape_week_model} |
| 2025 system peak days caught | {cp4_days_caught} of 4 |
| Reliable flex (95% of events) | {reliability.k_rel_mw} MW; promise of {promise_mw} MW {holds / doesn't} |
| Hours over the grid limit | rigid {grid.rigid.hours_over_planning_peak}, flexible {grid.flexible.hours_over_planning_peak} |
| Real-time AI served | 100% of hours |
| Compute lost | {data_center.flexible.compute_lost_pct}% |
[Screenshot: Tab 2 forecast] [Screenshot: Tab 5 impact]

## 4b. Plan the week
For any day and hour in the coming week, the app forecasts load and picks what the data center runs: batch, battery, backup, training. On the real peak week of Aug 2025, it planned {n} hours of cuts and kept real-time AI whole. [Screenshot: Plan the week, Aug 2025, peak hour selected]

## 5. Business value
- Grid: {grid.flexible.firm_capacity_avoided_mw} MW of firm capacity avoided, about ${firm_capacity_avoided_usd_yr} a year at $120/kW-year.
- Data center: online about {months_earlier_online} months sooner, worth {extra_compute_mwh} MWh of compute, for {compute_lost_pct}% compute lost.
- Ratepayers: {avoided_cost_usd_per_mwh} $/MWh avoided across ERCOT load. Side effect stated openly: cutting in 4CP hours shifts transmission cost to others.
- Emissions: net {headline.net_co2_change_t} t CO2 (cleaner batch timing vs dirtier backup hours).
- The deal test: flexing costs less than waiting for power. Pricing that deal is the next step.

## 6. Use of AI
The day-ahead stress forecast is the AI component, scored against ERCOT's own forecast and a naive baseline on a held-out summer. The optimizer turns that forecast into an hour-by-hour plan.

## 7. Feasibility, risks and limits
Grid-wide view only (no substation limits); average not marginal emissions, estimated from fuel mix; actual temperatures stand in for weather forecasts; cost defaults are rough and are all sliders; compute assumed linear in power; backup permits not modelled; design search is a grid; operators may not curtail in a real emergency, so reliable MW is sized on what real events allowed.

## 8. Acceptance tests
| Test | Result |
|---|---|
| A1 headroom matches Duke's order of magnitude | pass / fail |
| A2 recall >= 90% | |
| A3 beats naive, near ERCOT | |
| A4 all four 4CP days | |
| A5 reliable MW holds out of sample | |
| A6 0 hours over limit | |
| A7 real-time AI 100% | |
| A8 cheapest design found | |
| A9 compute accounting balances | |
| A10 loads < 15 s | |

## 9. Next steps
- **Moving work between data centers on different grids.** When ERCOT is tight, batch and training work could run at a sister site on another grid instead of pausing. Real-time AI stays put (latency). The grid side needs no new data: the EIA-930 files we already use cover every US grid. First test: in ERCOT's tight hours, how often did a second grid have room at the same time, and how often did one heat wave tighten both? Needs assumptions for what isn't public: which sites one company owns, their spare GPUs, and network limits.
- Deal pricing ($/kW-year, per MWh, or months earlier).
- Local substation and line limits.
- Marginal emissions and real prices in the optimizer.
- More regions (PJM, MISO).
