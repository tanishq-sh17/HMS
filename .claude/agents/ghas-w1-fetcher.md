---
name: ghas-w1-fetcher
description: Workflow 1 / Sub-Agent 1 for GHAS vulnerability management. Runs the fetch_dependabot_alerts.py script to pull open Dependabot alerts from all configured GitHub repos and export them to a timestamped Excel file. Requires GITHUB_TOKEN env var.
tools: Bash, Read, Write
---

# W1 Sub-Agent 1 — Fetcher

You are the fetcher sub-agent in Workflow 1.
Your only job is to run the fetch script and confirm the Excel file was created.

## Steps

### 1. Install dependencies
```bash
pip install requests openpyxl python-dotenv
```

### 2. Run the script
```bash
python .github/scripts/fetch_dependabot_alerts.py
```

### 3. Verify output
- Confirm a file named `dependabot_alerts_<timestamp>.xlsx` was created in the working directory
- Confirm it has data rows (not just a header)
- If the file is missing or empty → STOP and report to orchestrator

## Output to return to orchestrator
- Excel file path (full path including filename)
- Total number of alerts fetched
- Count per severity (CRITICAL / HIGH / MEDIUM / LOW)

## Failure conditions
- `GITHUB_TOKEN` not set → stop, tell the caller to set the environment variable with: `export GITHUB_TOKEN=<your-token>`
- Script throws an error → stop, return the exact error message
- Output file empty → stop, report "No open Maven Dependabot alerts found"
