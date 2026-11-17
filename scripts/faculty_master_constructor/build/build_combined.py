"""
build_combined.py
=================

Builds the compact combined faculty catalog (faculty_combined_v1.json), the
same shape served by cache.amazecc.com/faculty/combined.

Merge inputs:
  1. chennai.vit.ac.in roster (from faculty_sync state) — employeeId, name,
     school, wpId, slug, profileUrl, wpUrl, featuredMedia
  2. directorycc profiles (from faculty_sync state) — designation, email,
     researchArea, areasOfSpecialization, links, phd, etc.
  3. VITFAC review catalog (from build_vitfac_catalog.py) — designation,
     dept, courses, wPct, reviews, wCount, lCount, metrics, lore

Image is NOT base64 — it points to https://cdn.amazecc.com/faculty/<empId>.webp.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from store import VersionedStore  # noqa: E402

SYNC_STATE_ROOT = Path(__file__).resolve().parents[1] / "sync" / "state"
OUT_DIR = Path(__file__).resolve().parents[1] / "state"
CDN_BASE = "https://cdn.amazecc.com"


def load_latest_directorycc() -> Dict[str, Any]:
    """Load the latest directorycc snapshot from faculty_sync state."""
    store = VersionedStore(SYNC_STATE_ROOT / "directorycc", "directorycc")
    latest = store.latest_snapshot()
    if latest is None:
        return {}
    _, data = latest
    return data.get("faculty", {})


def load_latest_chennai_aggregate() -> Dict[str, Any]:
    """Load the latest chennai aggregate snapshot from faculty_sync state."""
    store = VersionedStore(SYNC_STATE_ROOT / "chennai", "chennai-aggregate")
    latest = store.latest_snapshot()
    if latest is None:
        return {}
    _, data = latest
    return data.get("faculty", {})


def load_vitfac_catalog() -> Dict[str, Any]:
    """Load the VITFAC catalog built by build_vitfac_catalog.py."""
    path = OUT_DIR / "vitfac_catalog_v1.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    faculty = payload.get("faculty", [])
    return {str(f["employeeId"]): f for f in faculty}


def build_combined(
    directorycc: Dict[str, Any],
    chennai: Dict[str, Any],
    vitfac: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Merge directorycc + chennai + vitfac into the combined catalog shape."""
    combined: List[Dict[str, Any]] = []
    seen = set()

    # Primary iteration order: chennai roster (authoritative for school/wp fields)
    for emp_id, r in chennai.items():
        seen.add(emp_id)
        v = vitfac.get(emp_id)
        d = directorycc.get(emp_id)
        dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
        cdn_url = f"{CDN_BASE}/{emp_id}.webp"

        combined.append({
            "employeeId": emp_id,
            "name": (dir_api.get("name") or r.get("name") or (v or {}).get("name")),
            "school": r.get("school"),
            "wpId": r.get("wpId"),
            "slug": r.get("slug"),
            "profileUrl": r.get("profileUrl"),
            "wpUrl": r.get("wpUrl"),
            "featuredMedia": r.get("featuredMedia"),
            "vitfacAvailable": bool(v and v.get("reviews", 0) > 0),
            "directoryAvailable": bool(d),
            "designation": dir_api.get("designation") or (v or {}).get("designation") or None,
            "dept": (v or {}).get("dept") or r.get("school") or None,
            "campus": (v or {}).get("dept") or "Chennai",
            "image_url": cdn_url,
            "imageUrl": cdn_url,
            "email": dir_api.get("email") or None,
            "intercom": dir_api.get("intercom") or None,
            "wPct": (v or {}).get("wPct", 0) or 0,
            "reviews": (v or {}).get("reviews", 0) or 0,
            "wCount": (v or {}).get("wCount", 0) or 0,
            "lCount": (v or {}).get("lCount", 0) or 0,
            "metrics": (v or {}).get("metrics") or None,
            "lore": (v or {}).get("lore") or None,
            "courses": (v or {}).get("courses", []) or [],
        })

    # Add directorycc-only faculty not in chennai roster
    for emp_id, d in directorycc.items():
        if emp_id in seen:
            continue
        seen.add(emp_id)
        dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
        v = vitfac.get(emp_id)
        cdn_url = f"{CDN_BASE}/{emp_id}.webp"
        combined.append({
            "employeeId": emp_id,
            "name": dir_api.get("name") or (v or {}).get("name") or emp_id,
            "school": (v or {}).get("dept") or "general",
            "wpId": 0,
            "slug": "",
            "profileUrl": "",
            "wpUrl": "",
            "featuredMedia": None,
            "vitfacAvailable": bool(v and v.get("reviews", 0) > 0),
            "directoryAvailable": True,
            "designation": dir_api.get("designation") or (v or {}).get("designation") or None,
            "dept": (v or {}).get("dept") or None,
            "campus": (v or {}).get("dept") or "Chennai",
            "image_url": cdn_url,
            "imageUrl": cdn_url,
            "email": dir_api.get("email") or None,
            "intercom": dir_api.get("intercom") or None,
            "wPct": (v or {}).get("wPct", 0) or 0,
            "reviews": (v or {}).get("reviews", 0) or 0,
            "wCount": (v or {}).get("wCount", 0) or 0,
            "lCount": (v or {}).get("lCount", 0) or 0,
            "metrics": (v or {}).get("metrics") or None,
            "lore": (v or {}).get("lore") or None,
            "courses": (v or {}).get("courses", []) or [],
        })

    # Add vitfac-only faculty not in either
    for emp_id, v in vitfac.items():
        if emp_id in seen:
            continue
        seen.add(emp_id)
        d = directorycc.get(emp_id)
        dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
        cdn_url = f"{CDN_BASE}/{emp_id}.webp"
        combined.append({
            "employeeId": emp_id,
            "name": dir_api.get("name") or v.get("name") or emp_id,
            "school": v.get("dept") or "general",
            "wpId": 0,
            "slug": "",
            "profileUrl": "",
            "wpUrl": "",
            "featuredMedia": None,
            "vitfacAvailable": v.get("reviews", 0) > 0,
            "directoryAvailable": bool(d),
            "designation": dir_api.get("designation") or v.get("designation") or None,
            "dept": v.get("dept") or None,
            "campus": v.get("dept") or "Chennai",
            "image_url": cdn_url,
            "imageUrl": cdn_url,
            "email": dir_api.get("email") or None,
            "intercom": dir_api.get("intercom") or None,
            "wPct": v.get("wPct", 0) or 0,
            "reviews": v.get("reviews", 0) or 0,
            "wCount": v.get("wCount", 0) or 0,
            "lCount": v.get("lCount", 0) or 0,
            "metrics": v.get("metrics") or None,
            "lore": v.get("lore") or None,
            "courses": v.get("courses", []) or [],
        })

    combined.sort(key=lambda x: int(x["employeeId"]) if str(x["employeeId"]).isdigit() else x["employeeId"])
    return combined


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("[combined] loading directorycc state ...")
    directorycc = load_latest_directorycc()
    print(f"[combined] directorycc: {len(directorycc)} entries")

    print("[combined] loading chennai state ...")
    chennai = load_latest_chennai_aggregate()
    print(f"[combined] chennai: {len(chennai)} entries")

    print("[combined] loading vitfac catalog ...")
    vitfac = load_vitfac_catalog()
    print(f"[combined] vitfac: {len(vitfac)} entries")

    combined = build_combined(directorycc, chennai, vitfac)
    print(f"[combined] merged: {len(combined)} faculty entries")

    payload = {
        "source": "faculty_master_constructor/build_combined.py",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "count": len(combined),
        "inputs": {
            "directorycc": len(directorycc),
            "chennai": len(chennai),
            "vitfac": len(vitfac),
        },
        "faculty": combined,
    }

    out_path = OUT_DIR / "faculty_combined_v1.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[combined] saved to {out_path} ({out_path.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
