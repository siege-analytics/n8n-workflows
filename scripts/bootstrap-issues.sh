#!/usr/bin/env bash
# Bootstrap script: sync existing GitHub issues to ClickUp via n8n webhook
# Sends synthetic "opened" webhook payloads so the GitHub→ClickUp workflow
# creates ClickUp tasks and saves the mapping in static data.
#
# For closed issues, follows up with a "closed" payload.
# For issues with status labels, follows up with a "labeled" payload.
#
# Usage: ./bootstrap-issues.sh [--dry-run] [--repo REPO] [--delay SECONDS]

set -uo pipefail

WEBHOOK_URL="https://n8n.elect.info/webhook/github-to-clickup"
DELAY="${DELAY:-1.5}"
DRY_RUN=false
FILTER_REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --repo) FILTER_REPO="$2"; shift 2 ;;
    --delay) DELAY="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

REPOS=(
  "electinfo/enterprise"
  "siege-analytics/siege_utilities"
  "electinfo/rundeck"
  "electinfo/ops"
  "electinfo/portainer"
)

send_webhook() {
  local payload="$1"
  if $DRY_RUN; then
    echo "[DRY RUN] Would POST payload ($(echo "$payload" | jq -r '.issue.title' 2>/dev/null || echo 'unknown'))"
    return 0
  fi
  local http_code
  http_code=$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$payload")
  echo "$http_code"
}

total_sent=0
total_errors=0

for full_repo in "${REPOS[@]}"; do
  repo_short=$(basename "$full_repo")

  if [[ -n "$FILTER_REPO" && "$repo_short" != "$FILTER_REPO" ]]; then
    continue
  fi

  echo ""
  echo "=== Processing $full_repo ==="

  # Fetch all issues (open + closed)
  issues_json=$(gh issue list --repo "$full_repo" --state all --limit 500 \
    --json number,title,body,state,labels,url 2>/dev/null || echo "[]")

  issue_count=$(echo "$issues_json" | jq 'length')
  echo "Found $issue_count issues"

  for i in $(seq 0 $((issue_count - 1))); do
    issue=$(echo "$issues_json" | jq ".[$i]")
    number=$(echo "$issue" | jq -r '.number')
    title=$(echo "$issue" | jq -r '.title')
    body=$(echo "$issue" | jq -r '.body // ""')
    state=$(echo "$issue" | jq -r '.state')
    url=$(echo "$issue" | jq -r '.url')
    labels=$(echo "$issue" | jq '[.labels[].name]')

    echo -n "  #$number ($state): $title ... "

    # Build synthetic "opened" webhook payload (mimics GitHub webhook format)
    opened_payload=$(jq -n \
      --arg action "opened" \
      --arg full_name "$full_repo" \
      --arg repo_name "$repo_short" \
      --argjson number "$number" \
      --arg title "$title" \
      --arg body "$body" \
      --arg state "$state" \
      --arg html_url "$url" \
      --argjson labels "$labels" \
      '{
        action: $action,
        issue: {
          number: $number,
          title: $title,
          body: $body,
          state: "open",
          html_url: $html_url,
          labels: [$labels[] | {name: .}]
        },
        repository: {
          full_name: $full_name,
          name: $repo_name
        },
        sender: {
          login: "bootstrap-script"
        }
      }')

    # Send "opened" event
    result=$(send_webhook "$opened_payload")
    if [[ "$result" == "200" ]] || $DRY_RUN; then
      echo -n "created "
      total_sent=$((total_sent + 1))
    else
      echo -n "ERROR($result) "
      total_errors=$((total_errors + 1))
    fi

    sleep "$DELAY"

    # If issue is closed, send "closed" event
    if [[ "$state" == "CLOSED" ]]; then
      closed_payload=$(jq -n \
        --arg full_name "$full_repo" \
        --arg repo_name "$repo_short" \
        --argjson number "$number" \
        --arg title "$title" \
        --arg html_url "$url" \
        --argjson labels "$labels" \
        '{
          action: "closed",
          issue: {
            number: $number,
            title: $title,
            body: "",
            state: "closed",
            html_url: $html_url,
            labels: [$labels[] | {name: .}]
          },
          repository: {
            full_name: $full_name,
            name: $repo_name
          },
          sender: {
            login: "bootstrap-script"
          }
        }')

      result=$(send_webhook "$closed_payload")
      if [[ "$result" == "200" ]] || $DRY_RUN; then
        echo -n "→closed "
        total_sent=$((total_sent + 1))
      else
        echo -n "CLOSE-ERROR($result) "
        total_errors=$((total_errors + 1))
      fi

      sleep "$DELAY"
    fi

    # If issue has status labels, send "labeled" event for the last one
    status_label=$(echo "$labels" | jq -r '[.[] | select(startswith("status:"))] | last // empty')
    if [[ -n "$status_label" ]]; then
      labeled_payload=$(jq -n \
        --arg full_name "$full_repo" \
        --arg repo_name "$repo_short" \
        --argjson number "$number" \
        --arg title "$title" \
        --arg html_url "$url" \
        --argjson labels "$labels" \
        --arg label_name "$status_label" \
        '{
          action: "labeled",
          issue: {
            number: $number,
            title: $title,
            body: "",
            state: "open",
            html_url: $html_url,
            labels: [$labels[] | {name: .}]
          },
          label: {
            name: $label_name
          },
          repository: {
            full_name: $full_name,
            name: $repo_name
          },
          sender: {
            login: "bootstrap-script"
          }
        }')

      result=$(send_webhook "$labeled_payload")
      if [[ "$result" == "200" ]] || $DRY_RUN; then
        echo -n "→labeled($status_label) "
        total_sent=$((total_sent + 1))
      else
        echo -n "LABEL-ERROR($result) "
        total_errors=$((total_errors + 1))
      fi

      sleep "$DELAY"
    fi

    echo ""
  done
done

echo ""
echo "=== Bootstrap Complete ==="
echo "Total webhook calls sent: $total_sent"
echo "Total errors: $total_errors"
