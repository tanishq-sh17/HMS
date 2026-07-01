# GHAS Vulnerability Management Workflows

Two-workflow, multi-agent system (Claude Code) that automates GitHub Advanced Security vulnerability management — from alert discovery to a merged PR.

```
GitHub GHAS alerts → Workflow 1 (Alert Ingestion) → Jira ticket ID → Workflow 2 (Vulnerability Resolver) → PR + Jira Done
```

**Always run Workflow 1 first.** It produces the Jira ticket ID that Workflow 2 consumes.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Claude Code | latest |
| Git for Windows (`bash.exe`) | any recent |
| GitHub CLI (`gh`) | 2.x+ |
| Python | 3.8+ |
| Java | 17 |
| Maven | 3.8+ |
| jq | 1.6+ |

Install Python packages once:
```bash
pip install requests python-dotenv
```

---

## Setup

**1. Authenticate GitHub CLI**
```bash
gh auth login   # select HTTPS, grant security_events + repo scopes
```

**2. Create `.env` in the repo root**
```
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=your@email.com
JIRA_API_TOKEN=your-api-token
```

**3. Fill in required fields in the workflow config files**

- **Workflow 1** (alert ingestion): `.github/config/ghas-w1-config.yml`
- **Workflow 2** (vulnerability resolver): `.github/config/ghas-w2-config.yml`

Both files share this core block:
```yaml
environment:
  repo_owner: <github-org-or-username>
  repo_name:  <repo-name>
  service_name: <display-name>
  repo_root:  <absolute-path-to-repo>

jira:
  site_url:    https://<your-site>.atlassian.net
  project_key: <jira-project-key>
```

Validate:
```bash
python .github/scripts/validate_config.py .github/config/ghas-w1-config.yml
python .github/scripts/validate_config.py .github/config/ghas-w2-config.yml
```

**4. Open the project in Claude Code**
```bash
claude .
```

---

## Running Workflow 1 — Alert Ingestion

```
@alert-ingestion-orchestrator
```

Fetches all open GHAS alerts, groups by service, creates/updates one Jira ticket per service. Outputs a timestamped CSV at the repo root.

Note the Jira ticket ID from the output (e.g. `HMS-16`) — needed for Workflow 2.

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

**Human gates (2 on the normal path):**

| Gate | When | Options |
|------|------|---------|
| Plan review | After change plan is generated | `approve` / `feedback: <comments>` / `abort` |
| Implementation review | After fixes are validated | `approve` / `fix: <comments>` / `abort` |

On `approve` at both gates, the agent pushes the branch, opens a GitHub PR, posts a report to Jira, and transitions the ticket to Done.

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
