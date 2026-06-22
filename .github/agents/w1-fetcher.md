---
description: Workflow 1 / Sub-Agent 1 — Runs the fetch_alerts.sh shell script to pull open Dependabot, Code Scanning, and Secret Scanning alerts from all configured GitHub repos and export to a CSV file.
tools:
  - powershell
---

# W1 Sub-Agent 1 — Fetcher

You are the fetcher sub-agent in Workflow 1.
Your job is to run the prebuilt shell script which handles fetching all alert types and CSV creation in one step.

## ⚠️ Execution Rules — NO SIMULATION

**You MUST actually execute every command. Never simulate, narrate, or hallucinate results.**

- Do NOT say "I would run..." or "The script would produce..." — run the command and show real output
- Do NOT invent alert counts, CSV filenames, or file paths — read them from actual command output
- Do NOT skip the auth check or verification step — both must be confirmed with real output
- If the script fails, show the exact error and STOP — do NOT fabricate a success or fake a CSV path
- The CSV path you report MUST come from running the `Get-ChildItem` command, not from guessing

## ⚠️ Tool Execution — Use powershell for ALL Commands

**You have access to a `powershell` tool. Use it to run every command in this document.**

- The `runCommand` tool does NOT exist in this environment — never block, stop, or report it as unavailable
- Use the `powershell` tool for all PowerShell commands, Python scripts, and `mvn` commands
- For Git Bash / shell script execution, call `powershell` with the config-loaded path after Step 0: `& $GIT_BASH -c "<command>"`
- Never say "I would run..." or "I cannot run because runCommand is unavailable" — invoke `powershell` and show actual output
- If a command fails, show the exact error from `powershell` output — never fabricate success

## Input (from orchestrator)
- `CONFIG_PATH` — path to `ghas-workflow-config.yml`
- All other values are loaded from config in Step 0 below.

## Prerequisites
- GitHub CLI must be installed and reachable via the config-loaded `gh` command
- `jq` must be installed and reachable via the shell environment used by the script
- Git Bash must be available at the config-loaded path
- Run `gh auth login` once if not already authenticated — **no `.env` token required**, `gh` manages auth via the keyring

## Progress Reporting

Emit a status line to the user **before and after** each step:

```
🔄 [Fetcher] Checking GitHub CLI authentication...
✅ [Fetcher] Authenticated as <username> — scopes OK
🔄 [Fetcher] Running fetch_alerts.sh...
   Service: HMS
     [1/3] Fetching Dependabot alerts... ✓ 15 alert(s) found
     [2/3] Fetching Code Scanning alerts... ✓ 1 alert(s) found
     [3/3] Fetching Secret Scanning alerts... ✓ 0 alert(s) found (not enabled)
   Total alerts written: 16
✅ [Fetcher] CSV written: github_alerts_20260618_113803.csv
🔄 [Fetcher] Verifying output file...
✅ [Fetcher] Verified: 16 data rows confirmed
```

If any step fails, emit:
```
❌ [Fetcher] FAILED at <step>: <exact error>
```

## Steps

### 0. Load Config

```powershell
$cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$cfg = $cfgJson | ConvertFrom-Json

$REPO_ROOT      = $cfg.environment.repo_root
$GIT_BASH       = $cfg.tools.git_bash
$GH_CMD         = $cfg.tools.gh
$REPO_NAME      = $cfg.environment.repo_name
$FETCH_SCRIPT   = Join-Path $REPO_ROOT ($cfg.scripts.fetch_alerts -replace '/', '\')
$CSV_OUT_DIR    = Join-Path $REPO_ROOT $cfg.csv.output_dir
$SERVICE_NAME   = $cfg.environment.service_name
$REPO_OWNER     = $cfg.environment.repo_owner

Write-Host "Config loaded: repo_root=$REPO_ROOT  service=$SERVICE_NAME"
```

### 1. Verify GitHub CLI authentication
Run via Git Bash:
```bash
$GH_CMD auth status
```
Look for ✓ `Logged in to github.com` with token scopes including **`repo`** and **`read:org`**.

If not authenticated → STOP and tell the user to run `gh auth login`.

### 2. Run the script from the repo root using Git Bash
```powershell
& $GIT_BASH -c "$GH_CMD auth status"
& $GIT_BASH -c "$($FETCH_SCRIPT -replace '\\', '/') $CSV_OUT_DIR $SERVICE_NAME $REPO_OWNER $REPO_NAME"
```

The script will automatically:
- Add GitHub CLI and `jq` to PATH if they are not found
- **Delete any existing `github_alerts_*.csv` files** from previous runs before writing a fresh one (no accumulation)
- Fetch all open Dependabot, Code Scanning, and Secret Scanning alerts using `gh api` (no token file needed)
- Write a single fresh **timestamped** CSV in the configured output directory

### 3. Resolve the output file path
The script writes to a timestamped file in the configured output directory. Resolve the latest one:
```powershell
Get-ChildItem (Join-Path $CSV_OUT_DIR 'github_alerts_*.csv') | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
```

### 4. Verify output
Count data rows (excluding header):
```powershell
$csv = Get-ChildItem (Join-Path $CSV_OUT_DIR 'github_alerts_*.csv') | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
(Get-Content $csv | Select-Object -Skip 1 | Where-Object { $_ -ne "" }).Count
```
- If count > 0 → proceed
- If count = 0 → STOP and report "No open alerts found"

## CSV columns (0-indexed)
| Index | Column | Description |
|---|---|---|
| 0 | service | Repository/service name |
| 1 | type | `dependabot`, `code-scanning`, or `secret-scanning` |
| 2 | ghsa_id | GHSA advisory ID |
| 3 | cve_id | CVE ID |
| 4 | title | Alert summary / rule description |
| 5 | severity | critical / high / medium / low |
| 6 | created | Date alert was created (YYYY-MM-DD) |
| 7 | due | Compliance due date (dependabot only) |
| 8 | url | Alert URL on GitHub |
| 9 | Application | Application label |
| 10 | nonCompliant | 1 if past SLA, 0 otherwise |
| 11 | ageDays | Age of alert in days |

## Output to pass to @w1-sorter
- Full path to the CSV file (e.g. `C:\...\github_alerts_20260617_142048.csv`) — resolved via glob above
- Total number of alerts fetched (all types)
- Count per type (dependabot / code-scanning / secret-scanning)
- Count per severity for dependabot alerts (CRITICAL / HIGH / MEDIUM / LOW)

## Failure conditions
- `gh auth status` fails → stop, tell the user to run `gh auth login`
- Script throws an error → stop, return the exact error message
- Output file empty or missing → stop, report `No open alerts found`
