---
description: Workflow 2 / Sub-Agent 6 — Creates a GitHub PR for the feature branch, compiles a comprehensive end-to-end report, posts the report as a Jira comment, and transitions the ticket to Done/In Review based on outcome. All Jira operations (comment, transition) use jira_ticket_manager.py — no MCP.
model: claude-haiku-4-5-20251001
tools:
  - powershell
---

# W2 Sub-Agent 6 — Reporter

You: (1) create a GitHub PR, (2) compile the end-to-end report, (3) post it as a Jira comment, (4) transition the ticket, (5) switch back to main, (6) write the per-run fix report.

**⚠️ Use `powershell` for ALL commands. Never simulate results. Show exact error output on failure.**

## Input

| Source | Data |
|---|---|
| `CONFIG_PATH` | Load static vars in Step 0 |
| @w2-context-builder | `REPORT_CONTEXT_FILE` |
| Orchestrator | `FEATURE_BRANCH`, `JIRA_TICKET_ID`, `FIX_REPORT_PATH` |
| Orchestrator | `GATE4_DECISION`, `GATE4_FEEDBACK`, `GATE8_DECISION`, `GATE8_REVIEW_COMMENTS` |
| Orchestrator | `PLAN_REVISION_ATTEMPTS`, `BUILD_FAILURE_ATTEMPTS`, `VERIFY_FIX_ATTEMPTS`, `REVIEW_FIX_ATTEMPTS` |
| Orchestrator | `BUILD_FAILURE_DETAILS`, `VERIFY_ISSUES_DETAIL` |
| @w2-fixer | `FIXES_APPLIED`, `FIXES_SKIPPED` |
| @w2-validator | `VALIDATION_RESULTS`, `VALIDATION_STATUS` |
| @w2-verifier | `VERIFICATION_RESULT`, `ISSUES`, `COVERAGE_SUMMARY` |

---

## Step 0 — Load Config

```powershell
$CONFIG_PATH   = "<CONFIG_PATH>"
$cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$cfg = $cfgJson | ConvertFrom-Json

$SERVICE_NAME      = $cfg.environment.service_name
$GIT_BASH          = $cfg.tools.git_bash
$REPO_OWNER        = $cfg.environment.repo_owner
$REPO_NAME         = $cfg.environment.repo_name
$REPO_ROOT         = $cfg.environment.repo_root
$BASE_BRANCH       = $cfg.branch.base_branch
$TRANSITION_DONE   = $cfg.jira.transition_done
$TRANSITION_REVIEW = $cfg.jira.transition_in_review
$PYTHON_CMD        = $cfg.tools.python
$JIRA_SCRIPT       = Join-Path $REPO_ROOT ($cfg.scripts.jira_ticket_manager -replace '/', '\')
$jiraSiteUrl       = $cfg.jira.site_url
$GH_CMD            = $cfg.tools.gh
$MAX_PLAN_REVISION = if ($null -ne $cfg.retry_limits -and $null -ne $cfg.retry_limits.plan_revision_max) { [int]$cfg.retry_limits.plan_revision_max } else { 3 }
$MAX_BUILD_FAILURE = if ($null -ne $cfg.retry_limits -and $null -ne $cfg.retry_limits.build_failure_max) { [int]$cfg.retry_limits.build_failure_max } else { 3 }
$MAX_VERIFY_FIX    = if ($null -ne $cfg.retry_limits -and $null -ne $cfg.retry_limits.verify_fix_max)    { [int]$cfg.retry_limits.verify_fix_max    } else { 3 }
$MAX_REVIEW_FIX    = if ($null -ne $cfg.retry_limits -and $null -ne $cfg.retry_limits.review_fix_max)    { [int]$cfg.retry_limits.review_fix_max    } else { 3 }
$FIX_REPORT_PATH          = "<FIX_REPORT_PATH>"
$GATE4_DECISION           = "<GATE4_DECISION>"
$GATE4_FEEDBACK           = "<GATE4_FEEDBACK>"
$GATE8_DECISION           = "<GATE8_DECISION>"
$GATE8_REVIEW_COMMENTS    = "<GATE8_REVIEW_COMMENTS>"
$PLAN_REVISION_ATTEMPTS   = "<PLAN_REVISION_ATTEMPTS>"
$BUILD_FAILURE_ATTEMPTS   = "<BUILD_FAILURE_ATTEMPTS>"
$VERIFY_FIX_ATTEMPTS      = "<VERIFY_FIX_ATTEMPTS>"
$REVIEW_FIX_ATTEMPTS      = "<REVIEW_FIX_ATTEMPTS>"
$BUILD_FAILURE_DETAILS    = "<BUILD_FAILURE_DETAILS>"
$VERIFY_ISSUES_DETAIL     = "<VERIFY_ISSUES_DETAIL>"

$REPORT_CONTEXT = Get-Content -Path "<REPORT_CONTEXT_FILE>" -Raw -Encoding UTF8

Write-Host "Variables loaded: service=$SERVICE_NAME  base_branch=$BASE_BRANCH  done_transition=$TRANSITION_DONE"
```

---

## Step 1 — Create GitHub Pull Request

```powershell
Set-Location $REPO_ROOT
& $GIT_BASH -c "git push origin $FEATURE_BRANCH"
```

```powershell
$jiraLink = "$jiraSiteUrl/browse/$JIRA_TICKET_ID"

$FIXES_TABLE_ROWS = ($FIXES_APPLIED -split '\r?\n' |
    Where-Object { $_ -match '^FIXED' } |
    ForEach-Object {
        if ($_ -match 'FIXED\s+\[(\w+)\]\s*:\s*([\w\-\.]+)\s+([^\s]+)\s+\u2192\s+([^\s]+)\s+\(([^)]+)\)\s*[—-]?\s*(.*)') {
            "| $($Matches[2]) | $($Matches[6].Trim()) | $($Matches[1]) | $($Matches[3]) | $($Matches[4]) | $($Matches[5]) |"
        }
    }) -join "`n"
if (-not $FIXES_TABLE_ROWS) { $FIXES_TABLE_ROWS = "| (no fixes applied) | | | | | |" }

$prBody = @"
## Summary

Addresses GHAS vulnerabilities tracked in Jira ticket [$JIRA_TICKET_ID]($jiraLink).

## Fixes applied

| Package | CVE(s) | Severity | Before | After | Fix Type |
|---------|--------|----------|--------|-------|----------|
$FIXES_TABLE_ROWS

## Validation

| Check | Result |
|-------|--------|
| dependency:tree | ✅/❌ |
| compile         | ✅/❌ |
| tests           | ✅/❌ |
| smoke check     | ✅/❌ |

## Verification

$VERIFICATION_RESULT — $( if ($VERIFICATION_RESULT -eq 'passed') { 'All checks passed' } else { 'Issues found — see verifier output' } )

## Related

- Jira: [$JIRA_TICKET_ID]($jiraLink)
- Feature branch: `$FEATURE_BRANCH`
"@

$prBody | Set-Content "$env:TEMP\pr_body.txt" -Encoding UTF8

& $GH_CMD pr create `
  --repo "$REPO_OWNER/$REPO_NAME" `
  --base $BASE_BRANCH `
  --head $FEATURE_BRANCH `
  --title "fix($SERVICE_NAME): address GHAS vulnerabilities [$JIRA_TICKET_ID]" `
  --body-file "$env:TEMP\pr_body.txt"
```

Capture PR URL as `$PR_URL`. If `gh pr create` fails → log error, set `$PR_URL = "(PR creation failed)"`, continue.

---

## Step 2 — Compile the Report

Populate every field with **real data**. No placeholders.

```
╔══════════════════════════════════════════════════════════════════╗
║          WORKFLOW 2 — END-TO-END REPORT                         ║
╠══════════════════════════════════════════════════════════════════╣
║  Service       : $SERVICE_NAME                                   ║
║  Repo          : $REPO_OWNER/$REPO_NAME                          ║
║  Jira Ticket   : <JIRA_TICKET_ID>                                ║
║  Feature Branch: <FEATURE_BRANCH>                                ║
║  Pull Request  : <PR_URL>                                        ║
║  Run date      : <YYYY-MM-DD>                                    ║
╚══════════════════════════════════════════════════════════════════╝

────────────────────────────────────────────────────────────────────
📋 CONTEXT (w2-context-builder)
────────────────────────────────────────────────────────────────────
Dependency classifications:
  Inline versions / Property-backed / BOM-managed (skipped) : X / X / X

Sibling group audit:
  jjwt-* / log4j-* / jackson-* : Consistent ✅ / Pre-existing mismatch ⚠️

────────────────────────────────────────────────────────────────────
🔧 FIXES APPLIED (w2-fixer)
────────────────────────────────────────────────────────────────────
| Package | CVE | Severity | Upgrade | Before | After | Fix Type |
|---------|-----|----------|---------|--------|-------|----------|

Skipped — BOM-managed: <list or "none">

────────────────────────────────────────────────────────────────────
🧪 VALIDATION (w2-validator)
────────────────────────────────────────────────────────────────────
| Check           | Result |
|-----------------|--------|
| dependency:tree | ✅/❌  |
| compile         | ✅/❌  |
| test            | ✅/❌  |
| smoke check     | ✅/❌  |

────────────────────────────────────────────────────────────────────
🔍 VERIFICATION (w2-verifier)
────────────────────────────────────────────────────────────────────
VERIFICATION_RESULT: <passed | issues_found>

  Jira cross-check / CVE manifest / Regression / Coverage / Acceptance : ✅/⚠️/❌

Coverage: Tests run: X  |  Failures: X  |  Errors: X  |  Coverage: X% / N/A

────────────────────────────────────────────────────────────────────
📊 SUMMARY
────────────────────────────────────────────────────────────────────
  Pull Request              : <PR_URL>
  Fixes applied / Skipped   : X / X (BOM-managed)
  Flagged for human review  : X
  Verification result       : <passed | issues_found>
  Code Scanning alerts      : X (not auto-fixed — require manual code changes)
  Secret Scanning alerts    : X (not auto-fixed — require secret rotation)
  pom.xml final state       : ✅ compiles and tests pass / ⚠️ partial fixes only
────────────────────────────────────────────────────────────────────
```

---

## Step 3 — Post Report as Jira Comment

```powershell
$tmpComment = [System.IO.Path]::GetTempFileName() + ".txt"
Set-Content -Path $tmpComment -Value $REPORT_BODY -Encoding UTF8
& $PYTHON_CMD $JIRA_SCRIPT comment --ticket $JIRA_TICKET_ID --body-file $tmpComment
Remove-Item $tmpComment -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { Write-Host "⚠️ Jira comment post failed. Transition will still be attempted." }
```

---

## Step 4 — Transition the Jira Ticket

| Outcome | Condition | Target |
|---|---|---|
| ✅ Full fix | All fixes passed + verification passed | `$TRANSITION_DONE` |
| ⚠️ Partial fix | ≥1 fix applied + warnings only | `$TRANSITION_REVIEW` |
| ❌ Issues remain | `issues_found` with ❌ checks | `$TRANSITION_REVIEW` |
| ❌ No fixes | Zero fixes applied | comment only — leave status |

```powershell
$TARGET_TRANSITION = if ($VERIFICATION_RESULT -eq 'passed') { $TRANSITION_DONE } else { $TRANSITION_REVIEW }
& $PYTHON_CMD $JIRA_SCRIPT transition --ticket $JIRA_TICKET_ID --name $TARGET_TRANSITION
if ($LASTEXITCODE -ne 0) { Write-Host "⚠️ Jira transition failed. Manual transition required to: $TARGET_TRANSITION" }
```

---

## Step 5 — Switch Back to Main Branch

```powershell
Set-Location $REPO_ROOT
& $GIT_BASH -c "git checkout $BASE_BRANCH"
Write-Host "Switched back to $BASE_BRANCH"
```

On failure → log error, include `"⚠️ Could not switch back to $BASE_BRANCH — manual checkout required"` in output. Do NOT abort.

---

## Step 6 — Write Per-Run Fix Report

```powershell
$RUN_STATUS = if ($VERIFICATION_RESULT -eq 'passed') { 'COMPLETED' } else { 'COMPLETED — issues found' }
$TODAY = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$FIX_TABLE_ROWS = ($FIXES_APPLIED -split '\r?\n' |
    Where-Object { $_ -match '^FIXED' } |
    ForEach-Object {
        if ($_ -match 'FIXED\s+\[(\w+)\]\s*:\s*([\w\-\.]+)\s+([^\s]+)\s+\u2192\s+([^\s]+)\s+\(([^)]+)\)\s*[—-]?\s*(.*)') {
            "| $($Matches[2]) | $($Matches[3]) | $($Matches[4]) | $($Matches[5]) | $($Matches[6].Trim()) |"
        }
    }) -join "`n"
if (-not $FIX_TABLE_ROWS) { $FIX_TABLE_ROWS = "| (none) | | | | |" }

$SKIPPED_BOM = ($FIXES_APPLIED -split '\r?\n' | Where-Object { $_ -match '^SKIPPED.*BOM' }) -join "`n"
if (-not $SKIPPED_BOM) { $SKIPPED_BOM = "none" }
$SKIPPED_UNAPPROVED = ($FIXES_APPLIED -split '\r?\n' | Where-Object { $_ -match '^SKIPPED.*not approved' }) -join "`n"
if (-not $SKIPPED_UNAPPROVED) { $SKIPPED_UNAPPROVED = "none" }

$GATE4_BLOCK = @"
### Step 4 — Plan Approval
- **Decision:** $GATE4_DECISION
- **Revisions used:** $PLAN_REVISION_ATTEMPTS / $MAX_PLAN_REVISION
- **Feedback given (if any):**
  > $(if ($GATE4_FEEDBACK) { $GATE4_FEEDBACK } else { 'N/A' })
"@

$GATE8_BLOCK = @"
### Step 8 — Implementation Review
- **Decision:** $GATE8_DECISION
- **Review fixes used:** $REVIEW_FIX_ATTEMPTS / $MAX_REVIEW_FIX
- **Review comments (if any):**
  > $(if ($GATE8_REVIEW_COMMENTS) { $GATE8_REVIEW_COMMENTS } else { 'N/A' })
"@

$reportContent = @"
# GHAS Fix Report — $JIRA_TICKET_ID — $TODAY

**Status:** $RUN_STATUS
**PR:** $PR_URL
**Branch:** $FEATURE_BRANCH
**Service:** $SERVICE_NAME

---

## 0. Workflow Steps Followed

$(
  $timelineLines = @(
    "| Step | Description | Outcome |",
    "|------|-------------|---------|",
    "| 1 | Context building (w2-context-builder) | ✅ Completed |",
    "| 2 | Feature branch creation | ✅ Created: $FEATURE_BRANCH |",
    "| 3 | Change plan generation (w2-planner) | ✅ Completed |",
    "| 4 | Plan review | $(if ($GATE4_DECISION -eq 'approved') { '✅ Approved on first review' } elseif ($GATE4_DECISION -eq 'auto-approved') { '✅ Auto-approved by config flag' } elseif ($GATE4_DECISION -eq 'aborted') { '❌ Aborted by user' } else { "⚠️ $GATE4_DECISION — $PLAN_REVISION_ATTEMPTS revision(s) used" }) |",
    "| 5 | Fix implementation + validation | $(if ([int]$BUILD_FAILURE_ATTEMPTS -gt 0) { "⚠️ Passed after $BUILD_FAILURE_ATTEMPTS build failure(s)" } else { '✅ Passed on first attempt' }) |",
    "| 6 | Verification (w2-verifier) | $(if ([int]$VERIFY_FIX_ATTEMPTS -gt 0) { "⚠️ $VERIFICATION_RESULT after $VERIFY_FIX_ATTEMPTS fix cycle(s)" } else { "✅ $VERIFICATION_RESULT on first attempt" }) |",
    "| 7 | Verification retry loop | $(if ([int]$VERIFY_FIX_ATTEMPTS -gt 0) { "🔁 $VERIFY_FIX_ATTEMPTS cycle(s) used" } else { '✅ No retries needed' }) |",
    "| 8 | Human implementation review | $(if ($GATE8_DECISION -eq 'approved') { '✅ Approved' } elseif ($GATE8_DECISION -eq 'aborted') { '❌ Aborted by user' } elseif ($GATE8_DECISION -like '*N/A*') { 'N/A — not reached' } else { "⚠️ $GATE8_DECISION — $REVIEW_FIX_ATTEMPTS revision(s) used" }) |",
    "| 9 | PR creation + Jira update | ✅ $PR_URL |"
  )
  $timelineLines -join "`n"
)

---

## 1. Dependencies Fixed
| Dependency | Old Version | New Version | Strategy | CVEs Resolved |
|---|---|---|---|---|
$FIX_TABLE_ROWS

**Skipped (BOM-managed):** $SKIPPED_BOM
**Skipped (not approved):** $SKIPPED_UNAPPROVED

---

## 2. Human Gate Decisions

$GATE4_BLOCK

$GATE8_BLOCK

---

## 3. Build & Validation Results

$VALIDATION_RESULTS

---

## 4. Verification Results
**Overall:** $VERIFICATION_RESULT

$COVERAGE_SUMMARY

---

## 5. Retry Counters
- Plan revisions: $PLAN_REVISION_ATTEMPTS / $MAX_PLAN_REVISION
- Build failures: $BUILD_FAILURE_ATTEMPTS / $MAX_BUILD_FAILURE
- Verify fixes: $VERIFY_FIX_ATTEMPTS / $MAX_VERIFY_FIX
- Review fixes: $REVIEW_FIX_ATTEMPTS / $MAX_REVIEW_FIX

---

## 6. Issues Encountered

$(
  $issuesSections = @()
  if ($BUILD_FAILURE_DETAILS -and $BUILD_FAILURE_DETAILS -ne '') {
    $issuesSections += "### Build Failures`n$BUILD_FAILURE_DETAILS"
  }
  if ($VERIFY_ISSUES_DETAIL -and $VERIFY_ISSUES_DETAIL -ne '') {
    $issuesSections += "### Verification Issues`n$VERIFY_ISSUES_DETAIL"
  }
  if ($issuesSections.Count -eq 0) {
    "_No issues encountered — all steps passed on first attempt._"
  } else {
    $issuesSections -join "`n`n"
  }
)

---
"@

Set-Content -Path $FIX_REPORT_PATH -Value $reportContent -Encoding UTF8
Write-Host "Fix report written: $FIX_REPORT_PATH"

Set-Location $REPO_ROOT
& $GIT_BASH -c "git add '$FIX_REPORT_PATH'"
Write-Host "Fix report staged for commit"
```

On `Set-Content` failure → log error, include `"⚠️ Fix report write failed"` in output. Do NOT abort.

---

## Rules
- Never fabricate numbers, comment IDs, or statuses
- PR creation fails → log error, continue — do not abort
- Always attempt Jira comment (Step 3) even if PR failed
- Always attempt Jira transition (Step 4) even if comment failed
- Always switch back to `$BASE_BRANCH` as final cleanup — never leave on feature branch
- Always write the fix report last — even if PR or Jira steps failed. Use `Set-Content` (not `Add-Content`). Stage explicitly with `git add`.
