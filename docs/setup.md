# Initial Setup Guide

This documents the full setup process for the GitHub ↔ ClickUp sync via n8n.

## Prerequisites

- A self-hosted n8n instance (Community Edition, tested on 1.x)
- A ClickUp workspace with API access (free tier works)
- GitHub repos with admin access (for webhook registration)
- API tokens for both platforms

## Step 0: Persist API Tokens

Store these tokens securely (e.g., 1Password, environment variables):

| Token | Purpose |
|-------|---------|
| ClickUp Personal API Token | `pk_*` format, from ClickUp Settings → Apps |
| GitHub Personal Access Token | Classic or fine-grained with `repo` scope |
| n8n API Key | JWT format, from n8n Settings → API |

## Step 1: Create ClickUp Structure

### Create a Folder

```bash
curl -s -X POST "https://api.clickup.com/api/v2/space/<SPACE_ID>/folder" \
  -H "Authorization: <CLICKUP_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "GitHub Issues"}'
```

### Create Lists (one per repo + Master)

```bash
# Repeat for each repo name: enterprise, siege_utilities, rundeck, ops, portainer
curl -s -X POST "https://api.clickup.com/api/v2/folder/<FOLDER_ID>/list" \
  -H "Authorization: <CLICKUP_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "<repo_name>"}'

# Plus the Master list
curl -s -X POST "https://api.clickup.com/api/v2/folder/<FOLDER_ID>/list" \
  -H "Authorization: <CLICKUP_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Master"}'
```

### Configure ClickUp Statuses

Add these statuses to the Space (Settings → Statuses):

1. backlog (default open)
2. scoping
3. in design
4. ready for development
5. in development
6. in review
7. testing
8. shipped (closed)
9. cancelled (closed)

## Step 2: Create GitHub Status Labels

Create these labels on each synced GitHub repo:

```bash
REPOS="electinfo/enterprise siege-analytics/siege_utilities electinfo/rundeck electinfo/ops electinfo/portainer"

for repo in $REPOS; do
  gh label create "status:scoping" --color "1d76db" --repo "$repo" 2>/dev/null
  gh label create "status:in-design" --color "5319e7" --repo "$repo" 2>/dev/null
  gh label create "status:ready" --color "0e8a16" --repo "$repo" 2>/dev/null
  gh label create "status:in-progress" --color "fbca04" --repo "$repo" 2>/dev/null
  gh label create "status:in-review" --color "d93f0b" --repo "$repo" 2>/dev/null
  gh label create "status:testing" --color "b60205" --repo "$repo" 2>/dev/null
done
```

## Step 3: Create n8n Credentials

Before importing workflows, create two `httpHeaderAuth` credentials in n8n:

### ClickUp API Credential

1. In n8n UI: Settings → Credentials → Add Credential
2. Type: **Header Auth**
3. Name: `ClickUp API`
4. Header Name: `Authorization`
5. Header Value: your ClickUp personal API token (`pk_*`)
6. Save — note the credential ID

### GitHub API Credential

1. In n8n UI: Settings → Credentials → Add Credential
2. Type: **Header Auth**
3. Name: `GitHub API`
4. Header Name: `Authorization`
5. Header Value: `token <YOUR_GITHUB_PAT>`
6. Save — note the credential ID

### Update Workflow JSON Files

Edit both workflow JSON files and replace the credential IDs:

**In `workflows/github-to-clickup.json`:** replace all instances of `"id": "ju5QMIyIYhk1qUcc"` with your ClickUp credential ID.

**In `workflows/clickup-to-github.json`:** replace all instances of `"id": "SbQTxL2kanI77ymm"` with your GitHub credential ID.

## Step 4: Import Workflows

1. In n8n UI: click **Add workflow**
2. Click the three-dot menu (top right) → **Import from File**
3. Select `workflows/github-to-clickup.json`, then **Save**
4. Repeat for `workflows/clickup-to-github.json`
5. **Publish** each workflow using the toggle/button

## Step 5: Update List IDs in Workflows

The workflow JSON files contain hardcoded ClickUp list IDs. After creating your ClickUp structure (Step 1), update these IDs in the **Anti-Loop & Parse** code node of `github-to-clickup.json`:

```javascript
const listMap = {
  'enterprise': '<YOUR_ENTERPRISE_LIST_ID>',
  'siege_utilities': '<YOUR_SIEGE_UTILITIES_LIST_ID>',
  'rundeck': '<YOUR_RUNDECK_LIST_ID>',
  'ops': '<YOUR_OPS_LIST_ID>',
  'portainer': '<YOUR_PORTAINER_LIST_ID>'
};

const masterListId = '<YOUR_MASTER_LIST_ID>';
```

And in `clickup-to-github.json`, update the **Lookup Mapping** code node's `repoMap`.

## Step 6: Bootstrap Existing Issues

Run the bootstrap script to sync existing GitHub issues into ClickUp:

```bash
# Test with dry run first
./scripts/bootstrap-issues.sh --dry-run

# Run for real (1.5s delay between calls to respect ClickUp rate limits)
./scripts/bootstrap-issues.sh
```

The ClickUp free tier allows 100 API requests per minute. Each issue creates 2 ClickUp tasks (repo + master), so the default 1.5s delay keeps you under the limit.

## Step 7: Register Webhooks

### GitHub Webhooks

Register a webhook on each synced repo pointing to your n8n instance:

```bash
REPOS="electinfo/enterprise siege-analytics/siege_utilities electinfo/rundeck electinfo/ops electinfo/portainer"
WEBHOOK_URL="https://<YOUR_N8N_HOST>/webhook/github-to-clickup"

for repo in $REPOS; do
  gh api repos/$repo/hooks -X POST \
    -f "config[url]=$WEBHOOK_URL" \
    -f "config[content_type]=json" \
    -F "events[]=issues" \
    -F "events[]=issue_comment" \
    -F "active=true"
done
```

### ClickUp Webhook

Register a webhook on the ClickUp workspace:

```bash
curl -s -X POST "https://api.clickup.com/api/v2/team/<TEAM_ID>/webhook" \
  -H "Authorization: <CLICKUP_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "https://<YOUR_N8N_HOST>/webhook/clickup-to-github",
    "events": [
      "taskCreated",
      "taskUpdated",
      "taskStatusUpdated",
      "taskCommentPosted",
      "taskAssigneeUpdated"
    ]
  }'
```

## Step 8: Rebuild Mappings (after reimport)

If you reimport Workflow A via the n8n UI, the static data (issue-to-task mappings) is lost. Rebuild it:

```bash
export CLICKUP_TOKEN="pk_..."

# Preview what will be rebuilt
./scripts/rebuild-mappings.sh --dry-run

# Rebuild for real
./scripts/rebuild-mappings.sh
```

This fetches all ClickUp tasks, parses their names, and POSTs the mapping to the workflow's rebuild webhook (`/webhook/rebuild-mappings`).

## Step 9: End-to-End Test

1. Create a test issue on GitHub: `gh issue create --repo electinfo/enterprise --title "Test sync" --body "Testing two-way sync"`
2. Wait ~5 seconds, check ClickUp — a task should appear in both the enterprise list and the Master list
3. Change the task status in ClickUp to "in development"
4. Wait ~5 seconds, check GitHub — the issue should have a `status:in-progress` label
5. Close the GitHub issue
6. Wait ~5 seconds, check ClickUp — the task status should be "shipped"
7. Delete the test issue and task when done

## Deployment Reference

| Resource | Value |
|----------|-------|
| n8n instance | `https://n8n.elect.info` |
| ClickUp Team ID | `9017833757` |
| ClickUp Space ID | `90173962997` |
| ClickUp Folder ID | `90176500013` |
| ClickUp credential (n8n) | `ju5QMIyIYhk1qUcc` |
| GitHub credential (n8n) | `SbQTxL2kanI77ymm` |
| Workflow A (GitHub→ClickUp) | imported via UI |
| Workflow B (ClickUp→GitHub) | imported via UI |

### ClickUp List IDs

| List | ID |
|------|----|
| enterprise | `901710789803` |
| siege_utilities | `901710789806` |
| rundeck | `901710789808` |
| ops | `901710789809` |
| portainer | `901710789810` |
| Master | `901710789811` |
