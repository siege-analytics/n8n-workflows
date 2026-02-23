# Architecture Overview

This document describes the architecture of both issue sync systems: GitHub <-> ClickUp and GitHub <-> GitLab.

## Data Flow

```
                         n8n CE (self-hosted)
                    ┌─────────────────────────┐
                    │                         │
  GitHub Issues ────┤  github-to-clickup.json ├───── ClickUp Tasks
  (5 repos)    ◄────┤  clickup-to-github.json │
                    │                         │
                    │  github-to-gitlab.json  ├───── GitLab Issues
                    │  gitlab-to-github.json  │      (1 repo pair)
                    │                         │
                    └─────────────────────────┘

  Webhooks (inbound):
    POST /webhook/github-to-clickup    ← GitHub (issues, issue_comment)
    POST /webhook/clickup-to-github    ← ClickUp (task events)
    POST /webhook/github-to-gitlab     ← GitHub (issues, issue_comment)
    POST /webhook/gitlab-to-github     ← GitLab (issues, notes)
```

### Synced Repos

**ClickUp Sync** (multi-repo):

| GitHub Repo | ClickUp List |
|-------------|-------------|
| `electinfo/enterprise` | enterprise |
| `siege-analytics/siege_utilities` | siege_utilities |
| `electinfo/rundeck` | rundeck |
| `electinfo/ops` | ops |
| `electinfo/portainer` | portainer |

**GitLab Sync** (single repo pair):

| GitHub Repo | GitLab Project |
|-------------|---------------|
| `electinfo/enterprise` | `siege-analytics/fec/pure-translation` |

## Workflow Node Pipeline

All four workflows follow the same pipeline pattern:

```
Webhook
  │
  ▼
Anti-Loop & Parse          (Code node)
  │  - Check for [sync] markers in title/body/comment
  │  - Validate event type and repo
  │  - Normalize action (opened, closed, assigned, comment, etc.)
  │  - Return [] to skip (anti-loop or irrelevant event)
  │
  ▼
Fetch Mapping              (httpRequest node)
  │  - GET the current issue mapping
  │  - ClickUp: n8n static data (in-memory)
  │  - GitLab: JSON file via GitHub Contents API
  │
  ▼
Decode Mapping             (Code node)
  │  - ClickUp: read from $getWorkflowStaticData('global')
  │  - GitLab: base64-decode the file content, JSON.parse
  │  - Look up existing mapping for the issue
  │
  ▼
Route by Action            (Switch v2 node)
  │  - Routes to action-specific branches:
  │    opened, closed, reopened, assigned, unassigned,
  │    labeled, unlabeled, comment
  │
  ├──► Prepare [Action]    (Code node per action)
  │      - Build API request body for the target platform
  │      - Include [sync] marker in synced content
  │
  └──► API Call            (httpRequest node)
         - POST/PUT/PATCH to target platform API
         - Update mapping if a new issue/task was created
```

### Key Design Decision: Sequential Execution

All branches execute sequentially (not in parallel). n8n parallel branches merging into a single downstream node cause "Node X hasn't been executed" errors. Each action branch has its own terminal httpRequest node.

## Anti-Loop Protection

Both sync directions use `[sync]` markers to prevent infinite loops:

| Content Type | Marker Location |
|-------------|-----------------|
| Issue title | Not modified (would be disruptive) |
| Issue body | `[sync]` appended to body on create |
| Comments | `[sync]` prefix on synced comments |
| Assignees | No marker needed -- API idempotency handles this |

**How it works:**
1. GitHub webhook fires for issue #42 ("opened")
2. n8n GitHub->GitLab workflow creates GitLab issue with `[sync]` in body
3. GitLab webhook fires for the new issue
4. n8n GitLab->GitHub workflow sees `[sync]` in body, returns `[]` (skips)
5. No infinite loop

**Assignee sync** relies on API idempotency: adding an already-present assignee or removing an already-absent one is a no-op that does not fire a new webhook event.

## Mapping Storage

### ClickUp Mapping

**Storage**: n8n workflow static data (`$getWorkflowStaticData('global')`)

**Structure:**
```json
{
  "github_to_clickup": {
    "enterprise/42": { "repoTaskId": "abc123", "masterTaskId": "def456" },
    "rundeck/7": { "repoTaskId": "ghi789", "masterTaskId": "jkl012" }
  },
  "clickup_to_github": {
    "abc123": "enterprise/42",
    "def456": "enterprise/42"
  }
}
```

**WARNING**: n8n static data may not persist between executions on all n8n CE instances. If mappings are lost after a workflow reimport or n8n restart, run `scripts/rebuild-mappings.sh` to reconstruct from existing ClickUp tasks.

### GitLab Mapping

**Storage**: `issue_mapping.json` file in a GitHub repo, fetched at runtime via the GitHub Contents API.

**Structure:**
```json
{
  "github_to_gitlab": {
    "1": 1,
    "2": 2,
    "42": 38
  },
  "gitlab_to_github": {
    "1": 1,
    "2": 2,
    "38": 42
  }
}
```

**How updates work:**
1. Workflow fetches the file via `GET /repos/ORG/REPO/contents/path/to/issue_mapping.json`
2. Response includes `sha` of the current file version
3. After creating a new mapping, workflow PUTs the updated JSON back with the `sha` for optimistic locking:
   ```
   PUT /repos/ORG/REPO/contents/path/to/issue_mapping.json
   { "message": "...", "content": "<base64>", "sha": "<current_sha>" }
   ```

This approach is more reliable than static data because the mapping survives workflow reimports, n8n restarts, and server migrations.

## n8n CE Limitations

These limitations were discovered during development and deployment. They apply to n8n Community Edition (self-hosted, tested on 1.x).

### Static Data Persistence

`$getWorkflowStaticData('global')` may not persist between executions on all n8n CE instances. This was the primary motivation for using file-based mapping for the GitLab sync.

**Workaround**: For ClickUp sync, run `scripts/rebuild-mappings.sh` after any workflow reimport.

### Switch Node Version

Only Switch v2 (`typeVersion: 2`) is supported. Switch v3 may not be available on all instances.

**Important**: Switch v2 strips `output` fields on import. Rules map sequentially to their output index (rule 0 -> output 0, rule 1 -> output 1, etc.). When multiple rules need to reach the same downstream node, create duplicate connections from each relevant output.

### Code Node Output Behavior

Code nodes that return objects like `{skipped: true}` still pass data downstream to connected nodes. This causes unexpected httpRequest executions.

**Fix**: Always return an empty array `[]` to silently skip processing:

```javascript
// WRONG - downstream httpRequest node still executes
if (shouldSkip) return [{ json: { skipped: true } }];

// CORRECT - stops the pipeline branch
if (shouldSkip) return [];
```

### Error Handling for Expected 404s

Some API calls are expected to return 404 (e.g., removing a label that does not exist). Use `onError: "continueRegularOutput"` on the httpRequest node to prevent workflow failure:

```json
{
  "options": {
    "response": {
      "response": {
        "neverError": true
      }
    }
  }
}
```

### Parallel Branch Merging

n8n does not support parallel branches merging into a single downstream node. If two branches both connect to the same node, n8n throws "Node X hasn't been executed" errors.

**Fix**: Give each branch its own terminal node (separate httpRequest nodes for each action).

### Workflow API Update (PUT)

The n8n API `PUT /api/v1/workflows/{id}` may return empty responses. For reliable workflow updates, delete the old workflow and recreate it:

```bash
# Delete
curl -X DELETE "https://N8N_HOST/api/v1/workflows/WORKFLOW_ID" \
  -H "X-N8N-API-KEY: API_KEY"

# Recreate
curl -X POST "https://N8N_HOST/api/v1/workflows" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: API_KEY" \
  -d @workflow.json
```

### Concurrent Webhook Events

GitHub may fire multiple events simultaneously (e.g., "opened" and "assigned" at the same time when creating an issue with an assignee). The "assigned" event may arrive before the n8n workflow finishes processing "opened", meaning the mapping does not yet exist.

**Fix**: In Prepare nodes, check whether the mapping exists. If not, return `[]` to skip. The bootstrap script handles this by sending events sequentially with a delay between them.

## User Mapping

Both sync systems use hardcoded user maps in Code nodes. There is no external user directory lookup.

### ClickUp User Map

Located in Workflow A (`github-to-clickup.json`) Prepare Create and Prepare Assignee Update nodes, and Workflow B (`clickup-to-github.json`) Map Assignee node:

```javascript
// GitHub -> ClickUp direction
const userMap = {
  'github_username': CLICKUP_USER_ID,
  // Add new users here
};

// ClickUp -> GitHub direction
const userMap = {
  CLICKUP_USER_ID: 'github_username',
  // Add new users here
};
```

### GitLab User Map

Located in Workflow A (`github-to-gitlab.json`) Prepare Assign node, and Workflow B (`gitlab-to-github.json`) Prepare Assign node:

```javascript
// GitHub -> GitLab direction
const userMap = {
  'github_username': GITLAB_USER_ID,
  // Add new users here
};

// GitLab -> GitHub direction
const userMap = {
  GITLAB_USER_ID: 'github_username',
  // Add new users here
};
```

### Adding a New User

To add a new user to either sync system:

1. Look up the user's ID on the target platform (ClickUp user ID or GitLab user ID)
2. Edit the mapping objects in the relevant Code nodes (see above)
3. Update both directions (A and B workflows)
4. Save and publish the workflows in n8n
5. Update the workflow JSON files in this repo to keep them in sync

## Synced Events Summary

### ClickUp Sync

| GitHub Event | ClickUp Action |
|-------------|----------------|
| Issue opened | Task created (repo list + Master list) |
| Issue closed | Task status -> shipped/cancelled |
| Issue reopened | Task status -> backlog |
| Label added/removed | Task status updated |
| Comment posted | Comment synced |
| Issue assigned | Task assignee added |
| Issue unassigned | Task assignee removed |

| ClickUp Event | GitHub Action |
|---------------|--------------|
| Status changed | Issue closed/reopened + label updated |
| Comment posted | Comment synced |
| Assignee added | Issue assignee added |
| Assignee removed | Issue assignee removed |

### GitLab Sync

| GitHub Event | GitLab Action |
|-------------|---------------|
| Issue opened | Issue created |
| Issue closed | Issue closed |
| Issue reopened | Issue reopened |
| Comment posted | Note created |
| Issue assigned | Issue assignee updated |
| Issue unassigned | Issue assignee updated |

| GitLab Event | GitHub Action |
|--------------|--------------|
| Issue opened | Issue created |
| Issue closed | Issue closed |
| Issue reopened | Issue reopened |
| Note created | Comment posted |
| Assignee changed | Issue assignee updated |
| Label changed | Label added/removed |

## Health Monitoring & Reconciliation

Three layers of defense prevent undetected sync outages:

### Layer 1: Blackbox Probe (Kubernetes)

A Prometheus blackbox exporter probe runs every 60s against the webhook endpoints. If ingress is down for >5 minutes, Grafana fires `N8nWebhookEndpointDown` and routes to the Zulip alerting channel.

- **Probe**: `n8n-probe.yaml` in `electinfo/ops/util-observability/base/`
- **Module**: `http_webhook` (accepts 200/404/405 — any HTTP response = ingress up)
- **Targets**: `https://n8n.elect.info/webhook/clickup-to-github`, `https://n8n.elect.info/webhook/github-to-clickup`
- **Alert**: `N8nWebhookEndpointDown` (critical, 5m), `N8nWebhookSlowResponse` (warning, >10s)

### Layer 2: Webhook Health Monitor (n8n)

**File**: `workflows/webhook-health-monitor.json`
**Schedule**: Every 2 hours

Probes both webhook endpoints from within n8n and checks the n8n execution API for recent webhook-triggered executions. If endpoints are unreachable or no executions have run in 4 hours, it creates/updates a GitHub issue in `electinfo/ops` with label `sync-health-alert`.

**Node pipeline**:
```
Schedule Trigger (2h)
  → Probe CU-GH Endpoint (GET)
  → Probe GH-CU Endpoint (GET)
  → Fetch Recent Executions (n8n API)
  → Evaluate Health (Code: check probes + execution gap)
  → Route by Health (Switch)
    → Healthy: Search open alert → Prepare Resolution → Post comment → Close issue
    → Unhealthy: Search open alert → Prepare Alert → Route Action → Create or Comment
```

**Credentials**: `GitHub API` (SbQTxL2kanI77ymm), `n8n API` (httpHeaderAuth with X-N8N-API-KEY)

### Layer 3: Sync Reconciliation (n8n)

**File**: `workflows/sync-reconciliation.json`
**Schedule**: Every 6 hours

Fetches all GitHub issues (search API) and all ClickUp tasks (list API per list), then compares open/closed state to detect drift. Auto-fixes `close_gh` drift (ClickUp closed but GitHub open). Posts a summary to a GitHub issue in `electinfo/ops` with label `sync-reconciliation`.

**Node pipeline**:
```
Schedule Trigger (6h)
  → Fetch GH Issues (search API, paginated)
  → Build GH Lookup (Code: key by repoShort#number)
  → Fetch CU Tasks (sequential: enterprise, siege_utilities, rundeck, ops)
  → Find Drift (Code: compare state, build fix list)
  → Route by Drift (Switch)
    → Has Drift: Build Fixes → Apply Fixes (PATCH, 1/sec) → Build Summary → Post
    → No Drift: Build Summary → Post
```

**Drift detection**:
- ClickUp "shipped"/"cancelled" but GitHub "open" → auto-close GH issue
- ClickUp open but GitHub "closed" → flag for manual review (no auto-reopen)

**Credentials**: `GitHub API` (SbQTxL2kanI77ymm), `ClickUp API` (ju5QMIyIYhk1qUcc)

## Project Structure

```
.
├── README.md
├── workflows/
│   ├── github-to-clickup.json       # GitHub -> ClickUp (multi-repo)
│   ├── clickup-to-github.json       # ClickUp -> GitHub (multi-repo)
│   ├── github-to-gitlab.json        # GitHub -> GitLab (single repo pair)
│   ├── gitlab-to-github.json        # GitLab -> GitHub (single repo pair)
│   ├── meet-standup-to-clickup.json # Google Meet standup → ClickUp docs
│   ├── webhook-health-monitor.json  # Webhook health probing + alerting
│   └── sync-reconciliation.json     # Cross-platform state drift detection
├── scripts/
│   ├── bootstrap-issues.sh          # Bootstrap ClickUp sync
│   ├── bootstrap-gitlab-issues.sh   # Bootstrap GitLab sync
│   ├── rebuild-mappings.sh          # Rebuild ClickUp static data
│   └── backfill-standup-notes.py    # Backfill standup docs
└── docs/
    ├── setup.md                     # ClickUp sync setup guide
    ├── gitlab-setup.md              # GitLab sync setup guide
    ├── adding-repos.md              # Adding repos to ClickUp sync
    ├── meet-standup-setup.md        # Standup workflow setup
    └── architecture.md              # This file
```
