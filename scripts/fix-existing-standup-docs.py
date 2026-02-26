#!/usr/bin/env python3
"""Fix existing ClickUp standup docs that have blank default pages.

ClickUp auto-creates a blank default page (page 1) when a doc is created.
The old workflow added content as page 2 via POST, leaving page 1 blank.
This script copies page 2 content to page 1 and removes page 2.

When both pages are empty (content was never written), the script can
optionally re-fetch content from Google Drive with ``--refill-from-drive``.

Also identifies duplicate docs (same date, different IDs) for manual cleanup.

Prerequisites:
    export CLICKUP_API_TOKEN=pk_xxx

    For --refill-from-drive:
    gcloud auth application-default login --no-browser \\
        --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform

Usage:
    python scripts/fix-existing-standup-docs.py --dry-run
    python scripts/fix-existing-standup-docs.py --refill-from-drive
    python scripts/fix-existing-standup-docs.py --refill-from-drive --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

WORKSPACE_ID = "9017833757"
FOLDER_ID = "90176857901"
CLICKUP_BASE = f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}"
RATE_LIMIT_DELAY = 0.6

# Google Drive settings (match backfill-standup-notes.py)
DRIVE_FOLDER_ID = "1OFRQrDFm1buSwdh2IX_YbkWAaWfge4bk"
DRIVE_NAME_FILTER = "Daily Standup and Checkin"
DRIVE_API = "https://www.googleapis.com/drive/v3"
GCP_QUOTA_PROJECT = "gold-box-488021-d9"
ADC_FILE = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"

KNOWN_DUPLICATES = {
    "2026-02-18": {"original": "8cr2e8x-1717", "duplicate": "8cr2e8x-1857"},
    "2026-02-17": {"original": "8cr2e8x-1737", "duplicate": "8cr2e8x-1877"},
    "2026-02-16": {"original": "8cr2e8x-1757", "duplicate": "8cr2e8x-1897"},
    "2026-02-13": {"original": "8cr2e8x-1777", "duplicate": "8cr2e8x-1917"},
    "2026-02-12": {"original": "8cr2e8x-1797", "duplicate": "8cr2e8x-1937"},
    "2026-02-11": {"original": "8cr2e8x-1817", "duplicate": "8cr2e8x-1957"},
    "2026-02-10": {"original": "8cr2e8x-1837", "duplicate": "8cr2e8x-1977"},
}


def get_clickup_token() -> str:
    """Retrieve ClickUp API token from env var or 1Password."""
    token = os.environ.get("CLICKUP_API_TOKEN")
    if token:
        return token

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


def api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "Content-Type": "application/json",
    }


def list_docs(token: str) -> list[dict]:
    """List all docs in the standup folder."""
    url = f"{CLICKUP_BASE}/docs"
    resp = requests.get(
        url,
        headers=api_headers(token),
        params={"parent.id": FOLDER_ID, "parent.type": "5"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    all_docs = data.get("docs", [])
    # Client-side filter (API may ignore parent filter)
    return [d for d in all_docs if d.get("parent", {}).get("id") == FOLDER_ID]


def get_doc_pages(token: str, doc_id: str) -> list[dict]:
    """Get page listing for a doc."""
    url = f"{CLICKUP_BASE}/docs/{doc_id}/page_listing"
    resp = requests.get(url, headers=api_headers(token), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("pages", [])


def get_page_content(token: str, doc_id: str, page_id: str) -> dict:
    """Get full page content."""
    url = f"{CLICKUP_BASE}/docs/{doc_id}/pages/{page_id}"
    resp = requests.get(url, headers=api_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json()


def edit_page(token: str, doc_id: str, page_id: str, name: str, content: str) -> None:
    """Edit a page's content via PUT."""
    url = f"{CLICKUP_BASE}/docs/{doc_id}/pages/{page_id}"
    payload = {
        "name": name,
        "content": content,
        "content_format": "text/md",
        "content_edit_mode": "replace",
    }
    resp = requests.put(url, headers=api_headers(token), json=payload, timeout=30)
    resp.raise_for_status()


def clear_page(token: str, doc_id: str, page_id: str) -> None:
    """Clear a page's content (ClickUp Pages API does not support DELETE)."""
    url = f"{CLICKUP_BASE}/docs/{doc_id}/pages/{page_id}"
    payload = {
        "name": "(duplicate - see page 1)",
        "content": " ",
        "content_format": "text/md",
        "content_edit_mode": "replace",
    }
    resp = requests.put(url, headers=api_headers(token), json=payload, timeout=30)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Google Drive helpers (for --refill-from-drive)
# ---------------------------------------------------------------------------

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
    return {
        "Authorization": f"Bearer {access_token}",
        "x-goog-user-project": GCP_QUOTA_PROJECT,
    }


def list_drive_docs(access_token: str) -> list[dict]:
    """List all standup Google Docs from the Drive folder."""
    query = (
        f"'{DRIVE_FOLDER_ID}' in parents"
        " and mimeType='application/vnd.google-apps.document'"
        f" and name contains '{DRIVE_NAME_FILTER}'"
        " and trashed=false"
    )
    headers = _drive_headers(access_token)
    docs: list[dict] = []
    page_token = None

    while True:
        params: dict[str, object] = {
            "q": query,
            "fields": "nextPageToken, files(id, name, createdTime)",
            "pageSize": 100,
            "orderBy": "createdTime",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(
            f"{DRIVE_API}/files", headers=headers, params=params, timeout=30,
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


def format_doc_content(file_name: str, created_time: str, text_content: str) -> tuple[str, str]:
    """Format content matching the n8n workflow output.

    Returns (doc_name, content).
    """
    dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
    iso_date = dt.strftime("%Y-%m-%d")
    display_date = dt.strftime("%A, %B %-d, %Y")
    doc_name = f"Daily Standup \u2014 {iso_date}"
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
    return doc_name, content


def build_drive_date_index(drive_docs: list[dict]) -> dict[str, dict]:
    """Build a date-keyed index of Drive docs.

    Returns {iso_date: drive_doc} for O(1) lookup.
    """
    index: dict[str, dict] = {}
    for doc in drive_docs:
        dt = datetime.fromisoformat(doc["createdTime"].replace("Z", "+00:00"))
        iso_date = dt.strftime("%Y-%m-%d")
        index[iso_date] = doc
    return index


def extract_date_from_name(name: str) -> str | None:
    """Extract ISO date from a ClickUp doc name like 'Daily Standup — 2026-02-25'."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return match.group(1) if match else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fix existing standup docs with blank default pages.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be fixed without making changes.",
    )
    p.add_argument(
        "--refill-from-drive",
        action="store_true",
        help="When both pages are empty, re-fetch content from Google Drive.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print("Retrieving ClickUp API token...")
    token = get_clickup_token()

    # --- Optionally load Google Drive index ---
    drive_index: dict[str, dict] = {}
    google_token = ""
    if args.refill_from_drive:
        print("Authenticating to Google Drive...")
        google_token = get_google_access_token()
        print("Listing Google Drive standup docs...")
        drive_docs = list_drive_docs(google_token)
        drive_index = build_drive_date_index(drive_docs)
        print(f"Found {len(drive_docs)} Google Drive doc(s) ({len(drive_index)} unique dates).")

    print(f"Listing docs in folder {FOLDER_ID}...")
    docs = list_docs(token)
    print(f"Found {len(docs)} doc(s).")

    if not docs:
        print("No docs found. Nothing to do.")
        return

    # --- Identify duplicates ---
    by_name: dict[str, list[dict]] = {}
    for doc in docs:
        by_name.setdefault(doc["name"], []).append(doc)

    duplicates = {name: entries for name, entries in by_name.items() if len(entries) > 1}
    if duplicates:
        print(f"\n{'=' * 50}")
        print(f"DUPLICATE DOCS ({len(duplicates)} names with multiple docs):")
        for name, entries in sorted(duplicates.items()):
            print(f"  {name}:")
            for entry in entries:
                doc_id = entry.get("id", "unknown")
                print(f"    - id: {doc_id}")
        print(f"{'=' * 50}")
        print("Known duplicates to delete (from folder 7080):")
        for date, ids in sorted(KNOWN_DUPLICATES.items()):
            print(f"  {date}: delete {ids['duplicate']}, keep {ids['original']}")
        print()

    # --- Fix blank default pages ---
    fixed = 0
    refilled = 0
    already_ok = 0
    errors = 0

    dup_ids = {v["duplicate"] for v in KNOWN_DUPLICATES.values()}

    for doc in docs:
        doc_id = doc.get("id", "unknown")
        doc_name = doc.get("name", "unknown")

        # Skip known duplicates
        if doc_id in dup_ids:
            print(f"  [{doc_name}] Skipping known duplicate {doc_id}")
            continue

        try:
            time.sleep(RATE_LIMIT_DELAY)
            pages = get_doc_pages(token, doc_id)

            if not pages:
                print(f"  [{doc_name}] No pages found — skipping")
                errors += 1
                continue

            page1 = pages[0]
            page1_id = page1["id"]

            # Check page 1 content
            time.sleep(RATE_LIMIT_DELAY)
            page1_detail = get_page_content(token, doc_id, page1_id)
            page1_content = page1_detail.get("content", "")

            if page1_content and page1_content.strip():
                already_ok += 1
                continue

            # Page 1 is blank — check page 2 if it exists
            if len(pages) >= 2:
                page2 = pages[1]
                page2_id = page2["id"]
                time.sleep(RATE_LIMIT_DELAY)
                page2_detail = get_page_content(token, doc_id, page2_id)
                page2_content = page2_detail.get("content", "")
                page2_name = page2_detail.get("name", doc_name)

                if page2_content and page2_content.strip():
                    # Page 2 has content → copy to page 1
                    print(f"  [{doc_name}] Page 1 blank, page 2 has content ({len(page2_content)} chars)")
                    if args.dry_run:
                        print(f"    DRY RUN: Would copy page 2 → page 1, then clear page 2")
                        fixed += 1
                        continue

                    time.sleep(RATE_LIMIT_DELAY)
                    edit_page(token, doc_id, page1_id, page2_name, page2_content)
                    print(f"    Copied content to page 1 ({page1_id})")

                    time.sleep(RATE_LIMIT_DELAY)
                    clear_page(token, doc_id, page2_id)
                    print(f"    Cleared page 2 ({page2_id})")
                    fixed += 1
                    continue

            # Both pages empty (or only 1 page) — try refill from Drive
            if not args.refill_from_drive:
                print(f"  [{doc_name}] All pages empty — use --refill-from-drive to populate")
                already_ok += 1
                continue

            doc_date = extract_date_from_name(doc_name)
            if not doc_date or doc_date not in drive_index:
                print(f"  [{doc_name}] No matching Google Drive doc found for date {doc_date}")
                errors += 1
                continue

            drive_doc = drive_index[doc_date]
            print(f"  [{doc_name}] Refilling from Google Drive: {drive_doc['name']}")

            if args.dry_run:
                print(f"    DRY RUN: Would fetch and write content from Drive")
                refilled += 1
                continue

            text = export_doc_as_text(google_token, drive_doc["id"])
            _, content = format_doc_content(
                drive_doc["name"], drive_doc["createdTime"], text,
            )

            time.sleep(RATE_LIMIT_DELAY)
            edit_page(token, doc_id, page1_id, doc_name, content)
            print(f"    Wrote {len(content)} chars to page 1 ({page1_id})")
            refilled += 1

        except requests.HTTPError as e:
            print(f"  [{doc_name}] ERROR: {e}", file=sys.stderr)
            if e.response is not None:
                print(f"    Response: {e.response.text[:300]}", file=sys.stderr)
            errors += 1

        except Exception as e:
            print(f"  [{doc_name}] ERROR: {e}", file=sys.stderr)
            errors += 1

    # --- Summary ---
    print(f"\n{'=' * 40}")
    action = "Would fix" if args.dry_run else "Fixed"
    refill_action = "Would refill" if args.dry_run else "Refilled"
    print(f"{action}:       {fixed}")
    print(f"{refill_action}:    {refilled}")
    print(f"Already OK:   {already_ok}")
    print(f"Errors:       {errors}")
    if duplicates:
        print(f"Duplicates:   {len(duplicates)} name(s) — delete manually in ClickUp UI")


if __name__ == "__main__":
    main()
