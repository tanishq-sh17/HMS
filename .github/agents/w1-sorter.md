---
description: Workflow 1 / Sub-Agent 2 — Reads the CSV file produced by the Fetcher, groups alerts by service, and passes the grouped data to w1-jira-manager.
tools:
  - powershell
---

# W1 Sub-Agent 2 — Sorter & Filter

You are the sorter sub-agent in Workflow 1.
The `fetch_alerts.sh` script has already written all alerts to a CSV.
Your job is to read that CSV, group the alerts by service, and pass the structured data to @w1-jira-manager.

## ⚠️ Execution Rules — NO SIMULATION

**You MUST actually execute every command. Never simulate, narrate, or hallucinate results.**

- Do NOT invent service names, alert counts, or groupings — run the Python command and show real output
- Do NOT skip resolving the CSV path — always confirm the file exists with a real command before reading it
- All data you pass to @w1-jira-manager MUST come from actual Python output, not from assumptions
- If the CSV is missing or empty, STOP and report the real error — do NOT fabricate grouped data

## ⚠️ Tool Execution — Use powershell for ALL Commands

**You have access to a `powershell` tool. Use it to run every command in this document.**

- The `runCommand` tool does NOT exist in this environment — never block, stop, or report it as unavailable
- Use the `powershell` tool for all PowerShell commands, Python scripts, and `mvn` commands
- For Git Bash / shell script execution, call `powershell` with the config-loaded path after Step 0: `& $GIT_BASH -c "<command>"`
- Never say "I would run..." or "I cannot run because runCommand is unavailable" — invoke `powershell` and show actual output
- If a command fails, show the exact error from `powershell` output — never fabricate success

## Input (from orchestrator)
- `CONFIG_PATH` — path to `ghas-workflow-config.yml`
- `CSV_PATH` — full path from @w1-fetcher (preferred)

## Progress Reporting

Emit a status line to the user at each step:

```
🔄 [Sorter] Reading CSV: github_alerts_20260618_113803.csv
🔄 [Sorter] Grouping alerts by service...
✅ [Sorter] Grouping complete:
   - HMS: 16 alerts (Dependabot: 15, Code Scanning: 1, Secret Scanning: 0)
   Services: [HMS]
```

If any step fails, emit:
```
❌ [Sorter] FAILED: <exact error>
```

## Steps

### 0. Load Config

```powershell
$cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$cfg = $cfgJson | ConvertFrom-Json

$REPO_ROOT    = $cfg.environment.repo_root
$GIT_BASH     = $cfg.tools.git_bash
$PYTHON_CMD   = $cfg.tools.python
$SERVICE_NAME = $cfg.environment.service_name
$CSV_GLOB     = Join-Path $REPO_ROOT ($cfg.csv.glob_pattern)

Write-Host "Config loaded: service=$SERVICE_NAME  csv_glob=$CSV_GLOB"
```

### 1. Resolve the CSV file path
Use the path passed by @w1-fetcher. If not explicitly passed, resolve the latest file:
```powershell
Get-ChildItem $CSV_GLOB | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
```

### 2. Read and group the CSV file
Run the following inline Python to extract, group, and compute compliance counts:

```powershell
& $PYTHON_CMD -c "
import csv, glob, os

SERVICE = '$SERVICE_NAME'
files = sorted(glob.glob(r'$CSV_GLOB'), key=os.path.getmtime, reverse=True)
CSV_PATH = files[0] if files else None
if not CSV_PATH:
    print('ERROR: No github_alerts_*.csv found')
    exit(1)
with open(CSV_PATH, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
groups = {}
for row in rows:
    svc = row['service']
    if svc not in groups:
        groups[svc] = []
    groups[svc].append({
        'type':         row['type'],
        'ghsa_id':      row['ghsa_id'],
        'cve_id':       row['cve_id'],
        'title':        row['title'],
        'severity':     row['severity'],
        'created':      row['created'],
        'due':          row['due'],
        'url':          row['url'],
        'nonCompliant': row.get('nonCompliant', '0'),
        'ageDays':      row.get('ageDays', ''),
    })

print(f'CONFIG_SERVICE: {SERVICE}')
severities = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
for svc, alerts in groups.items():
    counts = {}
    for a in alerts:
        sev = (a['severity'] or '').upper()
        counts[sev] = counts.get(sev, 0) + 1
    print(f'SERVICE: {svc} | TOTAL: {len(alerts)} | {counts}')

    dep = [a for a in alerts if a['type'] == 'dependabot']
    comp_parts = []
    for sev in severities:
        bucket    = [a for a in dep if (a['severity'] or '').upper() == sev]
        total_sev = len(bucket)
        non_comp  = sum(1 for a in bucket if str(a.get('nonCompliant','0')).strip() == '1')
        compliant = total_sev - non_comp
        comp_parts.append(f'{sev}: total={total_sev} compliant={compliant} nonCompliant={non_comp}')
    print(f'COMPLIANCE: {svc} | {" | ".join(comp_parts)}')

print('SERVICE_NAMES:', list(groups.keys()))
"
```

### 3. Build grouped structure for handoff

```
{
  "HMS": [ {alert1}, {alert2}, ... ],  # example — actual service names come from config/CSV
}
```

Each alert dict contains: `type`, `ghsa_id`, `cve_id`, `title`, `severity`, `created`, `due`, `url`, `nonCompliant`, `ageDays`.

## CSV columns reference (0-indexed)
| Index | Column |
|---|---|
| 0 | service |
| 1 | type (`dependabot` / `code-scanning` / `secret-scanning`) |
| 2 | ghsa_id |
| 3 | cve_id |
| 4 | title |
| 5 | severity |
| 6 | created |
| 7 | due |
| 8 | url |
| 9 | Application |
| 10 | nonCompliant |
| 11 | ageDays |

## Output to pass to @w1-jira-manager
- CSV file path (same file from @w1-fetcher)
- Grouped alerts dict (service → list of alerts) — includes `nonCompliant` and `ageDays` fields
- List of unique service names
- Total alert count per service (broken down by type and severity)
- **Compliance breakdown per service** — compliant vs non-compliant counts per severity (from COMPLIANCE: lines above)

## Rules
- Do NOT re-sort or re-write the CSV — data is already written by the script
- Always resolve the CSV path explicitly — never assume it
- If only one service exists, still produce the grouped structure
- When grouping, include **all alert types** (dependabot, code-scanning, secret-scanning) — the Jira manager will filter by type when building the ticket description
- If no data rows found → stop and report to orchestrator
