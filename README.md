# n8n-workflows

n8n workflow definitions for two-way sync between GitHub Issues and ClickUp tasks. Deployed on a self-hosted n8n Community Edition instance.

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| n8n | Community Edition (self-hosted) | Tested on 1.x; Switch node must be typeVersion 2 |
| Node types | `webhook` v2, `code` v2, `switch` v2, `httpRequest` v4.2 | Switch v3 is **not supported** on all instances |
| ClickUp API | v2 | Personal API token (free tier works) |
| GitHub API | REST v3 | Fine-grained or classic PAT with `repo` scope |
| GitHub CLI | 2.x | Used by bootstrap script |
| jq | 1.6+ | Used by bootstrap script |
| curl | 7.x+ | Used by bootstrap script |
| bash | 4.x+ | Bootstrap script |

## Overview

Two workflows keep GitHub Issues and ClickUp tasks in sync across multiple repositories:

| Workflow | Direction | Webhook Path |
|----------|-----------|--------------|
| `github-to-clickup.json` | GitHub → ClickUp | `/webhook/github-to-clickup` |
| `clickup-to-github.json` | ClickUp → GitHub | `/webhook/clickup-to-github` |

### Synced Repositories

| GitHub Repo | ClickUp List |
|-------------|-------------|
| `electinfo/enterprise` | enterprise |
| `siege-analytics/siege_utilities` | siege_utilities |
| `electinfo/rundeck` | rundeck |
| `electinfo/ops` | ops |
| `electinfo/portainer` | portainer |

All tasks also appear in a **Master** list for a unified cross-repo view.

### What Gets Synced

| GitHub Event | ClickUp Action |
|-------------|----------------|
| Issue opened | Task created (repo list + Master list) |
| Issue closed | Task status → shipped/cancelled |
| Issue reopened | Task status → backlog |
| Label added/removed | Task status updated |
| Comment posted | Comment synced |

| ClickUp Event | GitHub Action |
|---------------|--------------|
| Status changed | Issue closed/reopened + label updated |
| Comment posted | Comment synced |

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

### Anti-Loop Protection

Both workflows check for `[sync]` markers in titles, bodies, and comments. Synced content is prefixed with `[sync]` to prevent infinite loops.

## Setup

See [docs/setup.md](docs/setup.md) for initial deployment instructions.

## Adding a New Repository

See [docs/adding-repos.md](docs/adding-repos.md) for step-by-step instructions.

## Bootstrap

The `scripts/bootstrap-issues.sh` script syncs existing GitHub issues into ClickUp by sending synthetic webhook payloads to the n8n workflow.

```bash
# Dry run (no changes)
./scripts/bootstrap-issues.sh --dry-run

# Bootstrap a single repo
./scripts/bootstrap-issues.sh --repo enterprise

# Bootstrap all repos (default 1.5s delay between calls)
./scripts/bootstrap-issues.sh

# Custom delay (seconds between webhook calls)
./scripts/bootstrap-issues.sh --delay 2
```

## Architecture Notes

- **ID mapping** is stored in n8n workflow static data (`$getWorkflowStaticData('global')`). The mapping keys are `github_to_clickup` (maps `repo/issue_number` → ClickUp task IDs) and `clickup_to_github` (maps ClickUp task ID → `repo/issue_number`).
- **Dual-write**: every GitHub issue creates tasks in both the repo-specific list and the Master list. Both ClickUp task IDs are stored in the mapping.
- **Sequential execution**: the Create Repo Task and Create Master Task nodes run sequentially (not in parallel). n8n parallel branches into a single downstream node cause "Node X hasn't been executed" errors.
- **n8n credentials**: the workflows reference two `httpHeaderAuth` credentials by ID — one for ClickUp API and one for GitHub API. These must exist in n8n before importing the workflows. See [docs/setup.md](docs/setup.md).

## Project Structure

```
.
├── README.md
├── workflows/
│   ├── github-to-clickup.json    # Workflow A: GitHub → ClickUp
│   └── clickup-to-github.json    # Workflow B: ClickUp → GitHub
├── scripts/
│   └── bootstrap-issues.sh       # Bulk sync existing issues
└── docs/
    ├── setup.md                  # Initial deployment guide
    └── adding-repos.md           # How to add new repos to sync
```
