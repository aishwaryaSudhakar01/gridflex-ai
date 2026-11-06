# GridFlex AI
Vibathon 2026, UCLA Anderson MSBA, Theme 03 "Time to power". **Read `CONTEXT.md` first.**

Forecast when the Texas grid is tight, schedule an AI data center through those hours, prove it on real summers, measure the impact.

## Setup (Python 3.12, from repo root)
    pip install -r requirements.txt
    python -m pytest             # must be green before every push (skips = parts not built yet)
    streamlit run app.py

## Data (Builder A, 1:00 PM)
    python scripts/fetch_raw.py  # EIA-930 + Open-Meteo, no keys, 5-15 min, into data/raw/ (gitignored)
    python build.py              # M0 -> M7, writes data/grid_hourly.parquet and out/

## Status
There is no fake data. `data/` and `out/` start empty and fill as each builder's real code runs. `python build.py` prints "NOT BUILT YET" with owners; the app shows "Waiting for <owner>" panels; `python -m pytest` lists skipped parts with owners.

## Layout
    CONTEXT.md            team spec (roles, rules, timeline)
    src/contracts.py      paths, columns, params, shared formulas, read()/write()  <- everyone imports this
    src/*.py              M0-M7 stubs with final signatures and task docstrings
    app.py                Streamlit app (M8)
    build.py              offline pipeline
    tests/                contract, interface, app-boot and acceptance (A1-A10) tests
    mockups/              what the final app should look like
    docs/                 PDF spec, submission doc template, team message
