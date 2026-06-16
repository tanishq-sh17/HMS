---
description: Workflow 1 / Sub-Agent 1 — Runs the fetch_dependabot_alerts.py script to pull open Dependabot alerts from all configured GitHub repos, sort them by service + severity, and export to a timestamped Excel file.
tools:
  - runCommand
---

# W1 Sub-Agent 1 — Fetcher

You are the fetcher sub-agent in Workflow 1.
Your job is to run the prebuilt script which handles fetching, sorting, and Excel creation in one step.

## Steps

### 1. Install dependencies (if not already installed)
```bash
pip install requests openpyxl python-dotenv
```

### 2. Run the script from the repo root
```bash
cd C:\Users\TanishqShrivas\DummyProj\GHAS-dummy-projects\HMS
python .github/scripts/fetch_dependabot_alerts.py
```

The script will:
- Load `GITHUB_TOKEN` from `.env` at the repo root
- Fetch all open Maven Dependabot alerts from all configured repos
- Sort alerts by service name (A→Z), then by severity (CRITICAL→HIGH→MEDIUM→LOW)
- Export to `dependabot_alerts_<YYYYMMDD_HHMMSS>.xlsx` in the repo root with an Alerts sheet and a Summary sheet

### 3. Resolve the output file path
After the script completes, resolve the exact path of the generated file:
```powershell
Get-ChildItem "C:\Users\TanishqShrivas\DummyProj\GHAS-dummy-projects\HMS\dependabot_alerts_*.xlsx" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
```

### 4. Verify output
- Confirm the file exists and has data rows (not just a header)
- If the file is missing or empty → STOP and report to orchestrator

## Output to pass to @w1-sorter
- Full path to the Excel file (e.g. `C:\...\dependabot_alerts_20260616_142048.xlsx`)
- Total number of alerts fetched
- Count per severity (CRITICAL / HIGH / MEDIUM / LOW)

## Failure conditions
- `GITHUB_TOKEN` not set in `.env` → stop, tell the user
- Script throws an error → stop, return the exact error message
- Output file empty or missing → stop, report "No open Maven Dependabot alerts found"
