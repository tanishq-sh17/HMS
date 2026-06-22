#!/usr/bin/env python3
"""
jira_ticket_manager.py — Concrete Jira REST API helper for GHAS Workflow 1 & 2.

All commands load JIRA_EMAIL, JIRA_API_TOKEN, and JIRA_BASE_URL from the .env
file at the repo root (or from environment variables if already set).

Usage:
    python jira_ticket_manager.py search   --project HMS --labels "GHAS,HMS"
    python jira_ticket_manager.py create   --project HMS --csv <path>
    python jira_ticket_manager.py comment  --ticket HMS-16 --body-file <path>
    python jira_ticket_manager.py transitions --ticket HMS-16
    python jira_ticket_manager.py transition  --ticket HMS-16 --name "Done"
"""

import argparse
import csv
import json
import os
import sys
from base64 import b64encode
from pathlib import Path

# ── Try to load requests; give a clear error if missing ──────────────────────
try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# ── Try to load PyYAML for config file ───────────────────────────────────────
try:
    import yaml
except ImportError:
    yaml = None  # config loading will fall back to defaults if yaml is missing


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_CACHE = None

def load_config():
    """Load ghas-workflow-config.yml from .github/config/ at the repo root.
    Returns a dict. Falls back to built-in defaults if the file is missing or
    PyYAML is not installed.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    defaults = {
        "jira": {
            "labels": ["GHAS"],
            "assignee": None,
            "parent_jira": None,
            "story_points": None,
            "team": None,
            "priority": "High",
            "template": {"include_columns": ["ghsa_id", "cve_id", "title"]},
            "skip_statuses_for_duplicate_check": [
                "To Do", "Funnel", "Analysis", "In Dev", "Blocked", "On Hold"
            ],
        },
        "workflow": {"build_tool": "maven"},
    }

    if yaml is None:
        print("[WARN] PyYAML not installed — using default config. Run: pip install pyyaml", file=sys.stderr)
        _CONFIG_CACHE = defaults
        return _CONFIG_CACHE

    script_dir  = Path(__file__).resolve().parent   # .github/scripts/
    repo_root   = script_dir.parent.parent           # repo root
    config_path = repo_root / ".github" / "config" / "ghas-workflow-config.yml"

    if not config_path.exists():
        print(f"[WARN] Config not found at {config_path} — using defaults.", file=sys.stderr)
        _CONFIG_CACHE = defaults
        return _CONFIG_CACHE

    with open(config_path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    # Deep-merge loaded values over defaults
    def _merge(base, override):
        result = dict(base)
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                result[k] = _merge(base[k], v)
            else:
                result[k] = v
        return result

    _CONFIG_CACHE = _merge(defaults, loaded)
    return _CONFIG_CACHE


def get_jira_config():
    """Shortcut: return the jira section of the config."""
    return load_config().get("jira", {})


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_env():
    """Load .env from repo root (two levels up from this script)."""
    script_dir = Path(__file__).resolve().parent          # .github/scripts/
    repo_root  = script_dir.parent.parent                  # repo root
    env_file   = repo_root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:              # env var takes precedence
                os.environ[k.strip()] = v.strip()


def get_auth():
    load_env()
    email    = os.environ.get("JIRA_EMAIL", "")
    token    = os.environ.get("JIRA_API_TOKEN", "")
    base_url = os.environ.get("JIRA_BASE_URL", "https://tanishqshrivas.atlassian.net")

    if not email or not token:
        print("ERROR: JIRA_EMAIL and JIRA_API_TOKEN must be set in .env or environment.", file=sys.stderr)
        sys.exit(1)
    if "your_jira_api_token_here" in token:
        print("ERROR: JIRA_API_TOKEN is still the placeholder value. Update .env with your real token.", file=sys.stderr)
        sys.exit(1)

    creds   = b64encode(f"{email}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    return base_url.rstrip("/"), headers


def jira_request(method, url, headers, **kwargs):
    try:
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Cannot connect to Jira: {e}", file=sys.stderr)
        sys.exit(1)
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Command: search
# ─────────────────────────────────────────────────────────────────────────────

def cmd_search(args):
    """Search Jira for GHAS tickets by label. Returns ALL statuses so the caller
    can apply the skip_statuses_for_duplicate_check logic from config."""
    base_url, headers = get_auth()
    labels = [l.strip() for l in args.labels.split(",")]
    label_clauses = " AND ".join(f'labels = "{l}"' for l in labels)
    jql = f'project = "{args.project}" AND {label_clauses} ORDER BY created DESC'
    params = {"jql": jql, "fields": "summary,status,labels,priority", "maxResults": 50}
    url  = f"{base_url}/rest/api/3/search/jql"
    resp = jira_request("GET", url, headers, params=params)

    if resp.status_code != 200:
        print(f"ERROR: Jira search failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    data   = resp.json()
    issues = data.get("issues", [])
    result = [
        {
            "key":     i["key"],
            "status":  i["fields"]["status"]["name"],
            "summary": i["fields"]["summary"],
        }
        for i in issues
    ]
    print(json.dumps(result, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# ADF builders
# ─────────────────────────────────────────────────────────────────────────────

def _text(text, marks=None):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def _para(*nodes):
    return {"type": "paragraph", "content": list(nodes)}


def _strong_text(text):
    return _text(text, [{"type": "strong"}])


def _colored_text(text, color, bold=False):
    marks = [{"type": "textColor", "attrs": {"color": color}}]
    if bold:
        marks.insert(0, {"type": "strong"})
    return _text(text, marks)


def _table_header_cell(text, bg="#0052CC", text_color="#FFFFFF"):
    return {
        "type": "tableHeader",
        "attrs": {"background": bg},
        "content": [_para(_text(text, [{"type": "strong"},
                                        {"type": "textColor", "attrs": {"color": text_color}}]))],
    }


def _table_cell(text, bg=None, bold=False, text_color=None):
    marks = []
    if bold:
        marks.append({"type": "strong"})
    if text_color:
        marks.append({"type": "textColor", "attrs": {"color": text_color}})
    cell = {"type": "tableCell", "content": [_para(_text(text, marks) if marks else _text(text))]}
    if bg:
        cell["attrs"] = {"background": bg}
    return cell


def _table_row(cells):
    return {"type": "tableRow", "content": cells}


def build_adf_description(service_name, grouped_alerts):
    """Build a full ADF doc for a Jira ticket description.
    Respects include_columns config for the Dependabot alert table.
    Includes a compliance/non-compliance breakdown table after the severity summary.
    """
    jira_cfg      = get_jira_config()
    include_cols  = jira_cfg.get("template", {}).get("include_columns", ["ghsa_id", "cve_id", "title"])

    dep_alerts = [a for a in grouped_alerts if a.get("type") == "dependabot"]
    cs_alerts  = [a for a in grouped_alerts if a.get("type") == "code-scanning"]
    ss_alerts  = [a for a in grouped_alerts if a.get("type") == "secret-scanning"]

    def count_sev(alerts):
        c = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for a in alerts:
            sev = (a.get("severity") or "").upper()
            if sev in c:
                c[sev] += 1
        return c

    dep_counts   = count_sev(dep_alerts)
    cs_counts    = count_sev(cs_alerts)
    total_counts = {k: dep_counts[k] + cs_counts[k] for k in dep_counts}

    severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    content = []

    # ── Intro paragraph ───────────────────────────────────────────────────────
    content.append(_para(_text(
        f"Address the GHAS issues for the below vulnerabilities for {service_name}"
    )))

    # ── Severity summary table ────────────────────────────────────────────────
    header_row = _table_row([_table_header_cell(h) for h in
                              ["Vulnerability", "Critical", "High", "Medium", "Low"]])
    dep_row   = _table_row([_table_cell("Dependabot")] +
                           [_table_cell(str(dep_counts[s])) for s in severities])
    cs_row    = _table_row([_table_cell("Code Scanning")] +
                           [_table_cell(str(cs_counts[s])) for s in severities])
    total_row = _table_row([_table_cell("Total", bg="#36B37E", bold=True)] +
                           [_table_cell(str(total_counts[s]), bg="#36B37E", bold=True) for s in severities])
    content.append({
        "type": "table",
        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
        "content": [header_row, dep_row, cs_row, total_row],
    })

    # ── Compliance / Non-Compliance breakdown table ───────────────────────────
    all_dep = dep_alerts  # compliance tracking is based on Dependabot alerts
    content.append(_para(_colored_text("Compliance Summary:", "#0052CC", bold=True)))
    comp_header = _table_row([_table_header_cell(h) for h in
                               ["Severity", "Total", "Compliant", "Non-Compliant"]])
    comp_rows = [comp_header]
    for sev in severities:
        bucket      = [a for a in all_dep if (a.get("severity") or "").upper() == sev]
        total_sev   = len(bucket)
        non_comp    = sum(1 for a in bucket if str(a.get("nonCompliant", "0")).strip() == "1")
        compliant   = total_sev - non_comp
        bg = "#FFEBE6" if non_comp > 0 else None
        comp_rows.append(_table_row([
            _table_cell(sev.capitalize()),
            _table_cell(str(total_sev)),
            _table_cell(str(compliant), bg="#E3FCEF" if compliant > 0 and non_comp == 0 else None, text_color="#000000"),
            _table_cell(str(non_comp),  bg=bg, bold=(non_comp > 0)),
        ]))
    content.append({
        "type": "table",
        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
        "content": comp_rows,
    })
    content.append({"type": "rule"})

    # ── Dependabot section — columns driven by config ─────────────────────────
    # Column config: map key → display header
    COL_MAP = {
        "ghsa_id":      "GHSA ID",
        "cve_id":       "CVE ID",
        "title":        "Issue",
        "severity":     "Severity",
        "created":      "Created",
        "due":          "Due",
        "ageDays":      "Age (days)",
        "nonCompliant": "Non-Compliant",
        "url":          "URL",
    }
    active_cols = [c for c in include_cols if c in COL_MAP]

    if dep_alerts:
        content.append(_para(_colored_text("Dependabot Issues:", "#FF8B00", bold=True)))
        for sev in severities:
            bucket = [a for a in dep_alerts if (a.get("severity") or "").upper() == sev]
            if not bucket:
                continue
            content.append(_para(_strong_text(f"{sev.capitalize()}:")))
            header = _table_row([
                {"type": "tableHeader", "content": [_para(_text(COL_MAP[c]))]}
                for c in active_cols
            ])
            rows = [header]
            for a in bucket:
                rows.append(_table_row([
                    _table_cell(str(a.get(c) or "—")) for c in active_cols
                ]))
            content.append({
                "type": "table",
                "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                "content": rows,
            })

    # ── Code Scanning section ─────────────────────────────────────────────────
    if cs_alerts:
        content.append(_para(_colored_text("Code Scanning Issues:", "#FF8B00", bold=True)))
        for sev in severities:
            bucket = [a for a in cs_alerts if (a.get("severity") or "").upper() == sev]
            if not bucket:
                continue
            content.append(_para(_strong_text(f"{sev.capitalize()}:")))
            header = _table_row([
                {"type": "tableHeader", "content": [_para(_text("Title"))]},
                {"type": "tableHeader", "content": [_para(_text("URL"))]},
            ])
            rows = [header]
            for a in bucket:
                rows.append(_table_row([
                    _table_cell(a.get("title") or "—"),
                    _table_cell(a.get("url") or "—"),
                ]))
            content.append({
                "type": "table",
                "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                "content": rows,
            })

    # ── Secret Scanning section ───────────────────────────────────────────────
    if ss_alerts:
        content.append(_para(_colored_text("Secret Scanning Issues:", "#FF8B00", bold=True)))
        for a in ss_alerts:
            content.append(_para(_text(f"• {a.get('title','—')} | {a.get('url','—')}")))

    content.append({"type": "rule"})
    content.append(_para({
        "type": "text", "text": "Auto-created by GHAS Vulnerability Management — Workflow 1 / Jira Manager",
        "marks": [{"type": "em"}],
    }))

    return {"version": 1, "type": "doc", "content": content}


# ─────────────────────────────────────────────────────────────────────────────
# Command: create
# ─────────────────────────────────────────────────────────────────────────────

def cmd_create(args):
    """Create a Jira ticket for a service from CSV data. Prints the new Jira key."""
    base_url, headers = get_auth()

    # Load CSV
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    service_rows = [r for r in rows if r.get("service", "").strip().lower() == args.service.strip().lower()]
    if not service_rows:
        print(f"ERROR: No rows found for service '{args.service}' in {csv_path}", file=sys.stderr)
        sys.exit(1)

    # Map CSV rows to alert dicts — include all fields needed for compliance table & configurable columns
    alerts = []
    for r in service_rows:
        alerts.append({
            "type":         r.get("type", ""),
            "ghsa_id":      r.get("ghsa_id", ""),
            "cve_id":       r.get("cve_id", ""),
            "title":        r.get("title", ""),
            "severity":     r.get("severity", ""),
            "created":      r.get("created", ""),
            "due":          r.get("due", ""),
            "ageDays":      r.get("ageDays", ""),
            "nonCompliant": r.get("nonCompliant", "0"),
            "url":          r.get("url", ""),
        })

    # Compute counts for ticket title
    dep_alerts = [a for a in alerts if a["type"] == "dependabot"]
    cs_alerts  = [a for a in alerts if a["type"] == "code-scanning"]
    all_vuln   = dep_alerts + cs_alerts

    sev_totals = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for a in all_vuln:
        sev = (a.get("severity") or "").upper()
        if sev in sev_totals:
            sev_totals[sev] += 1

    sev_parts = [f"{k.capitalize()}-{v}" for k, v in sev_totals.items() if v > 0]
    summary   = f"Address GHAS vulnerabilities for {args.service} [{', '.join(sev_parts)}]"

    # Priority — read from config (set by business, not severity-mapped)
    jira_cfg = get_jira_config()
    priority = jira_cfg.get("priority", "High")

    # Labels — base from config + service name appended
    base_labels = list(jira_cfg.get("labels", ["GHAS"]))
    labels = base_labels + [args.service]

    # ADF description
    adf_desc = build_adf_description(args.service, alerts)

    payload = {
        "fields": {
            "project":     {"key": args.project},
            "issuetype":   {"name": "Bug"},
            "summary":     summary,
            "priority":    {"name": priority},
            "labels":      labels,
            "description": adf_desc,
        }
    }

    # Optional fields from config
    assignee     = jira_cfg.get("assignee")
    story_points = jira_cfg.get("story_points")
    parent_jira  = jira_cfg.get("parent_jira")

    if assignee:
        payload["fields"]["assignee"] = {"accountId": assignee}
    if story_points is not None:
        payload["fields"]["story_points"] = story_points
    if parent_jira:
        payload["fields"]["parent"] = {"key": parent_jira}

    url  = f"{base_url}/rest/api/3/issue"
    resp = jira_request("POST", url, headers, json=payload)

    if resp.status_code not in (200, 201):
        print(f"ERROR: Ticket creation failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    key  = data.get("key", "")
    print(json.dumps({"key": key, "summary": summary, "priority": priority}))


# ─────────────────────────────────────────────────────────────────────────────
# Command: comment
# ─────────────────────────────────────────────────────────────────────────────

def cmd_comment(args):
    """Post a markdown comment on a Jira ticket. Body is read from --body-file."""
    base_url, headers = get_auth()

    body_path = Path(args.body_file)
    if not body_path.exists():
        print(f"ERROR: Body file not found: {body_path}", file=sys.stderr)
        sys.exit(1)

    body = body_path.read_text(encoding="utf-8")

    # Build ADF paragraph block from markdown text (simple line-by-line)
    lines = body.splitlines()
    content_nodes = []
    for line in lines:
        if line.strip() == "":
            content_nodes.append({"type": "paragraph", "content": []})
        else:
            content_nodes.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})

    adf_comment = {"version": 1, "type": "doc", "content": content_nodes}

    payload = {"body": adf_comment}
    url  = f"{base_url}/rest/api/3/issue/{args.ticket}/comment"
    resp = jira_request("POST", url, headers, json=payload)

    if resp.status_code not in (200, 201):
        print(f"ERROR: Comment post failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    print(json.dumps({"comment_id": data.get("id"), "ticket": args.ticket, "status": "posted"}))


# ─────────────────────────────────────────────────────────────────────────────
# Command: transitions
# ─────────────────────────────────────────────────────────────────────────────

def cmd_transitions(args):
    """List available workflow transitions for a ticket. Prints JSON list."""
    base_url, headers = get_auth()
    url  = f"{base_url}/rest/api/3/issue/{args.ticket}/transitions"
    resp = jira_request("GET", url, headers)

    if resp.status_code != 200:
        print(f"ERROR: Get transitions failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    data   = resp.json()
    result = [{"id": t["id"], "name": t["name"]} for t in data.get("transitions", [])]
    print(json.dumps(result, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Command: transition
# ─────────────────────────────────────────────────────────────────────────────

def cmd_transition(args):
    """Transition a Jira ticket to the named status. Looks up the transition ID first."""
    base_url, headers = get_auth()

    # Get available transitions
    url  = f"{base_url}/rest/api/3/issue/{args.ticket}/transitions"
    resp = jira_request("GET", url, headers)
    if resp.status_code != 200:
        print(f"ERROR: Get transitions failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    transitions = resp.json().get("transitions", [])
    target_name = args.name.lower()
    match = next((t for t in transitions if target_name in t["name"].lower()), None)

    if not match:
        available = [t["name"] for t in transitions]
        print(f"ERROR: No transition matching '{args.name}'. Available: {available}", file=sys.stderr)
        sys.exit(1)

    transition_id = match["id"]
    payload = {"transition": {"id": transition_id}}
    url2  = f"{base_url}/rest/api/3/issue/{args.ticket}/transitions"
    resp2 = jira_request("POST", url2, headers, json=payload)

    if resp2.status_code not in (200, 201, 204):
        print(f"ERROR: Transition failed ({resp2.status_code}): {resp2.text}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({"ticket": args.ticket, "transitioned_to": match["name"], "status": "success"}))


# ─────────────────────────────────────────────────────────────────────────────
# Command: update-description
# ─────────────────────────────────────────────────────────────────────────────

def cmd_update_description(args):
    """Re-generate and PUT the ADF description for an existing ticket from a CSV."""
    import csv as _csv

    base_url, headers = get_auth()

    # Read alerts for the service from the CSV
    alerts = []
    with open(args.csv, newline="", encoding="utf-8") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            svc = (row.get("service") or "").strip().upper()
            if svc == args.service.strip().upper():
                alerts.append(row)

    if not alerts:
        print(f"ERROR: No alerts found for service '{args.service}' in {args.csv}", file=sys.stderr)
        sys.exit(1)

    adf = build_adf_description(args.service, alerts)
    payload = {"fields": {"description": adf}}
    url = f"{base_url}/rest/api/3/issue/{args.ticket}"
    resp = jira_request("PUT", url, headers, json=payload)

    if resp.status_code not in (200, 201, 204):
        print(f"ERROR: Update failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({"ticket": args.ticket, "updated": True, "alerts_used": len(alerts)}))


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Jira REST API helper for GHAS workflows")
    sub    = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Search for open GHAS tickets")
    p_search.add_argument("--project", required=True)
    p_search.add_argument("--labels",  required=True, help="Comma-separated labels, e.g. GHAS,HMS")

    # create
    p_create = sub.add_parser("create", help="Create one consolidated ticket for a service")
    p_create.add_argument("--project", required=True)
    p_create.add_argument("--service", required=True)
    p_create.add_argument("--csv",     required=True, help="Path to github_alerts_*.csv")

    # comment
    p_comment = sub.add_parser("comment", help="Post a comment on a ticket")
    p_comment.add_argument("--ticket",    required=True)
    p_comment.add_argument("--body-file", required=True, dest="body_file",
                           help="Path to a text file containing the comment body")

    # update-description
    p_upd = sub.add_parser("update-description", help="Re-generate ADF description for an existing ticket")
    p_upd.add_argument("--ticket",  required=True, help="Jira issue key, e.g. HMS-22")
    p_upd.add_argument("--service", required=True, help="Service name, e.g. HMS")
    p_upd.add_argument("--csv",     required=True, help="Path to github_alerts_*.csv")

    # transitions
    p_trans = sub.add_parser("transitions", help="List available transitions for a ticket")
    p_trans.add_argument("--ticket", required=True)

    # transition
    p_do_trans = sub.add_parser("transition", help="Transition a ticket to a named status")
    p_do_trans.add_argument("--ticket", required=True)
    p_do_trans.add_argument("--name",   required=True, help='Target status name, e.g. "Done"')

    args = parser.parse_args()

    commands = {
        "update-description": cmd_update_description,
        "search":      cmd_search,
        "create":      cmd_create,
        "comment":     cmd_comment,
        "transitions": cmd_transitions,
        "transition":  cmd_transition,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
