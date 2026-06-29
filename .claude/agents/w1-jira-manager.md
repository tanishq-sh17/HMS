---
description: Workflow 1 / Sub-Agent 2 (active) — For each service, checks Jira for an existing active GHAS ticket using jira_ticket_manager.py search. If one exists in an active work status (e.g. In Dev, To Do, Blocked), compares CVEs and updates the existing ticket's description in place. A new ticket is only created when no active ticket exists. All Jira operations (search, get, create, update-description) use jira_ticket_manager.py — no MCP. Note- previously numbered Sub-Agent 3 when w1-sorter was active; w1-sorter is now deprecated.
model: claude-sonnet-4-6
tools:
  - powershell
---

# W1 Sub-Agent 2 — Jira Manager

You receive the CSV path and service names from the orchestrator, check for a duplicate Jira ticket per service, create **one consolidated ticket per service** (covering all CVEs), and update the CSV.

**⚠️ Use `powershell` for ALL commands. Never simulate results. For multi-line Python, write to a temp `.py` file. Show exact error output on failure.**

## Input (from orchestrator)
`CONFIG_PATH`, `CSV_PATH`, `SERVICE_NAMES` (comma-separated), `SKIP_STATUSES`, `PYTHON_CMD`, `JIRA_SCRIPT`, `JIRA_PROJECT`, `BASE_LABEL`, `CSV_GLOB`, `PARENT_JIRA`, `SEARCH_LABELS`

## Progress Format

```
🔄 [Jira Manager] Processing service: <SERVICE> (<N> alerts)
   → Checking Jira for existing active GHAS ticket (label=<SERVICE>)...
   → <creating fresh ticket | updating <KEY> description | skipping — all CVEs covered>
✅ [Jira Manager] <Ticket created: HMS-XX | Updated: HMS-XX | Skipped HMS (no new CVEs since HMS-XX)>
   → Updating CSV with Jira key / status...
✅ [Jira Manager] CSV updated
❌ [Jira Manager] FAILED for <SERVICE>: <exact error>
```

---

## Steps

### 0. Load Config

```powershell
$CONFIG_PATH          = "<CONFIG_PATH>"
$CSV_PATH             = "<CSV_PATH>"
$SERVICE_NAMES        = "<SERVICE_NAMES>"
$SKIP_STATUSES_PASSED = "<SKIP_STATUSES>"
$PYTHON_CMD           = "<PYTHON_CMD>"
$JIRA_SCRIPT          = "<JIRA_SCRIPT>"
$JIRA_PROJECT         = "<JIRA_PROJECT>"
$BASE_LABEL           = "<BASE_LABEL>"
$CSV_GLOB             = "<CSV_GLOB>"
$PARENT_JIRA          = "<PARENT_JIRA>"
$SEARCH_LABELS        = "<SEARCH_LABELS>"
$REPO_ROOT            = Split-Path $JIRA_SCRIPT -Parent | Split-Path -Parent

$SKIP_STATUSES = if ($SKIP_STATUSES_PASSED -and $SKIP_STATUSES_PASSED -ne "<SKIP_STATUSES>") {
    $SKIP_STATUSES_PASSED -split ','
} else {
    $cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
    ($cfgJson | ConvertFrom-Json).jira.skip_statuses_for_duplicate_check
}

Write-Host "Config loaded: jira=$JIRA_PROJECT  skip_statuses=$($SKIP_STATUSES -join ',')  parent_jira=$PARENT_JIRA"
```

### 0b. Define Retry Helper

```powershell
function Invoke-WithRetry {
    param([scriptblock]$Command, [int]$MaxAttempts = 3, [int]$BaseDelaySec = 5)
    for ($i = 1; $i -le $MaxAttempts; $i++) {
        $output = & $Command 2>&1
        if ($LASTEXITCODE -eq 0) { return $output }
        if ($i -lt $MaxAttempts) {
            $delay = $BaseDelaySec * [Math]::Pow(2, $i - 1)
            Write-Host "  [Retry $i/$MaxAttempts] Command failed (exit $LASTEXITCODE) — waiting ${delay}s"
            Start-Sleep -Seconds $delay
        }
    }
    Write-Host "  [FAILED after $MaxAttempts attempts] last exit: $LASTEXITCODE"
    return $output
}
```

---

### 1. Resolve CSV Path

Use the path passed by the orchestrator. If not provided, resolve the latest:
```powershell
$CSV_PATH = Get-ChildItem $CSV_GLOB | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
Write-Host "CSV: $CSV_PATH"
```

---

### 2. Delta Detection — Compare Existing Ticket CVEs Against Current Alerts

**2a. Search for an existing active ticket:**

```powershell
$searchLabelsList = if ($SEARCH_LABELS -and $SEARCH_LABELS -ne "<SEARCH_LABELS>") {
    $SEARCH_LABELS -split ','
} else {
    @($BASE_LABEL)
}
$statusList   = ($SKIP_STATUSES | ForEach-Object { "`"$_`"" }) -join ", "
$labelClauses = ($searchLabelsList | ForEach-Object { "labels = `"$($_.Trim())`"" }) -join " AND "
$jql = "project = `"$JIRA_PROJECT`" AND $labelClauses AND labels = `"$SERVICE_NAME`" AND status in ($statusList)"
if ($PARENT_JIRA -and $PARENT_JIRA -ne "null" -and $PARENT_JIRA -ne "<PARENT_JIRA>") {
    $jql += " AND parent = `"$PARENT_JIRA`""
}
$jql += " ORDER BY created DESC"

$searchRaw = Invoke-WithRetry -Command { & $PYTHON_CMD $JIRA_SCRIPT search --jql $jql }
$tickets = $searchRaw | ConvertFrom-Json
Write-Host "Search returned $($tickets.Count) ticket(s)"
```

**2b. Determine active ticket:**

```powershell
$activeTicket = $tickets | Where-Object {
    $s = $_.status.ToLower()
    $SKIP_STATUSES | Where-Object { $_.ToLower() -eq $s }
} | Select-Object -First 1
```

- Match found → `ACTIVE_TICKET_KEY = $activeTicket.key`, `ACTIVE_TICKET_STATUS = $activeTicket.status`
- No match → `ACTIVE_TICKET_KEY = "NONE"` → skip to Step 3 (`CREATE_MODE = all`)

**2b.5. Fetch description for active ticket:**

```powershell
$getRaw = Invoke-WithRetry -Command { & $PYTHON_CMD $JIRA_SCRIPT get --ticket $ACTIVE_TICKET_KEY }
$ticketDetail = $getRaw | ConvertFrom-Json
$ACTIVE_TICKET_DESC = $ticketDetail.description_text.Substring(0, [Math]::Min(2000, $ticketDetail.description_text.Length))
```

**2d. Run CVE delta detection (only when active ticket exists):**

```powershell
$tmpDesc = [System.IO.Path]::GetTempFileName() + ".txt"
Set-Content -Path $tmpDesc -Value $ACTIVE_TICKET_DESC -Encoding UTF8
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
@"
import csv, glob, os, re, json, tempfile

SERVICE  = '<SERVICE_NAME>'
CSV_GLOB = r'$CSV_GLOB'

with open(r'$tmpDesc', encoding='utf-8') as f:
    TICKET_DESC = f.read()

existing_ids = set(re.findall(r'CVE-\d{4}-\d+', TICKET_DESC, re.IGNORECASE))
existing_ids |= set(re.findall(r'GHSA-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+', TICKET_DESC, re.IGNORECASE))
existing_ids = {i.upper() for i in existing_ids}
print(f'EXISTING_IDS ({len(existing_ids)}): {sorted(existing_ids)}')

files = sorted(glob.glob(CSV_GLOB), key=os.path.getmtime, reverse=True)
if not files:
    print('ERROR: No CSV found'); exit(1)
with open(files[0], newline='', encoding='utf-8') as f:
    all_rows = list(csv.DictReader(f))

service_rows = [r for r in all_rows if r.get('service','').strip().lower() == SERVICE.lower()]
current_ids  = set()
for r in service_rows:
    if r.get('cve_id','').strip():  current_ids.add(r['cve_id'].strip().upper())
    if r.get('ghsa_id','').strip(): current_ids.add(r['ghsa_id'].strip().upper())
print(f'CURRENT_IDS ({len(current_ids)}): {sorted(current_ids)}')

new_ids = current_ids - existing_ids
print(f'NEW_IDS ({len(new_ids)}): {sorted(new_ids)}')

if not new_ids:
    print('DELTA_RESULT=NO_NEW_CVES')
else:
    new_rows = [r for r in service_rows
                if r.get('cve_id','').strip().upper()  in new_ids
                or r.get('ghsa_id','').strip().upper() in new_ids]
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False,
                                      newline='', encoding='utf-8')
    fieldnames = list(all_rows[0].keys())
    writer = csv.DictWriter(tmp, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(new_rows)
    tmp.close()
    print(f'DELTA_RESULT=NEW_CVES_FOUND')
    print(f'DELTA_CSV={tmp.name}')
    print(f'DELTA_ROW_COUNT={len(new_rows)}')
"@ | Set-Content -Path $tmpPy -Encoding UTF8
& $PYTHON_CMD $tmpPy
Remove-Item $tmpPy, $tmpDesc -ErrorAction SilentlyContinue
```

**Delta result actions:**

| `DELTA_RESULT` | Action |
|---|---|
| `NO_NEW_CVES` | Set `JIRA_KEY = <ACTIVE_TICKET_KEY>`, `JIRA_STATUS = SKIPPED` → jump to Step 4 |
| `NEW_CVES_FOUND` | Update existing ticket description (Step 2e) → jump to Step 4 |

**2e. Update existing ticket description (only when `NEW_CVES_FOUND`):**

```powershell
$updateOutput = Invoke-WithRetry -Command {
    & $PYTHON_CMD $JIRA_SCRIPT `
        update-description --ticket "<ACTIVE_TICKET_KEY>" --service "<SERVICE_NAME>" --csv "<CSV_PATH>"
}
Write-Host $updateOutput
```

On success: `JIRA_KEY = <ACTIVE_TICKET_KEY>`, `JIRA_STATUS = UPDATED` → jump to Step 4.
On failure: log error, fall back to `CREATE_MODE = all` → proceed to Step 3.

---

### 3. Create Jira Ticket (only when `ACTIVE_TICKET_KEY = NONE`)

```powershell
$createOutput = Invoke-WithRetry -Command {
    & $PYTHON_CMD $JIRA_SCRIPT `
        create --project $JIRA_PROJECT --service "<SERVICE_NAME>" --csv "<CSV_TO_USE>"
}
Write-Host $createOutput
```

Parse `key` from JSON output → `JIRA_KEY`. Set `JIRA_STATUS = CREATED`.
On all retries failing → log exact error, mark service FAILED, continue to next service.

---

### 4. Update the CSV with Jira Key and Status

```powershell
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
@"
import csv, glob, os

SERVICE     = '<SERVICE_NAME>'
JIRA_KEY    = '<JIRA_KEY>'
JIRA_STATUS = '<JIRA_STATUS>'
CSV_GLOB    = r'$CSV_GLOB'

files = sorted(glob.glob(CSV_GLOB), key=os.path.getmtime, reverse=True)
CSV_PATH = files[0] if files else None
if not CSV_PATH:
    print('ERROR: No github_alerts_*.csv found')
    exit(1)

with open(CSV_PATH, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

for row in rows:
    if row.get('service', '').strip().lower() == SERVICE.strip().lower():
        row['jira_key']    = JIRA_KEY
        row['jira_status'] = JIRA_STATUS
    else:
        row.setdefault('jira_key', '')
        row.setdefault('jira_status', '')

fieldnames = list(rows[0].keys()) if rows else []
for col in ('jira_key', 'jira_status'):
    if col not in fieldnames:
        fieldnames.append(col)

with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print('Updated CSV for ' + SERVICE + ' -> ' + JIRA_KEY + ' (' + JIRA_STATUS + ')')
"@ | Set-Content -Path $tmpPy -Encoding UTF8
& $PYTHON_CMD $tmpPy
Remove-Item $tmpPy -ErrorAction SilentlyContinue
```

Confirm the `print(...)` line appears in output before proceeding.

---

## Output to pass to @orchestrator
```
W1 COMPLETE
─────────────────────────────────────────
CSV file       : <CSV_PATH>
Services found : X
Total alerts   : X  (Dependabot: X, Code Scanning: X, Secret Scanning: X)
Severity       : CRITICAL: X, HIGH: X, MEDIUM: X, LOW: X

Jira results:
  CREATED (fresh)      : X  → [HMS-XX, ...]
  UPDATED (description): X  → [HMS-XX, ...]
  SKIPPED              : X  → [HMS-XX, ...]
  FAILED               : X  → (errors if any)

Services with NEW tickets (for Workflow 2):
  - HMS → HMS-XX
```

## Rules
- One consolidated ticket per service — never one ticket per CVE
- Always run Step 2 (search + delta) before Step 3 (create) — never skip it
- Active ticket (status in `$SKIP_STATUSES`) + new CVEs → update description in place via `update-description`; never create a separate delta ticket
- No active ticket (none found, or existing is Done/Testing/QA/In Review) → create fresh ticket covering all current alerts
- Search fails → log real error, fall back to `CREATE_MODE = all`
- Ticket creation fails → log exact error, continue with remaining services
- Always run Step 4 (CSV update) after every service regardless of CREATED/SKIPPED/UPDATED
- Use only config-loaded values for project, labels, priority, story points, and skip-status logic
