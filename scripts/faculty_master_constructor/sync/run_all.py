"""
faculty_sync.run_all
====================

Orchestrator that runs a full sync pass over both sources:

  1. directorycc.vit.ac.in  (per-ID scan + increment + snapshot)
  2. chennai.vit.ac.in      (school discovery -> per-school faculty -> increment + snapshot -> aggregate)

Usage:
  python scripts/faculty_sync/run_all.py                 # full sync
  python scripts/faculty_sync/run_all.py --dry-run       # no writes
  python scripts/faculty_sync/run_all.py --no-images     # skip chennai media URL resolution
  python scripts/faculty_sync/run_all.py --source directorycc   # only directorycc
  python scripts/faculty_sync/run_all.py --source chennai       # only chennai
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_directorycc import run_directorycc  # noqa: E402
from sync_chennai import run_chennai  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run faculty sync for directorycc and/or chennai VIT")
    parser.add_argument("--source", choices=["directorycc", "chennai", "all"], default="all",
                        help="Which source to sync (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Compute changes but do not write state")
    parser.add_argument("--no-images", action="store_true", help="Skip chennai media image URL resolution")
    args = parser.parse_args()

    results = {}

    if args.source in ("directorycc", "all"):
        print("=" * 70)
        print("SYNC: directorycc.vit.ac.in")
        print("=" * 70)
        r = run_directorycc(dry_run=args.dry_run)
        results["directorycc"] = r
        print(json.dumps({
            "changed": r["changed"],
            "count_before": r["count_before"],
            "count_after": r["count_after"],
            "added": len(r["added"]),
            "modified": len(r["modified"]),
            "removed": len(r["removed"]),
            "requests": r["requests"],
            "version": r["version"],
        }, indent=2))

    if args.source in ("chennai", "all"):
        print("\n" + "=" * 70)
        print("SYNC: chennai.vit.ac.in")
        print("=" * 70)
        r = run_chennai(dry_run=args.dry_run, resolve_images=not args.no_images)
        results["chennai"] = r
        print(json.dumps({
            "school_types": r["school_types"],
            "aggregate_count": r["aggregate_count"],
            "total_requests": r["total_requests"],
            "per_school": r["per_school"],
        }, indent=2))

    print("\n" + "=" * 70)
    print("SYNC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
