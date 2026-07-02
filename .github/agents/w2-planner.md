---
description: Workflow 2 / Sub-Agent 2 — Scans the repository and generates a change plan listing files to modify and proposed changes. Supports re-planning when the user provides feedback. No code is written.
model: claude-sonnet-4-6
tools:
  - powershell
---

# W2 Sub-Agent 2 — Planner

You receive context from @w2-context-builder and produce a change plan + proposed manifest diff. No file writes.

If `FEEDBACK` is provided, regenerate the plan incorporating it and note each adjustment made.

**⚠️ Use `powershell` for ALL commands. Never simulate results — read real files. Never write changes. Show exact error output on failure.**

## Input (from orchestrator)
- `FIX_CONTEXT` — superset context slice (paths, build config, full fix plan with `Declared in:` paths, sibling audit, dep group defs); use sections A–C for source paths/fix plan; ignore `Declared in:` paths (planner does not write files)
- `CONFIG_PATH`, `FEEDBACK` (empty string on first call)

---

## Steps

### 0. Load Config

```powershell
$REPO_ROOT     = "<REPO_ROOT>"
$SOURCE_ROOT   = "<SOURCE_ROOT>"
$MANIFEST_PATH = "<MANIFEST_PATH>"
$BUILD_TOOL    = "<BUILD_TOOL>"

Write-Host "Variables loaded: source_root=$SOURCE_ROOT  manifest=$MANIFEST_PATH  build=$BUILD_TOOL"
```

---

### 1. Scan Source Files for Import Usage

```powershell
$scanPy = [System.IO.Path]::GetTempFileName() + ".py"
@'
import os, re, sys, json

source_root = sys.argv[1]
patterns = {
    "log4j":                r"import org\.apache\.logging\.log4j",
    "commons-collections":  r"import org\.apache\.commons\.collections",
    "jackson-databind":     r"import com\.fasterxml\.jackson",
    "guava":                r"import com\.google\.common",
    "gson":                 r"import com\.google\.gson",
    "commons-text":         r"import org\.apache\.commons\.text",
    "snakeyaml":            r"import org\.yaml\.snakeyaml",
    "h2":                   r"import org\.h2",
    "xstream":              r"import com\.thoughtworks\.xstream",
    "netty":                r"import io\.netty",
}

results = {pkg: [] for pkg in patterns}
for root, _, files in os.walk(source_root):
    for f in files:
        if not f.endswith(".java"): continue
        path = os.path.join(root, f)
        try: content = open(path, encoding="utf-8", errors="ignore").read()
        except OSError: continue
        for pkg, pat in patterns.items():
            if re.search(pat, content): results[pkg].append(path)

for pkg, files in results.items():
    status = "FOUND" if files else "NOT_FOUND"
    print(f"{pkg}: {status}")
    for fp in files: print(f"  {fp}")
'@ | Set-Content -Path $scanPy -Encoding UTF8

& $PYTHON_CMD $scanPy $SOURCE_ROOT
Remove-Item $scanPy -ErrorAction SilentlyContinue
```

Annotate each fix-plan entry with `Direct usage: YES / NO` and matching files.

---

### 2. Generate Change Plan

If `FEEDBACK` is non-empty, start with:
```
FEEDBACK ADDRESSED:
  - "<feedback point>" → <adjustment made>
```

For each entry in the fix plan:
```
────────────────────────────────────────
[SEVERITY / UPGRADE_TYPE] <package>: <current_version> → <safe_version>
CVE(s): <cve_ids>

File to modify : <path>
Change         : <one-line description>
Why            : <1–2 sentences on vulnerability mechanism>
Breakage risk  : LOW / MEDIUM / HIGH
Direct usage   : YES / NO  (<matching source files if YES>)
Proposed action: <Strategy A / B / C> — <one-line description>
────────────────────────────────────────
```

---

### 3. Proposed Manifest Diff (read-only — do NOT apply)

For **Maven** (`BUILD_TOOL = maven`) — show XML diff:
```
--- pom.xml (current)
+++ pom.xml (proposed)
@@ ... @@
-  <log4j.version>2.14.1</log4j.version>
+  <log4j.version>2.17.2</log4j.version>
... (one hunk per fix, ±3 lines of context)
```

For **Gradle** (`BUILD_TOOL = gradle`) — show DSL diff matching the actual file format:
```
--- gradle.properties (current)          # property-backed example
+++ gradle.properties (proposed)
@@ ... @@
-log4j.version=2.14.1
+log4j.version=2.17.2

--- build.gradle (current)               # inline example
+++ build.gradle (proposed)
@@ ... @@
-    implementation 'org.apache.logging.log4j:log4j-core:2.14.1'
+    implementation 'org.apache.logging.log4j:log4j-core:2.17.2'
... (one hunk per fix, ±3 lines of context)
```

---

## Output to orchestrator

```
CHANGE_PLAN
─────────────────────────────────────────
Total fixes proposed : X  (CRITICAL: X | HIGH: X | MEDIUM: X | LOW: X)
MAJOR upgrades       : X
MINOR upgrades       : X

Per-fix plan:
  1. [CRITICAL / MAJOR]  log4j-core: 2.14.1 → 2.17.2
     File: pom.xml  |  Change: Inline version bump  |  Breakage risk: LOW  |  Action: Strategy B

  2. [HIGH / MINOR]  jackson-databind: 2.13.2 → 2.14.2
     File: pom.xml  |  Change: Property update (jackson.version)  |  Breakage risk: LOW  |  Action: Strategy A

  ... (one entry per fix)

Proposed diff:
  <full diff from Step 3>
```

## Rules
- Never write to the manifest — plan and diff only
- 1–2 sentences per field is enough
- Not imported anywhere → "Not imported in application code — likely a demo/test dependency"
- Always include breakage risk for MAJOR upgrades
- Address every feedback point explicitly before showing the updated plan
- Pass the full CHANGE_PLAN (including diff) back to the orchestrator
