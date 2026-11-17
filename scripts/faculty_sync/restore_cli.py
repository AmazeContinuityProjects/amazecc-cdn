"""
faculty_sync.restore_cli
========================

Point-in-time restore for faculty sync state.

Usage examples:
  # List versions for directorycc
  python scripts/faculty_sync/restore_cli.py list directorycc

  # Restore directorycc to a specific version and export JSON
  python scripts/faculty_sync/restore_cli.py restore directorycc 20261116_103000 --out restored_dircc.json

  # Restore chennai aggregate to a version
  python scripts/faculty_sync/restore_cli.py restore chennai 20261116_103000 --out restored_chennai.json

  # Restore a specific chennai school
  python scripts/faculty_sync/restore_cli.py restore chennai:sbst 20261116_103000 --out restored_sbst.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import VersionedStore  # noqa: E402

STATE_ROOT = Path(__file__).resolve().parent / "state"


def resolve_store(source: str) -> VersionedStore:
    """source can be 'directorycc', 'chennai', or 'chennai:<school_type>'."""
    if source == "directorycc":
        return VersionedStore(STATE_ROOT / "directorycc", "directorycc")
    if source == "chennai":
        return VersionedStore(STATE_ROOT / "chennai", "chennai-aggregate")
    if source.startswith("chennai:"):
        school = source.split(":", 1)[1]
        return VersionedStore(STATE_ROOT / "chennai" / "schools" / school, f"chennai:{school}")
    raise ValueError(f"unknown source: {source}")


def cmd_list(source: str) -> None:
    store = resolve_store(source)
    print(f"=== {source} snapshots ===")
    snaps = store.list_snapshot_versions()
    if not snaps:
        print("  (none)")
    for v in snaps:
        data = store.load_snapshot(v)
        count = data.get("count", "?") if data else "?"
        print(f"  {v}  ({count} faculty)")
    print(f"\n=== {source} increments ===")
    incs = store.list_increment_versions()
    if not incs:
        print("  (none)")
    for v in incs:
        inc = store.load_increment(v)
        if inc:
            a = inc.get("added_count", 0)
            m = inc.get("modified_count", 0)
            r = inc.get("removed_count", 0)
            print(f"  {v}  (+{a} ~{m} -{r})")


def cmd_restore(source: str, target_version: str, out: Optional[str]) -> None:
    store = resolve_store(source)
    result = store.restore_to(target_version)
    print(f"[restore] source={source} target={target_version}")
    print(f"[restore] base_snapshot={result.get('base_snapshot')}")
    print(f"[restore] increments_applied={result.get('increments_applied')}")
    print(f"[restore] faculty_count={result.get('count')}")
    if out:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[restore] written to {out}")
    else:
        print(json.dumps({"count": result.get("count"), "sample_ids": list(result.get("faculty", {}).keys())[:5]}, indent=2))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        source = sys.argv[2] if len(sys.argv) > 2 else "directorycc"
        cmd_list(source)
    elif cmd == "restore":
        if len(sys.argv) < 4:
            print("usage: restore_cli.py restore <source> <version> [--out <file>]")
            sys.exit(1)
        source = sys.argv[2]
        target = sys.argv[3]
        out = None
        if "--out" in sys.argv:
            idx = sys.argv.index("--out")
            if idx + 1 < len(sys.argv):
                out = sys.argv[idx + 1]
        cmd_restore(source, target, out)
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
