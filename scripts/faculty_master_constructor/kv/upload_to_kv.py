"""
kv/upload_to_kv.py
==================

Uploads the built faculty catalog JSONs to Cloudflare KV (namespace `CACHE`
used by amazecc-cache / cache.amazecc.com).

Uploads:
  - faculty_list_v1          <- state/vitfac_catalog_v1.json
  - faculty_combined_v1      <- state/faculty_combined_v1.json
  - faculty_all_data_v1      <- state/faculty_all_data_v1.json
  - master_registry_v1       <- state/master_registry.json (active/inactive tracking)
  - incremental_diff_v1      <- state/incremental_diff.json (change log)

Also copies every payload to artifacts/ for manual download/upload if the
wrangler push fails.

Required env (set as GitHub Actions secrets):
  CLOUDFLARE_API_TOKEN   - Cloudflare API token with Workers KV write access
  CLOUDFLARE_ACCOUNT_ID  - Your Cloudflare account ID

KV namespace ID (hardcoded from wrangler.toml of amazecc-cache):
  3cf003b8421b4de0a8bc54776f1df337
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "artifacts"

KV_NAMESPACE_ID = "3cf003b8421b4de0a8bc54776f1df337"

# (kv_key, state_file_path, description)
UPLOADS: List[tuple[str, Path, str]] = [
    ("faculty_list_v1", STATE_DIR / "vitfac_catalog_v1.json", "VITFAC review catalog"),
    ("faculty_combined_v1", STATE_DIR / "faculty_combined_v1.json", "Combined compact catalog"),
    ("faculty_all_data_v1", STATE_DIR / "faculty_all_data_v1.json", "Complete all-data catalog"),
    ("master_registry_v1", STATE_DIR / "master_registry.json", "Master faculty registry (active/inactive)"),
    ("incremental_diff_v1", STATE_DIR / "incremental_diff.json", "Incremental change log"),
]


def ensure_wrangler_auth() -> None:
    """Check that wrangler is authenticated and the account is set."""
    result = subprocess.run(
        ["npx", "wrangler", "whoami"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("[kv] ERROR: wrangler not authenticated. Run `npx wrangler login` first.")
        print(result.stderr)
        sys.exit(1)
    print("[kv] wrangler authenticated")


def copy_to_artifacts(src: Path) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARTIFACTS_DIR / src.name
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[kv] artifact copied: {dst}")


def kv_key_put(key: str, path: Path) -> bool:
    """Upload a single key to KV via wrangler. Returns True on success."""
    copy_to_artifacts(path)
    cmd = [
        "npx", "wrangler", "kv", "key", "put", key,
        "--namespace-id", KV_NAMESPACE_ID,
        "--path", str(path),
        "--remote",
    ]
    print(f"[kv] uploading {key} -> namespace {KV_NAMESPACE_ID}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[kv] FAILED to upload {key}:")
        print(result.stderr)
        return False
    print(f"[kv] uploaded {key} OK")
    return True


def kv_bulk_put_profile_batches(batch_dir: Path) -> bool:
    """Optional: upload individual directory_profile_<empId>_v1 batches."""
    if not batch_dir.exists():
        print(f"[kv] no profile batch directory at {batch_dir}, skipping profile uploads")
        return True
    batch_files = sorted(batch_dir.glob("batch-*.json"))
    if not batch_files:
        print("[kv] no batch files found, skipping profile uploads")
        return True
    ok = True
    for bf in batch_files:
        copy_to_artifacts(bf)
        # Each batch file is a JSON array of {key, value, base64, metadata}
        # wrangler kv bulk put expects the file as-is
        cmd = [
            "npx", "wrangler", "kv", "bulk", "put", str(bf),
            "--namespace-id", KV_NAMESPACE_ID,
            "--remote",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[kv] FAILED bulk put {bf.name}: {result.stderr}")
            ok = False
        else:
            print(f"[kv] bulk put {bf.name} OK")
    return ok


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Upload faculty catalogs to Cloudflare KV")
    parser.add_argument("--upload-profiles", action="store_true",
                        help="Also upload individual directory_profile_<id>_v1 batches")
    parser.add_argument("--profiles-dir", default=str(Path(__file__).resolve().parents[1] / "state" / "profile_batches"),
                        help="Directory containing profile batch JSON files")
    parser.add_argument("--artifacts-only", action="store_true",
                        help="Only copy JSONs to artifacts/, do not upload to KV")
    args = parser.parse_args()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy all state JSONs to artifacts regardless
    for key, path, desc in UPLOADS:
        if path.exists():
            copy_to_artifacts(path)
            print(f"[kv] staged {desc} ({path.name}) -> {path.stat().st_size / 1024:.1f} KB")
        else:
            print(f"[kv] WARNING: {desc} not found at {path}, skipping")

    if args.artifacts_only:
        print("\n[kv] artifacts-only mode: no KV upload performed.")
        print(f"[kv] artifacts ready at: {ARTIFACTS_DIR}")
        print("[kv] Download them and upload manually with:")
        for key, path, desc in UPLOADS:
            if path.exists():
                print(f"  npx wrangler kv key put {key} --path artifacts/{path.name} --namespace-id {KV_NAMESPACE_ID} --remote")
        return

    ensure_wrangler_auth()

    print("\n[kv] starting uploads to Cloudflare KV ...")
    all_ok = True
    for key, path, desc in UPLOADS:
        if not path.exists():
            print(f"[kv] SKIP {key}: {path} not found")
            all_ok = False
            continue
        if not kv_key_put(key, path):
            all_ok = False

    # Optional profile batches
    if args.upload_profiles:
        profile_dir = Path(args.profiles_dir)
        if not kv_bulk_put_profile_batches(profile_dir):
            all_ok = False

    if all_ok:
        print("\n[kv] all uploads completed successfully.")
    else:
        print("\n[kv] SOME UPLOADS FAILED. Artifacts are available in artifacts/ for manual upload.")
        sys.exit(1)


if __name__ == "__main__":
    main()
