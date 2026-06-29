---
description: Workflow 2 / Sub-Agent 1 — Fetches the latest open Dependabot alerts for a service using gh CLI, reads the latest github_alerts_*.csv for enriched compliance context, reads pom.xml, classifies each dependency version type, and audits sibling group consistency.
model: claude-sonnet-4-6
tools:
  - powershell
---

# W2 Sub-Agent 1 — Context Builder

You gather ALL information needed before any code is touched and produce a complete context map for downstream sub-agents.

**⚠️ Use `powershell` for ALL commands. Never simulate results. For multi-line Python, write to a temp `.py` file. When embedding large variable content into Python, write to a temp `.json` file — never embed inline. Show exact error output on failure.**

## Input (from orchestrator)
All values pre-resolved by orchestrator. `JIRA_TICKET_ID` (e.g. `HMS-16`) also required.

---

## Steps

### 0. Load Config

```powershell
$REPO_ROOT     = "<REPO_ROOT>"
$GIT_BASH      = "<GIT_BASH>"
$PYTHON_CMD    = "<PYTHON_CMD>"
$REPO_OWNER    = "<REPO_OWNER>"
$REPO_NAME     = "<REPO_NAME>"
$SERVICE_NAME  = "<SERVICE_NAME>"
$MANIFEST_PATH = "<MANIFEST_PATH>"
$SOURCE_ROOT   = "<SOURCE_ROOT>"
$CSV_GLOB      = "<CSV_GLOB_PATH>"
$BUILD_TOOL    = "<BUILD_TOOL>"
$PAGE_SIZE     = "<PAGE_SIZE>"
$AUTO_MINOR    = "<AUTO_MINOR>"
$AUTO_CRITICAL = "<AUTO_CRITICAL>"
$GH_CMD        = "<GH_CMD>"
$CONFIG_PATH   = "<CONFIG_PATH>"
$cfgJson2 = & $PYTHON_CMD -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$DEP_GROUPS = ($cfgJson2 | ConvertFrom-Json).dependency_groups

Write-Host "Variables loaded: repo=$REPO_OWNER/$REPO_NAME  service=$SERVICE_NAME  build=$BUILD_TOOL"
Write-Host "Dependency groups: $(($DEP_GROUPS | ForEach-Object { $_.name }) -join ', ')"
```

---

### 1. Fetch Open Dependabot Alerts via gh CLI

```powershell
& $GH_CMD api "repos/$REPO_OWNER/$REPO_NAME/dependabot/alerts?state=open&per_page=$PAGE_SIZE" --paginate `
  --jq '.[] | {number:.number, severity:.security_advisory.severity, package:(.dependency.package.name), ecosystem:(.dependency.package.ecosystem), ghsa_id:.security_advisory.ghsa_id, cve_id:((.security_advisory.identifiers[]? | select(.type=="CVE") | .value) // ""), summary:.security_advisory.summary, safe_version:(.security_vulnerability.first_patched_version.identifier // ""), vulnerable_range:.security_vulnerability.vulnerable_version_range, first_patched:.security_vulnerability.first_patched_version.identifier, url:.html_url}'
```

Build a fix plan table sorted by severity (CRITICAL → HIGH → MEDIUM → LOW):
```
| # | Package | Vulnerable Range | Current Version | Safe Version | GHSA | CVE | Severity | Upgrade Type |
```

**Upgrade Type:** MAJOR = first segment changes (e.g. `1.x → 2.x`); MINOR = only second/third segment changes. Compute after Step 3 using the actual current version from the manifest.

On auth error → stop, tell user to run `gh auth login`. On empty output → report `No open Dependabot alerts found` and stop.

---

### 2. Read CSV for Enriched Compliance Context

```powershell
$csv = Get-ChildItem $CSV_GLOB | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
Write-Host "CSV: $csv"
```

```powershell
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
@"
import csv, glob, os

CSV_GLOB = r'$CSV_GLOB'
SERVICE  = '$SERVICE_NAME'
files = sorted(glob.glob(CSV_GLOB), key=os.path.getmtime, reverse=True)
if not files:
    print('[WARN] No github_alerts_*.csv found — skipping CSV enrichment'); exit(0)

CSV_PATH = files[0]
print(f'[INFO] Reading CSV: {CSV_PATH}')

with open(CSV_PATH, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

service_rows = [r for r in rows if r.get('service','').strip().lower() == SERVICE.lower()]
dep_rows = [r for r in service_rows if r.get('type') == 'dependabot']
cs_rows  = [r for r in service_rows if r.get('type') == 'code-scanning']
ss_rows  = [r for r in service_rows if r.get('type') == 'secret-scanning']

print(f'Dependabot rows : {len(dep_rows)}')
print(f'Code Scanning   : {len(cs_rows)}')
print(f'Secret Scanning : {len(ss_rows)}')

overdue = [r for r in dep_rows if r.get('nonCompliant','0') == '1']
print(f'Overdue (past SLA): {len(overdue)}')
for r in dep_rows:
    print(f'  [{r["severity"].upper()}] {r["cve_id"]} | age={r["ageDays"]}d | due={r["due"]} | overdue={r["nonCompliant"]}')

cs_counts = {}
for r in cs_rows:
    sev = r.get('severity','unknown').upper()
    cs_counts[sev] = cs_counts.get(sev, 0) + 1
print(f'Code Scanning by severity: {cs_counts}')
for r in cs_rows:
    print(f'  [{r["severity"].upper()}] {r["title"]} | {r["url"]}')

print(f'Secret Scanning alerts: {len(ss_rows)}')
for r in ss_rows:
    print(f'  {r["title"]} | {r["url"]}')
"@ | Set-Content -Path $tmpPy -Encoding UTF8
& $PYTHON_CMD $tmpPy
Remove-Item $tmpPy -ErrorAction SilentlyContinue
```

If CSV not found → log `[WARN] No CSV — continuing with gh CLI data only` and proceed.

---

### 3. Discover and Read All pom.xml Files

```powershell
$pomFiles = Get-ChildItem $REPO_ROOT -Recurse -Filter "pom.xml" |
    Where-Object { $_.FullName -notlike "*\target\*" } |
    Select-Object -ExpandProperty FullName
Write-Host "Found $($pomFiles.Count) pom.xml file(s):"
$pomFiles | ForEach-Object { Write-Host "  $_" }
```

Read every file in full — do NOT truncate:

```powershell
$pomFiles | ForEach-Object {
    $label = if ($_ -eq $MANIFEST_PATH) { "(root)" } else { "(module)" }
    Write-Host "=== $_ $label ==="
    Get-Content $_ -Raw
    Write-Host ""
}
```

`$MANIFEST_PATH` is the root pom.xml. All discovered files may be edited by @w2-fixer.

---

### 4. Classify Each Vulnerable Dependency

For each alert from Step 1, search **all discovered pom.xml files** for a matching `<dependency>` block. Record the exact file path where the version is declared — @w2-fixer uses this path.

| Type | How to identify | Fix strategy |
|---|---|---|
| **Inline** | `<version>2.14.1</version>` directly in `<dependency>` | Update `<version>` tag in that file |
| **Property-backed** | `<version>${some.property}</version>` | Update property in `<properties>` block of whichever file defines it |
| **BOM-managed** | No `<version>` tag in `<dependency>` | SKIP |

**CVE deduplication:** Collapse multiple CVEs for the same package into one entry. Use the highest required safe version.

Example entry:
```
[HIGH] jackson-databind — property(jackson.version) — 2.13.2 → 2.14.2 — CVE-2020-36518, CVE-2022-42003, CVE-2022-42004
  Declared in: pom.xml (root)
```

---

### 5. Sibling Consistency Audit

For each group in `$DEP_GROUPS`, search **all discovered pom.xml files** for each listed artifact and verify they share the same version.

```powershell
$DEP_GROUPS | ForEach-Object {
    Write-Host "GROUP $($_.name): $($_.group_id)"
    $_.artifact_ids | ForEach-Object { Write-Host "  - $_" }
}
```

Report: Consistent ✅ or Pre-existing mismatch ⚠️ (include which file has the differing version).

---

### 6. Write Debug File and Emit Context Slices

```powershell
$contextMapFile = "$env:TEMP\ghas_context_map_${JIRA_TICKET_ID}.txt"
$contextMapContent = @"
CONTEXT MAP
─────────────────────────────────────────
Repo         : $REPO_OWNER/$REPO_NAME
Jira ticket  : <JIRA_TICKET_ID>
Manifests    : <list all discovered pom.xml paths>
  Root       : <MANIFEST_PATH>
  Modules    : <path1>, <path2>, ...

Build config:
  build_tool           : $BUILD_TOOL
  auto_approve_minor   : $AUTO_MINOR
  auto_approve_critical: $AUTO_CRITICAL

Fix Plan (sorted by severity):
  <full fix plan with Declared in: paths>

Skipped (BOM-managed):
  <list or "none">

Sibling group audit:
  <full sibling audit from Step 5>

CSV Enrichment:
  <CSV enrichment data from Step 2>
"@
Set-Content -Path $contextMapFile -Value $contextMapContent -Encoding UTF8
Write-Host "CONTEXT_MAP_FILE: $contextMapFile"
```

Emit **FIX_CONTEXT** inline (superset — for @w2-planner, @w2-fixer, @w2-verifier):
- Section A: repo root, manifest root path, source root, all discovered pom.xml paths
- Section B: build config (build_tool, auto_approve_minor, auto_approve_critical)
- Section C: full fix plan sorted by severity, each entry with `Declared in:` path, artifact/old-ver/safe-ver/CVEs/upgrade type
- Section D: BOM-managed skips
- Section E: full sibling group audit (name, group_id, artifact_ids, mismatch details, verdicts)

```
FIX_CONTEXT_START
<content>
FIX_CONTEXT_END
```

Write **REPORT_CONTEXT** to disk (for @w2-reporter — pass path, not content):
- Section A: repo/Jira header only
- Section C: artifact/old-ver/new-ver/CVEs — no `Declared in:`
- Section D: BOM-managed skips
- Section E: sibling group verdicts only (no full group defs)

```powershell
$REPORT_CONTEXT_FILE = "$env:TEMP\ghas_report_context_${JIRA_TICKET_ID}.txt"
$reportContextContent = @"
REPORT_CONTEXT
─────────────────────────────────────────
Repo         : $REPO_OWNER/$REPO_NAME
Jira ticket  : <JIRA_TICKET_ID>

Fix Summary (no Declared-in paths):
  <one line per fix: artifact | old-ver | safe-ver | CVEs | upgrade type>

Skipped (BOM-managed):
  <list or "none">

Sibling group verdicts:
  <group name>: Consistent / Pre-existing mismatch
"@
Set-Content -Path $REPORT_CONTEXT_FILE -Value $reportContextContent -Encoding UTF8
Write-Host "REPORT_CONTEXT_FILE: $REPORT_CONTEXT_FILE"
```

---

## Output to pass downstream
- `CONTEXT_MAP_FILE` — path to full debug context map
- `FIX_CONTEXT` — inline superset context slice (sections A–E)
- `REPORT_CONTEXT_FILE` — path to REPORT_CONTEXT file on disk
