#!/usr/bin/env bash
# Bootstrap script: sync existing GitHub issues to GitLab via n8n webhook
# Sends synthetic GitHub webhook payloads to the GitHub->GitLab n8n workflow
# so that GitLab issues are created and the mapping file is updated.
#
# For closed issues, follows up with a "closed" payload.
# For issues with assignees, follows up with "assigned" payloads.
#
# This script operates on a SINGLE repo pair (GitHub repo -> GitLab project).
# The n8n workflow handles all GitLab API calls; we just send webhook payloads.
#
# Usage: ./bootstrap-gitlab-issues.sh [--dry-run] [--repo ORG/REPO] [--delay SECONDS]
#
# Examples:
#   ./bootstrap-gitlab-issues.sh --dry-run
#   ./bootstrap-gitlab-issues.sh --repo electinfo/enterprise --delay 2
#   ./bootstrap-gitlab-issues.sh

set -uo pipefail

WEBHOOK_URL="${WEBHOOK_URL:-https://YOUR_N8N_HOST/webhook/github-to-gitlab}"
DEFAULT_REPO="electinfo/enterprise"
DELAY="${DELAY:-1.5}"
DRY_RUN=false
REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --repo) REPO="$2"; shift 2 ;;
    --delay) DELAY="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Default to electinfo/enterprise if no repo specified
if [[ -z "$REPO" ]]; then
  REPO="$DEFAULT_REPO"
fi

REPO_SHORT=$(basename "$REPO")

echo "=== GitHub -> GitLab Bootstrap ==="
echo "Repo: $REPO"
echo "Webhook URL: $WEBHOOK_URL"
echo "Delay: ${DELAY}s between calls"
if $DRY_RUN; then
  echo "Mode: DRY RUN (no webhooks will be sent)"
fi
echo ""

send_webhook() {
  local payload="$1"
  local event_type="$2"
  if $DRY_RUN; then
    echo "[DRY RUN] Would POST $event_type payload ($(echo "$payload" | jq -r '.issue.title' 2>/dev/null || echo 'unknown'))"
    return 0
  fi
  local http_code
  http_code=$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -H "X-GitHub-Event: $event_type" \
    -d "$payload")
  echo "$http_code"
}

total_sent=0
total_errors=0

echo "=== Fetching issues from $REPO ==="

# Fetch all issues (open + closed)
issues_json=$(gh issue list --repo "$REPO" --state all --limit 500 \
  --json number,title,body,state,labels,url,assignees 2>/dev/null || echo "[]")

issue_count=$(echo "$issues_json" | jq 'length')
echo "Found $issue_count issues"
echo ""

for i in $(seq 0 $((issue_count - 1))); do
  issue=$(echo "$issues_json" | jq ".[$i]")
  number=$(echo "$issue" | jq -r '.number')
  title=$(echo "$issue" | jq -r '.title')
  body=$(echo "$issue" | jq -r '.body // ""')
  state=$(echo "$issue" | jq -r '.state')
  url=$(echo "$issue" | jq -r '.url')
  labels=$(echo "$issue" | jq '[.labels[].name]')
  assignees=$(echo "$issue" | jq '[.assignees[].login]')

  echo -n "  #$number ($state): $title ... "

  # --- Send "opened" event ---
  # Build synthetic webhook payload matching GitHub webhook format
  opened_payload=$(jq -n \
    --arg action "opened" \
    --arg full_name "$REPO" \
    --arg repo_name "$REPO_SHORT" \
    --argjson number "$number" \
    --arg title "$title" \
    --arg body "$body" \
    --arg state "$state" \
    --arg html_url "$url" \
    --argjson labels "$labels" \
    --argjson assignees "$assignees" \
    '{
      action: $action,
      issue: {
        number: $number,
        title: $title,
        body: $body,
        state: "open",
        html_url: $html_url,
        labels: [$labels[] | {name: .}],
        assignees: [$assignees[] | {login: .}]
      },
      repository: {
        full_name: $full_name,
        name: $repo_name
      },
      sender: {
        login: "bootstrap-script"
      }
    }')

  result=$(send_webhook "$opened_payload" "issues")
  if [[ "$result" == "200" ]] || $DRY_RUN; then
    echo -n "created "
    total_sent=$((total_sent + 1))
  else
    echo -n "ERROR($result) "
    total_errors=$((total_errors + 1))
  fi

  sleep "$DELAY"

  # --- Send "closed" event if issue is closed ---
  if [[ "$state" == "CLOSED" ]]; then
    closed_payload=$(jq -n \
      --arg full_name "$REPO" \
      --arg repo_name "$REPO_SHORT" \
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

    result=$(send_webhook "$closed_payload" "issues")
    if [[ "$result" == "200" ]] || $DRY_RUN; then
      echo -n "->closed "
      total_sent=$((total_sent + 1))
    else
      echo -n "CLOSE-ERROR($result) "
      total_errors=$((total_errors + 1))
    fi

    sleep "$DELAY"
  fi

  # --- Send "assigned" event for each assignee ---
  assignee_count=$(echo "$assignees" | jq 'length')
  for ai in $(seq 0 $((assignee_count - 1))); do
    assignee_login=$(echo "$assignees" | jq -r ".[$ai]")

    # GitHub "assigned" webhooks include both the single assignee field
    # and the full assignees array on the issue
    assigned_payload=$(jq -n \
      --arg full_name "$REPO" \
      --arg repo_name "$REPO_SHORT" \
      --argjson number "$number" \
      --arg title "$title" \
      --arg html_url "$url" \
      --argjson labels "$labels" \
      --arg assignee_login "$assignee_login" \
      --argjson all_assignees "$assignees" \
      '{
        action: "assigned",
        assignee: {
          login: $assignee_login
        },
        issue: {
          number: $number,
          title: $title,
          body: "",
          state: "open",
          html_url: $html_url,
          labels: [$labels[] | {name: .}],
          assignees: [$all_assignees[] | {login: .}]
        },
        repository: {
          full_name: $full_name,
          name: $repo_name
        },
        sender: {
          login: "bootstrap-script"
        }
      }')

    result=$(send_webhook "$assigned_payload" "issues")
    if [[ "$result" == "200" ]] || $DRY_RUN; then
      echo -n "->assigned($assignee_login) "
      total_sent=$((total_sent + 1))
    else
      echo -n "ASSIGN-ERROR($result) "
      total_errors=$((total_errors + 1))
    fi

    sleep "$DELAY"
  done

  echo ""
done

echo ""
echo "=== Bootstrap Complete ==="
echo "Repo: $REPO"
echo "Total webhook calls sent: $total_sent"
echo "Total errors: $total_errors"
