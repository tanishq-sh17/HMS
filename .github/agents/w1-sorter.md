---
description: Workflow 1 / Sub-Agent 2 (DEPRECATED — no longer invoked by orchestrator) — Reads the CSV file produced by the Fetcher, groups alerts by service, and passes the grouped data to w1-jira-manager. Service grouping is now performed inline by the orchestrator.
model: claude-haiku-4-5-20251001
tools:
  - powershell
---

# W1 Sub-Agent 2 — Sorter & Filter (**DEPRECATED**)

> **This sub-agent is no longer invoked.** Service grouping is performed inline by the orchestrator. This file is kept for reference only.

You read the CSV, group alerts by service, and pass structured data to @w1-jira-manager.

**⚠️ Use `powershell` for ALL commands. Never simulate results. For multi-line Python, write to a temp `.py` file. Show exact error output on failure.**

## Input
- `CONFIG_PATH`, `CSV_PATH`, `SERVICE_NAME` (from orchestrator)

## Steps

### 0. Load Config

```powershell
$CONFIG_PATH  = "<CONFIG_PATH>"
$CSV_PATH     = "<CSV_PATH>"
$SERVICE_NAME = "<SERVICE_NAME>"

$cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$cfg = $cfgJson | ConvertFrom-Json

$REPO_ROOT  = $cfg.environment.repo_root
$PYTHON_CMD = $cfg.tools.python
$CSV_GLOB   = Join-Path $REPO_ROOT ($cfg.csv.glob_pattern)

Write-Host "Config loaded: service=$SERVICE_NAME  csv_glob=$CSV_GLOB"
```

### 1. Resolve CSV Path

```powershell
Get-ChildItem $CSV_GLOB | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
```

### 2. Read and group the CSV

```powershell
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
@"
import csv, glob, os

SERVICE = '$SERVICE_NAME'
CSV_GLOB = r'$CSV_GLOB'
files = sorted(glob.glob(CSV_GLOB), key=os.path.getmtime, reverse=True)
CSV_PATH = files[0] if files else None
if not CSV_PATH:
    print('ERROR: No github_alerts_*.csv found'); exit(1)
with open(CSV_PATH, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
groups = {}
for row in rows:
    svc = row['service']
    if svc not in groups: groups[svc] = []
    groups[svc].append({k: row.get(k,'') for k in ['type','ghsa_id','cve_id','title','severity','created','due','url','nonCompliant','ageDays']})

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
        bucket   = [a for a in dep if (a['severity'] or '').upper() == sev]
        non_comp = sum(1 for a in bucket if str(a.get('nonCompliant','0')).strip() == '1')
        comp_parts.append(f'{sev}: total={len(bucket)} compliant={len(bucket)-non_comp} nonCompliant={non_comp}')
    print(f'COMPLIANCE: {svc} | {" | ".join(comp_parts)}')

print('SERVICE_NAMES:', list(groups.keys()))
print(f'TOTAL_ALERTS: {sum(len(v) for v in groups.values())}')
"@ | Set-Content -Path $tmpPy -Encoding UTF8
& $PYTHON_CMD $tmpPy
Remove-Item $tmpPy -ErrorAction SilentlyContinue
```

## Output to @w1-jira-manager
- CSV file path, grouped alerts dict, unique service names, alert counts per type/severity, compliance breakdown

## Rules
- Do NOT re-sort or re-write the CSV
- Include all alert types (dependabot, code-scanning, secret-scanning) in grouped structure
- If no data rows found → stop and report to orchestrator
