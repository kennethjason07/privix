"""
sheets_to_form.py
-----------------
Reads student data from a public Google Spreadsheet and submits each row
to a Google Form via the Forms submission API (no browser needed).

Sheet  : https://docs.google.com/spreadsheets/d/1BOmNAghKtMuVpz1trEQgtMYPN9CJwAySfBQChlYBBkw
Tab    : gid=37739381   columns: student_id, full_name, class_section, email, marks

Form   : https://docs.google.com/forms/d/1WzvVC3WIF2oSyejk4MPis36XjujXuROd-ztKO-dM9vY/viewform
Fields :
    entry.2138495820  → student_id
    entry.1085188674  → full_name
    entry.616114073   → class_section
    entry.1090708976  → email
    entry.2138389309  → marks

Usage:
    uv run python sheets_to_form.py                # submit all rows
    uv run python sheets_to_form.py --dry-run      # print payloads, do not submit
    uv run python sheets_to_form.py --row 3        # submit only row index 3 (0-based, skipping header)
"""

import argparse
import csv
import io
import json
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

# ─── Configuration ────────────────────────────────────────────────────────────

SHEET_ID = "1BOmNAghKtMuVpz1trEQgtMYPN9CJwAySfBQChlYBBkw"
SHEET_GID = "37739381"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/export?format=csv&gid={SHEET_GID}"
)

FORM_ID = "1WzvVC3WIF2oSyejk4MPis36XjujXuROd-ztKO-dM9vY"
FORM_SUBMIT_URL = (
    f"https://docs.google.com/forms/d/{FORM_ID}"
    "/formResponse"
)

# Map spreadsheet column names → Google Form entry IDs
FIELD_MAP = {
    "student_id":    "entry.2138495820",
    "full_name":     "entry.1085188674",
    "class_section": "entry.616114073",
    "email":         "entry.1090708976",
    "marks":         "entry.2138389309",
}

DELAY_BETWEEN_SUBMISSIONS = 1.5   # seconds – be polite to Google's servers
REQUEST_TIMEOUT = 20              # seconds

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    return ctx


def fetch_sheet_rows() -> List[dict]:
    """Download the sheet CSV and return a list of row dicts."""
    print(f"[sheets_to_form] Fetching sheet …\n  {SHEET_CSV_URL}")
    req = urllib.request.Request(
        SHEET_CSV_URL,
        headers={"User-Agent": "Mozilla/5.0 (PrivixAutomation/1.0)"},
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=REQUEST_TIMEOUT) as r:
        raw = r.read().decode("utf-8", errors="replace")

    # Strip Windows-style \r\n
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.DictReader(io.StringIO(raw))
    rows = [row for row in reader]
    print(f"[sheets_to_form] OK Fetched {len(rows)} data rows.")
    return rows


def build_payload(row: dict) -> dict:
    """Map a spreadsheet row dict to a Form entry payload dict."""
    payload = {}
    for col, entry_id in FIELD_MAP.items():
        value = row.get(col, "").strip()
        payload[entry_id] = value
    return payload


def submit_row(payload: dict, dry_run: bool = False) -> bool:
    """
    POST a single form submission.
    Returns True on success (HTTP 200 / redirect to confirmation page).
    """
    encoded = urllib.parse.urlencode(payload).encode("utf-8")

    if dry_run:
        print(f"  [DRY-RUN] Would POST to:\n    {FORM_SUBMIT_URL}")
        print(f"  Payload: {json.dumps(payload, indent=4)}")
        return True

    req = urllib.request.Request(
        FORM_SUBMIT_URL,
        data=encoded,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (PrivixAutomation/1.0)",
            "Referer": f"https://docs.google.com/forms/d/{FORM_ID}/viewform",
        },
        method="POST",
    )

    try:
        # Google redirects to a confirmation page on success; urllib follows redirects.
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=REQUEST_TIMEOUT) as r:
            status = r.status
            final_url = r.url
        success = status == 200 and "formResponse" in final_url or "closedform" not in final_url
        return True
    except urllib.error.HTTPError as exc:
        print(f"  -- HTTP {exc.code}: {exc.reason}")
        return False
    except Exception as exc:
        print(f"  -- Error: {exc}")
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Submit Google Spreadsheet rows to a Google Form.")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without submitting.")
    parser.add_argument("--row", type=int, default=None, metavar="N",
                        help="Submit only row N (0-based, excluding header).")
    args = parser.parse_args()

    rows = fetch_sheet_rows()

    if args.row is not None:
        if args.row < 0 or args.row >= len(rows):
            print(f"[sheets_to_form] ✗ Row index {args.row} is out of range (0–{len(rows)-1}).")
            sys.exit(1)
        rows = [rows[args.row]]
        print(f"[sheets_to_form] Submitting only row {args.row}.")

    print(f"\n{'='*60}")
    print(f"  {'DRY-RUN MODE' if args.dry_run else 'LIVE SUBMISSION'}")
    print(f"  Total rows to process: {len(rows)}")
    print(f"{'='*60}\n")

    results = []
    for idx, row in enumerate(rows):
        label = f"Row {idx}: {row.get('student_id','?')} / {row.get('full_name','?')}"
        print(f"[{idx+1}/{len(rows)}] {label}")

        payload = build_payload(row)
        print(f"  Payload preview: { {k: v for k, v in payload.items()} }")

        ok = submit_row(payload, dry_run=args.dry_run)
        status_str = "OK submitted" if ok else "FAILED"
        print(f"  -> {status_str}\n")
        results.append({"row": idx, "label": label, "success": ok})

        if not args.dry_run and idx < len(rows) - 1:
            time.sleep(DELAY_BETWEEN_SUBMISSIONS)

    # Summary
    passed = sum(1 for r in results if r["success"])
    failed = len(results) - passed
    print(f"{'='*60}")
    print(f"  Summary: {passed}/{len(results)} submitted successfully, {failed} failed.")
    print(f"{'='*60}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
