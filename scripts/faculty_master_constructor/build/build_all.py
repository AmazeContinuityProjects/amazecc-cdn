"""
build_all.py
============

Orchestrates the full master-constructor pipeline:

  1. Sync directorycc + chennai (faculty_sync via subprocess)
  2. Fetch VITFAC catalog from cache.amazecc.com/faculty
  3. Build master registry (active/inactive tracking + dates)
  4. Build faculty_combined_v1.json
  5. Build faculty_all_data_v1.json
  6. Save all JSONs to artifacts/ for download
  7. Optionally upload to Cloudflare KV

Usage (CLI):
  python build_all.py                              # full sync + build
  python build_all.py --no-sync                    # skip sync, build from existing state
  python build_all.py --dry-run-sync               # dry-run sync only
  python build_all.py --upload-to-kv               # also upload to KV after building
  python build_all.py --upload-profiles            # also upload individual profile batches

Usage (programmatic):
  from build_all import run_pipeline
  run_pipeline(sync=True, upload_to_kv=False, upload_profiles=False, profiles_dir=None)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_vitfac_catalog import main as build_vitfac  # noqa: E402
from build_master_registry import main as build_master_registry  # noqa: E402
from build_combined import main as build_combined  # noqa: E402
from build_all_data import main as build_all_data  # noqa: E402

SYNC_DIR = Path(__file__).resolve().parents[1] / "sync"


def run_sync(dry_run: bool = False) -> None:
    """Run faculty_sync via subprocess."""
    cmd = [sys.executable, str(SYNC_DIR / "run_all.py"), "--source", "all"]
    if dry_run:
        cmd.append("--dry-run")
    print(f"[sync] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(SYNC_DIR.parent))
    if result.returncode != 0:
        raise RuntimeError(f"faculty_sync failed with exit code {result.returncode}")


def upload_to_kv(upload_profiles: bool = False, profiles_dir: Optional[str] = None) -> None:
    """Upload built JSONs to Cloudflare KV via subprocess."""
    cmd = [sys.executable, str(Path(__file__).parent / "kv" / "upload_to_kv.py")]
    if upload_profiles:
        cmd.append("--upload-profiles")
        if profiles_dir:
            cmd += ["--profiles-dir", profiles_dir]
    print(f"[kv] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]))
    if result.returncode != 0:
        raise RuntimeError(f"KV upload failed with exit code {result.returncode}")


def run_pipeline(
    sync: bool = True,
    dry_run_sync: bool = False,
    upload_to_kv_flag: bool = False,
    upload_profiles: bool = False,
    profiles_dir: Optional[str] = None,
) -> None:
    """Run the full pipeline programmatically."""
    if dry_run_sync:
        print("=" * 60)
        print("DRY-RUN SYNC (faculty_sync)")
        print("=" * 60)
        run_sync(dry_run=True)
        return

    should_sync = sync
    if should_sync:
        print("=" * 60)
        print("STEP 1: faculty_sync (directorycc + chennai)")
        print("=" * 60)
        run_sync(dry_run=False)

    print("\n" + "=" * 60)
    print("STEP 2: fetch VITFAC catalog from cache.amazecc.com/faculty")
    print("=" * 60)
    build_vitfac()

    print("\n" + "=" * 60)
    print("STEP 3: build master registry (active/inactive tracking)")
    print("=" * 60)
    build_master_registry()

    print("\n" + "=" * 60)
    print("STEP 4: build faculty_combined_v1.json")
    print("=" * 60)
    build_combined()

    print("\n" + "=" * 60)
    print("STEP 5: build faculty_all_data_v1.json")
    print("=" * 60)
    build_all_data()

    # Copy all outputs to artifacts for download
    print("\n" + "=" * 60)
    print("STEP 6: staging artifacts for download")
    print("=" * 60)
    state_dir = Path(__file__).resolve().parents[1] / "state"
    artifacts_dir = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("vitfac_catalog_v1.json", "master_registry.json", "incremental_diff.json",
                  "faculty_combined_v1.json", "faculty_all_data_v1.json"):
        src = state_dir / fname
        if src.exists():
            dst = artifacts_dir / fname
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[artifacts] {fname} -> {dst} ({dst.stat().st_size / 1024:.1f} KB)")
        else:
            print(f"[artifacts] SKIP {fname}: not found")

    if upload_to_kv_flag:
        print("\n" + "=" * 60)
        print("STEP 7: upload to Cloudflare KV")
        print("=" * 60)
        upload_to_kv(upload_profiles=upload_profiles, profiles_dir=profiles_dir)

    print("\n" + "=" * 60)
    print("MASTER CONSTRUCTOR COMPLETE")
    print("=" * 60)
    print(f"Artifacts ready for download at: {artifacts_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build master faculty catalogs")
    parser.add_argument("--sync", action="store_true", help="Run faculty_sync before building")
    parser.add_argument("--no-sync", action="store_true", help="Skip sync, build from existing state")
    parser.add_argument("--dry-run-sync", action="store_true", help="Dry-run sync only (no writes)")
    parser.add_argument("--upload-to-kv", action="store_true", help="Upload built JSONs to Cloudflare KV")
    parser.add_argument("--upload-profiles", action="store_true", help="Also upload individual profile batches")
    parser.add_argument("--profiles-dir", default=None, help="Directory containing profile batch JSON files")
    args = parser.parse_args()

    if args.dry_run_sync:
        run_pipeline(sync=True, dry_run_sync=True)
        return

    run_pipeline(
        sync=(args.sync or not args.no_sync),
        dry_run_sync=False,
        upload_to_kv_flag=args.upload_to_kv,
        upload_profiles=args.upload_profiles,
        profiles_dir=args.profiles_dir,
    )


if __name__ == "__main__":
    main()
