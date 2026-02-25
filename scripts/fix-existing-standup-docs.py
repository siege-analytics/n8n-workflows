#!/usr/bin/env python3
"""Fix existing ClickUp standup docs that have blank default pages.

ClickUp auto-creates a blank default page (page 1) when a doc is created.
The old workflow added content as page 2 via POST, leaving page 1 blank.
This script copies page 2 content to page 1 and removes page 2.

Also identifies duplicate docs (same date, different IDs) for manual cleanup.

Prerequisites:
    export CLICKUP_API_TOKEN=pk_xxx

Usage:
    python scripts/fix-existing-standup-docs.py --dry-run
    python scripts/fix-existing-standup-docs.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import requests

WORKSPACE_ID = "9017833757"
FOLDER_ID = "90176857901"
CLICKUP_BASE = f"https://api.clickup.com/api/v3/workspaces/{WORKSPACE_ID}"
RATE_LIMIT_DELAY = 0.6

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
    return data.get("pages", data if isinstance(data, list) else [])


def get_page_content(token: str, doc_id: str, page_id: str) -> dict:
    """Get full page content."""
    url = f"{CLICKUP_BASE}/docs/{doc_id}/pages/{page_id}"
    resp = requests.get(url, headers=api_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json()


def edit_page(token: str, doc_id: str, page_id: str, name: str, content: str) -> dict:
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
    return resp.json()


def delete_page(token: str, doc_id: str, page_id: str) -> None:
    """Delete a page from a doc."""
    url = f"{CLICKUP_BASE}/docs/{doc_id}/pages/{page_id}"
    resp = requests.delete(url, headers=api_headers(token), timeout=30)
    resp.raise_for_status()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fix existing standup docs with blank default pages.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be fixed without making changes.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print("Retrieving ClickUp API token...")
    token = get_clickup_token()

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
    already_ok = 0
    errors = 0

    for doc in docs:
        doc_id = doc.get("id", "unknown")
        doc_name = doc.get("name", "unknown")

        # Skip known duplicates (from the 7080 folder)
        dup_ids = {v["duplicate"] for v in KNOWN_DUPLICATES.values()}
        if doc_id in dup_ids:
            print(f"  [{doc_name}] Skipping known duplicate {doc_id}")
            continue

        try:
            time.sleep(RATE_LIMIT_DELAY)
            pages = get_doc_pages(token, doc_id)

            if len(pages) < 2:
                # Only one page — either already fixed or content was never added
                already_ok += 1
                continue

            # Check if page 1 is blank and page 2 has content
            page1 = pages[0]
            page2 = pages[1]
            page1_id = page1["id"]
            page2_id = page2["id"]

            # Get page 2 content
            time.sleep(RATE_LIMIT_DELAY)
            page2_detail = get_page_content(token, doc_id, page2_id)
            page2_content = page2_detail.get("content", "")
            page2_name = page2_detail.get("name", doc_name)

            if not page2_content or not page2_content.strip():
                print(f"  [{doc_name}] Page 2 is also empty — skipping")
                already_ok += 1
                continue

            # Get page 1 content to verify it's blank
            time.sleep(RATE_LIMIT_DELAY)
            page1_detail = get_page_content(token, doc_id, page1_id)
            page1_content = page1_detail.get("content", "")

            if page1_content and page1_content.strip():
                print(f"  [{doc_name}] Page 1 already has content — skipping")
                already_ok += 1
                continue

            # Page 1 is blank, page 2 has content → fix it
            print(f"  [{doc_name}] Page 1 blank, page 2 has content ({len(page2_content)} chars)")

            if args.dry_run:
                print(f"    DRY RUN: Would copy page 2 → page 1, then delete page 2")
                fixed += 1
                continue

            # Copy page 2 content to page 1
            time.sleep(RATE_LIMIT_DELAY)
            edit_page(token, doc_id, page1_id, page2_name, page2_content)
            print(f"    Copied content to page 1 ({page1_id})")

            # Delete page 2
            time.sleep(RATE_LIMIT_DELAY)
            delete_page(token, doc_id, page2_id)
            print(f"    Deleted page 2 ({page2_id})")

            fixed += 1

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
    print(f"{action}:      {fixed}")
    print(f"Already OK:  {already_ok}")
    print(f"Errors:      {errors}")
    if duplicates:
        print(f"Duplicates:  {len(duplicates)} name(s) — review and delete manually")


if __name__ == "__main__":
    main()
