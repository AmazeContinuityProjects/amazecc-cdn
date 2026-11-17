"""
build_vitfac_catalog.py
=======================

Fetches the VITFAC review catalog from cache.amazecc.com/faculty and saves it
locally as faculty_master_constructor/state/vitfac_catalog_v1.json.

The catalog contains per-faculty review aggregates: designation, dept, courses,
wPct, reviews, wCount, lCount, metrics (lecture/da/vibe), lore (green/red).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from http_client import RobustHttp  # noqa: E402

VITFAC_CATALOG_URL = "https://cache.amazecc.com/faculty"
OUT_PATH = Path(__file__).resolve().parents[1] / "state" / "vitfac_catalog_v1.json"


def fetch_vitfac_catalog(http: RobustHttp) -> List[Dict[str, Any]]:
    status, headers, data = http.get_json(VITFAC_CATALOG_URL)
    if status != 200:
        raise RuntimeError(f"VITFAC catalog fetch failed: HTTP {status}")
    if not isinstance(data, dict):
        raise RuntimeError(f"VITFAC catalog unexpected shape: {type(data)}")
    faculty = data.get("data", {}).get("faculty", [])
    if not isinstance(faculty, list):
        faculty = []
    return faculty


def main() -> None:
    http = RobustHttp()
    print(f"[vitfac] fetching catalog from {VITFAC_CATALOG_URL} ...")
    faculty = fetch_vitfac_catalog(http)
    print(f"[vitfac] got {len(faculty)} entries")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": VITFAC_CATALOG_URL,
        "fetched_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "count": len(faculty),
        "faculty": faculty,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[vitfac] saved to {OUT_PATH} ({OUT_PATH.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
