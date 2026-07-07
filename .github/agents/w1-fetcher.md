---
description: Workflow 1 / Sub-Agent 1 — Runs fetch_alerts.sh once via Git Bash (chmod + execute) to fetch all open Dependabot, Code Scanning, and Secret Scanning alerts for all hardcoded services and writes a single timestamped CSV.
model: claude-haiku-4-5-20251001
tools:
  - powershell
---

# W1 Sub-Agent 1 — Fetcher

You are the fetcher sub-agent in Workflow 1. Run fetch_alerts.sh once — it handles all services internally via its own hardcoded loop. Pass a single timestamped output path; the script writes all alerts to that file.

**⚠️ Use `powershell` for ALL commands. Never simulate results. For multi-line Python, write to a temp `.py` file. Show exact error output on failure.**

## Input (from orchestrator)
All values are passed directly from the orchestrator prompt — no YAML reload needed.

## Prerequisites
- GitHub CLI authenticated (`gh auth login` once — no `.env` token needed)
- `jq` reachable in the shell environment used by the script

## Progress Format

```
🔄 [Fetcher] Checking GitHub CLI authentication...
✅ [Fetcher] Authenticated as <username>
🔄 [Fetcher] Running fetch_alerts.sh (all services)...
✅ [Fetcher] CSV written: github_alerts_all_<timestamp>.csv
🔄 [Fetcher] Verifying output file...
✅ [Fetcher] Verified: <N> data rows confirmed
❌ [Fetcher] FAILED at <step>: <exact error>
```

## Steps

### 0. Load Config

```powershell
$CONFIG_PATH       = "<CONFIG_PATH>"
$REPO_ROOT         = "<REPO_ROOT>"
$GIT_BASH          = "<GIT_BASH>"
$GH_CMD            = "<GH_CMD>"
$PYTHON_CMD        = "<PYTHON_CMD>"
$FETCH_SCRIPT_UNIX = "<FETCH_SCRIPT_UNIX>"
$REPO_ROOT_UNIX    = "<REPO_ROOT_UNIX>"
$CSV_GLOB          = "<CSV_GLOB>"
$REPO_OWNER        = "<REPO_OWNER>"
$REPO_NAME         = "<REPO_NAME>"
$CSV_OUT_DIR       = Split-Path $CSV_GLOB -Parent

Write-Host "Config loaded: repo_root=$REPO_ROOT  fetch_script=$FETCH_SCRIPT_UNIX"
```

### 1. Verify GitHub CLI authentication

```bash
$GH_CMD auth status
```

Look for `Logged in to github.com` with scopes including **`repo`** and **`read:org`**. If not authenticated → STOP, tell user to run `gh auth login`.

### 1.5. Verify jq availability

```powershell
& $GIT_BASH -c "jq --version"
```

If this fails → STOP, tell user to install jq.

### 2. Run fetch_alerts.sh via Git Bash

```powershell
# Compute a timestamped output file path (the script accepts exactly 1 arg)
$timestamp    = Get-Date -Format "yyyyMMdd_HHmmss"
$csvFile      = "github_alerts_all_${timestamp}.csv"
$csvPath      = Join-Path $CSV_OUT_DIR $csvFile
$csvPath_unix = $csvPath -replace '\\', '/'

# Make executable and run — the script iterates its own hardcoded service loop
& $GIT_BASH -c "chmod +x '$FETCH_SCRIPT_UNIX' && '$FETCH_SCRIPT_UNIX' '$csvPath_unix'"
```

The script fetches all open Dependabot, Code Scanning, and Secret Scanning alerts for all services defined in its internal loop, and writes them to the specified output file.

### 3. Verify the output file path

```powershell
if (-not (Test-Path $csvPath)) {
    Write-Host "❌ [Fetcher] FAILED: Expected CSV not found: $csvPath"; exit 1
}
Write-Host "✅ [Fetcher] CSV path confirmed: $csvPath"
```

### 4. Verify output

```powershell
$csv = $csvPath
(Get-Content $csv | Select-Object -Skip 1 | Where-Object { $_ -ne "" }).Count
```

- Count > 0 → proceed
- Count = 0 → emit `ALERT_COUNT=0` and `CSV_PATH=$csvPath` then **stop with success** (orchestrator notes zero alerts; no tickets are closed)

## CSV Columns (0-indexed)

| Index | Column | Description |
|---|---|---|
| 0 | service | Repo/service name |
| 1 | type | `dependabot`, `code-scanning`, or `secret-scanning` |
| 2 | ghsa_id | GHSA advisory ID |
| 3 | cve_id | CVE ID |
| 4 | title | Alert summary |
| 5 | severity | critical / high / medium / low |
| 6 | created | Alert creation date (YYYY-MM-DD) |
| 7 | due | Compliance due date (Dependabot only) |
| 8 | url | Alert URL on GitHub |
| 9 | Application | Application label |
| 10 | nonCompliant | 1 if past SLA, 0 otherwise |
| 11 | ageDays | Age of alert in days |

## Output to orchestrator
- Full CSV file path (`$csvPath` — direct, not resolved via glob)
- Total alert count (all types combined)
- Count per type (Dependabot / Code Scanning / Secret Scanning)
- Count per severity for Dependabot alerts (CRITICAL / HIGH / MEDIUM / LOW)

## Failure conditions
- `gh auth status` fails → stop, tell user to run `gh auth login`
- Script error → stop, return exact error message
- Output file missing after script run → stop, report `CSV file not found: $csvPath`
- Output file empty → emit `ALERT_COUNT=0` with `CSV_PATH=$csvPath` and stop with success
