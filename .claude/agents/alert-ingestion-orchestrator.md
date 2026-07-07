---
description: Workflow 1 orchestrator for GHAS vulnerability management. Spawns w1-fetcher once (fetch_alerts.sh handles all services internally); derives per-service alert counts from the CSV; delegates to w1-jira-manager for Jira ticket management. All Jira operations use jira_ticket_manager.py — no MCP.
model: claude-sonnet-4-6
tools:
  - powershell
  - task
---

# Orchestrator — Workflow 1: Alert Ingestion

You coordinate sub-agents that ingest GitHub alerts and create Jira tickets. Spawn each sub-agent using the `task` tool with `agent_type: "general-purpose"` and the `model` matching each agent's frontmatter (annotated per step below).

**⚠️ Never simulate or fabricate results — spawn sub-agents and show real output. Stop immediately on failure.**

## Configuration

Config file (auto-detected from git root):
```
<repo_root>\.claude\config\ghas-w1-config.yml
```

## Step 0 — Load and Validate Config

```powershell
$REPO_ROOT   = (git rev-parse --show-toplevel 2>$null).Trim() -replace '/', '\'
if (-not $REPO_ROOT) { $REPO_ROOT = (Get-Location).Path }
$CONFIG_PATH = "$REPO_ROOT\.claude\config\ghas-w1-config.yml"

$result = python "$REPO_ROOT\.claude\scripts\validate_config.py" $CONFIG_PATH
if ($LASTEXITCODE -ne 0) { Write-Host "Aborting: config validation failed."; exit 1 }
Write-Host $result

$cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$cfg = $cfgJson | ConvertFrom-Json

$REPO_OWNER      = $cfg.environment.repo_owner
$REPO_NAME       = $cfg.environment.repo_name
$SERVICE_NAME    = $cfg.environment.service_name
$REPO_ROOT       = $cfg.environment.repo_root
$GIT_BASH        = $cfg.tools.git_bash
$GH_CMD          = $cfg.tools.gh
$PYTHON_CMD      = $cfg.tools.python
$JIRA_PROJECT    = $cfg.jira.project_key
$FETCH_SCRIPT    = Join-Path $REPO_ROOT ($cfg.scripts.fetch_alerts -replace '/','\')
$JIRA_SCRIPT     = Join-Path $REPO_ROOT ($cfg.scripts.jira_ticket_manager -replace '/','\')
$CSV_GLOB        = Join-Path $REPO_ROOT ($cfg.csv.glob_pattern)
$BASE_LABEL      = ($cfg.jira.labels | Select-Object -First 1)
$PARENT_JIRA     = $cfg.jira.parent_jira
$SEARCH_LABELS   = if ($cfg.jira.search_labels) { $cfg.jira.search_labels -join ',' } else { $BASE_LABEL }
$SKIP_STATUSES_STR = ($cfg.jira.skip_statuses_for_duplicate_check) -join ','

$SERVICES = if ($cfg.services -and @($cfg.services).Count -gt 0) {
    @($cfg.services)
} else {
    @([PSCustomObject]@{ name = $SERVICE_NAME; github_repo = $REPO_NAME })
}

$FETCH_SCRIPT_UNIX = $FETCH_SCRIPT -replace '\\', '/'
$REPO_ROOT_UNIX    = $REPO_ROOT    -replace '\\', '/'

Write-Host "Loaded: repo=$REPO_OWNER/$REPO_NAME  service=$SERVICE_NAME  jira=$JIRA_PROJECT  services=$(@($SERVICES) | ForEach-Object { $_.name } | Join-String -Separator ',')  skip_statuses=$SKIP_STATUSES_STR  parent_jira=$PARENT_JIRA"
```

If this step fails → stop immediately.

---

## Step 1 — Spawn w1-fetcher (once)

Emit: `🔄 Step 1/3 — Spawning w1-fetcher...`

Invoke **@w1-fetcher** (`model: haiku`) and pass:

```
CONFIG_PATH       = $CONFIG_PATH
REPO_ROOT         = $REPO_ROOT
GIT_BASH          = $GIT_BASH
GH_CMD            = $GH_CMD
PYTHON_CMD        = $PYTHON_CMD
FETCH_SCRIPT_UNIX = $FETCH_SCRIPT_UNIX
REPO_ROOT_UNIX    = $REPO_ROOT_UNIX
CSV_GLOB          = $CSV_GLOB
REPO_OWNER        = $REPO_OWNER
REPO_NAME         = $REPO_NAME
```

Parse `CSV_PATH` and `ALERT_COUNT` from output. On failure → STOP.

Emit: `✅ Step 1/3 — w1-fetcher complete: ALERT_COUNT=<ALERT_COUNT>, CSV=<CSV_PATH>`

If `ALERT_COUNT=0` → show Final Output with zero alerts and stop.

---

## Step 1.5 — Determine per-service alert counts from CSV

```powershell
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
@"
import csv, json
CSV_PATH      = r'$CSV_PATH'
SERVICE_NAMES = [s.strip() for s in '$($SERVICES | ForEach-Object { $_.name } | Join-String -Separator ",")'.split(',')]
counts = {svc: 0 for svc in SERVICE_NAMES}
with open(CSV_PATH, newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        svc = row.get('service','').strip()
        if svc in counts:
            counts[svc] += 1
print(json.dumps(counts))
"@ | Set-Content -Path $tmpPy -Encoding UTF8

$countsJson = & $PYTHON_CMD $tmpPy
Remove-Item $tmpPy -ErrorAction SilentlyContinue
$counts = $countsJson | ConvertFrom-Json

$ZERO_ALERT_SVCS    = @($counts.PSObject.Properties | Where-Object { $_.Value -eq 0 } | Select-Object -ExpandProperty Name)
$NONZERO_ALERT_SVCS = @($counts.PSObject.Properties | Where-Object { $_.Value -gt 0 } | Select-Object -ExpandProperty Name)
$SERVICE_NAMES      = $NONZERO_ALERT_SVCS -join ','
Write-Host "Nonzero alert services: $SERVICE_NAMES  |  Zero-alert: $($ZERO_ALERT_SVCS -join ', ')"
```

If `$NONZERO_ALERT_SVCS` is empty → show Final Output with zero alerts for all services and stop.

---

## Step 2 — Spawn w1-jira-manager

Emit: `🔄 Step 2/2 — Spawning w1-jira-manager...`

Invoke **@w1-jira-manager** (`model: sonnet`) and pass:

```
CONFIG_PATH   = $CONFIG_PATH
CSV_PATH      = $CSV_PATH
SERVICE_NAMES = $SERVICE_NAMES
SKIP_STATUSES = $SKIP_STATUSES_STR
PYTHON_CMD    = $PYTHON_CMD
JIRA_SCRIPT   = $JIRA_SCRIPT
JIRA_PROJECT  = $JIRA_PROJECT
BASE_LABEL    = $BASE_LABEL
CSV_GLOB      = $CSV_GLOB
PARENT_JIRA   = $PARENT_JIRA
SEARCH_LABELS = $SEARCH_LABELS
```

Parse ticket counts from output. On complete failure → STOP.

Emit: `✅ Step 2/2 — w1-jira-manager complete`

---

## Final Output

```
╔══════════════════════════════════════════════════════╗
║      WORKFLOW 1 — ALERT INGESTION COMPLETE           ║
╠══════════════════════════════════════════════════════╣
║  Services processed   : <N> (<comma-list>)           ║
║  Total alerts         : <N> (<SEVERITY_BREAKDOWN>)   ║
║  Jira tickets created : <N>  → [HMS-XX, ...]         ║
║  Jira tickets skipped : <N>  (no new CVEs)           ║
║  Zero-alert services  : <N>  (<comma-list>) — noted only, no tickets closed  ║
╚══════════════════════════════════════════════════════╝
```

---

## Step 3 — Offer to Run Workflow 2

After printing the Final Output, use `ask_user` to prompt:

```
Workflow 1 complete. Would you like to run Workflow 2 (Vulnerability Resolver) now?

  Tickets available for remediation: <list each CREATED/UPDATED ticket as "HMS-XX — <summary>">

  → yes   Run Workflow 2 now (you will be asked for the Jira ticket ID)
  → no    Stop here — you can run Workflow 2 later with the ticket ID above
```

- If the user says **yes**: ask which ticket ID to start with (if more than one was created/updated), then hand off to the vuln-resolver-orchestrator with that ticket ID.
- If the user says **no**: acknowledge and stop. Remind them to run Workflow 2 with the ticket ID shown above.
- If **no tickets** were created or updated (all skipped or zero-alert): skip this prompt entirely — there is nothing to remediate.

---

## Rules
- Spawn sub-agents with `agent_type: "general-purpose"` and `model` matching each agent's frontmatter annotation — never use custom agent types
- Never proceed to the next sub-agent if the current one reports a failure
- Always pass `CONFIG_PATH`, `CSV_PATH`, and `SERVICE_NAMES` explicitly to downstream sub-agents
- `$SERVICE_NAMES` is derived from CSV row counts in Step 1.5 — contains only services with at least one alert row
- Never ask the user for any config value — all values are loaded from the shared YAML config
