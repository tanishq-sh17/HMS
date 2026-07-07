---
description: Workflow 1 / Sub-Agent 2 (active) — Batch-searches Jira for all services in a single JQL call, then processes all services in parallel using Start-Job. Delta detection (CVE diff) and ticket create/update run concurrently per service; CSV is written once after all jobs complete. All Jira operations use jira_ticket_manager.py — no MCP.
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

### 1. Verify CSV Path

```powershell
if (-not $CSV_PATH -or -not (Test-Path $CSV_PATH)) {
    Write-Host "❌ [Jira Manager] ERROR: CSV not found at: $CSV_PATH"; exit 1
}
Write-Host "✅ [Jira Manager] CSV verified: $CSV_PATH"
```

---

### 1.5 — Batch Jira Search (all services, single API call)

Issue **one** JQL covering all configured services instead of one search per service. Results are bucketed into `$ticketsByService` — no per-service search calls needed in Step 2.

```powershell
$allServiceNames = @($SERVICE_NAMES -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$searchLabelsList = if ($SEARCH_LABELS -and $SEARCH_LABELS -ne "<SEARCH_LABELS>") {
    $SEARCH_LABELS -split ','
} else { @($BASE_LABEL) }

$statusList   = ($SKIP_STATUSES | ForEach-Object { "`"$_`"" }) -join ", "
$labelClauses = ($searchLabelsList | ForEach-Object { "labels = `"$($_.Trim())`"" }) -join " AND "
$svcInClause  = ($allServiceNames | ForEach-Object { "`"$_`"" }) -join ","

$jql = "project = `"$JIRA_PROJECT`" AND $labelClauses AND labels in ($svcInClause) AND status in ($statusList)"
if ($PARENT_JIRA -and $PARENT_JIRA -ne "null" -and $PARENT_JIRA -ne "<PARENT_JIRA>") {
    $jql += " AND parent = `"$PARENT_JIRA`""
}
$jql += " ORDER BY created DESC"

$allTicketsRaw = Invoke-WithRetry -Command { & $PYTHON_CMD $JIRA_SCRIPT search --jql $jql }
$allTickets    = if ($LASTEXITCODE -eq 0) { $allTicketsRaw | ConvertFrom-Json } else { @() }
Write-Host "Batch search: $($allTickets.Count) ticket(s) across $($allServiceNames.Count) service(s)"

# Bucket tickets by service label
$ticketsByService = @{}
foreach ($svc in $allServiceNames) { $ticketsByService[$svc] = @() }
foreach ($t in $allTickets) {
    foreach ($svc in $allServiceNames) {
        if ($t.labels -icontains $svc) { $ticketsByService[$svc] += $t; break }
    }
}
```

If the search call fails entirely → `$allTickets = @()`, so every service falls back to `CREATE_MODE = all` inside its job.

---

### 2. Delta Detection — Compare Existing Ticket CVEs Against Current Alerts

**2a — Active ticket lookup (from pre-fetched Step 1.5 data):**

For each service, determine its active ticket from `$ticketsByService[$svc]` — no additional Jira API call needed:

```powershell
$activeTicket = $ticketsByService[$svc] | Where-Object {
    $s = $_.status.ToLower()
    ($SKIP_STATUSES | ForEach-Object { $_.ToLower() }) -contains $s
} | Select-Object -First 1
# Match found → use $activeTicket.key; No match → $activeTicket is $null → CREATE_MODE = all
```

---

### 2b-3 — Parallel Per-Service Processing (Start-Job)

Launch one job per service. Each job handles Steps 2b.5 (fetch description), 2d (CVE delta), 2e (update) or 3 (create). **Do NOT write to the CSV inside jobs** — return result tuples only.

```powershell
$retryFn = ${function:Invoke-WithRetry}.ToString()
$jobs    = @{}

foreach ($svc in $allServiceNames) {
    $svcTickets   = $ticketsByService[$svc]
    $activeTicket = $svcTickets | Where-Object {
        $s = $_.status.ToLower()
        ($SKIP_STATUSES | ForEach-Object { $_.ToLower() }) -contains $s
    } | Select-Object -First 1

    Write-Host "🔄 [Jira Manager] Processing service: $svc ($($svcTickets.Count) pre-fetched ticket(s))"

    $jobs[$svc] = Start-Job -ScriptBlock {
        param($SERVICE_NAME, $activeTicket, $CSV_PATH, $PYTHON_CMD, $JIRA_SCRIPT, $JIRA_PROJECT, $retryFn)
        Set-StrictMode -Off
        Invoke-Expression "function Invoke-WithRetry { $retryFn }"

        $out = [PSCustomObject]@{ Service=$SERVICE_NAME; JiraKey=""; JiraStatus="FAILED"; Error="" }

        try {
            if (-not $activeTicket) {
                # Step 3: No active ticket — create fresh
                $createRaw = Invoke-WithRetry -Command {
                    & $PYTHON_CMD $JIRA_SCRIPT create --project $JIRA_PROJECT --service $SERVICE_NAME --csv $CSV_PATH
                }
                $key = ($createRaw | ConvertFrom-Json).key
                $out.JiraKey = $key; $out.JiraStatus = "CREATED"
                Write-Host "✅ [Jira Manager] $SERVICE_NAME — Created: $key"
            } else {
                $ACTIVE_KEY = $activeTicket.key
                # Step 2b.5: Fetch description for delta check
                $getRaw = Invoke-WithRetry -Command { & $PYTHON_CMD $JIRA_SCRIPT get --ticket $ACTIVE_KEY }
                $ticketDetail = $getRaw | ConvertFrom-Json
                $descText = $ticketDetail.description_text.Substring(0, [Math]::Min(2000, $ticketDetail.description_text.Length))

                # Step 2d: CVE delta via temp Python script
                $tmpDesc = [System.IO.Path]::GetTempFileName() + ".txt"
                $tmpPy   = [System.IO.Path]::GetTempFileName() + ".py"
                Set-Content -Path $tmpDesc -Value $descText -Encoding UTF8

                $pySrc  = "import csv, re`n"
                $pySrc += "SERVICE = '" + $SERVICE_NAME + "'`n"
                $pySrc += "with open(r'" + $tmpDesc + "', encoding='utf-8') as f: TICKET_DESC = f.read()`n"
                $pySrc += "existing_ids = set(re.findall(r'CVE-\d{4}-\d+|GHSA-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+', TICKET_DESC, re.IGNORECASE))`n"
                $pySrc += "existing_ids = {i.upper() for i in existing_ids}`n"
                $pySrc += "with open(r'" + $CSV_PATH + "', newline='', encoding='utf-8') as f: rows = list(csv.DictReader(f))`n"
                $pySrc += "svc_rows = [r for r in rows if r.get('service','').strip().lower() == SERVICE.lower()]`n"
                $pySrc += "current_ids = set()`n"
                $pySrc += "for r in svc_rows:`n"
                $pySrc += "    if r.get('cve_id','').strip(): current_ids.add(r['cve_id'].strip().upper())`n"
                $pySrc += "    if r.get('ghsa_id','').strip(): current_ids.add(r['ghsa_id'].strip().upper())`n"
                $pySrc += "new_ids = current_ids - existing_ids`n"
                $pySrc += "print('DELTA_RESULT=' + ('NEW_CVES_FOUND' if new_ids else 'NO_NEW_CVES'))"

                Set-Content -Path $tmpPy -Value $pySrc -Encoding UTF8
                $deltaOut = & $PYTHON_CMD $tmpPy
                Remove-Item $tmpPy, $tmpDesc -ErrorAction SilentlyContinue

                if ($deltaOut -match 'NO_NEW_CVES') {
                    # Step 2d result: no new CVEs — skip
                    $out.JiraKey = $ACTIVE_KEY; $out.JiraStatus = "SKIPPED"
                    Write-Host "✅ [Jira Manager] $SERVICE_NAME — Skipped $ACTIVE_KEY (no new CVEs)"
                } else {
                    # Step 2e: New CVEs found — update description in place
                    Invoke-WithRetry -Command {
                        & $PYTHON_CMD $JIRA_SCRIPT update-description --ticket $ACTIVE_KEY --service $SERVICE_NAME --csv $CSV_PATH
                    } | Out-Null
                    $out.JiraKey = $ACTIVE_KEY; $out.JiraStatus = "UPDATED"
                    Write-Host "✅ [Jira Manager] $SERVICE_NAME — Updated $ACTIVE_KEY"
                }
            }
        } catch {
            $out.Error = $_.Exception.Message
            Write-Host "❌ [Jira Manager] $SERVICE_NAME — FAILED: $($_.Exception.Message)"
        }

        return ($out | ConvertTo-Json -Compress)
    } -ArgumentList $svc, $activeTicket, $CSV_PATH, $PYTHON_CMD, $JIRA_SCRIPT, $JIRA_PROJECT, $retryFn
}

Write-Host "⏳ Waiting for $($jobs.Count) parallel service job(s) to complete..."
```

---

### 4. Collect Results + Single-Pass CSV Update

Wait for all jobs, collect result tuples, then write the CSV once — avoids concurrent file-write conflicts.

```powershell
$serviceResults = @()
foreach ($entry in $jobs.GetEnumerator()) {
    $job       = $entry.Value
    $jobOutput = $job | Wait-Job | Receive-Job
    $job | Remove-Job
    try {
        $serviceResults += ($jobOutput | ConvertFrom-Json)
    } catch {
        Write-Host "❌ [Jira Manager] Job output parse error for $($entry.Key): $($_.Exception.Message)"
        $serviceResults += [PSCustomObject]@{ Service=$entry.Key; JiraKey=""; JiraStatus="FAILED"; Error="Parse error" }
    }
}

# Write results to temp JSON so Python can read safely
$tmpResults = [System.IO.Path]::GetTempFileName() + ".json"
$serviceResults | ConvertTo-Json -Compress -Depth 5 | Set-Content -Path $tmpResults -Encoding UTF8

$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
@"
import csv, json

CSV_PATH     = r'$CSV_PATH'
RESULTS_PATH = r'$tmpResults'

with open(RESULTS_PATH, encoding='utf-8') as f:
    raw = json.load(f)
results = raw if isinstance(raw, list) else [raw]
key_map = {r['Service']: (r['JiraKey'], r['JiraStatus']) for r in results}

with open(CSV_PATH, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

for row in rows:
    svc = row.get('service', '').strip()
    if svc in key_map:
        row['jira_key']    = key_map[svc][0]
        row['jira_status'] = key_map[svc][1]
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
print('CSV batch-updated for', len(key_map), 'service(s):', ', '.join(key_map.keys()))
"@ | Set-Content -Path $tmpPy -Encoding UTF8
& $PYTHON_CMD $tmpPy
Remove-Item $tmpPy, $tmpResults -ErrorAction SilentlyContinue
```

Confirm `CSV batch-updated` line appears in output before proceeding.

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
- Always run Step 1.5 (batch search) before Step 2 — never skip it
- Active ticket (status in `$SKIP_STATUSES`) + new CVEs → update description in place via `update-description`; never create a separate delta ticket
- No active ticket (none found, or existing is Done/Testing/QA/In Review) → create fresh ticket covering all current alerts
- Search fails → log real error, fall back to `CREATE_MODE = all`
- Ticket creation fails → log exact error, continue with remaining services
- Always run Step 4 (CSV batch update) once after ALL parallel jobs complete — never write CSV inside individual jobs
- Use only config-loaded values for project, labels, priority, story points, and skip-status logic
