"""
faculty_sync.store
==================

Versioned, append-only store for faculty sync state.

Layout (per source, e.g. directorycc or chennai:<school>):
  <base>/<source>/
    snapshots/   <version>.json   -- full point-in-time export
    increments/  <version>.inc.json -- incremental diff since parent
    checkpoint.json                -- last run cursor / metadata

Restore model:
  - A snapshot is the authoritative full state at its version timestamp.
  - An increment carries {added, modified, removed} relative to its parent.
  - To restore to time T: load the latest snapshot at-or-before T, then
    replay every increment with version <= T in order.

All writes are atomic (temp file + os.replace). JSON is written with
ensure_ascii=False, indent=2 for readability and git-friendliness.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically via temp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class VersionedStore:
    """
    Append-only versioned store for one faculty source.

    Parameters
    ----------
    base_dir : str | Path
        Root of the store (e.g. .../state/directorycc).
    source_name : str
        Human-readable source label (for metadata).
    """

    def __init__(self, base_dir: str | Path, source_name: str) -> None:
        self.base_dir = Path(base_dir)
        self.source_name = source_name
        self.snapshots_dir = self.base_dir / "snapshots"
        self.increments_dir = self.base_dir / "increments"
        self.checkpoint_path = self.base_dir / "checkpoint.json"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.increments_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # checkpoint
    # ------------------------------------------------------------------ #
    def load_checkpoint(self) -> Dict[str, Any]:
        if self.checkpoint_path.exists():
            return read_json(self.checkpoint_path)
        return {"source": self.source_name, "created_at": utc_now_iso(), "runs": []}

    def save_checkpoint(self, cp: Dict[str, Any]) -> None:
        cp["updated_at"] = utc_now_iso()
        atomic_write_json(self.checkpoint_path, cp)

    # ------------------------------------------------------------------ #
    # snapshots
    # ------------------------------------------------------------------ #
    def list_snapshot_versions(self) -> List[str]:
        """Return sorted list of snapshot version strings."""
        versions = []
        for p in self.snapshots_dir.glob("*.json"):
            versions.append(p.stem)
        return sorted(versions)

    def save_snapshot(self, version: str, data: Dict[str, Any]) -> Path:
        data = dict(data)
        data["version"] = version
        data["timestamp"] = utc_now_iso()
        data["source"] = self.source_name
        path = self.snapshots_dir / f"{version}.json"
        atomic_write_json(path, data)
        return path

    def load_snapshot(self, version: str) -> Optional[Dict[str, Any]]:
        path = self.snapshots_dir / f"{version}.json"
        if not path.exists():
            matches = list(self.snapshots_dir.glob(f"{version}*.json"))
            if not matches:
                return None
            path = matches[0]
        return read_json(path)

    def latest_snapshot(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        versions = self.list_snapshot_versions()
        if not versions:
            return None
        v = versions[-1]
        return v, self.load_snapshot(v)

    def snapshot_at_or_before(self, version: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Find the latest snapshot with version <= target (lexicographic, since
        version is a UTC timestamp string it sorts chronologically)."""
        versions = self.list_snapshot_versions()
        candidates = [v for v in versions if v <= version]
        if not candidates:
            return None
        v = candidates[-1]
        return v, self.load_snapshot(v)

    # ------------------------------------------------------------------ #
    # increments
    # ------------------------------------------------------------------ #
    def list_increment_versions(self) -> List[str]:
        versions = []
        for p in self.increments_dir.glob("*.inc.json"):
            versions.append(p.stem.replace(".inc", ""))
        return sorted(versions)

    def save_increment(self, version: str, inc: Dict[str, Any]) -> Path:
        inc = dict(inc)
        inc["version"] = version
        inc["timestamp"] = utc_now_iso()
        inc["source"] = self.source_name
        path = self.increments_dir / f"{version}.inc.json"
        atomic_write_json(path, inc)
        return path

    def load_increment(self, version: str) -> Optional[Dict[str, Any]]:
        path = self.increments_dir / f"{version}.inc.json"
        if not path.exists():
            return None
        return read_json(path)

    def increments_between(self, after_version: Optional[str], up_to_version: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Return (version, inc) for all increments in (after_version, up_to_version]."""
        all_versions = self.list_increment_versions()
        result = []
        for v in all_versions:
            if v <= (after_version or ""):
                continue
            if v <= up_to_version:
                result.append((v, self.load_increment(v)))
        return result

    # ------------------------------------------------------------------ #
    # point-in-time restore
    # ------------------------------------------------------------------ #
    def restore_to(self, target_version: str) -> Dict[str, Any]:
        """
        Reconstruct full faculty state as of `target_version`.

        Strategy:
          1. Find latest snapshot at-or-before target_version.
          2. Start from that snapshot's faculty map.
          3. Replay every increment with version <= target_version in order,
             applying added/modified/removed.
        Returns a dict with metadata + the reconstructed faculty map.
        """
        base = self.snapshot_at_or_before(target_version)
        if base is None:
            # No snapshot exists yet; build empty state from increments alone.
            faculty: Dict[str, Any] = {}
            base_version = None
            base_count = 0
        else:
            base_version, base_data = base
            faculty = dict(base_data.get("faculty", {}))
            base_count = base_data.get("count", 0)

        increments = self.increments_between(base_version, target_version)
        applied = []
        for inc_version, inc in increments:
            applied.append(inc_version)
            for emp_id, record in (inc.get("added") or {}).items():
                faculty[emp_id] = record
            for emp_id, record in (inc.get("modified") or {}).items():
                # modified value may be {"old":..., "new":...} or just the new record
                if isinstance(record, dict) and "new" in record:
                    faculty[emp_id] = record["new"]
                else:
                    faculty[emp_id] = record
            for emp_id in (inc.get("removed") or []):
                faculty.pop(emp_id, None)

        return {
            "restored_to": target_version,
            "base_snapshot": base_version,
            "increments_applied": applied,
            "source": self.source_name,
            "count": len(faculty),
            "faculty": faculty,
            "restored_at": utc_now_iso(),
        }


def diff_faculty(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute an increment dict between two faculty maps (emp_id -> record).

    Returns {"added": {...}, "modified": {"emp_id": {"old":..., "new":...}}, "removed": [...]}
    """
    old_ids = set(old.keys())
    new_ids = set(new.keys())

    added = {eid: new[eid] for eid in new_ids - old_ids}
    removed = sorted(old_ids - new_ids)
    modified = {}
    for eid in old_ids & new_ids:
        if old[eid] != new[eid]:
            modified[eid] = {"old": old[eid], "new": new[eid]}

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "added_count": len(added),
        "modified_count": len(modified),
        "removed_count": len(removed),
    }
