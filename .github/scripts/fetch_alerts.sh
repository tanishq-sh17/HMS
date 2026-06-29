#!/bin/sh
set -eu

# Default path (Windows Git Bash format)
DEFAULT_OUT_PATH="/c/MCKC-OPS/GHAS-ISSUES/github-issues.csv"

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [output.csv]" >&2
  exit 2
fi

out_path="${1:-$DEFAULT_OUT_PATH}"
mkdir -p "$(dirname "$out_path")"

printf '%s\n' 'service,type,ghsa_id,cve_id,title,severity,created,due,url,Application,nonCompliant,ageDays' > "$out_path"

_gh_out=$(mktemp)
_gh_err=$(mktemp)
trap 'rm -f "$_gh_out" "$_gh_err"' EXIT

for entry in \
  "HMS HMS" \
  ; do

  svc="${entry%% *}"
  Application="${entry#* }"

  echo "Fetching alerts for service: $svc" >&2

  # =======================
  # Dependabot
  # =======================
  if gh api "repos/tanishq-sh17/${svc}/dependabot/alerts?state=open&per_page=100" --paginate \
    --jq '.[] |
      .created_at as $created |
      (.security_advisory.severity // "") as $s |
      (if $s == "critical" then ($created | fromdateiso8601 + 1296000 | strftime("%Y-%m-%d"))
       elif $s == "high" then ($created | fromdateiso8601 + 2592000 | strftime("%Y-%m-%d"))
       elif $s == "medium" then ($created | fromdateiso8601 + 7776000 | strftime("%Y-%m-%d"))
       elif $s == "low" then ($created | fromdateiso8601 + 10368000 | strftime("%Y-%m-%d"))
       else "" end) as $due |
      (((now - ($created | fromdateiso8601)) / 86400) | floor) as $age |
      {
        type: "dependabot",
        ghsa_id: (.security_advisory.ghsa_id // ""),
        cve_id: ((.security_advisory.identifiers[]? | select(.type=="CVE") | .value) // ""),
        title: (.security_advisory.summary // ""),
        severity: $s,
        created: ($created | fromdateiso8601 | strftime("%Y-%m-%d")),
        due: $due,
        url: (.html_url // ""),
        non_compliant: (if $s=="critical" and $age>15 then 1
                        elif $s=="high" and $age>30 then 1
                        elif $s=="medium" and $age>90 then 1
                        elif $s=="low" and $age>120 then 1 else 0 end),
        age_days: $age
      }' > "$_gh_out" 2>"$_gh_err"; then

    jq -rc --arg svc "$svc" --arg Application "$Application" \
      '. | [$svc,.type,.ghsa_id,.cve_id,.title,.severity,.created,.due,.url,$Application,.non_compliant,.age_days] | @csv' \
      "$_gh_out" >> "$out_path"
  fi

  # =======================
  # Code Scanning
  # =======================
  if gh api "repos/tanishq-sh17/${svc}/code-scanning/alerts?state=open&per_page=100" --paginate \
    --jq '.[] |
      .created_at as $created |
      ((.rule.security_severity_level // .rule.severity // .severity //"") | ascii_downcase) as $s |
      (((now - ($created | fromdateiso8601)) / 86400) | floor) as $age |
      {
        type:"code-scanning",
        ghsa_id:"",
        cve_id:"",
        title:(.rule.description // .rule.name // ""),
        severity:$s,
        created:($created | fromdateiso8601 | strftime("%Y-%m-%d")),
        due:"",
        url:(.html_url // ""),
        non_compliant:0,
        age_days:$age
      }' > "$_gh_out" 2>"$_gh_err"; then

    jq -rc --arg svc "$svc" --arg Application "$Application" \
      '. | [$svc,.type,.ghsa_id,.cve_id,.title,.severity,.created,.due,.url,$Application,.non_compliant,.age_days] | @csv' \
      "$_gh_out" >> "$out_path"
  fi

  # =======================
  # Secret Scanning
  # =======================
  if gh api "repos/tanishq-sh17/${svc}/secret-scanning/alerts?state=open&per_page=100" --paginate \
    --jq '.[] |
      .created_at as $created |
      ((.severity // .rule.severity //"") | ascii_downcase) as $s |
      (((now - ($created | fromdateiso8601)) / 86400) | floor) as $age |
      {
        type:"secret-scanning",
        ghsa_id:"",
        cve_id:"",
        title:(.secret_type_display_name // .secret_type // ""),
        severity:$s,
        created:($created | fromdateiso8601 | strftime("%Y-%m-%d")),
        due:"",
        url:(.html_url // ""),
        non_compliant:0,
        age_days:$age
      }' > "$_gh_out" 2>"$_gh_err"; then

    jq -rc --arg svc "$svc" --arg Application "$Application" \
      '. | [$svc,.type,.ghsa_id,.cve_id,.title,.severity,.created,.due,.url,$Application,.non_compliant,.age_days] | @csv' \
      "$_gh_out" >> "$out_path"
  fi

done

printf '%s\n' "Wrote CSV to: $out_path"
