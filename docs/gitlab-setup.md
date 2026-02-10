# GitHub <-> GitLab Issue Sync Setup Guide

This documents the full setup process for the GitHub <-> GitLab issue sync via n8n.

## Prerequisites

- A self-hosted n8n instance (Community Edition, tested on 1.x)
- A GitHub repo with admin access (for webhook registration)
- A GitLab project with API access (Maintainer role or higher)
- API tokens for both platforms:

| Token | Purpose |
|-------|---------|
| GitHub Personal Access Token | Classic or fine-grained with `repo` scope |
| GitLab Personal Access Token | With `api` scope (Settings > Access Tokens) |
| n8n API Key | JWT format, from n8n Settings > API |

## Step 1: Create Issue Mapping File

The GitLab sync stores issue mappings in a JSON file committed to a GitHub repo (unlike ClickUp, which uses n8n static data). Create the initial mapping file:

```bash
# Create the mapping file in a repo accessible by your n8n GitHub credential
mkdir -p scripts/github-gitlab-sync

cat > scripts/github-gitlab-sync/issue_mapping.json << 'EOF'
{
  "github_to_gitlab": {},
  "gitlab_to_github": {}
}
EOF

git add scripts/github-gitlab-sync/issue_mapping.json
git commit -m "Add empty GitHub-GitLab issue mapping file"
git push
```

The n8n workflows fetch this file at runtime via the GitHub Contents API and update it after each sync operation. The mapping keys are GitHub issue numbers and the values are GitLab issue IIDs (and vice versa).

## Step 2: Look Up GitLab Project ID and User IDs

### Find Your GitLab Project ID

```bash
# URL-encode the project path (e.g., "my-group/my-project" becomes "my-group%2Fmy-project")
curl -s "https://gitlab.com/api/v4/projects/YOUR_GROUP%2FYOUR_PROJECT" \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" | jq '.id, .path_with_namespace'
```

Note the numeric `id` from the response -- you will need it in Step 4.

### Find Your GitLab User ID

```bash
# Get the authenticated user's ID
curl -s "https://gitlab.com/api/v4/user" \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" | jq '.id, .username'
```

### Find Other GitLab User IDs

```bash
# Search for a user by username
curl -s "https://gitlab.com/api/v4/users?username=OTHER_USERNAME" \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" | jq '.[0].id, .[0].username'
```

Record the GitLab user IDs for all users you want to include in assignee sync. You will need to map them to GitHub usernames in the workflow Code nodes.

## Step 3: Create n8n Credentials

Before importing workflows, create two `httpHeaderAuth` credentials in n8n:

### GitHub API Credential

1. In n8n UI: Settings > Credentials > Add Credential
2. Type: **Header Auth**
3. Name: `GitHub API`
4. Header Name: `Authorization`
5. Header Value: `token <YOUR_GITHUB_PAT>`
6. Save -- note the credential ID

### GitLab API Credential

1. In n8n UI: Settings > Credentials > Add Credential
2. Type: **Header Auth**
3. Name: `GitLab API`
4. Header Name: `PRIVATE-TOKEN`
5. Header Value: your GitLab personal access token
6. Save -- note the credential ID

## Step 4: Update Workflow JSON Files

Edit both workflow JSON files before importing.

### In `workflows/github-to-gitlab.json`:

1. **Replace credential IDs**: Search for the `httpHeaderAuth` blocks and replace the `id` values:
   - GitHub API credential: replace `"id": "PLACEHOLDER_GITHUB_CRED_ID"` with your GitHub credential ID
   - GitLab API credential: replace `"id": "PLACEHOLDER_GITLAB_CRED_ID"` with your GitLab credential ID

2. **Replace repo and project paths**:
   - Replace `YOUR_ORG/YOUR_REPO` with your GitHub repo (e.g., `electinfo/enterprise`)
   - Replace `YOUR_GITLAB_PROJECT_ID` with the numeric project ID from Step 2

3. **Replace mapping file URL**: Update the Fetch Mapping node URL:
   ```
   https://api.github.com/repos/YOUR_ORG/YOUR_MAPPING_REPO/contents/scripts/github-gitlab-sync/issue_mapping.json
   ```

4. **Update user mappings** in the Prepare Assign code node:
   ```javascript
   const userMap = {
     'github_username_1': GITLAB_USER_ID_1,
     'github_username_2': GITLAB_USER_ID_2
   };
   ```

### In `workflows/gitlab-to-github.json`:

1. **Replace credential IDs**: Same as above for both GitHub and GitLab credentials.

2. **Replace repo and project references**: Update the Anti-Loop & Parse code node to check for your repo name, and update all httpRequest node URLs.

3. **Update user mappings** in the Prepare Assign code node:
   ```javascript
   const userMap = {
     GITLAB_USER_ID_1: 'github_username_1',
     GITLAB_USER_ID_2: 'github_username_2'
   };
   ```

## Step 5: Import Workflows

### Option A: Import via n8n UI (Recommended)

1. In n8n UI: click **Add workflow**
2. Click the three-dot menu (top right) > **Import from File**
3. Select `workflows/github-to-gitlab.json`, then **Save**
4. Repeat for `workflows/gitlab-to-github.json`
5. **Publish** each workflow using the toggle/button

### Option B: Import via n8n API

```bash
# Strip extra fields for API compatibility (n8n API rejects unknown top-level keys)
python3 -c "
import json
wf = json.load(open('workflows/github-to-gitlab.json'))
print(json.dumps({k: wf[k] for k in ['name', 'nodes', 'connections', 'settings'] if k in wf}))
" > /tmp/clean-gh-to-gl.json

curl -X POST 'https://YOUR_N8N_HOST/api/v1/workflows' \
  -H 'Content-Type: application/json' \
  -H 'X-N8N-API-KEY: YOUR_API_KEY' \
  -d @/tmp/clean-gh-to-gl.json

# Repeat for gitlab-to-github.json
python3 -c "
import json
wf = json.load(open('workflows/gitlab-to-github.json'))
print(json.dumps({k: wf[k] for k in ['name', 'nodes', 'connections', 'settings'] if k in wf}))
" > /tmp/clean-gl-to-gh.json

curl -X POST 'https://YOUR_N8N_HOST/api/v1/workflows' \
  -H 'Content-Type: application/json' \
  -H 'X-N8N-API-KEY: YOUR_API_KEY' \
  -d @/tmp/clean-gl-to-gh.json
```

Note the workflow IDs from the API responses.

## Step 6: Activate Workflows

If you imported via the API, activate both workflows:

```bash
# Activate GitHub -> GitLab workflow
curl -X POST 'https://YOUR_N8N_HOST/api/v1/workflows/WORKFLOW_ID_1/activate' \
  -H 'X-N8N-API-KEY: YOUR_API_KEY'

# Activate GitLab -> GitHub workflow
curl -X POST 'https://YOUR_N8N_HOST/api/v1/workflows/WORKFLOW_ID_2/activate' \
  -H 'X-N8N-API-KEY: YOUR_API_KEY'
```

If you imported via the UI, use the toggle in the workflow editor to activate each workflow.

## Step 7: Register Webhooks

### GitHub Webhook

Register a webhook on the synced GitHub repo:

```bash
REPO="YOUR_ORG/YOUR_REPO"
WEBHOOK_URL="https://YOUR_N8N_HOST/webhook/github-to-gitlab"

gh api repos/$REPO/hooks -X POST \
  -f "config[url]=$WEBHOOK_URL" \
  -f "config[content_type]=json" \
  -F "events[]=issues" \
  -F "events[]=issue_comment" \
  -F "active=true"
```

### GitLab Webhook

Register a webhook on the synced GitLab project:

```bash
GITLAB_PROJECT_ID="YOUR_GITLAB_PROJECT_ID"

curl -X POST "https://gitlab.com/api/v4/projects/$GITLAB_PROJECT_ID/hooks" \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://YOUR_N8N_HOST/webhook/gitlab-to-github",
    "issues_events": true,
    "note_events": true,
    "confidential_issues_events": false,
    "push_events": false,
    "merge_requests_events": false,
    "tag_push_events": false,
    "pipeline_events": false,
    "wiki_page_events": false,
    "enable_ssl_verification": true
  }'
```

## Step 8: Bootstrap Existing Issues

If the GitHub repo has existing issues, sync them to GitLab using the bootstrap script:

```bash
# Test with dry run first
./scripts/bootstrap-gitlab-issues.sh --dry-run

# Run for real (default 1.5s delay between calls)
./scripts/bootstrap-gitlab-issues.sh

# Custom repo and delay
./scripts/bootstrap-gitlab-issues.sh --repo YOUR_ORG/YOUR_REPO --delay 2
```

The script sends synthetic GitHub webhook payloads to the n8n workflow, which handles creating GitLab issues, syncing state, and mapping assignees.

## Step 9: End-to-End Test

1. **Create a test issue on GitHub:**
   ```bash
   gh issue create --repo YOUR_ORG/YOUR_REPO --title "Test sync" --body "Testing GitHub-GitLab sync"
   ```

2. **Wait ~10 seconds**, then check GitLab -- an issue should appear with `[sync]` in the body

3. **Assign the GitHub issue** to a mapped user:
   ```bash
   gh issue edit ISSUE_NUMBER --repo YOUR_ORG/YOUR_REPO --add-assignee YOUR_USERNAME
   ```

4. **Wait ~10 seconds**, check GitLab -- the issue should be assigned to the mapped GitLab user

5. **Close the GitHub issue:**
   ```bash
   gh issue close ISSUE_NUMBER --repo YOUR_ORG/YOUR_REPO
   ```

6. **Wait ~10 seconds**, check GitLab -- the issue should be closed

7. **Add a comment on GitLab**, verify it appears on the GitHub issue

8. **Clean up** test issues on both platforms when done

## Deployment Reference

Fill in your actual values after setup:

| Resource | Value |
|----------|-------|
| n8n instance | `https://YOUR_N8N_HOST` |
| GitHub repo | `YOUR_ORG/YOUR_REPO` |
| GitLab project ID | `YOUR_GITLAB_PROJECT_ID` |
| GitLab project path | `YOUR_GROUP/YOUR_PROJECT` |
| Mapping file repo | `YOUR_ORG/YOUR_MAPPING_REPO` |
| GitHub credential (n8n) | `YOUR_GITHUB_CRED_ID` |
| GitLab credential (n8n) | `YOUR_GITLAB_CRED_ID` |
| Workflow A (GitHub->GitLab) | imported via UI |
| Workflow B (GitLab->GitHub) | imported via UI |

### User Mapping

| Person | GitHub Login | GitLab User ID |
|--------|-------------|----------------|
| User 1 | `github_username` | `GITLAB_USER_ID` |
