#!/usr/bin/env python3
"""Backfill existing Google Meet standup notes to ClickUp Docs.

One-time script that reads all "Daily Standup and Checkin" Google Docs
from a Google Drive folder and creates corresponding ClickUp Docs.

Prerequisites:
    gcloud auth application-default login --no-browser \
        --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform
    export CLICKUP_API_TOKEN=pk_xxx

Usage:
    python scripts/backfill-standup-notes.py --dry-run
    python scripts/backfill-standup-notes.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

# ---------------------------------------------------------------------------
# Defaults (match n8n workflow: meet-standup-to-clickup.json)
# ---------------------------------------------------------------------------
DEFAULT_FOLDER_ID = "1OFRQrDFm1buSwdh2IX_YbkWAaWfge4bk"
DEFAULT_WORKSPACE_ID = "9017833757"
DEFAULT_PARENT_ID = "90173963039"  # subfolder inside 90176857901
DEFAULT_PARENT_TYPE = 4            # Space
DEFAULT_NAME_FILTER = "Daily Standup and Checkin"

DRIVE_API = "https://www.googleapis.com/drive/v3"
CLICKUP_DOCS_URL = "https://api.clickup.com/api/v3/workspaces/{workspace}/docs"
STATE_FILE = Path.home() / ".cache" / "standup-backfill-state.json"
ADC_FILE = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# ClickUp rate limit: 100 req/min → 0.6s between requests
RATE_LIMIT_DELAY = 0.6


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

GCP_QUOTA_PROJECT = "gold-box-488021-d9"


def get_google_access_token() -> str:
    """Get a Google OAuth2 access token from gcloud ADC credentials."""
    if not ADC_FILE.exists():
        print(
            f"ERROR: No credentials found at {ADC_FILE}\n"
            "Run: gcloud auth application-default login --no-browser "
            "--scopes=https://www.googleapis.com/auth/drive.readonly,"
            "https://www.googleapis.com/auth/cloud-platform",
            file=sys.stderr,
        )
        sys.exit(1)

    adc = json.loads(ADC_FILE.read_text())
    resp = requests.post(TOKEN_URL, data={
        "client_id": adc["client_id"],
        "client_secret": adc["client_secret"],
        "refresh_token": adc["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _drive_headers(access_token: str) -> dict[str, str]:
    """Headers for Google Drive API calls, including quota project."""
    return {
        "Authorization": f"Bearer {access_token}",
        "x-goog-user-project": GCP_QUOTA_PROJECT,
    }


def get_clickup_token() -> str:
    """Retrieve ClickUp API token from env var or 1Password."""
    token = os.environ.get("CLICKUP_API_TOKEN")
    if token:
        return token

    # Try 1Password CLI
    try:
        result = subprocess.run(
            [
                "op", "read",
                "op://Private/ClickUp API Token/credential",
                "--no-newline",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    print(
        "ERROR: No ClickUp API token found.\n"
        "Set CLICKUP_API_TOKEN env var.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# State management (idempotency)
# ---------------------------------------------------------------------------

def load_state() -> set[str]:
    """Load set of already-processed Google Doc IDs."""
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("processed_ids", []))
    return set()


def save_state(processed_ids: set[str]) -> None:
    """Persist processed doc IDs to state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(
        {"processed_ids": sorted(processed_ids)},
        indent=2,
    ))


# ---------------------------------------------------------------------------
# Google Drive operations (raw HTTP, no googleapiclient)
# ---------------------------------------------------------------------------

def list_standup_docs(access_token: str, folder_id: str, name_filter: str) -> list[dict]:
    """List all Google Docs in folder matching the name filter."""
    query = (
        f"'{folder_id}' in parents"
        f" and mimeType='application/vnd.google-apps.document'"
        f" and name contains '{name_filter}'"
        f" and trashed=false"
    )
    headers = _drive_headers(access_token)
    docs = []
    page_token = None

    while True:
        params = {
            "q": query,
            "fields": "nextPageToken, files(id, name, createdTime, modifiedTime)",
            "pageSize": 100,
            "orderBy": "createdTime",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(
            f"{DRIVE_API}/files",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        docs.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return docs


def export_doc_as_text(access_token: str, doc_id: str) -> str:
    """Export a Google Doc as plain text."""
    resp = requests.get(
        f"{DRIVE_API}/files/{doc_id}/export",
        headers=_drive_headers(access_token),
        params={"mimeType": "text/plain"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# ClickUp operations
# ---------------------------------------------------------------------------

def create_clickup_doc(
    token: str,
    workspace_id: str,
    parent_id: str,
    parent_type: int,
    name: str,
    description: str,
    content: str,
) -> dict:
    """Create a ClickUp Doc via API v3."""
    url = CLICKUP_DOCS_URL.format(workspace=workspace_id)
    payload = {
        "name": name,
        "description": description,
        "content": content,
        "parent": {
            "id": parent_id,
            "type": parent_type,
        },
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Formatting (matches n8n workflow "Format for ClickUp" node)
# ---------------------------------------------------------------------------

def format_doc(file_name: str, created_time: str, text_content: str) -> tuple[str, str, str]:
    """Format a doc matching the n8n workflow output.

    Returns (doc_name, description, content).
    """
    dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
    iso_date = dt.strftime("%Y-%m-%d")
    display_date = dt.strftime("%A, %B %-d, %Y")

    doc_name = f"Daily Standup and Checkin \u2014 {iso_date}"
    description = f"Standup notes from Google Meet ({iso_date})"
    content = "\n".join([
        f"# {doc_name}",
        "",
        f"**Source:** {file_name}",
        f"**Date:** {display_date}",
        "",
        "---",
        "",
        text_content,
    ])

    return doc_name, description, content


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill Google Meet standup notes to ClickUp Docs.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List docs that would be processed without creating ClickUp Docs.",
    )
    p.add_argument(
        "--folder-id",
        default=DEFAULT_FOLDER_ID,
        help=f"Google Drive folder ID (default: {DEFAULT_FOLDER_ID})",
    )
    p.add_argument(
        "--workspace-id",
        default=DEFAULT_WORKSPACE_ID,
        help=f"ClickUp workspace ID (default: {DEFAULT_WORKSPACE_ID})",
    )
    p.add_argument(
        "--clickup-parent-id",
        default=DEFAULT_PARENT_ID,
        help=f"ClickUp parent (folder/space) ID (default: {DEFAULT_PARENT_ID})",
    )
    p.add_argument(
        "--clickup-parent-type",
        type=int,
        default=DEFAULT_PARENT_TYPE,
        help=f"ClickUp parent type: 4=Space, 5=Folder, 6=List (default: {DEFAULT_PARENT_TYPE})",
    )
    p.add_argument(
        "--filter",
        default=DEFAULT_NAME_FILTER,
        help=f"Name filter for Google Docs (default: '{DEFAULT_NAME_FILTER}')",
    )
    p.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear the processed-IDs state file before running.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.reset_state and STATE_FILE.exists():
        STATE_FILE.unlink()
        print(f"Cleared state file: {STATE_FILE}")

    # --- Auth ---
    print("Authenticating to Google Drive...")
    access_token = get_google_access_token()

    if not args.dry_run:
        print("Retrieving ClickUp API token...")
        clickup_token = get_clickup_token()

    # --- List docs ---
    print(f"Listing docs in folder {args.folder_id} matching '{args.filter}'...")
    docs = list_standup_docs(access_token, args.folder_id, args.filter)
    print(f"Found {len(docs)} matching doc(s) in Google Drive.")

    if not docs:
        print("Nothing to do.")
        return

    # --- Load state ---
    processed = load_state()
    to_process = [d for d in docs if d["id"] not in processed]
    skipped = len(docs) - len(to_process)

    if skipped:
        print(f"Skipping {skipped} already-processed doc(s).")

    if not to_process:
        print("All docs already processed. Use --reset-state to re-process.")
        return

    print(f"{len(to_process)} doc(s) to process.")

    if args.dry_run:
        print("\n--- DRY RUN ---")
        for doc in to_process:
            dt = datetime.fromisoformat(doc["createdTime"].replace("Z", "+00:00"))
            print(f"  [{dt.strftime('%Y-%m-%d')}] {doc['name']}  (id: {doc['id']})")
        print(f"\nTotal: {len(to_process)} doc(s) would be created in ClickUp.")
        return

    # --- Process ---
    created = 0
    errors = 0

    for i, doc in enumerate(to_process, 1):
        print(f"\n[{i}/{len(to_process)}] {doc['name']}")

        try:
            # Export from Google Drive
            text = export_doc_as_text(access_token, doc["id"])
            doc_name, description, content = format_doc(
                doc["name"], doc["createdTime"], text
            )

            # Create in ClickUp
            result = create_clickup_doc(
                clickup_token,
                args.workspace_id,
                args.clickup_parent_id,
                args.clickup_parent_type,
                doc_name,
                description,
                content,
            )

            doc_id = result.get("id", "unknown")
            print(f"  Created ClickUp Doc: {doc_name} (id: {doc_id})")

            # Record success
            processed.add(doc["id"])
            save_state(processed)
            created += 1

        except requests.HTTPError as e:
            print(f"  ERROR (ClickUp API): {e}", file=sys.stderr)
            if e.response is not None:
                print(f"  Response: {e.response.text}", file=sys.stderr)
            errors += 1

        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            errors += 1

        # Rate limit (skip delay after last item)
        if i < len(to_process):
            time.sleep(RATE_LIMIT_DELAY)

    # --- Summary ---
    print(f"\n{'=' * 40}")
    print(f"Backfill complete.")
    print(f"  Created:  {created}")
    print(f"  Skipped:  {skipped}")
    print(f"  Errors:   {errors}")
    print(f"  State:    {STATE_FILE}")


if __name__ == "__main__":
    main()
