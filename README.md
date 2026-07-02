# GHAS Vulnerability Management Workflows

Two-workflow, multi-agent system that automates GitHub Advanced Security vulnerability management — from alert discovery to a merged PR. Agent definitions live in `.github/agents/` (loaded by GitHub Copilot CLI) and are mirrored in `.claude/agents/` (loaded by Claude Code) — keep both folders in sync when editing agents.

```
GitHub GHAS alerts → Workflow 1 (Alert Ingestion) → Jira ticket ID → Workflow 2 (Vulnerability Resolver) → PR + Jira Done
```

**Always run Workflow 1 first.** It produces the Jira ticket ID that Workflow 2 consumes.

---

## Prerequisites

| Tool | Version |
|------|---------|
| GitHub Copilot CLI or Claude Code | latest |
| Git for Windows (`bash.exe`) | any recent |
| GitHub CLI (`gh`) | 2.x+ (`gh auth login` completed) |
| Python | 3.8+ |
| Java | 17 |
| Maven | 3.8+ |
| jq | 1.6+ |
| MySQL 8 | running on `localhost:3306` (required for Workflow 2's smoke check; app auto-creates `hms_db` on first startup) |

Install Python packages once:
```bash
pip install requests python-dotenv pyyaml
```
> `pyyaml` is required — both `validate_config.py` and `jira_ticket_manager.py` parse the split YAML config files (`ghas-w1-config.yml` / `ghas-w2-config.yml`) at startup.

---

## Setup

**1. Authenticate GitHub CLI**
```bash
gh auth login   # select HTTPS, grant security_events + repo scopes
```
`fetch_alerts.sh` and `jira_ticket_manager.py` use this keyring auth — no GitHub token file is needed.

**2. Create `.env` in the repo root**
```
JIRA_BASE_URL=https://your-site.atlassian.net   # also accepted as JIRA_URL, but prefer JIRA_BASE_URL
JIRA_EMAIL=your@email.com
JIRA_API_TOKEN=your-api-token
```
Verify auth:
```bash
python .github/scripts/jira_ticket_manager.py search --project HMS --labels GHAS
```

**3. Fill in required fields in the split workflow config files**

Configuration is now split into two independent files (each validated separately at startup):

- **Workflow 1** (alert ingestion): `.github/config/ghas-w1-config.yml`
- **Workflow 2** (vulnerability resolver): `.github/config/ghas-w2-config.yml`

Both files share this core block:
```yaml
environment:
  repo_owner: <github-org-or-username>
  repo_name:  <repo-name>
  service_name: <display-name>   # Workflow 2's single-service fallback; Workflow 1 uses services[] instead
  repo_root:  <absolute-path-to-repo>

jira:
  site_url:    https://<your-site>.atlassian.net
  project_key: <jira-project-key>
```

**Workflow 1 also requires a `services` array** (one entry per GitHub repo to scan — supports multi-service/multi-repo scanning in a single run):
```yaml
services:
  - name: HMS               # display name used in Jira labels and CSV
    github_repo: HMS        # GitHub repo name (may differ from service name)
  - name: BillingService
    github_repo: billing-svc
```

**Workflow 2 also has these tunable sections** — adjust to your build toolchain and escalation policy:
```yaml
workflow2:
  auto_approve_minor: false     # true = MINOR-only fix plans skip the Step 4 approval gate
  auto_approve_critical: false  # true = plans containing CRITICAL fixes skip the Step 4 gate (use with caution)
  smoke_check_url: http://localhost:8080/api/v1/swagger-ui/index.html

branch:
  base_branch: main
  naming_single: "{jira_id}-GHAS-{primary_package}"
  naming_multi:  "{jira_id}-GHAS-{primary_package}-and-{extra_count}-more"

retry_limits:                  # max attempts before escalating to a human, per counter (default 3 each)
  plan_revision_max: 3
  build_failure_max: 3
  verify_fix_max: 3
  review_fix_max: 3
```

Validate both configs:
```bash
python .github/scripts/validate_config.py .github/config/ghas-w1-config.yml
python .github/scripts/validate_config.py .github/config/ghas-w2-config.yml
```

**4. Open the project in GitHub Copilot CLI or Claude Code**
```bash
copilot   # or: claude .
```

---

## Running Workflow 1 — Alert Ingestion

```
@alert-ingestion-orchestrator
```

Fetches all open GHAS alerts, groups by service, creates/updates one Jira ticket per service. Outputs a timestamped CSV at the repo root.

Note the Jira ticket ID from the output (e.g. `HMS-16`) — needed for Workflow 2.

> ⚠️ The completion output templates in `alert-ingestion-orchestrator.md`, `w1-jira-manager.md`, and `w1-fetcher.md` use `HMS` / `HMS-XX` as literal illustrative examples (e.g. `Jira tickets created : <N> → [HMS-XX, ...]`) instead of generic placeholders like `<SERVICE_NAME>` / `<PROJECT_KEY>-XX`. Real runs still substitute your actual service name and ticket keys correctly — this only affects how the templates read as documentation on a different project.

---

## Running Workflow 2 — Vulnerability Resolver

Ensure the working tree is clean first:
```bash
git status      # must be clean
git checkout main
```

```
@vuln-resolver-orchestrator HMS-16
```

**Human gates (2 on the normal path, each a blocking prompt — the orchestrator waits for your response before continuing):**

| Gate | When | Options |
|------|------|---------|
| Step 4 — Plan review | After the change plan is generated | `approve` / `feedback: <comments>` (re-plans, up to `plan_revision_max` cycles) / `abort` (deletes the feature branch) |
| Step 8 — Implementation review | After fixes are validated + verified | `approve` / `fix: <comments>` (loops back through fixer→validator→verifier, up to `review_fix_max` cycles) / `abort` (leaves branch as-is, no PR) |

Both gates auto-skip if `auto_approve_minor` / `auto_approve_critical` are set in `ghas-w2-config.yml` and the plan qualifies.

On `approve` at Step 8, the agent stages **and commits** the modified `pom.xml` file(s), pushes the branch, opens a GitHub PR, posts a report to Jira, and transitions the ticket to Done (or In Review if issues remain).

> ⚠️ If a fix is deferred (e.g. a MAJOR version bump you reject via Step 4 feedback), its CVEs remain open on the ticket by design — track them in a follow-up ticket.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `gh auth status` missing `security_events` | Re-run `gh auth login` with a PAT that includes `security_events` and `repo` scopes |
| `dependabot/alerts` returns 404 | Enable GHAS: GitHub repo → Settings → Security → Code security |
| `validate_config.py` fails | Fill in the missing field it names in `ghas-w1-config.yml` or `ghas-w2-config.yml` |
| Jira 401 Unauthorized | Check `JIRA_EMAIL` and `JIRA_API_TOKEN` in `.env` |
| Workflow 2 aborts "Uncommitted changes" | `git stash` or commit the changes, then re-run |
| `gh pr create` permission denied | Re-authenticate `gh auth login` with a PAT that has `repo` write scope |
| `validate_config.py` / `jira_ticket_manager.py` warns "PyYAML not installed" | `pip install pyyaml` — required to parse the split `ghas-w1-config.yml` / `ghas-w2-config.yml` files |
| Workflow 2 smoke check fails with `NoClassDefFoundError: org.yaml.snakeyaml...` | Pre-existing incompatibility between the intentionally-vulnerable `snakeyaml 1.30` pin and Spring Boot 3.2.3's config loader — unrelated to most fixes; safe to treat as non-blocking if `mvn compile`/`mvn test` pass, or bump `snakeyaml` in a dedicated follow-up |
| Need to re-test a Workflow 2 run from scratch | Close the PR (`gh pr close <n> --delete-branch`), delete the local feature branch (`git branch -D <branch>`), revert the Jira ticket status (`jira_ticket_manager.py transition --name "To Do"`), and optionally delete any posted comment via a direct `DELETE /rest/api/3/issue/{ticket}/comment/{id}` call |
