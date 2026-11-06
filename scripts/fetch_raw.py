"""Downloads every raw dataset. No API keys, no accounts. OWNER: Builder A. Run at 1:00 PM (or earlier).
Run from repo root: python scripts/fetch_raw.py     (about 5 to 15 min, ~600 MB, depends on Wi-Fi)
Writes to data/raw/ (gitignored, never commit raw files):
  EIA930_BALANCE_<year>_<Jan_Jun|Jul_Dec>.csv   14 files, 2019 to 2025, EIA Hourly Grid Monitor (EIA-930)
  weather_<city>.csv                             4 files, Open-Meteo historical archive, UTC, hourly temperature_2m
Safe to rerun: skips files that already exist.
Fallback if eia.gov is down: PUDL's cleaned EIA-930 (https://data.catalyst.coop), same fields.
"""
import sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, ".")
from src.contracts import RAW, CITIES

EIA = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_{y}_{h}.csv"
METEO = ("https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
         "&start_date=2018-12-31&end_date=2025-10-01&hourly=temperature_2m&timezone=UTC&format=csv")


def get(url: str, dest: Path):
    if dest.exists() and dest.stat().st_size > 1000:
        print("skip", dest.name); return
    for attempt in range(3):
        try:
            t = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "gridflex-ai/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                f.write(r.read())
            print(f"ok   {dest.name}  {dest.stat().st_size/1e6:.1f} MB  {time.time()-t:.0f}s"); return
        except Exception as e:
            print(f"retry {dest.name}: {e}"); time.sleep(5)
    print(f"FAILED {dest.name}. Download it by hand from {url}")


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for y in range(2019, 2026):
        for h in ("Jan_Jun", "Jul_Dec"):
            get(EIA.format(y=y, h=h), RAW / f"EIA930_BALANCE_{y}_{h}.csv")
    for name, (lat, lon, _) in CITIES.items():
        get(METEO.format(lat=lat, lon=lon), RAW / f"weather_{name.replace(' ', '_')}.csv")
    print("Done. Open-Meteo CSVs have 3 metadata lines before the header: read with skiprows=3.")
