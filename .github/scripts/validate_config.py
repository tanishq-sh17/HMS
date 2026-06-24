#!/usr/bin/env python3
"""
validate_config.py — GHAS Workflow Configuration Validator

Called by orchestrators before any agent is invoked.
Exits 0 on success, non-zero with clear error messages on failure.

Usage:
    python validate_config.py <path-to-ghas-workflow-config.yml>
"""

import sys
import os

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def get_nested(data: dict, dotted_key: str):
    """Retrieve a value from a nested dict using dot notation."""
    keys = dotted_key.split(".")
    current = data
    for k in keys:
        if not isinstance(current, dict) or k not in current:
            return None
        current = current[k]
    return current


def validate(config_path: str) -> bool:
    if not os.path.isfile(config_path):
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        return False

    with open(config_path, encoding="utf-8") as f:
        try:
            cfg = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"ERROR: Invalid YAML in {config_path}: {e}", file=sys.stderr)
            return False

    if not cfg:
        print("ERROR: Config file is empty.", file=sys.stderr)
        return False

    errors = []

    # ── Required fields ──────────────────────────────────────────
    required_fields = get_nested(cfg, "validation.required_fields") or [
        "environment.repo_owner",
        "environment.repo_name",
        "environment.service_name",
        "environment.repo_root",
        "jira.site_url",
        "jira.project_key",
    ]
    for field in required_fields:
        value = get_nested(cfg, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"  [MISSING] {field} — required but not set")

    # ── Required paths ────────────────────────────────────────────
    required_paths = get_nested(cfg, "validation.required_paths") or [
        "environment.repo_root",
    ]
    for path_field in required_paths:
        path_val = get_nested(cfg, path_field)
        if path_val and not os.path.exists(path_val):
            errors.append(f"  [PATH NOT FOUND] {path_field} = '{path_val}'")

    # ── Semantic checks ───────────────────────────────────────────
    build_tool = get_nested(cfg, "workflow2.build_tool")
    if build_tool and build_tool not in ("maven", "gradle"):
        errors.append(f"  [INVALID] workflow2.build_tool must be 'maven' or 'gradle', got '{build_tool}'")

    jira_url = get_nested(cfg, "jira.site_url") or ""
    if jira_url and not jira_url.startswith("http"):
        errors.append(f"  [INVALID] jira.site_url must start with http/https, got '{jira_url}'")

    services = get_nested(cfg, "services")
    if not services:
        errors.append("  [MISSING] services — at least one service entry is required")

    # ── Jira template checks ──────────────────────────────────────
    valid_columns = {"ghsa_id", "cve_id", "title", "severity", "created", "due", "ageDays", "nonCompliant", "url"}
    ticket_table_columns = get_nested(cfg, "jira.ticket_table_columns")
    if ticket_table_columns is not None:
        if not isinstance(ticket_table_columns, list) or len(ticket_table_columns) == 0:
            errors.append("  [INVALID] jira.ticket_table_columns must be a non-empty list")
        else:
            unknown = [c for c in ticket_table_columns if c not in valid_columns]
            if unknown:
                errors.append(
                    f"  [INVALID] jira.ticket_table_columns contains unknown column(s): {unknown}. "
                    f"Valid values: {sorted(valid_columns)}"
                )

    summary_template = get_nested(cfg, "jira.ticket_summary_template")
    if summary_template is not None:
        if "{service_name}" not in summary_template:
            errors.append("  [INVALID] jira.ticket_summary_template must contain the '{service_name}' placeholder")
        if "{severity_summary}" not in summary_template:
            errors.append("  [INVALID] jira.ticket_summary_template must contain the '{severity_summary}' placeholder")

    if errors:
        print(f"\nConfig validation FAILED ({len(errors)} error(s)):\n", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        print(f"\nFix the errors in: {config_path}\n", file=sys.stderr)
        return False

    # ── Success ───────────────────────────────────────────────────
    repo_owner   = get_nested(cfg, "environment.repo_owner")
    repo_name    = get_nested(cfg, "environment.repo_name")
    service_name = get_nested(cfg, "environment.service_name")
    project_key  = get_nested(cfg, "jira.project_key")
    print(f"Config OK — repo={repo_owner}/{repo_name}  service={service_name}  jira={project_key}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_config.py <config.yml>", file=sys.stderr)
        sys.exit(1)
    ok = validate(sys.argv[1])
    sys.exit(0 if ok else 1)
