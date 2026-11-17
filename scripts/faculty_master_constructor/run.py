#!/usr/bin/env python3
"""
faculty_master_constructor/run.py
=================================

Master entry point for the faculty master constructor pipeline.

Combines:
  - faculty_sync (directorycc.vit.ac.in + chennai.vit.ac.in) with
    incremental snapshots + point-in-time restore
  - VITFAC review catalog from cache.amazecc.com/faculty
  - Master faculty registry with active/inactive tracking + date stamps
  - Builds faculty_combined_v1.json and faculty_all_data_v1.json
  - Optional upload to Cloudflare KV (cache.amazecc.com)

Usage:
  python run.py                              # full sync + build
  python run.py --no-sync                    # skip sync, build from existing state
  python run.py --dry-run-sync               # dry-run sync (no writes)
  python run.py --upload-to-kv               # upload built JSONs to Cloudflare KV
  python run.py --upload-profiles            # also upload individual profile batches
  python run.py restore <source> <version> --out file.json   # point-in-time restore
  python run.py list <source>                # list snapshot versions
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "build"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "sync"))

from build_all import run_pipeline  # noqa: E402
from restore_cli import main as restore_main  # noqa: E402

SYNC_DIR = Path(__file__).resolve().parent / "sync"


def run_sync(dry_run: bool = False) -> None:
    cmd = [sys.executable, str(SYNC_DIR / "run_all.py"), "--source", "all"]
    if dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, cwd=str(SYNC_DIR.parent))
    if result.returncode != 0:
        raise RuntimeError(f"faculty_sync failed with exit code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Faculty Master Constructor")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run full sync + build pipeline")
    p_run.add_argument("--no-sync", action="store_true", help="Skip sync, build from existing state")
    p_run.add_argument("--dry-run-sync", action="store_true", help="Dry-run sync only (no writes)")
    p_run.add_argument("--upload-to-kv", action="store_true", help="Upload built JSONs to Cloudflare KV")
    p_run.add_argument("--upload-profiles", action="store_true", help="Also upload individual profile batches")
    p_run.add_argument("--profiles-dir", default=None, help="Directory containing profile batch JSON files")

    sub.add_parser("sync", help="Run faculty_sync only (directorycc + chennai)")
    sub.add_parser("build", help="Build combined + all-data + master registry from existing state")
    sub.add_parser("dry-run-sync", help="Dry-run faculty_sync (no writes)")

    p_restore = sub.add_parser("restore", help="Restore faculty state to a point in time")
    p_restore.add_argument("source", nargs="?", help="Source (directorycc, chennai, chennai:<school>)")
    p_restore.add_argument("version", nargs="?", help="Version timestamp (e.g. 20261116_122348)")
    p_restore.add_argument("--out", help="Output JSON file path")

    p_list = sub.add_parser("list", help="List available snapshot versions")
    p_list.add_argument("source", nargs="?", default="directorycc", help="Source to list")

    args = parser.parse_args()

    if args.command in ("run", None):
        run_pipeline(
            sync=(not args.no_sync),
            dry_run_sync=args.dry_run_sync,
            upload_to_kv_flag=args.upload_to_kv,
            upload_profiles=args.upload_profiles,
            profiles_dir=args.profiles_dir,
        )
    elif args.command == "sync":
        run_sync(dry_run=False)
    elif args.command == "build":
        run_pipeline(sync=False, dry_run_sync=False)
    elif args.command == "dry-run-sync":
        run_sync(dry_run=True)
    elif args.command == "restore":
        if not args.source or not args.version:
            print("usage: run.py restore <source> <version> --out <file>")
            sys.exit(1)
        old = sys.argv
        sys.argv = ["restore_cli.py", "restore", args.source, args.version]
        if args.out:
            sys.argv += ["--out", args.out]
        try:
            restore_main()
        finally:
            sys.argv = old
    elif args.command == "list":
        old = sys.argv
        sys.argv = ["restore_cli.py", "list", args.source]
        try:
            restore_main()
        finally:
            sys.argv = old
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
