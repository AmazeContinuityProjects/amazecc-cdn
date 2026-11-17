"""
build_master_registry.py
========================

Builds the master faculty registry with active/inactive tracking.

Logic:
  - Load the existing master registry (if any).
  - Determine the CURRENT active faculty set from the latest sync snapshots
    (directorycc + chennai aggregate) plus the VITFAC catalog.
  - Faculty present in the current run  -> active=True, added_at set on first
    sight, inactive_at cleared (if they were previously inactive, they're back).
  - Faculty NOT present in the current run but previously known  -> active=False,
    inactive_at set to now (only the first time they go inactive).
  - Faculty never seen before            -> active=True, added_at=now.

Outputs (written to state/ and copied to artifacts/ for download):
  - master_registry.json        : full registry, every faculty ever seen
  - incremental_diff.json       : what changed since the last registry build
    (added, removed, reactivated, still-active, still-inactive)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from store import VersionedStore  # noqa: E402

SYNC_STATE_ROOT = Path(__file__).resolve().parents[1] / "sync" / "state"
STATE_DIR = Path(__file__).resolve().parents[1] / "state"
ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "artifacts"

MASTER_REGISTRY_PATH = STATE_DIR / "master_registry.json"
INCREMENTAL_DIFF_PATH = STATE_DIR / "incremental_diff.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_to_artifacts(src: Path) -> None:
    """Copy a state file to the artifacts directory for download."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARTIFACTS_DIR / src.name
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def load_latest_directorycc() -> Dict[str, Any]:
    store = VersionedStore(SYNC_STATE_ROOT / "directorycc", "directorycc")
    latest = store.latest_snapshot()
    if latest is None:
        return {}
    _, data = latest
    return data.get("faculty", {})


def load_latest_chennai_aggregate() -> Dict[str, Any]:
    store = VersionedStore(SYNC_STATE_ROOT / "chennai", "chennai-aggregate")
    latest = store.latest_snapshot()
    if latest is None:
        return {}
    _, data = latest
    return data.get("faculty", {})


def load_vitfac_catalog() -> Dict[str, Any]:
    path = STATE_DIR / "vitfac_catalog_v1.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    faculty = payload.get("faculty", [])
    return {str(f["employeeId"]): f for f in faculty}


def current_active_ids(
    directorycc: Dict[str, Any],
    chennai: Dict[str, Any],
    vitfac: Dict[str, Any],
) -> Set[str]:
    """Union of all faculty IDs seen in the current sync run."""
    ids: Set[str] = set()
    for eid in directorycc:
        ids.add(str(eid))
    for eid in chennai:
        ids.add(str(eid))
    for eid in vitfac:
        ids.add(str(eid))
    return ids


def build_master_registry() -> Dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Load current sources ------------------------------------- #
    print("[registry] loading directorycc state ...")
    directorycc = load_latest_directorycc()
    print(f"[registry] directorycc: {len(directorycc)} entries")

    print("[registry] loading chennai state ...")
    chennai = load_latest_chennai_aggregate()
    print(f"[registry] chennai: {len(chennai)} entries")

    print("[registry] loading vitfac catalog ...")
    vitfac = load_vitfac_catalog()
    print(f"[registry] vitfac: {len(vitfac)} entries")

    current_ids = current_active_ids(directorycc, chennai, vitfac)
    print(f"[registry] current active IDs: {len(current_ids)}")

    # ---- Load existing registry ----------------------------------- #
    existing = load_json(MASTER_REGISTRY_PATH) or {
        "version": 1,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "total_ever_seen": 0,
        "faculty": {},
    }

    existing_faculty: Dict[str, Any] = existing.get("faculty", {})
    now = utc_now_iso()

    added: List[Dict[str, Any]] = []
    reactivated: List[Dict[str, Any]] = []
    inactivated: List[Dict[str, Any]] = []
    still_active: List[str] = []
    still_inactive: List[str] = []
    modified: List[Dict[str, Any]] = []

    new_registry: Dict[str, Any] = {}

    # Helper to build a base record from sources
    def base_record(emp_id: str) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "emp_id": emp_id,
            "sources": [],
        }
        # directorycc
        d = directorycc.get(emp_id)
        if d:
            dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
            rec["sources"].append("directorycc")
            rec["name"] = dir_api.get("name") or rec.get("name")
            rec["designation"] = dir_api.get("designation") or rec.get("designation")
            rec["email"] = dir_api.get("email") or rec.get("email")
            rec["image_url"] = f"https://cdn.amazecc.com/{emp_id}.webp"
        # chennai
        c = chennai.get(emp_id)
        if c:
            rec["sources"].append("chennai")
            rec["name"] = c.get("name") or rec.get("name")
            rec["school"] = c.get("school")
            rec["wpId"] = c.get("wpId")
            rec["slug"] = c.get("slug")
            rec["profileUrl"] = c.get("profileUrl")
            rec["featuredMedia"] = c.get("featuredMedia")
            if "image_url" not in rec:
                rec["image_url"] = f"https://cdn.amazecc.com/{emp_id}.webp"
        # vitfac
        v = vitfac.get(emp_id)
        if v:
            rec["sources"].append("vitfac")
            rec["name"] = v.get("name") or rec.get("name")
            rec["designation"] = v.get("designation") or rec.get("designation")
            rec["dept"] = v.get("dept") or rec.get("dept")
            rec["wPct"] = v.get("wPct", 0)
            rec["reviews"] = v.get("reviews", 0)
            rec["metrics"] = v.get("metrics")
            rec["lore"] = v.get("lore")
            rec["courses"] = v.get("courses", [])
        return rec

    # ---- Process current IDs (active) ----------------------------- #
    for emp_id in sorted(current_ids, key=lambda x: int(x) if x.isdigit() else x):
        prev = existing_faculty.get(emp_id)
        rec = base_record(emp_id)

        if prev is None:
            # Brand new faculty
            rec["active"] = True
            rec["added_at"] = now
            rec["inactive_at"] = None
            rec["last_seen_at"] = now
            added.append(rec)
        else:
            # Existing faculty - check if was inactive
            was_active = prev.get("active", True)
            rec["active"] = True
            rec["added_at"] = prev.get("added_at", now)
            rec["inactive_at"] = None  # reactivated
            rec["last_seen_at"] = now
            # Preserve any extra fields from previous record
            for k in ("sources", "name", "designation", "email", "school",
                      "wpId", "slug", "profileUrl", "featuredMedia", "dept",
                      "wPct", "reviews", "metrics", "lore", "courses",
                      "image_url"):
                if k not in rec or rec[k] is None:
                    rec[k] = prev.get(k)
            if not was_active:
                reactivated.append(rec)
            else:
                still_active.append(emp_id)
                # Check if data changed
                if prev != rec:
                    modified.append({"emp_id": emp_id, "old": prev, "new": rec})

        new_registry[emp_id] = rec

    # ---- Process previous IDs not in current (inactive) ----------- #
    for emp_id, prev in existing_faculty.items():
        if emp_id in new_registry:
            continue
        # Was known before, not in current run -> mark inactive
        was_active = prev.get("active", True)
        rec = dict(prev)
        rec["active"] = False
        rec["last_seen_at"] = now
        if was_active:
            rec["inactive_at"] = now
            inactivated.append(rec)
        else:
            still_inactive.append(emp_id)
        new_registry[emp_id] = rec

    # Sort registry
    sorted_registry = dict(
        sorted(new_registry.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else kv[0])
    )

    registry = {
        "version": 2,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "total_ever_seen": len(sorted_registry),
        "active_count": sum(1 for r in sorted_registry.values() if r.get("active")),
        "inactive_count": sum(1 for r in sorted_registry.values() if not r.get("active")),
        "faculty": sorted_registry,
    }

    diff = {
        "version": 2,
        "generated_at": now,
        "added": added,
        "reactivated": reactivated,
        "inactivated": inactivated,
        "modified": modified,
        "still_active": still_active,
        "still_inactive": still_inactive,
        "added_count": len(added),
        "reactivated_count": len(reactivated),
        "inactivated_count": len(inactivated),
        "modified_count": len(modified),
        "still_active_count": len(still_active),
        "still_inactive_count": len(still_inactive),
        "current_active_ids": sorted(current_ids, key=lambda x: int(x) if x.isdigit() else x),
    }

    # ---- Persist -------------------------------------------------- #
    save_json(MASTER_REGISTRY_PATH, registry)
    save_json(INCREMENTAL_DIFF_PATH, diff)

    # Copy to artifacts for download
    copy_to_artifacts(MASTER_REGISTRY_PATH)
    copy_to_artifacts(INCREMENTAL_DIFF_PATH)

    print(f"[registry] total ever seen: {len(sorted_registry)}")
    print(f"[registry] active: {registry['active_count']}, inactive: {registry['inactive_count']}")
    print(f"[registry] added: {len(added)}, reactivated: {len(reactivated)}, inactivated: {len(inactivated)}")
    print(f"[registry] saved to {MASTER_REGISTRY_PATH}")
    print(f"[registry] diff saved to {INCREMENTAL_DIFF_PATH}")
    print(f"[registry] artifacts copied to {ARTIFACTS_DIR}")

    return {"registry": registry, "diff": diff}


def main() -> None:
    build_master_registry()


if __name__ == "__main__":
    main()
