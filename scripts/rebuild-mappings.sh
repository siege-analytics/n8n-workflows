#!/usr/bin/env bash
# Rebuild static data mappings after a workflow reimport.
# Fetches all ClickUp tasks from synced lists, parses [repoShort#issueNumber]
# from task names, builds the mapping dictionaries, and POSTs them to the
# n8n rebuild-mappings webhook.
#
# Usage: ./rebuild-mappings.sh [--dry-run]
#
# Requires: CLICKUP_TOKEN env var, curl, jq

set -uo pipefail

REBUILD_URL="https://n8n.elect.info/webhook/github-to-clickup"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "${CLICKUP_TOKEN:-}" ]]; then
  echo "ERROR: CLICKUP_TOKEN environment variable must be set"
  exit 1
fi

# Repo-specific list IDs
declare -A REPO_LISTS=(
  ["enterprise"]="901710789803"
  ["siege_utilities"]="901710789806"
  ["rundeck"]="901710789808"
  ["ops"]="901710789809"
  ["portainer"]="901710789810"
)
MASTER_LIST="901710789811"

# Initialize mapping objects
GH_TO_CU="{}"
CU_TO_GH="{}"

# Fetch all tasks from a list (handles pagination)
fetch_list_tasks() {
  local list_id="$1"
  local page=0
  local all_tasks="[]"

  while true; do
    local response
    response=$(curl -s "https://api.clickup.com/api/v2/list/${list_id}/task?page=${page}&include_closed=true" \
      -H "Authorization: ${CLICKUP_TOKEN}")
    local tasks
    tasks=$(echo "$response" | jq '.tasks // []')
    local count
    count=$(echo "$tasks" | jq 'length')
    if [[ "$count" -eq 0 ]]; then break; fi
    all_tasks=$(echo "$all_tasks" "$tasks" | jq -s '.[0] + .[1]')
    page=$((page + 1))
    # Rate limit: ClickUp free tier is 100 req/min
    sleep 0.5
  done

  echo "$all_tasks"
}

echo "=== Rebuilding static data mappings ==="
echo ""

# Process repo-specific lists
echo "--- Fetching repo-specific lists ---"
for repo_short in "${!REPO_LISTS[@]}"; do
  list_id="${REPO_LISTS[$repo_short]}"
  echo -n "  $repo_short (list $list_id)... "

  tasks=$(fetch_list_tasks "$list_id")
  task_count=$(echo "$tasks" | jq 'length')
  echo "$task_count tasks"

  for i in $(seq 0 $((task_count - 1))); do
    task_id=$(echo "$tasks" | jq -r ".[$i].id")
    task_name=$(echo "$tasks" | jq -r ".[$i].name")

    if [[ "$task_name" =~ ^\[([^#]+)#([0-9]+)\] ]]; then
      parsed_repo="${BASH_REMATCH[1]}"
      parsed_number="${BASH_REMATCH[2]}"
      mapping_key="${parsed_repo}/${parsed_number}"

      # Add repo_list to mapping
      GH_TO_CU=$(echo "$GH_TO_CU" | jq \
        --arg key "$mapping_key" \
        --arg tid "$task_id" \
        'if .[$key] then .[$key].repo_list = $tid else .[$key] = {repo_list: $tid, master_list: null} end')

      CU_TO_GH=$(echo "$CU_TO_GH" | jq \
        --arg tid "$task_id" \
        --arg key "$mapping_key" \
        '.[$tid] = $key')
    fi
  done
done

echo ""
echo "--- Fetching Master list ---"
echo -n "  Master (list $MASTER_LIST)... "

master_tasks=$(fetch_list_tasks "$MASTER_LIST")
master_count=$(echo "$master_tasks" | jq 'length')
echo "$master_count tasks"

for i in $(seq 0 $((master_count - 1))); do
  task_id=$(echo "$master_tasks" | jq -r ".[$i].id")
  task_name=$(echo "$master_tasks" | jq -r ".[$i].name")

  if [[ "$task_name" =~ ^\[([^#]+)#([0-9]+)\] ]]; then
    parsed_repo="${BASH_REMATCH[1]}"
    parsed_number="${BASH_REMATCH[2]}"
    mapping_key="${parsed_repo}/${parsed_number}"

    # Set master_list in mapping
    GH_TO_CU=$(echo "$GH_TO_CU" | jq \
      --arg key "$mapping_key" \
      --arg tid "$task_id" \
      'if .[$key] then .[$key].master_list = $tid else .[$key] = {repo_list: null, master_list: $tid} end')

    CU_TO_GH=$(echo "$CU_TO_GH" | jq \
      --arg tid "$task_id" \
      --arg key "$mapping_key" \
      '.[$tid] = $key')
  fi
done

# Build final payload
PAYLOAD=$(jq -n \
  --argjson g2c "$GH_TO_CU" \
  --argjson c2g "$CU_TO_GH" \
  '{action: "rebuild-mappings", github_to_clickup: $g2c, clickup_to_github: $c2g}')

gh_count=$(echo "$GH_TO_CU" | jq 'length')
cu_count=$(echo "$CU_TO_GH" | jq 'length')

echo ""
echo "=== Mapping Summary ==="
echo "  github_to_clickup entries: $gh_count"
echo "  clickup_to_github entries: $cu_count"

if $DRY_RUN; then
  echo ""
  echo "[DRY RUN] Would POST to $REBUILD_URL"
  echo "Payload size: $(echo "$PAYLOAD" | wc -c) bytes"
  echo ""
  echo "Sample entries (first 5):"
  echo "$GH_TO_CU" | jq 'to_entries[:5] | from_entries'
  exit 0
fi

echo ""
echo "=== Posting to rebuild webhook ==="
http_code=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "$REBUILD_URL" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

if [[ "$http_code" == "200" ]]; then
  echo "SUCCESS (HTTP $http_code) - $gh_count mappings restored"
else
  echo "ERROR (HTTP $http_code)"
  exit 1
fi
