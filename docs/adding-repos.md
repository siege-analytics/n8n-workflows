# Adding a New Repository to the Sync

This guide covers how to add a new GitHub repository to the ClickUp sync.

## Overview

Adding a repo requires changes in four places:

1. **ClickUp** — create a new list
2. **GitHub** — create status labels + register webhook
3. **Workflow A** (`github-to-clickup.json`) — add list ID mapping
4. **Workflow B** (`clickup-to-github.json`) — add repo mapping
5. **Bootstrap** — sync existing issues (optional)

## Step 1: Create ClickUp List

Create a new list in the "GitHub Issues" folder:

```bash
curl -s -X POST "https://api.clickup.com/api/v2/folder/90176500013/list" \
  -H "Authorization: $CLICKUP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "<repo_name>"}'
```

Note the `id` from the response — you'll need it in Step 3.

## Step 2: Create GitHub Labels

Add status labels to the new repo:

```bash
REPO="<org>/<repo_name>"

gh label create "status:scoping" --color "1d76db" --repo "$REPO"
gh label create "status:in-design" --color "5319e7" --repo "$REPO"
gh label create "status:ready" --color "0e8a16" --repo "$REPO"
gh label create "status:in-progress" --color "fbca04" --repo "$REPO"
gh label create "status:in-review" --color "d93f0b" --repo "$REPO"
gh label create "status:testing" --color "b60205" --repo "$REPO"
```

## Step 3: Update Workflow A (GitHub → ClickUp)

In the n8n UI, open the "GitHub → ClickUp Sync" workflow and edit the **Anti-Loop & Parse** code node. Find the `listMap` object and add the new repo:

```javascript
const listMap = {
  'enterprise': '901710789803',
  'siege_utilities': '901710789806',
  'rundeck': '901710789808',
  'ops': '901710789809',
  'portainer': '901710789810',
  '<new_repo_name>': '<NEW_LIST_ID>'   // ← add this line
};
```

Save the workflow.

**Also update the JSON file** in this repo (`workflows/github-to-clickup.json`) to keep it in sync with the deployed workflow.

## Step 4: Update Workflow B (ClickUp → GitHub)

In the n8n UI, open the "ClickUp → GitHub Sync" workflow and edit the **Lookup Mapping** code node. Find the `repoMap` object and add the new repo:

```javascript
const repoMap = {
  'enterprise': 'electinfo/enterprise',
  'siege_utilities': 'siege-analytics/siege_utilities',
  'rundeck': 'electinfo/rundeck',
  'ops': 'electinfo/ops',
  'portainer': 'electinfo/portainer',
  '<new_repo_name>': '<org>/<new_repo_name>'   // ← add this line
};
```

Save the workflow.

**Also update the JSON file** in this repo (`workflows/clickup-to-github.json`).

## Step 5: Register GitHub Webhook

```bash
gh api repos/<org>/<new_repo_name>/hooks -X POST \
  -f "config[url]=https://n8n.elect.info/webhook/github-to-clickup" \
  -f "config[content_type]=json" \
  -F "events[]=issues" \
  -F "events[]=issue_comment" \
  -F "active=true"
```

No ClickUp webhook changes needed — the workspace-level webhook already covers all lists.

## Step 6: Bootstrap Existing Issues (Optional)

If the new repo has existing issues, bootstrap them:

```bash
# Add the repo to the REPOS array in scripts/bootstrap-issues.sh first, then:
./scripts/bootstrap-issues.sh --repo <new_repo_name>
```

Or add the repo to the `REPOS` array in `scripts/bootstrap-issues.sh`:

```bash
REPOS=(
  "electinfo/enterprise"
  "siege-analytics/siege_utilities"
  "electinfo/rundeck"
  "electinfo/ops"
  "electinfo/portainer"
  "<org>/<new_repo_name>"   # ← add this line
)
```

## Step 7: Test

1. Create a test issue: `gh issue create --repo <org>/<new_repo_name> --title "Test sync" --body "Testing"`
2. Check ClickUp — task should appear in the new list and in Master
3. Change status in ClickUp, verify label appears on GitHub
4. Clean up test issue and task

## Checklist

- [ ] ClickUp list created with correct statuses
- [ ] GitHub status labels created (6 labels)
- [ ] Workflow A `listMap` updated (n8n UI + JSON file)
- [ ] Workflow B `repoMap` updated (n8n UI + JSON file)
- [ ] GitHub webhook registered
- [ ] Existing issues bootstrapped
- [ ] End-to-end test passed
- [ ] `scripts/bootstrap-issues.sh` REPOS array updated
