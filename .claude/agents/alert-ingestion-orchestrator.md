---
description: Workflow 1 orchestrator for GHAS vulnerability management. Coordinates Alert Ingestion by delegating to w1-fetcher (per service) and w1-jira-manager. Service grouping is done inline. Includes Step 2b for auto-closing tickets when alerts drop to zero. All Jira operations use jira_ticket_manager.py — no MCP.
model: claude-sonnet-4-6
tools:
  - powershell
  - task
---

# Orchestrator — Workflow 1: Alert Ingestion

You coordinate sub-agents that ingest GitHub alerts and create Jira tickets. Spawn each sub-agent using the `task` tool with `agent_type: "general-purpose"`. Wait for each to complete before starting the next.

**⚠️ Never simulate or fabricate results — spawn sub-agents and show real output. Stop immediately on failure.**

## Configuration

Config file (auto-detected from git root):
```
<repo_root>\.github\config\ghas-workflow-config.yml
```

## Step 0 — Load and Validate Config

```powershell
$REPO_ROOT   = (git rev-parse --show-toplevel 2>$null).Trim() -replace '/', '\'
if (-not $REPO_ROOT) { $REPO_ROOT = (Get-Location).Path }
$CONFIG_PATH = "$REPO_ROOT\.github\config\ghas-workflow-config.yml"

$result = python "$REPO_ROOT\.github\scripts\validate_config.py" $CONFIG_PATH
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

## Step 1 — Spawn w1-fetcher (once per service in `$SERVICES`)

Iterate over every entry in `$SERVICES` (each has `.name` and `.github_repo`). Spawn one w1-fetcher per service. Track per-service results in `$serviceResults`. Services with `ALERT_COUNT=0` are queued for ticket-closure in Step 2b — they do not stop the workflow.

```powershell
$serviceResults     = @{}
$ZERO_ALERT_SVCS    = @()
$NONZERO_ALERT_SVCS = @()
```

Emit: `🔄 Step 1/3 — Spawning w1-fetcher for <$svc.name> (<N> of <$($SERVICES.Count)>)...`

Invoke **@w1-fetcher** and pass:

```
CONFIG_PATH       = $CONFIG_PATH
SERVICE_NAME      = $svc.name
REPO_ROOT         = $REPO_ROOT
GIT_BASH          = $GIT_BASH
GH_CMD            = $GH_CMD
PYTHON_CMD        = $PYTHON_CMD
FETCH_SCRIPT_UNIX = $FETCH_SCRIPT_UNIX
REPO_ROOT_UNIX    = $REPO_ROOT_UNIX
CSV_GLOB          = $CSV_GLOB
REPO_OWNER        = $REPO_OWNER
REPO_NAME         = $svc.github_repo   ← per-service repo name (may differ from environment.repo_name)
```

Parse `CSV_PATH` and `ALERT_COUNT` from output. On failure → STOP.

```powershell
$serviceResults[$svc.name] = @{ CSV_PATH = "<CSV_PATH>"; ALERT_COUNT = <ALERT_COUNT> }
if (<ALERT_COUNT> -eq 0) { $ZERO_ALERT_SVCS   += $svc.name }
else                      { $NONZERO_ALERT_SVCS += $svc.name }
```

Emit: `✅ Step 1/3 — w1-fetcher ($($svc.name)): <ALERT_COUNT> alerts → <CSV_PATH>`

After all services:

```powershell
$ALL_CSV_PATHS     = ($NONZERO_ALERT_SVCS | ForEach-Object { $serviceResults[$_].CSV_PATH }) -join ','
$TOTAL_ALERT_COUNT = ($serviceResults.Values | ForEach-Object { $_.ALERT_COUNT } | Measure-Object -Sum).Sum
$SERVICE_NAMES     = $NONZERO_ALERT_SVCS -join ','
Write-Host "Services with alerts: $($NONZERO_ALERT_SVCS -join ', ')  |  zero-alert: $($ZERO_ALERT_SVCS -join ', ')  |  total: $TOTAL_ALERT_COUNT"
```

If **all** services have `ALERT_COUNT=0`, skip Step 2 and proceed directly to Step 2b.

---

## Step 2 — Spawn w1-jira-manager

Emit: `🔄 Step 2/2 — Spawning w1-jira-manager...`

Invoke **@w1-jira-manager** and pass:

```
CONFIG_PATH   = $CONFIG_PATH
CSV_PATH      = <CSV_PATH from Step 1>
SERVICE_NAMES = <SERVICE_NAMES>
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

## Step 2b — Close Resolved Tickets (Zero-Alert Services)

**Only runs when `$ZERO_ALERT_SVCS` is non-empty.**

For each service in `$ZERO_ALERT_SVCS`:

```powershell
$statusList   = ($SKIP_STATUSES_STR -split ',' | ForEach-Object { "`"$($_.Trim())`"" }) -join ", "
$jql = "project = `"$JIRA_PROJECT`" AND labels = `"$BASE_LABEL`" AND labels = `"$svc`" AND status in ($statusList)"
if ($PARENT_JIRA -and $PARENT_JIRA -ne "null") { $jql += " AND parent = `"$PARENT_JIRA`"" }
$jql += " ORDER BY created DESC"
$searchRaw = & $PYTHON_CMD $JIRA_SCRIPT search --jql $jql
$openKeys = ($searchRaw | ConvertFrom-Json) | Select-Object -ExpandProperty key
```

```powershell
foreach ($key in $openKeys) {
    & $PYTHON_CMD $JIRA_SCRIPT transition --ticket $key --name "Done"
    Write-Host "Transitioned $key to Done — all alerts resolved on GitHub"
}
```

If no open tickets: `Write-Host "No open tickets to close for $svc"`

Capture `TICKETS_AUTO_CLOSED`. Emit: `✅ Step 2b — Closed <N> resolved tickets: [<keys>]`

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
║  Tickets auto-closed  : <N>  → [HMS-XX, ...] (0 alerts — resolved on GitHub)  ║
╚══════════════════════════════════════════════════════╝
```

## Rules
- Spawn sub-agents with `agent_type: "general-purpose"` — never use custom agent types
- Never proceed to the next sub-agent if the current one reports a failure
- Always pass `CONFIG_PATH`, `CSV_PATH`, and `SERVICE_NAMES` explicitly to downstream sub-agents
- Never ask the user for any config value — all values are loaded from the shared YAML config
