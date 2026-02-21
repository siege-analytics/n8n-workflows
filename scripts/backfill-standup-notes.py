#!/usr/bin/env python3
"""Backfill existing Google Meet standup notes to ClickUp Docs.

One-time script that reads all "Daily Standup and Checkin" Google Docs
from a Google Drive folder and creates corresponding ClickUp Docs.

Prerequisites:
    gcloud auth application-default login \
        --scopes=https://www.googleapis.com/auth/drive.readonly
    export CLICKUP_API_TOKEN=pk_xxx  # or sign into 1Password

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

import requests
import google.auth
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Defaults (match n8n workflow: meet-standup-to-clickup.json)
# ---------------------------------------------------------------------------
DEFAULT_FOLDER_ID = "1OFRQrDFm1buSwdh2IX_YbkWAaWfge4bk"
DEFAULT_WORKSPACE_ID = "9017833757"
DEFAULT_PARENT_ID = "90173963039"  # subfolder inside 90176857901
DEFAULT_PARENT_TYPE = 5            # Folder
DEFAULT_NAME_FILTER = "Daily Standup and Checkin"

CLICKUP_DOCS_URL = "https://api.clickup.com/api/v3/workspaces/{workspace}/docs"
STATE_FILE = Path.home() / ".cache" / "standup-backfill-state.json"

# ClickUp rate limit: 100 req/min → 0.6s between requests
RATE_LIMIT_DELAY = 0.6


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_drive_service():
    """Build a Google Drive v3 service using Application Default Credentials."""
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)


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
        "Set CLICKUP_API_TOKEN env var or sign into 1Password (eval $(op signin)).",
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
# Google Drive operations
# ---------------------------------------------------------------------------

def list_standup_docs(service, folder_id: str, name_filter: str) -> list[dict]:
    """List all Google Docs in folder matching the name filter."""
    query = (
        f"'{folder_id}' in parents"
        f" and mimeType='application/vnd.google-apps.document'"
        f" and name contains '{name_filter}'"
        f" and trashed=false"
    )
    docs = []
    page_token = None

    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, createdTime, modifiedTime)",
            pageSize=100,
            pageToken=page_token,
            orderBy="createdTime",
        ).execute()

        docs.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return docs


def export_doc_as_text(service, doc_id: str) -> str:
    """Export a Google Doc as plain text."""
    return service.files().export(
        fileId=doc_id,
        mimeType="text/plain",
    ).execute().decode("utf-8")


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
    # Parse the creation date from Drive metadata
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
    drive = get_drive_service()

    if not args.dry_run:
        print("Retrieving ClickUp API token...")
        clickup_token = get_clickup_token()

    # --- List docs ---
    print(f"Listing docs in folder {args.folder_id} matching '{args.filter}'...")
    docs = list_standup_docs(drive, args.folder_id, args.filter)
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
            text = export_doc_as_text(drive, doc["id"])
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
