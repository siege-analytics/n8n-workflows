# Google Meet Standup → ClickUp Docs Setup

Workflow: `workflows/meet-standup-to-clickup.json`

Polls Google Drive hourly for Gemini-generated meeting notes from Google Meet,
compares against existing ClickUp Docs, and creates only the missing ones.
This **poll-and-reconcile** approach eliminates gaps from downtime or missed triggers —
every run is a full sync.

## Flow

```
Schedule Trigger (every hour)
  → List Drive Docs (HTTP Request → Google Drive API)
  → List ClickUp Docs (HTTP Request → ClickUp API v3)
  → Find Missing Docs (Code — name-based dedup)
  → Export Doc as Text (HTTP Request → Google Drive export API, batched)
  → Format for ClickUp (Code — builds markdown doc payload)
  → Create ClickUp Doc (HTTP Request → ClickUp API v3, batched)
  → Build Page Payload (Code — extracts doc ID from create response)
  → Create Page Content (HTTP Request → ClickUp Pages API, batched)
```

**Important**: ClickUp Docs API v3 uses a two-stage creation process.
`POST /docs` creates an empty shell (ignoring the `content` field).
Content must be added via a separate `POST /docs/{id}/pages` call.
The API may wrap create responses in a `data` key (`response.data.id`).

## How It Works

1. **Schedule Trigger** fires every hour
2. **List Drive Docs** queries all Google Docs in the Meet Notes folder matching
   "Daily Standup and Checkin"
3. **List ClickUp Docs** queries all existing docs in the target ClickUp folder
4. **Find Missing Docs** compares the two lists by name:
   - For each Drive doc, computes the expected ClickUp name: `Daily Standup — YYYY-MM-DD`
     (using the Drive file's `createdTime`, not "today")
   - Returns only Drive docs whose expected name doesn't exist in ClickUp yet
   - If all docs are already synced, returns empty → downstream nodes skip gracefully
5. **Export Doc as Text** fetches the plain text content of each missing doc (1 req/sec)
6. **Format for ClickUp** builds the markdown payload with header, source, date, and body
7. **Create ClickUp Doc** creates each empty doc shell in ClickUp (1 req/sec)
8. **Build Page Payload** extracts the doc ID from the create response (`data.id` or `id`)
   and pairs it with the formatted content
9. **Create Page Content** populates each doc with its content via the Pages API (1 req/sec)

### Key Properties

- **Stateless dedup**: No persistent state needed — queries both APIs every run
- **Idempotent**: Safe to re-run; duplicate runs within the hour produce zero double-creates
- **Historical dates**: Uses `createdTime` from Drive, so backfilled docs get correct dates
- **Sequential processing**: Batched HTTP requests with 1-second intervals respect rate limits

## Prerequisites

### Google Workspace

1. **Gemini in Google Meet** must be enabled (Business Standard+ or Enterprise plan)
2. **Admin setting**: Enable automatic notes/transcripts org-wide so nobody forgets
   to click "Take notes for me" — Google Workspace Admin → Apps → Google Meet →
   Meet settings → Gemini → toggle on automatic notes
3. Gemini notes auto-save as Google Docs in the organizer's Drive, typically in a
   `Meet Notes` folder

### Google Drive Folder ID

Find the folder ID for your Meet Notes folder:
1. Open Google Drive in a browser
2. Navigate to the folder where Gemini saves meeting notes
3. The folder ID is the last segment of the URL:
   `https://drive.google.com/drive/folders/<THIS_IS_THE_FOLDER_ID>`

### n8n Credentials

**Google Drive OAuth2:**
1. In n8n, go to Settings → Credentials → Add Credential
2. Select "Google Drive OAuth2 API"
3. Follow the OAuth2 setup flow (requires a GCP project with Drive API enabled)
4. Note the credential ID after saving

**ClickUp API:**
- Reuses the existing `ClickUp API` credential (`httpHeaderAuth`, id: `ju5QMIyIYhk1qUcc`)
- If this credential doesn't exist, create an `httpHeaderAuth` credential with:
  - Header Name: `Authorization`
  - Header Value: your ClickUp API token (from ClickUp → Settings → Apps → API Token)

## Configuration

After importing the workflow, update these placeholder values:

### Node: "List Drive Docs"

| Parameter | Value to Set |
|-----------|-------------|
| `queryParameters[0].value` (the `q` param) | Update the folder ID in the query string |
| `credentials.googleDriveOAuth2Api.id` | Your n8n Google Drive credential ID |

### Node: "List ClickUp Docs"

| Parameter | Value to Set |
|-----------|-------------|
| `queryParameters[0].value` (`parent.id`) | ClickUp parent folder ID |
| `queryParameters[1].value` (`parent.type`) | `4` (Space). Other options: `5` (Folder), `6` (List) |
| `credentials.httpHeaderAuth.id` | Your n8n ClickUp credential ID |

### Node: "Export Doc as Text"

| Parameter | Value to Set |
|-----------|-------------|
| `credentials.googleDriveOAuth2Api.id` | Same Google Drive credential ID |

### Node: "Find Missing Docs" (Code)

The name pattern is hardcoded: `Daily Standup — YYYY-MM-DD`. Change the
template literal in the Code node if your meeting has a different name.

### Node: "Format for ClickUp" (Code)

| Variable | Current Value | Purpose |
|----------|--------------|---------|
| `parent.id` | `90173963039` | ClickUp Space ID for Daily Standup Notes |
| `parent.type` | `4` | ClickUp parent type (4 = Space) |

### Node: "Create ClickUp Doc"

| Parameter | Current Value | Notes |
|-----------|--------------|-------|
| `url` | `.../workspaces/9017833757/docs` | Workspace ID from ClickUp URL |

## ClickUp IDs

Extracted from the ClickUp URL `https://app.clickup.com/9017833757/v/f/90176857901/90173963039`:

- **Workspace ID:** `9017833757`
- **Folder ID:** `90176857901`
- **Subfolder ID:** `90173963039` (Daily Standup Notes — used as `parent.id`)

## Troubleshooting

### No new docs created when expected

1. Check the "List Drive Docs" node output — does it return files?
2. Check the "List ClickUp Docs" node output — does it return existing docs?
3. Check "Find Missing Docs" output — are there items in the missing list?
4. Verify the name pattern matches: the expected ClickUp name is
   `Daily Standup — YYYY-MM-DD` (em dash, not hyphen)

### Docs created but empty

ClickUp Docs API v3 ignores the `content` field on create — content must be added
via a separate `POST /docs/{id}/pages` call. If docs are created but empty:

1. Check "Build Page Payload" output — does `docId` have a value?
2. The create response may wrap the doc in a `data` key — the code tries `data.id`
   first, then `id`. Check the n8n execution log for the actual response shape.
3. Check "Create Page Content" output for HTTP errors (4xx/5xx).
4. Both HTTP nodes have `onError: continueRegularOutput` so failures don't stop
   the batch — look for error fields in the output items.

### `parent` field rejected by ClickUp API

The ClickUp Docs API v3 may not accept the `parent` field. If you get a 400 error:
1. Open the "Format for ClickUp" Code node
2. Remove the `parent` property from the payload object
3. The doc will be created at workspace level — move it to the correct folder manually
4. Alternatively, try `parent.type` values: `4` (Space), `5` (Folder), `6` (List)

### Google Drive API returns empty

- Verify the folder ID is correct (test by manually listing files in the folder)
- Check that the Google Drive OAuth2 credential has `drive.readonly` or `drive` scope
- The query filter requires `mimeType='application/vnd.google-apps.document'` — non-Doc
  files (e.g., .mp4 recordings) are excluded by the API query

### Export returns empty content

- Gemini notes may take a few minutes to fully generate after the meeting ends
- The Google Docs export API requires `https://www.googleapis.com/auth/drive` scope
- Check the "Export Doc as Text" node output for error responses

### Rate limiting / 429 errors

- Both Export and Create nodes are batched at 1 request per second
- ClickUp rate limit: 100 requests/minute
- If you hit limits, increase `batchInterval` in the HTTP Request node options

### ClickUp pagination

- The "List ClickUp Docs" node fetches one page of results (typically ~100 docs)
- For daily standups this is sufficient for months of history
- If you accumulate >100 docs, update the "Find Missing Docs" Code node to handle
  pagination (fetch multiple pages from ClickUp before building the name set)

## Polling Interval

Default: every 1 hour. For daily standups this is more than sufficient — meetings
happen once per day, and the reconciliation approach means nothing is missed even
if a cycle is skipped.

To change the interval, edit the "Schedule Trigger" node:
- In n8n UI: click the node → change interval
- In JSON: modify `rule.interval[0].hoursInterval` or change `field` to `"minutes"`

## CLI Debugging Tool

The standalone Python script `scripts/backfill-standup-notes.py` remains available
as a CLI debugging and one-time migration tool. It is no longer needed for ongoing
sync — the workflow handles both new and historical docs automatically.

```bash
# Dry run — list docs that would be processed
python scripts/backfill-standup-notes.py --dry-run

# Full backfill (uses local state file for idempotency)
python scripts/backfill-standup-notes.py

# See all options
python scripts/backfill-standup-notes.py --help
```

## Deployment

Deploy the workflow to n8n via API PUT:

```bash
# Get the workflow ID from n8n
curl -s -H "X-N8N-API-KEY: $N8N_API_KEY" \
  https://n8n.example.com/api/v1/workflows | \
  jq '.data[] | select(.name | contains("Standup")) | .id'

# Update the workflow
curl -X PUT \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  -d @workflows/meet-standup-to-clickup.json \
  https://n8n.example.com/api/v1/workflows/<WORKFLOW_ID>
```

Then activate it in the n8n UI or via API.
