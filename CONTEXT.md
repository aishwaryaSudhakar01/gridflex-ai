# GridFlex AI: Team Context (CONTEXT.md)

**Vibathon 2026, UCLA Anderson MSBA. Theme 03: Time to power.**
**Event:** 1:00 PM to 6:00 PM. **Hand in:** (1) a working prototype that runs live on real data, (2) a short written document that matches what the app shows. No slides, no video.
**Repo:** `gridflex-ai` (public GitHub). **Spec:** `docs/PoC_Spec.pdf` (Nandana, Nov 6). This file adapts that spec to 3 builders and 5 hours. Where they differ, this file wins.

---

## 0. Read this first

### If you are an AI assistant reading this
You are helping ONE member of a 4-person team. The human tells you their role (Builder A, B or C). Then:
1. Read this whole file, then `src/contracts.py`, then the stub files your role owns (section 9). Decisions in section 13 are final.
2. Build only that role's files. Start at their first unchecked step.
3. **Never change** a function name, argument, return key, file name, column name or param name. They are in `src/contracts.py` and the stubs. Other people's code imports them.
4. Read and write handoff files **only** with `contracts.read(name)` and `contracts.write(name, obj)`. Never `pd.read_csv("out/...")`.
5. Write complete, runnable files. No new libraries beyond `requirements.txt`. No new data sources.
6. Before saying "done", run `python -m pytest`. It must be green. If a test in `tests/` fails, fix your code, never the test.
7. If something is ambiguous, pick the simplest option consistent with this file, state it in one line, keep going.

### Roster (Logistics fills in names)
| Role | Name | Owns (only these files) | Spec modules |
|---|---|---|---|
| Logistics | Aish | `CONTEXT.md`, `README.md`, `docs/SUBMISSION.md` | none. Writes no code. |
| Builder A: Data + Forecast | ______ | `src/pipeline.py`, `src/grid_capacity.py`, `src/forecast.py`, `build.py`, `scripts/fetch_raw.py`, `src/contracts.py` (frozen, edits need all 3) | M0, M1, M2 |
| Builder B: Optimization | ______ | `src/scheduler.py`, `src/replay.py`, `src/flex_supply.py`, `src/design.py` | M3, M4, M5, M6 |
| Builder C: Impact + App | ______ | `src/impact.py`, `src/live.py`, `src/week_plan.py` (glue, already done), `app.py`, deployment | M7, M8, Plan the week |

Nobody edits `tests/`, `src/week_plan.py` signatures, or `mockups/`.

### First message to paste into your AI (after attaching this file and `src/contracts.py`)
- **Builder A:** "I am Builder A. Read CONTEXT.md and src/contracts.py. Raw data is in data/raw/. First print the column names of one EIA file. Then implement build_grid_hourly in src/pipeline.py (M0) using find_col, so `python build.py --only m0` writes the real data/grid_hourly.parquet."
- **Builder B:** "I am Builder B. Read CONTEXT.md and src/contracts.py. Implement scheduler.schedule and scheduler.schedule_week (M3) in src/scheduler.py with scipy linprog HiGHS, using contracts.source_costs for the objective. Keep every signature. `python -m pytest` must stay green; my tests run on a test-only grid until the real one lands."
- **Builder C:** "I am Builder C. Read CONTEXT.md, src/contracts.py and mockups/gridflex-app-mockup.html. app.py already runs and shows 'waiting' panels for parts not built yet. Make it match the mockup, keep every tab inside section(), then implement compute_impact (M7) in src/impact.py."

### Team rules (these are what make the pieces fit)
1. Only edit files you own (table above). Need a change elsewhere? Ask the owner in chat.
2. `python -m pytest` must be green before **every** push. Run `sh scripts/check.sh` (tests + pull + status). A red push breaks the live app for everyone.
3. `git pull --rebase` before every push. Push at least every 45 min. Commit messages start with your role: `[B] replay on real grid`.
4. One branch: `main`.
5. **No fake data, ever.** A file in `data/` or `out/` exists only when its owner's real code wrote it. Until then the app shows "Waiting for <owner>" and its tests skip with the owner's name. Tests check shapes on an in-memory test grid (`tests/fixtures.py`) that is never saved. Push your files with `git add -A data out src`.
6. Post "done" or "blocked on X" in chat at each checkpoint (section 10).
7. Contract changes (`src/contracts.py`) need all three builders to agree in chat. Builder A commits them. Then everyone pulls.

---

## 1. TLDR

The grid is not full. It is full for a few dozen hours a year. GridFlex AI forecasts those hours a day ahead, schedules an AI data center through them (shift batch work, use the battery, run backup, slow training, never touch customer-facing AI), proves it on real Texas summers, and measures what that does for the grid, the data center and emissions.

Four questions, one app:
1. **Capacity:** how much new load fits on ERCOT, and when is it tight? (M1 headroom, M2 forecast)
2. **Operation:** how should the data center run each hour? (M3 LP scheduler)
3. **Reliability and design:** how many MW can it reliably deliver, and what's the cheapest build? (M4 replay, M5 reliable MW, M6 design search)
4. **Impact:** what changes for the grid, the data center, emissions and ratepayers? (M7)
5. **Plan the week:** for any day and hour in the next 7 days, what will the load be and what should the data center run? (week-ahead forecast + the same LP scheduler)

**Analogy:** a highway is jammed for 40 hours a year. Instead of building a new lane, a trucking company agrees to stay off it during those hours. We forecast the jams, plan the trucks' routes around them, check on two real summers that the company actually kept its word, and count what everyone saved.

---

## 2. Problem and user
- **User:** a data center developer and the grid operator (ERCOT) negotiating a flexible connection. Secondary: ratepayers and regulators.
- **Problem:** a data center takes months to build and years to connect, because grids plan for their single worst hour. A flexible load could connect sooner, but nobody can show how often the tight hours happen, how the site would run through them, and whether its promise holds.
- **Evidence:** Duke's Nicholas Institute: 22 US grids could absorb ~98 GW of new load if it curtailed 0.5% of its energy, ~10 GW in ERCOT. Texas SB 6 (2025) requires new loads of 75 MW+ to accept emergency curtailment.
- **Our angle:** one open system that links forecast, operation, proof on real history and impact. Emerald AI (workload flexing), GridCARE (finding capacity) and Camus (utility software) each cover one piece.

---

## 3. How it works (flow; each arrow is a saved file)

```
scripts/fetch_raw.py ─► data/raw/ (EIA-930, Open-Meteo)
        │
   M0 pipeline ─► data/grid_hourly.parquet ─┬─► M1 grid_capacity ─► out/headroom.csv
                                            ├─► M2 forecast ─► out/forecast_test.csv, out/metrics.json
                                            │          │
                                            │          ▼
                                            └─► M4 replay (uses M3 scheduler each day) ─► out/replay_hourly.csv, out/events.csv
                                                       │
                                                       ├─► M5 flex_supply ─► out/reliability.json, out/supply_curve.csv
                                                       ├─► M6 design ─► out/scenarios.parquet   (should-ship)
                                                       ▼
                                                 M7 impact ─► out/impact.json
                                                       ▼
                                                 M8 app.py (reads only these files + fast live functions)
                                                       ▲
  live ERCOT + Open-Meteo forecast ─► week_plan (predict_week + scheduler.schedule) ─► Plan the week tab
```
No handoff file exists until its owner's real code writes it. Builders still start in parallel at 1:00: every function's exact inputs and outputs are fixed in `src/contracts.py`, and the tests check each builder's output against them on a test-only grid held in memory. The real ERCOT table lands about 1:50, and everything after it is real.

---

## 4. The data center model
| Workload | Default | Flex | How | Cost of flexing |
|---|---|---|---|---|
| Real-time inference | 20% of IT | Never | Always full power | none |
| Batch + fine-tuning | 30% of IT | In time | Pause in peak, catch up on idle GPUs within 24 h | ~$0 |
| Training | 50% of IT | In depth | Power-cap or pause | $1,000 per IT MWh lost |
| Battery | 100 MW x 4 h | Yes | Discharge | $20/MWh wear |
| Backup gen | 150 MW | Last resort | On-site gas | $150/MWh fuel + 0.5 t CO2/MWh |
| Cooling | x PUE 1.2 | Auto | Each IT MW cut saves PUE MW at the meter | none |

Idle GPUs: installed IT = `it_mw / gpu_util` (default 0.90), so there is ~111 MW of spare GPU for batch catch-up. Response times are assumptions.

---

## 5. Defaults and sliders
All defaults, slider ranges and design grids live in `src/contracts.py` (`DEFAULTS`, `SLIDERS`, `DESIGN_GRID_*`). app.py builds every slider from `SLIDERS` with `key=` the param name. Defaults are rough starting points, not forecasts.

---

## 6. Data (all real, all free, no accounts)

| Dataset | Source (in the kickoff deck) | Fields | Period | File |
|---|---|---|---|---|
| Hourly demand + ERCOT day-ahead forecast + net generation by fuel | EIA Hourly Grid Monitor (EIA-930) six-month BALANCE CSVs | Demand, Demand Forecast, Net Generation from each fuel | 2019 to 2025 | `data/raw/EIA930_BALANCE_*.csv` |
| Hourly temperature, 4 cities | Open-Meteo historical archive | temperature_2m, UTC | 2019 to Sep 2025 | `data/raw/weather_*.csv` |
| Live ERCOT load, last 8 days + ERCOT 7-day load forecast | ERCOT public data via the `gridstatus` library | load, load forecast | now | fetched live; never saved |
| 7-day temperature forecast, 4 cities | Open-Meteo forecast API (no key) | temperature_2m | next 8 days | fetched live; never saved |
| Fallback for EIA | PUDL (kickoff deck), cleaned EIA-930 | same | same | manual |

**6.1 Get it:** `python scripts/fetch_raw.py` (Builder A at 1:00 PM; 5 to 15 min). Raw files are gitignored. Only `data/grid_hourly.parquet` (~1 MB) is committed. That is the frozen snapshot the app runs on.
**6.2 Cleaning rules:** in the `src/pipeline.py` docstring. EIA times are hour-ending; subtract 1 hour.
**6.3 CO2:** EIA-930 BALANCE files carry fuel mix, not CO2. We estimate `co2_t_per_mwh` = fuel mix x factors (coal 1.00, gas 0.43, oil 0.80 t/MWh, others 0). Stated as an assumption.
**6.4 Planning peak rule (pre-decided, no debate):** P = max hourly load 2022 to 2024. At 1:45 PM Builder A counts test-summer (Jun to Sep 2025) hours with a required cut at default F. If fewer than 10, set `PLANNING_MARGIN = 0.97` in contracts.py (P becomes a 3% reserve margin below the record), commit, tell the team, and the doc states it. Reason: if 2025 never got close to the record, the test summer has nothing to test.
**6.5 Not used:** prices (stretch only), PNNL atlas, EIA-860/923, interconnection queues.

---

## 7. Formulas (implemented once in `src/contracts.py`; import them, never retype)
- `F = it_mw x pue` (normal draw). `F_max = it_mw / gpu_util x pue` (with catch-up).
- `P = planning_peak(grid)`.
- Required cut: `c_t = min(max(L_t + F - P, 0), F)` = `required_cut(L, F, P)`.
- Grid limit for the facility each hour: `clip(P - L_t, 0, F_max)` = `grid_limit(L, p, P)`. In every stress hour this equals `F - c_t` (the spec's constraint); in quiet hours it lets batch catch up.
- Event: run of consecutive hours with `c_t > 0` = `find_events(cut, ts)`.
- Summers and training cut-offs are Texas dates: `in_split(ts, split)`, `before_train_end(ts, split)`.
- Load with no blanks for the replay and planner: `filled_load(grid)`. Filled hours never count as events.
- Cost of each source of flex, $/MWh at the meter: `source_costs(p)`. The scheduler, supply curve and Right now panel all rank sources with it.
- The planning peak comes only from `planning_peak(grid)`. `metrics.json` stores the same number for the doc; a test checks they match.
- **The cut the grid asks for (K_ask)** = `ask_cut_mw(grid, p)`: big enough to cover 95% of tight periods in 2022 to 2024 (audit fix 5). Builder A and Builder B both call it; never compute it yourself.
- Important: at the hour where load equals the record (L = P) the required cut is the **whole facility**. That's why the worst hour is reported separately (`worst_hour_cut_mw`) and never used as the ask. Real-time inference (240 MW at the meter) must then come from battery + backup (100 + 150 = 250). So never demo with battery + backup below ~240 MW unless you want to show a failure.
- Scheduler LP, replay order, reliable MW, design cost, impact metrics: in each module's docstring (they follow PDF M3 to M7).

---

## 8. Interfaces (final; the stubs already return these shapes)
| Function | Called by | Returns |
|---|---|---|
| `pipeline.build_grid_hourly()` | build.py | df, schema `grid_hourly` |
| `grid_capacity.headroom_curve(grid)` | build.py | df, schema `headroom` |
| `grid_capacity.data_center_ask(grid, p)` | app (live, < 0.5 s) | dict: k_ask_mw, events_per_year, hours_per_year, longest_event_h, planning_peak_mw |
| `forecast.run_forecast(grid)` | build.py | (df `forecast_test`, dict `metrics`) |
| `scheduler.schedule(hours, p, P, soc0_mwh, backlog0_mwh)` | replay, design | (plan df with `PLAN_COLS`, state dict) |
| `scheduler.schedule_week(grid, forecast, p, start_utc)` | app (live, < 1 s) | df, schema `replay_hourly`, case "week" |
| `replay.run_replay(grid, forecast, p, split, cases)` | build.py, design | (df `replay_hourly`, df `events`) |
| `flex_supply.reliable_flex(grid, replay, events, p)` | build.py | dict `reliability` |
| `flex_supply.supply_curve(p)` | app (live, < 0.1 s) | df `supply_curve` |
| `design.design_search(grid, forecast, p, space)` | build.py | df `scenarios` |
| `impact.compute_impact(p, grid, replay, headroom, reliability)` | build.py, app (live, < 1 s) | dict `impact` |
| `live.latest_snapshot(grid, p, allow_network=True)` | app (cached 5 min, never raises) | dict with `live.LIVE_KEYS` |
| `live.fetch_week_inputs()` | week_plan | (history df ts_utc, load_mw; future df ts_utc, temp_c, load_ercot_mw, 168 rows) |
| `forecast.train_week_model(grid)` | build.py | fitted model or None; saved to `contracts.WEEK_MODEL` |
| `forecast.predict_week(history, future, P, F_mw, model=None)` | week_plan (live, < 1 s) | df ts_utc, load_model_mw, p_stress (168 rows) |
| `week_plan.plan_week(grid, p, mode, allow_network=True)` | app (cached 1 h, never raises on live problems) | (df with `contracts.WEEK_PLAN_COLS`, info dict) |

Columns, keys, units and allowed values: `SCHEMAS`, `IMPACT_KEYS`, `CASES`, `SOURCES` in `src/contracts.py`. `contracts.write()` refuses anything that doesn't match.

---

## 9. Roles

### Logistics: Aish (no code)
- [ ] L1. Fill in roster names above. Create the public repo `gridflex-ai`, upload this starter kit, add builders as collaborators.
- [ ] L2. Send `docs/TEAM_MESSAGE.md`.
- [ ] L3. 1:00 kickoff (10 min): everyone ran `python -m pytest` green (skips are fine); C posts the live URL; confirm nobody changes contracts without the group.
- [ ] L4. 1:10 to 3:00: draft `docs/SUBMISSION.md` sections that need no numbers.
- [ ] L5. 3:00 checkpoint 1 (section 10). 4:30 checkpoint 2 + feature freeze.
- [ ] L6. 4:30 to 5:30: test the live app like a judge: every tab, move every slider, reset; log bugs to owners; take screenshots. Ask a builder to run `python -m pytest` and send the SKIPPED list: by now only should-ship or stretch items may be skipped. Copy which acceptance tests pass into the doc.
- [ ] L6b. Before 1 PM: confirm with the organizers how the written doc is handed in (PDF, Google Doc, form) and any length limit. Trim `docs/SUBMISSION.md` to fit.
- [ ] L7. 5:30: fill numbers into the doc from `out/metrics.json`, `out/reliability.json`, `out/impact.json`. Submit by 5:50.

### Builder A: Data + Forecast (M0, M1, M2)
- [ ] A0. 1:00 sharp: `python scripts/fetch_raw.py` (runs in the background while you read).
- [ ] A1. M0 `src/pipeline.py` per its docstring. `python build.py --only m0`. Push `data/grid_hourly.parquet`. **Target 1:50.** Post P, the test-summer stress-hour count (apply rule 6.4), and the 2025 is_4cp dates vs `CP4_2025`.
- [ ] A2. M1 `src/grid_capacity.py`. `python build.py --only m1`. Check A1 (headroom at 0.5% vs Duke ~10 GW). **Target 2:30.**
- [ ] A3. M2 `src/forecast.py`. `python build.py --only m2`. **Target 3:45.** Short on time at 3:30? Ship the linear model only.
- [ ] A3b. Week-ahead model: `train_week_model` + `predict_week` in `src/forecast.py` per its docstring; add `mape_week_model` to metrics. `python build.py --only m2` saves `out/week_model.joblib`; commit it. **Target 4:15.** Skip if behind: the stub (load 7 days earlier) still works.
- [ ] A4. 4:30: `python build.py` full run; commit `data/` and `out/`. Post the "NOT BUILT YET" line.

### Builder B: Optimization (M3, M4, M5, M6)
- [ ] B1. M3 `scheduler.schedule` + `schedule_week` with `scipy.optimize.linprog(method="highs")`, tested on the in-memory test grid until A's real file lands. **Target 2:15.**
- [ ] B2. M4 `replay.run_replay`: first with perfect foresight (forecast=None, plans on actual load) on the real grid. Don't wait for A's forecast. `python build.py --only m4`. Check A6, A7, A9. **Target 3:00.**
- [ ] B3. M5 `flex_supply`. `python build.py --only m5`. Check A5. **Target 3:45.**
- [ ] B3b. Make sure `scheduler.schedule` solves 168 hours in < 1 s. The Plan the week tab calls it directly with the week's forecast. Nothing else to build.
- [ ] B4. Switch case "flexible" to forecast-driven (load_plan = model_mw + cut_buffer_mw); keep "perfect" as a comparison case. **Target 4:15.**
- [ ] B5. If green at 4:15: M6 on `DESIGN_GRID_REDUCED` (12 runs). Check A8.

### Builder C: Impact + App (M7, M8)
- [ ] C0. 1:00: deploy the skeleton on share.streamlit.io (repo `gridflex-ai`, branch `main`, file `app.py`, Advanced settings > Python 3.12). Post the URL. The app shows "Waiting" panels until data lands; that's expected. **Target 1:15.**
- [ ] C1. Make app.py match `mockups/gridflex-app-mockup.html` using the test grid locally and real files as they land: header numbers, 6 tabs, sidebar, footer. Keep every tab inside `section()`. **Target 2:30.**
- [ ] C2. M7 `src/impact.py` per its docstring. `python build.py --only m7`. **Target 3:30.**
- [ ] C2b. Plan the week tab is already wired (`src/week_plan.py` + tab in app.py). It works offline on the real peak week of Aug 2025. To make "Coming week (live)" real: implement `live.fetch_week_inputs()` (gridstatus + Open-Meteo forecast API) and `live._fetch_live()` per their docstrings. Both fall back automatically, so they can never break the app. **Target 4:15.** Skip live if behind.
- [ ] C3. Swap to real files as they land (nothing to change if you only used `contracts.read`). Polish labels, units, Central time. **Target 4:30.**
- [ ] C4. After freeze: check load < 15 s and sliders < 3 s on the public URL (A10).

---

## 10. Timeline (event day)
| Time | What | Who |
|---|---|---|
| 1:00 to 1:10 | Kickoff. `python -m pytest` green on every laptop (skips are fine). A starts the download. C deploys | All |
| 1:10 to 2:15 | A: M0 real parquet (1:50), then M1. B: M3 scheduler. C: app to mockup | A, B, C |
| 2:15 to 3:00 | A: M1. B: M4 replay on real grid (perfect foresight). C: app, start M7 | A, B, C |
| **3:00** | **Checkpoint 1:** real `grid_hourly` and `headroom`; replay runs on real data; app deployed with 6 tabs; pytest green | Aish |
| 3:00 to 4:30 | A: M2 forecast. B: M5, then forecast-driven replay, then M6. C: M7, wire real files | A, B, C |
| **4:30** | **Checkpoint 2 + feature freeze.** A runs `python build.py`, commits data/ and out/ | Aish, A |
| 4:30 to 5:30 | Bug fixes only. Aish tests and screenshots | All |
| 5:30 to 5:50 | Final numbers into the doc. Submit | Aish |

### Must ship / should ship / cut order
- **Must ship:** M0, M1, M2 (linear model OK), M3, M4 (perfect foresight OK), M5, M7 (grid, data center, emissions), M8 deployed. Tests A1 to A3, A6, A7, A9, A10.
- **Should ship:** live mode of Plan the week (C2b) and the week-ahead model (A3b), forecast-driven replay, A5 out-of-sample check, A4 4CP check, ratepayer metrics, M6 reduced grid (A8).
- **Stretch (skip unless everything is green at 4:15):** carbon-price scheduling demo, +/-2 C noise case, full design grid, prices, PJM.
- **Cut order if behind:** stretch, then live mode of Plan the week (the Aug 2025 mode stays), then the week-ahead model (stub stays), then M6, then forecast-driven replay (say "perfect foresight" in the doc), then ratepayer metrics.
- **Never cut:** the replay, the impact tab, the deployed app.

---

## 11. Judging criteria map (kickoff deck)
| Criterion | Weight | Where we win it |
|---|---|---|
| Problem and user | 15% | Section 2; doc section 1 |
| Business value | 20% | Plan the week (an operator's actual daily tool); Impact tab: capacity avoided ($/yr), months earlier online, compute lost; business case panel |
| Working prototype | 15% | Live URL, runs on the frozen real snapshot, sliders respond. Tie-break: ran live |
| Data and analysis | 10% | Real EIA-930 + weather, held-out 2025 summer, acceptance tests A1 to A10 |
| Originality | 10% | Links forecast + optimization + proof on history + impact in one open tool |
| Smart use of AI | 10% | ML day-ahead and week-ahead forecasts vs ERCOT's own and naive; LP optimizer picks the action every hour |
| Feasibility and risks | 10% | Section 12 limits, stated up front; reliable MW sized on real events |
| Pitch and document | 10% | `docs/SUBMISSION.md`, numbers match the app exactly |

---

## 12. Assumptions and limits (state them; never hide them)
| Limit | Why it matters | How we handle it |
|---|---|---|
| Grid-wide view only | Real connections also hit substation and line limits | Results are an upper bound; local studies next |
| Average, not marginal, CO2; CO2 estimated from fuel mix | Understates carbon saved at peaks | Labelled |
| Actual temps stand in for weather forecasts | Overstates forecast accuracy | Labelled; +/-2 C run is stretch |
| Cost defaults are rough | Change which source the optimizer picks | Every figure is a slider |
| Compute scales linearly with power | GPU throughput at lower power isn't proportional | Labelled |
| Backup permit hours | Air permits cap run-hours | Backup MW is a slider; permit hours future scope |
| Design search is a grid | May miss the exact best design | Labelled |
| Will operators curtail? | Market monitors doubt it in emergencies | Reliable MW is sized on what real events allowed |
| Impact tab live values use the default-design replay | Sliders change costs, not the replay | Labelled under the tab |

---

## 13. Decisions already made (don't reopen)
1. ERCOT only. Summer 2024 = tuning, summer 2025 = held-out test.
2. Streamlit Community Cloud from public GitHub. App reads only committed files.
3. scipy HiGHS for the LP. scikit-learn for the forecast. No other new libraries.
4. Everything UTC hour-beginning in files; Central only on screen.
5. Workload shares 20/30/50, 1,000 MW IT, PUE 1.2, battery 100 MW x 4 h, backup 150 MW.
6. Planning-peak rule 6.4.
7. No slides, no video. Prototype + written doc only.
8. Moving work between data centers on different grids is a NEXT STEP in the doc only. Don't build it.

### Changes from the PDF spec (why)
- No fake or synthetic data in the repo or app. Files appear only when real code writes them; tests use an in-memory test grid that is never saved.
- "The cut the grid asks for" covers 95% of tight periods, not the single worst hour (the PDF's version always equals the whole site, so it says nothing). Agreed with Nandana: ____ (Aish to confirm).
- Design sliders (size, PUE, mix, battery, backup) change only the live views (Plan the week, Right now, peak-week view, supply curve). Headline and impact numbers stay at the built design with a visible label; cost sliders still update them (`contracts.impact_params`).
- Exact library versions on Python 3.12 everywhere, so the saved week model loads on Streamlit Cloud.
- Every app tab is isolated: an error or a missing file shows inside that tab, never a blank app.
- 4 roles merged into 3 builders + logistics (Aish writes no code).
- Slides, video, pitch and demo script dropped: hand-in is prototype + doc.
- Added `gpu_util` and `grid_limit` so "batch catches up on idle GPUs" is actually possible (the PDF's cap of F alone leaves no room).
- `forecast_test.csv` holds both summers (`split` column), because the replay needs tuning-summer forecasts too.
- Extra columns: `case`, `split`, `planned_cut_mw`, `compute_*` (needed for A9), `is_stress`, `n_events`, `flagged_day_ahead`, `is_cheapest`.
- CO2 estimated from EIA-930 fuel mix (BALANCE files have no CO2 column).
- Prices dropped from must-ship (stretch only, no extra download).
- Added a Plan the week tab (not in the PDF): a week-ahead load forecast plus the scheduler's choice for every hour, with day and hour filters, and a Right now panel on top. Two modes: coming week (live) and the real peak week of Aug 2025 (offline). Live failures fall back to Aug 2025, so A10 still holds. In November the live week will show no cuts; demo the Aug 2025 mode.

## Sources
EIA-930 (EIA Hourly Grid Monitor); PUDL (Catalyst Cooperative); Open-Meteo Historical Weather API; Duke Nicholas Institute via POWER Magazine; Utility Dive on Texas SB 6; Hashrate Index on 4CP; S&P Global on PJM capacity prices; Utility Dive on GridCARE; SiliconANGLE on Emerald AI. Full list in `docs/PoC_Spec.pdf`.
