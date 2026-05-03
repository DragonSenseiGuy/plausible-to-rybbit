#!/usr/bin/env python3
"""
plausible_to_rybbit.py
======================
Converts a Plausible Analytics CSV export into a format
that can be imported into Rybbit via the Umami importer.

Pipeline: Plausible export ZIP → Umami (local Docker) → rybbit_import.csv → Rybbit

Usage
-----
  # Full pipeline (convert Plausible ZIP + pull from local Umami DB):
  python3 plausible_to_rybbit.py --plausible export.zip

  # Skip the Plausible→Umami step if you already ran plausible2umami.py
  # and have session.csv / website_event.csv from a Postgres dump:
  python3 plausible_to_rybbit.py --session session.csv --events website_event.csv

  # Specify a custom output file:
  python3 plausible_to_rybbit.py --session session.csv --events website_event.csv --output out.csv

Requirements
------------
  pip install uv  (only needed for the Plausible→Umami step)
  Docker running with Umami at container name "umami-db-1" (only for --plausible mode)

Steps this script handles
--------------------------
  1. (Optional) Unzip Plausible export, run plausible2umami.py to generate SQL,
     inject SQL into local Umami Postgres, then dump session + website_event tables.
  2. Join session metadata onto each event row.
  3. Reformat timestamps to the exact format Rybbit expects.
  4. Write a single rybbit_import.csv ready to upload to Rybbit → Site Settings → Import (Umami).
"""

import argparse
import csv
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: str, check=True) -> subprocess.CompletedProcess:
    """Run a shell command, printing it first."""
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=False)
    if check and result.returncode != 0:
        print(f"\n[ERROR] Command failed with exit code {result.returncode}")
        sys.exit(1)
    return result


def fix_timestamp(ts: str) -> str:
    """
    Rybbit expects: YYYY-MM-DD HH:MM:SS  (no timezone suffix)
    Postgres dumps:  YYYY-MM-DD HH:MM:SS+00
    """
    return ts.split("+")[0].strip()


# ---------------------------------------------------------------------------
# Step 1 – Plausible ZIP → Umami SQL → Postgres → CSV dumps
# ---------------------------------------------------------------------------

def plausible_to_umami_csvs(zip_path: str, container: str, tmp_dir: str):
    """
    Unzip Plausible export, convert to Umami SQL via plausible2umami,
    inject into Postgres, then dump session + website_event as CSVs.
    Returns (session_csv_path, events_csv_path).
    """
    print("\n[Step 1] Extracting Plausible export ZIP...")
    extract_dir = os.path.join(tmp_dir, "plausible_export")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)
    print(f"  Extracted to {extract_dir}")

    print("\n[Step 1] Cloning plausible-to-umami converter...")
    converter_dir = os.path.join(tmp_dir, "plausible-to-umami")
    run(f"git clone --depth=1 https://github.com/JeongJuhyeon/plausible-to-umami.git {converter_dir}")

    print("\n[Step 1] Getting Umami website UUID from Postgres...")
    result = subprocess.run(
        f'docker exec {container} psql -U umami -d umami -t -c "SELECT website_id FROM website LIMIT 1;"',
        shell=True, capture_output=True, text=True
    )
    uuid = result.stdout.strip()
    if not uuid:
        print("[ERROR] Could not find a website UUID in Umami. Have you created a site in Umami?")
        sys.exit(1)
    print(f"  Found website UUID: {uuid}")

    sql_path = os.path.join(tmp_dir, "plausible_migration.sql")
    print("\n[Step 1] Converting Plausible CSVs to Umami SQL...")
    run(f"cd {converter_dir} && uv run python plausible2umami.py {extract_dir} {uuid} --output {sql_path}")

    print("\n[Step 1] Importing SQL into Umami Postgres...")
    run(f"docker exec -i {container} psql -U umami -d umami < {sql_path}")

    print("\n[Step 1] Dumping session and website_event tables from Postgres...")
    session_csv = os.path.join(tmp_dir, "session.csv")
    events_csv = os.path.join(tmp_dir, "website_event.csv")

    run(f"docker exec {container} psql -U umami -d umami -c \"\\COPY session TO '/tmp/session.csv' CSV HEADER\"")
    run(f"docker exec {container} psql -U umami -d umami -c \"\\COPY website_event TO '/tmp/website_event.csv' CSV HEADER\"")
    run(f"docker cp {container}:/tmp/session.csv {session_csv}")
    run(f"docker cp {container}:/tmp/website_event.csv {events_csv}")

    return session_csv, events_csv


# ---------------------------------------------------------------------------
# Step 2 – Join + reformat → rybbit_import.csv
# ---------------------------------------------------------------------------

# Exact columns Rybbit's Umami importer expects (from rybbit source code)
RYBBIT_FIELDS = [
    "session_id", "hostname", "browser", "os", "device", "screen",
    "language", "country", "region", "city",
    "url_path", "url_query", "referrer_path", "referrer_domain",
    "page_title", "event_type", "event_name", "distinct_id", "created_at",
]


def convert(session_csv: str, events_csv: str, output_csv: str):
    """Join session data onto events and write the Rybbit-compatible CSV."""

    print("\n[Step 2] Loading session data...")
    with open(session_csv, newline="", encoding="utf-8") as f:
        session_lookup = {r["session_id"]: r for r in csv.DictReader(f)}
    print(f"  {len(session_lookup)} sessions loaded")

    print("\n[Step 2] Converting events...")
    written = 0
    skipped = 0

    with open(events_csv, newline="", encoding="utf-8") as fin, \
         open(output_csv, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=RYBBIT_FIELDS)
        writer.writeheader()

        for row in reader:
            sess = session_lookup.get(row["session_id"], {})

            # Skip rows with no session match (shouldn't happen but be safe)
            if not sess:
                skipped += 1
                continue

            # Rybbit only imports pageviews (event_type=1) and custom events (event_type=2)
            event_type = row.get("event_type", "1")
            if event_type not in ("1", "2"):
                skipped += 1
                continue

            writer.writerow({
                "session_id":      row["session_id"],
                "hostname":        row["hostname"],
                "browser":         sess.get("browser", ""),
                "os":              sess.get("os", ""),
                "device":          sess.get("device", ""),
                "screen":          sess.get("screen", ""),
                "language":        sess.get("language", ""),
                "country":         sess.get("country", ""),
                "region":          sess.get("region", ""),
                "city":            sess.get("city", ""),
                "url_path":        row["url_path"],
                "url_query":       row["url_query"],
                "referrer_path":   row["referrer_path"],
                "referrer_domain": row["referrer_domain"],
                "page_title":      row["page_title"],
                "event_type":      event_type,
                "event_name":      row.get("event_name", ""),
                "distinct_id":     "",
                "created_at":      fix_timestamp(row["created_at"]),
            })
            written += 1

    print(f"  {written} events written, {skipped} skipped")
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert Plausible Analytics export to Rybbit import CSV (via Umami format)."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--plausible", metavar="ZIP",
        help="Path to Plausible export ZIP. Requires Docker + local Umami running."
    )
    source.add_argument(
        "--session", metavar="CSV",
        help="Path to session.csv (already dumped from Umami Postgres)."
    )
    parser.add_argument(
        "--events", metavar="CSV",
        help="Path to website_event.csv (required with --session)."
    )
    parser.add_argument(
        "--output", metavar="CSV", default="rybbit_import.csv",
        help="Output CSV filename (default: rybbit_import.csv)."
    )
    parser.add_argument(
        "--container", default="umami-db-1",
        help="Docker container name for Umami Postgres (default: umami-db-1)."
    )
    args = parser.parse_args()

    # Validate args
    if args.session and not args.events:
        parser.error("--events is required when using --session")

    print("=" * 60)
    print("  Plausible → Rybbit Migration Tool")
    print("=" * 60)

    if args.plausible:
        # Full pipeline
        if not os.path.exists(args.plausible):
            print(f"[ERROR] File not found: {args.plausible}")
            sys.exit(1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_csv, events_csv = plausible_to_umami_csvs(
                args.plausible, args.container, tmp_dir
            )
            written = convert(session_csv, events_csv, args.output)
    else:
        # Already have the CSVs
        for path, label in [(args.session, "--session"), (args.events, "--events")]:
            if not os.path.exists(path):
                print(f"[ERROR] File not found ({label}): {path}")
                sys.exit(1)
        written = convert(args.session, args.events, args.output)

    if written == 0:
        print("\n[WARNING] No events were written. Check your input files.")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  Done! Output: {args.output}")
    print(f"  {written} events ready to import.")
    print(f"{'=' * 60}")
    print("\nNext steps:")
    print("  1. Open Rybbit → Site Settings → Import tab")
    print("  2. Select platform: Umami")
    print(f"  3. Upload: {args.output}")
    print("  4. Click Import and monitor Import History\n")


if __name__ == "__main__":
    main()
