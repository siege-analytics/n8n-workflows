# n8n-workflows

n8n workflow definitions for bidirectional issue sync between GitHub and external project management platforms. Deployed on a self-hosted n8n Community Edition instance.

Currently supports two sync targets:

| Sync | Workflows | Docs |
|------|-----------|------|
| **GitHub ↔ ClickUp** | `github-to-clickup.json`, `clickup-to-github.json` | [Setup](docs/setup.md) |
| **GitHub ↔ GitLab** | `github-to-gitlab.json`, `gitlab-to-github.json` | [Setup](docs/gitlab-setup.md) |

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| n8n | Community Edition (self-hosted) | Tested on 1.x/2.x; Switch node must be typeVersion 2 |
| Node types | `webhook` v2, `code` v2, `switch` v2, `httpRequest` v4.2 | Switch v3 is **not supported** on all instances |
| GitHub API | REST v3 | Fine-grained or classic PAT with `repo` scope |
| GitHub CLI | 2.x | Used by bootstrap scripts |
| jq | 1.6+ | Used by bootstrap scripts |
| curl | 7.x+ | Used by bootstrap scripts |
| bash | 4.x+ | Bootstrap scripts |

**Platform-specific:**

| Component | Notes |
|-----------|-------|
| ClickUp API v2 | Personal API token (free tier works) |
| GitLab API v4 | Personal Access Token with `api` scope |

## How It Works

All four workflows follow the same node pipeline:

```
Webhook → Anti-Loop & Parse → Fetch Mapping → Decode Mapping → Route by Action (Switch) → Prepare [Action] → API Call
```

1. **Webhook** receives events from the source platform
2. **Anti-Loop & Parse** checks for `[sync]` markers to prevent infinite loops, then normalizes the payload
3. **Fetch Mapping** retrieves the issue ID mapping (how issue numbers on one platform correspond to the other)
4. **Decode Mapping** merges the mapping with the parsed event data
5. **Route by Action** (Switch v2) routes to the appropriate handler based on event type
6. **Prepare** nodes look up the corresponding issue on the target platform and format the API request
7. **API Call** nodes execute the action on the target platform

See [docs/architecture.md](docs/architecture.md) for detailed architecture notes and n8n CE limitations.

## GitHub ↔ ClickUp Sync

Two workflows keep GitHub Issues and ClickUp tasks in sync across multiple repositories:

| Workflow | Direction | Webhook Path |
|----------|-----------|--------------|
| `github-to-clickup.json` | GitHub → ClickUp | `/webhook/github-to-clickup` |
| `clickup-to-github.json` | ClickUp → GitHub | `/webhook/clickup-to-github` |

### What Gets Synced (ClickUp)

| Direction | Events |
|-----------|--------|
| GitHub → ClickUp | Issue create, close, reopen, label, comment, assign, unassign |
| ClickUp → GitHub | Status change (close/reopen + label), comment, assign, unassign |

### Status Mapping

| ClickUp Status | GitHub Label |
|----------------|-------------|
| backlog | *(open, no label)* |
| scoping | `status:scoping` |
| in design | `status:in-design` |
| ready for development | `status:ready` |
| in development | `status:in-progress` |
| in review | `status:in-review` |
| testing | `status:testing` |
| shipped | *(closed, completed)* |
| cancelled | *(closed, not_planned)* |

### Setup & Docs

- [Initial setup guide](docs/setup.md)
- [Adding a new repository](docs/adding-repos.md)

### Bootstrap (ClickUp)

```bash
# Dry run
./scripts/bootstrap-issues.sh --dry-run

# Bootstrap a single repo
./scripts/bootstrap-issues.sh --repo enterprise

# Bootstrap all repos
./scripts/bootstrap-issues.sh
```

## GitHub ↔ GitLab Sync

Two workflows keep GitHub Issues and GitLab Issues in sync for a single repo pair:

| Workflow | Direction | Webhook Path |
|----------|-----------|--------------|
| `github-to-gitlab.json` | GitHub → GitLab | `/webhook/github-to-gitlab` |
| `gitlab-to-github.json` | GitLab → GitHub | `/webhook/gitlab-to-github` |

### What Gets Synced (GitLab)

| Direction | Events |
|-----------|--------|
| GitHub → GitLab | Issue create, close, reopen, label add/remove, comment, assign, unassign |
| GitLab → GitHub | Issue create, close, reopen, label change, comment, assignee change |

### Mapping Storage

The GitLab sync uses a JSON mapping file (`issue_mapping.json`) stored in a GitHub repository and fetched at runtime via the GitHub Contents API. This avoids reliance on n8n's `$getWorkflowStaticData()`, which does not persist between executions on some n8n CE instances.

```json
{
  "github_to_gitlab": { "1": 1, "2": 2 },
  "gitlab_to_github": { "1": 1, "2": 2 }
}
```

### Setup & Docs

- [Initial setup guide](docs/gitlab-setup.md)

### Bootstrap (GitLab)

```bash
# Dry run
WEBHOOK_URL="https://your-n8n/webhook/github-to-gitlab" ./scripts/bootstrap-gitlab-issues.sh --dry-run

# Bootstrap default repo
WEBHOOK_URL="https://your-n8n/webhook/github-to-gitlab" ./scripts/bootstrap-gitlab-issues.sh

# Bootstrap specific repo
WEBHOOK_URL="https://your-n8n/webhook/github-to-gitlab" ./scripts/bootstrap-gitlab-issues.sh --repo org/repo
```

## User Mapping (Assignee Sync)

Both sync systems map users between platforms using hardcoded lookup tables in the workflow Code nodes. To add a new user, update the `userMap` objects in the relevant **Prepare Assign** nodes.

### Anti-Loop Protection

All workflows check for `[sync]` markers in issue titles, bodies, and comments. Content synced from one platform is prefixed with `[sync]` to prevent infinite loops. Assignee sync relies on API idempotency (adding an already-present assignee is a no-op).

## Project Structure

```
.
├── README.md
├── workflows/
│   ├── github-to-clickup.json         # GitHub → ClickUp (28 nodes)
│   ├── clickup-to-github.json         # ClickUp → GitHub (17 nodes)
│   ├── github-to-gitlab.json          # GitHub → GitLab (17 nodes)
│   └── gitlab-to-github.json          # GitLab → GitHub (17 nodes)
├── scripts/
│   ├── bootstrap-issues.sh            # Bootstrap GitHub → ClickUp
│   ├── bootstrap-gitlab-issues.sh     # Bootstrap GitHub → GitLab
│   └── rebuild-mappings.sh            # Rebuild ClickUp static data
└── docs/
    ├── setup.md                       # ClickUp setup guide
    ├── gitlab-setup.md                # GitLab setup guide
    ├── adding-repos.md                # Adding repos to ClickUp sync
    └── architecture.md                # Architecture & n8n CE notes
```

## License

MIT
