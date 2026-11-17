"""
build_all_data.py
=================

Builds the complete all-data faculty catalog (faculty_all_data_v1.json), the
same shape served by cache.amazecc.com/faculty/all-data.

Merge inputs (same as combined, but with the FULL directorycc academic dossier):
  1. chennai.vit.ac.in roster — employeeId, name, school, wpId, slug, profileUrl, wpUrl, featuredMedia
  2. directorycc profiles — FULL academic dossier: education, publications,
     researchArea, areasOfSpecialization, links, awards, phd, metrics (hIndexScopus,
     i10Index), patentsGranted, patentsPublished, consultancyProjects,
     fundedProjects, collaborations, otherDetails, intercom, email
  3. VITFAC review catalog — designation, dept, courses, wPct, reviews,
     wCount, lCount, metrics, lore

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
    path = OUT_DIR / "vitfac_catalog_v1.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    faculty = payload.get("faculty", [])
    return {str(f["employeeId"]): f for f in faculty}


def build_all_data(
    directorycc: Dict[str, Any],
    chennai: Dict[str, Any],
    vitfac: Dict[str, Any],
) -> List[Dict[str, Any]]:
    all_data: List[Dict[str, Any]] = []
    seen = set()

    for emp_id, r in chennai.items():
        seen.add(emp_id)
        v = vitfac.get(emp_id)
        d = directorycc.get(emp_id)
        dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
        cdn_url = f"{CDN_BASE}/{emp_id}.webp"

        all_data.append({
            "employeeId": emp_id,
            "name": dir_api.get("name") or r.get("name") or (v or {}).get("name"),
            "school": r.get("school"),
            "dept": (v or {}).get("dept") or r.get("school") or None,
            "campus": "Chennai",
            "designation": dir_api.get("designation") or (v or {}).get("designation") or None,
            "wpId": r.get("wpId"),
            "slug": r.get("slug"),
            "profileUrl": r.get("profileUrl"),
            "wpUrl": r.get("wpUrl"),
            "featuredMedia": r.get("featuredMedia"),
            "imageUrl": cdn_url,
            "image_url": cdn_url,
            "image": None,
            "email": dir_api.get("email") or None,
            "intercom": dir_api.get("intercom") or None,
            "phd": dir_api.get("phd") or None,
            "directoryAvailable": bool(d),
            "vitfacAvailable": bool(v and v.get("reviews", 0) > 0),
            "wPct": (v or {}).get("wPct", 0) or 0,
            "reviews": (v or {}).get("reviews", 0) or 0,
            "wCount": (v or {}).get("wCount", 0) or 0,
            "lCount": (v or {}).get("lCount", 0) or 0,
            "metrics": (v or {}).get("metrics") or None,
            "lore": (v or {}).get("lore") or None,
            "courses": (v or {}).get("courses", []) or [],
            "researchArea": dir_api.get("researchArea") or [],
            "areasOfSpecialization": dir_api.get("areasOfSpecialization") or [],
            "links": dir_api.get("links") or None,
            "hIndexScopus": (dir_api.get("metrics") or {}).get("hIndexScopus") if isinstance(dir_api.get("metrics"), dict) else None,
            "i10Index": (dir_api.get("metrics") or {}).get("i10Index") if isinstance(dir_api.get("metrics"), dict) else None,
            "education": dir_api.get("education") or [],
            "publications": dir_api.get("publications") or [],
            "awards": dir_api.get("awards") or [],
            "patentsGranted": dir_api.get("patentsGranted") or [],
            "patentsPublished": dir_api.get("patentsPublished") or [],
            "consultancyProjects": dir_api.get("consultancyProjects") or None,
            "fundedProjects": dir_api.get("fundedProjects") or None,
            "collaborations": dir_api.get("collaborations") or [],
            "otherDetails": dir_api.get("otherDetails") or [],
        })

    # directorycc-only faculty not in chennai
    for emp_id, d in directorycc.items():
        if emp_id in seen:
            continue
        seen.add(emp_id)
        dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
        v = vitfac.get(emp_id)
        cdn_url = f"{CDN_BASE}/{emp_id}.webp"
        all_data.append({
            "employeeId": emp_id,
            "name": dir_api.get("name") or (v or {}).get("name") or emp_id,
            "school": (v or {}).get("dept") or "general",
            "dept": (v or {}).get("dept") or None,
            "campus": "Chennai",
            "designation": dir_api.get("designation") or (v or {}).get("designation") or None,
            "wpId": 0,
            "slug": "",
            "profileUrl": "",
            "wpUrl": "",
            "featuredMedia": None,
            "imageUrl": cdn_url,
            "image_url": cdn_url,
            "image": None,
            "email": dir_api.get("email") or None,
            "intercom": dir_api.get("intercom") or None,
            "phd": dir_api.get("phd") or None,
            "directoryAvailable": True,
            "vitfacAvailable": bool(v and v.get("reviews", 0) > 0),
            "wPct": (v or {}).get("wPct", 0) or 0,
            "reviews": (v or {}).get("reviews", 0) or 0,
            "wCount": (v or {}).get("wCount", 0) or 0,
            "lCount": (v or {}).get("lCount", 0) or 0,
            "metrics": (v or {}).get("metrics") or None,
            "lore": (v or {}).get("lore") or None,
            "courses": (v or {}).get("courses", []) or [],
            "researchArea": dir_api.get("researchArea") or [],
            "areasOfSpecialization": dir_api.get("areasOfSpecialization") or [],
            "links": dir_api.get("links") or None,
            "hIndexScopus": (dir_api.get("metrics") or {}).get("hIndexScopus") if isinstance(dir_api.get("metrics"), dict) else None,
            "i10Index": (dir_api.get("metrics") or {}).get("i10Index") if isinstance(dir_api.get("metrics"), dict) else None,
            "education": dir_api.get("education") or [],
            "publications": dir_api.get("publications") or [],
            "awards": dir_api.get("awards") or [],
            "patentsGranted": dir_api.get("patentsGranted") or [],
            "patentsPublished": dir_api.get("patentsPublished") or [],
            "consultancyProjects": dir_api.get("consultancyProjects") or None,
            "fundedProjects": dir_api.get("fundedProjects") or None,
            "collaborations": dir_api.get("collaborations") or [],
            "otherDetails": dir_api.get("otherDetails") or [],
        })

    # vitfac-only faculty not in either
    for emp_id, v in vitfac.items():
        if emp_id in seen:
            continue
        seen.add(emp_id)
        d = directorycc.get(emp_id)
        dir_api = (d or {}).get("api_response", {}) if isinstance(d, dict) else {}
        cdn_url = f"{CDN_BASE}/{emp_id}.webp"
        all_data.append({
            "employeeId": emp_id,
            "name": dir_api.get("name") or v.get("name") or emp_id,
            "school": v.get("dept") or "general",
            "dept": v.get("dept") or None,
            "campus": "Chennai",
            "designation": dir_api.get("designation") or v.get("designation") or None,
            "wpId": 0,
            "slug": "",
            "profileUrl": "",
            "wpUrl": "",
            "featuredMedia": None,
            "imageUrl": cdn_url,
            "image_url": cdn_url,
            "image": None,
            "email": dir_api.get("email") or None,
            "intercom": dir_api.get("intercom") or None,
            "phd": dir_api.get("phd") or None,
            "directoryAvailable": bool(d),
            "vitfacAvailable": v.get("reviews", 0) > 0,
            "wPct": v.get("wPct", 0) or 0,
            "reviews": v.get("reviews", 0) or 0,
            "wCount": v.get("wCount", 0) or 0,
            "lCount": v.get("lCount", 0) or 0,
            "metrics": v.get("metrics") or None,
            "lore": v.get("lore") or None,
            "courses": v.get("courses", []) or [],
            "researchArea": dir_api.get("researchArea") or [],
            "areasOfSpecialization": dir_api.get("areasOfSpecialization") or [],
            "links": dir_api.get("links") or None,
            "hIndexScopus": (dir_api.get("metrics") or {}).get("hIndexScopus") if isinstance(dir_api.get("metrics"), dict) else None,
            "i10Index": (dir_api.get("metrics") or {}).get("i10Index") if isinstance(dir_api.get("metrics"), dict) else None,
            "education": dir_api.get("education") or [],
            "publications": dir_api.get("publications") or [],
            "awards": dir_api.get("awards") or [],
            "patentsGranted": dir_api.get("patentsGranted") or [],
            "patentsPublished": dir_api.get("patentsPublished") or [],
            "consultancyProjects": dir_api.get("consultancyProjects") or None,
            "fundedProjects": dir_api.get("fundedProjects") or None,
            "collaborations": dir_api.get("collaborations") or [],
            "otherDetails": dir_api.get("otherDetails") or [],
        })

    all_data.sort(key=lambda x: int(x["employeeId"]) if str(x["employeeId"]).isdigit() else x["employeeId"])
    return all_data


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("[all-data] loading directorycc state ...")
    directorycc = load_latest_directorycc()
    print(f"[all-data] directorycc: {len(directorycc)} entries")

    print("[all-data] loading chennai state ...")
    chennai = load_latest_chennai_aggregate()
    print(f"[all-data] chennai: {len(chennai)} entries")

    print("[all-data] loading vitfac catalog ...")
    vitfac = load_vitfac_catalog()
    print(f"[all-data] vitfac: {len(vitfac)} entries")

    all_data = build_all_data(directorycc, chennai, vitfac)
    print(f"[all-data] merged: {len(all_data)} faculty entries")

    payload = {
        "source": "faculty_master_constructor/build_all_data.py",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_data),
        "inputs": {
            "directorycc": len(directorycc),
            "chennai": len(chennai),
            "vitfac": len(vitfac),
        },
        "faculty": all_data,
    }

    out_path = OUT_DIR / "faculty_all_data_v1.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[all-data] saved to {out_path} ({out_path.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
