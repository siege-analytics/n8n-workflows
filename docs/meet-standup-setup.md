# Google Meet Standup → ClickUp Docs Setup

Workflow: `workflows/meet-standup-to-clickup.json`

Watches Google Drive for Gemini-generated meeting notes from Google Meet,
exports them as text, and creates a ClickUp Doc in the Daily Standup Notes space.

## Flow

```
Watch Meet Notes Folder (Google Drive Trigger)
  → Is Google Doc? (If — skips .mp4 recordings)
  → Export Doc as Text (HTTP Request → Google Drive export API)
  → Format for ClickUp (Code — builds markdown doc payload)
  → Create ClickUp Doc (HTTP Request → ClickUp API v3)
```

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

### Node: "Watch Meet Notes Folder"

| Parameter | Value to Set |
|-----------|-------------|
| `folderToWatch.value` | Your Google Drive Meet Notes folder ID |
| `credentials.googleDriveOAuth2Api.id` | Your n8n Google Drive credential ID |

### Node: "Export Doc as Text"

| Parameter | Value to Set |
|-----------|-------------|
| `credentials.googleDriveOAuth2Api.id` | Same Google Drive credential ID |

### Node: "Format for ClickUp"

The Code node has these hardcoded values you may want to adjust:

| Variable | Current Value | Purpose |
|----------|--------------|---------|
| `parent.id` | `90173963039` | ClickUp Space ID for Daily Standup Notes |
| `parent.type` | `4` | ClickUp parent type (4 = Space) |
| Doc name prefix | `Daily Standup` | Change if your meeting has a different name |

### Node: "Create ClickUp Doc"

| Parameter | Current Value | Notes |
|-----------|--------------|-------|
| `url` | `.../workspaces/9017833757/docs` | Workspace ID from ClickUp URL |

## ClickUp IDs

Extracted from the provided URL `https://app.clickup.com/9017833757/v/s/90173963039`:

- **Workspace ID:** `9017833757`
- **Space ID:** `90173963039` (Daily Standup Notes)

## Troubleshooting

### `parent` field rejected by ClickUp API

The ClickUp Docs API v3 may not accept the `parent` field. If you get a 400 error:
1. Open the "Format for ClickUp" Code node
2. Remove the `parent` property from the payload object
3. The doc will be created at workspace level — move it to the correct space manually
4. Alternatively, try `parent.type` values: `4` (Space), `5` (Folder), `6` (List)

### Google Drive trigger not firing

- Verify the folder ID is correct (test by manually creating a file in the folder)
- Check that the Google Drive OAuth2 credential has `drive.readonly` or `drive` scope
- n8n CE polling can lag — check Settings → Executions for errors

### Export returns empty content

- Gemini notes may take a few minutes to fully generate after the meeting ends
- The Google Docs export API requires `https://www.googleapis.com/auth/drive` scope
- If the file isn't a Google Doc (e.g., recording .mp4), the If node should filter it out

### Duplicate docs in ClickUp

- If the trigger fires multiple times for the same file, duplicates will be created
- To add dedup: in the Code node, add a static data check:
  ```javascript
  const seen = $getWorkflowStaticData('global');
  if (seen[trigger.id]) return [];
  seen[trigger.id] = true;
  ```
- Note: static data may not persist across n8n CE restarts

## Polling Interval

Default: every 1 minute. For daily standups this is more frequent than needed.
To reduce API calls, change the trigger's `pollTimes` to every 5 or 15 minutes in the UI.
