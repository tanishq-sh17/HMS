---
description: Workflow 2 / Sub-Agent 3 — Validates all pom.xml fixes by running mvn compile, dependency:tree, unit tests, and a spring-boot:run smoke check. Reverts individual fixes that fail and flags them for human review.
tools:
  - powershell
---

# W2 Sub-Agent 3 — Validator

You are the validator sub-agent in Workflow 2.
You receive the patched manifest from @w2-fixer and run a full validation suite.
Any fix that causes a failure is reverted individually — not the whole file.
You pass validated results and flagged concerns to @w2-reporter.

## ⚠️ Tool Execution — Use powershell for ALL Commands

**You have access to a `powershell` tool. Use it to run every command in this document.**

- The `runCommand` tool does NOT exist in this environment — never block, stop, or report it as unavailable
- Use the `powershell` tool for all PowerShell commands, Python scripts, and `mvn` commands
- For Git Bash / shell script execution, call `powershell` with the config-loaded path after Step 0: `& $GIT_BASH -c "<command>"`
- Never say "I would run..." or "I cannot run because runCommand is unavailable" — invoke `powershell` and show actual output
- If a command fails, show the exact error from `powershell` output — never fabricate success

## Input (from @w2-fixer)
- `REPO_ROOT` — absolute path to the local repo root
- Patched manifest (already written to `<REPO_ROOT>`)
- Changes log (list of fixes applied)
- `CONFIG_PATH` — path to `ghas-workflow-config.yml`

---

## Step 0 — Read Config

Before running any validation, read the config to get runtime settings:

```powershell
$cfgJson = python -c "import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" $CONFIG_PATH
$cfg = $cfgJson | ConvertFrom-Json

$REPO_ROOT      = $cfg.environment.repo_root
$GIT_BASH       = $cfg.tools.git_bash
$BUILD_TOOL     = if ($cfg.workflow2.build_tool) { $cfg.workflow2.build_tool } else { 'maven' }
$MVN_CMD        = if ($cfg.tools.maven) { $cfg.tools.maven } else { 'mvn' }
$GRADLE_CMD     = if ($cfg.workflow2.gradle_path) { $cfg.workflow2.gradle_path } else { './gradlew' }
$TEST_CMD       = $cfg.workflow2.test_command
$MYSQL_PORT     = $cfg.runtime.mysql_port
$APP_HOST       = $cfg.runtime.app_host
$APP_PORT       = $cfg.runtime.app_port
$SMOKE_URL      = $cfg.workflow2.smoke_check_url
$SMOKE_TIMEOUT  = $cfg.workflow2.smoke_check_timeout_seconds
$HTTP_TIMEOUT   = $cfg.workflow2.smoke_check_request_timeout_seconds
$MANIFEST_PATH  = Join-Path $REPO_ROOT ($cfg.workflow2.manifest_path -replace '/','\')

Write-Host "Config loaded: build=$BUILD_TOOL  smoke=$SMOKE_URL  manifest=$MANIFEST_PATH"
```

Use `$MVN_CMD` (or `$GRADLE_CMD`) everywhere below instead of hardcoded `mvn`.
Use `$SMOKE_URL` for the health check, `$SMOKE_TIMEOUT` for the wait duration, and `$HTTP_TIMEOUT` for the request timeout.

---

## Validation Steps

Run these checks in order. On failure, revert only the specific fix that caused it.

---

### 1. Dependency Tree Check (per fixed dependency)

For each fixed dependency, confirm the old vulnerable version is gone:
```bash
$MVN_CMD dependency:tree -Dincludes=<groupId>:<artifactId> -q
```

**If old version still appears (transitive pull):**
Add a `<dependencyManagement>` override to force the safe version, then re-run `dependency:tree` to confirm the override worked.

---

### 2. Compile Check
```powershell
& $MVN_CMD compile 2>&1
```
Capture full output. If exit code is non-zero:
- Identify which dependency fix caused the failure
- Revert that specific fix in the manifest
- Add to flagged concerns: `Fix for <package> caused compile failure — reverted`
- Re-run compile to confirm the revert resolved it

---

### 3. Unit Tests
```powershell
if ($TEST_CMD) {
    Invoke-Expression $TEST_CMD
} elseif ($BUILD_TOOL -eq 'gradle') {
    & $GRADLE_CMD test 2>&1
} else {
    & $MVN_CMD test 2>&1
}
```
Capture full output. If exit code is non-zero:
- Identify which fix is likely responsible (check test failure stack trace)
- Revert that specific fix
- Add to flagged concerns: `Unit tests failed after fixing <package> — reverted`

---

### 4. Application Start Smoke Check

**4a. MySQL prerequisite check**

Before starting the application, confirm MySQL is listening on the configured port:
```powershell
$mysql = Get-NetTCPConnection -LocalPort $MYSQL_PORT -State Listen -ErrorAction SilentlyContinue
if (-not $mysql) {
    Write-Host "MYSQL_NOT_RUNNING — MySQL is not listening on port $MYSQL_PORT. Skipping smoke check."
    exit 1
}
Write-Host "MySQL check passed."
```
If MySQL is not running → skip the smoke check entirely, add to flagged concerns: `Smoke check skipped — MySQL not running on $($cfg.runtime.mysql_host):$MYSQL_PORT`, and continue to Step 5.

**4b. Start app and run health check**

```powershell
Set-Location $REPO_ROOT

$proc = Start-Process -FilePath $MVN_CMD `
    -ArgumentList "spring-boot:run" `
    -PassThru -NoNewWindow `
    -RedirectStandardOutput "$env:TEMP\hms-smoke-stdout.txt" `
    -RedirectStandardError  "$env:TEMP\hms-smoke-stderr.txt"

Write-Host "Spring Boot starting (PID $($proc.Id))... waiting $SMOKE_TIMEOUT seconds"
Start-Sleep -Seconds $SMOKE_TIMEOUT

$healthPassed = $false
try {
    $response = Invoke-WebRequest `
        -Uri $SMOKE_URL `
        -UseBasicParsing `
        -TimeoutSec $HTTP_TIMEOUT `
        -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "HEALTH_CHECK_PASSED"
        $healthPassed = $true
    } else {
        Write-Host "HEALTH_CHECK_FAILED — HTTP $($response.StatusCode)"
    }
} catch {
    Write-Host "HEALTH_CHECK_FAILED — $($_.Exception.Message)"
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Write-Host "Spring Boot process stopped."
}

if (-not $healthPassed) {
    Write-Host "--- App stdout (last 40 lines) ---"
    Get-Content "$env:TEMP\hms-smoke-stdout.txt" -Tail 40
    Write-Host "--- App stderr (last 20 lines) ---"
    Get-Content "$env:TEMP\hms-smoke-stderr.txt" -Tail 20
}
```

If health check fails:
- Revert the most recent fix that was not previously validated
- Add to flagged concerns: `App failed health check after fixing <package> — reverted`

---

## Output to pass to @w2-reporter
```
VALIDATION RESULTS
─────────────────────────────────────────
Validated fixes:
  ✅ log4j-core 2.14.1 → 2.17.2
  ✅ commons-collections 3.2.1 → 3.2.2
  ✅ jackson.version 2.13.2 → 2.14.0

Reverted fixes:
  ❌ guava 29.0-jre → 32.0-jre (compile failure)

Dependency tree confirmations:
  ✅ log4j-core — old version 2.14.1 no longer present
  ✅ commons-collections — old version 3.2.1 no longer present
  ⚠️  jackson-databind — transitive pull detected, dependencyManagement override added

Build checks:
  dependency tree   : ✅ PASSED
  compile           : ✅ PASSED
  tests             : ✅ PASSED
  health check      : ✅ PASSED

Flagged concerns:
  ⚠️  guava: 29.0-jre → 32.0-jre caused compile failure — reverted, manual review needed
```

## Rules
- Never revert the entire manifest — revert only the specific failing fix
- Always re-run compile after each individual revert to confirm stability
- If ALL fixes are reverted → report to orchestrator, do NOT pass to @w2-reporter
- A fix is only considered validated after compile + test + health check all pass
