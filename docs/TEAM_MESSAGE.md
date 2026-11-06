Hi all, the GridFlex AI repo is ready: <REPO LINK>

Before 1:00 PM (10 min):
1. Clone it, `pip install -r requirements.txt`, run `python -m pytest` (Python 3.12). It should say "passed" with many "skipped": skipped means not built yet, with the owner's name. Tell me if anything fails.
2. Open `CONTEXT.md`. Find your role in section 0 and section 9.
3. Open `mockups/gridflex-app-mockup.html` in a browser. That's what we're submitting.

Roles:
- Builder A, Data + Forecast: <NAME>. At 1:00 sharp run `python scripts/fetch_raw.py`.
- Builder B, Optimization: <NAME>.
- Builder C, Impact + App: <NAME>. Deploy the app at 1:00 and post the link.

Three rules that keep everything compatible:
1. Only edit your own files (CONTEXT.md section 0).
2. `sh scripts/check.sh` must be green before every push.
3. Read and write data only with `contracts.read()` / `contracts.write()`. Never retype a column name.

Give your AI CONTEXT.md and src/contracts.py, then paste the first message for your role from CONTEXT.md section 0.
New: a Plan the week tab (CONTEXT.md, steps A3b, B3b, C2b). It already works offline on Aug 2025.

Checkpoints: 3:00 PM and 4:30 PM (feature freeze). Submit by 5:50.
