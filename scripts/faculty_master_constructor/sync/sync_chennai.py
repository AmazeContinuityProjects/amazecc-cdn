"""
faculty_sync.sync_chennai
=========================

Sync faculty from https://chennai.vit.ac.in (WordPress REST API).

Two-phase discovery:
  1. Enumerate school post types from /wp-json/wp/v2/types
  2. For each school type, paginate /wp-json/wp/v2/<type>?per_page=100&page=N
     to collect every faculty post.

Per faculty post we keep:
  post_id, slug, name (title), featured_media (media id),
  emp_id (parsed from meta._links_to, e.g. .../faculty/52462-mr-mohamed-adam-k),
  links_to (raw url), date, and (optionally) resolved image source_url.

State per school lives under scripts/faculty_sync/state/chennai/schools/<type>/.
An aggregate chennai store combines every school.
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import VersionedStore, diff_faculty, utc_now_iso, utc_now_version  # noqa: E402
from http_client import RobustHttp  # noqa: E402

BASE = "https://chennai.vit.ac.in"
WP_TYPES = f"{BASE}/wp-json/wp/v2/types"

# Standard WP / non-faculty types we skip
SKIP_TYPES = {
    "post", "page", "attachment", "nav_menu_item", "wp_block", "wp_template",
    "wp_template_part", "wp_navigation", "wp_font_family", "wp_font_face",
    "elementor_library", "elementskit_content",
}

CONCURRENCY = 12
PER_PAGE = 100

STATE_ROOT = Path(__file__).resolve().parent / "state" / "chennai"


def discover_school_types(http: RobustHttp) -> List[str]:
    """Return custom post type slugs that look like faculty/school types."""
    status, headers, data = http.get_json(WP_TYPES)
    if not isinstance(data, dict):
        print(f"[chennai] unexpected /types response: {type(data)}")
        return []
    types = []
    for slug, info in data.items():
        if slug in SKIP_TYPES:
            continue
        if slug.startswith("wp_") or slug.startswith("elementor"):
            continue
        # heuristic: include anything that's a custom post type with an archive or rest base
        types.append(slug)
    return sorted(types)


_EMP_RE = re.compile(r"/faculty/(\d+)")


def parse_emp_id(links_to: Optional[str]) -> Optional[str]:
    if not links_to:
        return None
    m = _EMP_RE.search(links_to)
    return m.group(1) if m else None


def fetch_posts_for_type(http: RobustHttp, ptype: str) -> List[Dict[str, Any]]:
    """Paginate all posts for a given post type."""
    posts: List[Dict[str, Any]] = []
    page = 1
    while True:
        url = f"{BASE}/wp-json/wp/v2/{ptype}?per_page={PER_PAGE}&page={page}&_fields=id,slug,title,date,featured_media,meta,link,content,excerpt"
        try:
            status, headers, data = http.get_json(url)
        except Exception as e:
            print(f"[chennai:{ptype}] page {page} error: {e}")
            break
        if not isinstance(data, list) or len(data) == 0:
            break
        posts.extend(data)
        # WordPress sends X-WP-TotalPages header
        total_pages = headers.get("X-WP-Total-Pages") or headers.get("x-wp-totalpages")
        if total_pages:
            try:
                if page >= int(total_pages):
                    break
            except ValueError:
                pass
        elif len(data) < PER_PAGE:
            break
        page += 1
        if page > 500:  # safety cap
            break
    return posts


def build_faculty_map(posts: List[Dict[str, Any]], http: RobustHttp) -> Dict[str, Any]:
    """Convert raw WP posts into faculty records keyed by post_id."""
    faculty: Dict[str, Any] = {}
    for p in posts:
        pid = str(p.get("id"))
        title = p.get("title", {})
        name = title.get("rendered") if isinstance(title, dict) else str(title)
        meta = p.get("meta", {}) or {}
        links_to = meta.get("_links_to")
        emp_id = parse_emp_id(links_to)
        featured_media = p.get("featured_media")
        faculty[pid] = {
            "post_id": pid,
            "slug": p.get("slug"),
            "name": name,
            "emp_id": emp_id,
            "links_to": links_to,
            "featured_media": featured_media,
            "date": p.get("date"),
            "link": p.get("link"),
            "source": "chennai-wp",
            "synced_at": utc_now_iso(),
        }
    return faculty


def resolve_media_image(http: RobustHttp, faculty: Dict[str, Any], limit: Optional[int] = None) -> int:
    """
    For faculty records with featured_media, fetch /wp-json/wp/v2/media/<id>
    to get source_url (image URL). Returns count resolved.
    Resolves concurrently, capped by `limit` if provided.
    """
    to_resolve = [
        (pid, rec) for pid, rec in faculty.items()
        if rec.get("featured_media") and not rec.get("image_url")
    ]
    if limit is not None:
        to_resolve = to_resolve[:limit]

    resolved = 0

    def one(pid: str, rec: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        mid = rec["featured_media"]
        try:
            status, headers, data = http.get_json(f"{BASE}/wp-json/wp/v2/media/{mid}")
            if isinstance(data, dict):
                src = data.get("source_url") or (data.get("guid") or {}).get("rendered")
                return pid, src
        except Exception:
            pass
        return pid, None

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(one, pid, rec): pid for pid, rec in to_resolve}
        for fut in as_completed(futs):
            pid, src = fut.result()
            if src:
                faculty[pid]["image_url"] = src
                resolved += 1
    return resolved


def run_chennai(dry_run: bool = False, resolve_images: bool = True) -> Dict[str, Any]:
    http = RobustHttp()
    print(f"[chennai] discovering school post types from {WP_TYPES}")
    school_types = discover_school_types(http)
    print(f"[chennai] found {len(school_types)} candidate post types: {school_types}")

    aggregate: Dict[str, Any] = {}  # post_id -> record (with school_type tag)
    per_school_result: Dict[str, Any] = {}

    for ptype in school_types:
        print(f"\n[chennai:{ptype}] fetching posts...")
        posts = fetch_posts_for_type(http, ptype)
        print(f"[chennai:{ptype}] {len(posts)} posts")
        faculty = build_faculty_map(posts, http)

        if resolve_images and faculty:
            resolved = resolve_media_image(http, faculty)
            print(f"[chennai:{ptype}] resolved image URLs for {resolved}/{len(faculty)}")

        # persist per-school
        store = VersionedStore(STATE_ROOT / "schools" / ptype, f"chennai:{ptype}")
        prev = store.latest_snapshot()
        prev_faculty: Dict[str, Any] = {}
        prev_version = None
        if prev:
            prev_version, prev_data = prev
            prev_faculty = prev_data.get("faculty", {})

        inc = diff_faculty(prev_faculty, faculty)
        inc["requests_made"] = http.requests_made
        changed = inc["added_count"] > 0 or inc["modified_count"] > 0 or inc["removed_count"] > 0

        if not dry_run:
            version = utc_now_version()
            if changed:
                store.save_increment(version, inc)
                store.save_snapshot(version, {
                    "count": len(faculty),
                    "faculty": faculty,
                    "school_type": ptype,
                    "derived_from": prev_version,
                    "increment": version,
                })
                print(f"[chennai:{ptype}] saved snapshot v{version} ({len(faculty)} faculty) + increment")
            else:
                version = prev_version or version
                print(f"[chennai:{ptype}] no changes; snapshot {version}")

            cp = store.load_checkpoint()
            cp["last_run"] = utc_now_iso()
            cp["last_version"] = version
            cp["count"] = len(faculty)
            cp["school_type"] = ptype
            store.save_checkpoint(cp)
        else:
            version = prev_version or "dry"
            print(f"[chennai:{ptype}] dry-run; would be version {version}")

        per_school_result[ptype] = {
            "count": len(faculty),
            "added": inc["added_count"],
            "modified": inc["modified_count"],
            "removed": inc["removed_count"],
            "changed": changed,
            "version": version if not dry_run else None,
        }

        # tag with school type and add to aggregate
        for pid, rec in faculty.items():
            rec_tagged = dict(rec)
            rec_tagged["school_type"] = ptype
            aggregate[pid] = rec_tagged

    # ---- aggregate chennai store ----------------------------------- #
    agg_store = VersionedStore(STATE_ROOT, "chennai-aggregate")
    agg_prev = agg_store.latest_snapshot()
    agg_prev_faculty: Dict[str, Any] = {}
    if agg_prev:
        _, agg_prev_data = agg_prev
        agg_prev_faculty = agg_prev_data.get("faculty", {})

    agg_inc = diff_faculty(agg_prev_faculty, aggregate)
    if not dry_run:
        agg_version = utc_now_version()
        if agg_inc["added_count"] > 0 or agg_inc["modified_count"] > 0 or agg_inc["removed_count"] > 0:
            agg_store.save_increment(agg_version, agg_inc)
            agg_store.save_snapshot(agg_version, {
                "count": len(aggregate),
                "faculty": aggregate,
                "schools": school_types,
                "derived_from": agg_prev and agg_prev[0],
                "increment": agg_version,
            })
        else:
            agg_version = agg_prev and agg_prev[0] or agg_version
        print(f"\n[chennai] aggregate: {len(aggregate)} faculty across {len(school_types)} schools")

    return {
        "source": "chennai-vit-ac.in",
        "school_types": school_types,
        "per_school": per_school_result,
        "aggregate_count": len(aggregate),
        "total_requests": http.requests_made,
    }


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    no_images = "--no-images" in sys.argv
    result = run_chennai(dry_run=dry, resolve_images=not no_images)
    print("\n=== CHENNAI SUMMARY ===")
    print(json.dumps({
        "schools": result["school_types"],
        "aggregate_count": result["aggregate_count"],
        "total_requests": result["total_requests"],
        "per_school": result["per_school"],
    }, indent=2))
