"""
faculty_sync.sync_directorycc
=============================

Sync faculty from https://directorycc.vit.ac.in/api/faculty/<id>

Strategy per run:
  1. Load previous snapshot (emp_id -> full record) if it exists.
  2. Re-verify every previously-valid ID by re-fetching it.
     - Same data  -> unchanged
     - Different  -> modified
     - 404/gone   -> removed
  3. Probe an expanding window around the known ID range
     [min_id - PROBE_BUFFER, max_id + PROBE_BUFFER] (skipping IDs we've
     previously confirmed invalid) to detect new additions.
  4. Re-check a sample of previously-invalid IDs to catch ones that
     became valid again.
  5. Persist an increment (added/modified/removed) and, if anything
     changed, a new full snapshot. Update checkpoint.

All state lives under scripts/faculty_sync/state/directorycc/.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow running as a script: python scripts/faculty_sync/sync_directorycc.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import VersionedStore, diff_faculty, utc_now_iso, utc_now_version  # noqa: E402
from http_client import RobustHttp  # noqa: E402

BASE_URL = "https://directorycc.vit.ac.in/api/faculty/"
PROBE_BUFFER = 600          # probe this far beyond known min/max for new IDs
RECHECK_INVALID_SAMPLE = 200  # how many previously-invalid IDs to re-check per run
CONCURRENCY = 15

STATE_DIR = Path(__file__).resolve().parent / "state" / "directorycc"


def fetch_one(http: RobustHttp, emp_id: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[int]]:
    """
    Fetch a single directorycc faculty record.
    Returns (emp_id, api_dict_or_None, status_code).
    """
    url = f"{BASE_URL}{emp_id}"
    try:
        status, headers, data = http.get_json(url)
        if status == 200 and isinstance(data, dict) and "employeeId" in data:
            return emp_id, data, status
        # 200 but not a faculty record (e.g. error message)
        return emp_id, None, status
    except urllib.error.HTTPError as e:
        return emp_id, None, e.code
    except Exception:
        return emp_id, None, None


# imported lazily to keep the module importable
import urllib.error  # noqa: E402


def run_directorycc(dry_run: bool = False) -> Dict[str, Any]:
    http = RobustHttp()
    store = VersionedStore(STATE_DIR, "directorycc")
    checkpoint = store.load_checkpoint()

    prev = store.latest_snapshot()
    prev_faculty: Dict[str, Any] = {}
    prev_version: Optional[str] = None
    if prev:
        prev_version, prev_data = prev
        prev_faculty = prev_data.get("faculty", {})

    known_valid = set(prev_faculty.keys())
    known_invalid: List[str] = checkpoint.get("invalid_ids", [])

    print(f"[directorycc] previous snapshot: {prev_version or 'none'} ({len(prev_faculty)} faculty)")
    print(f"[directorycc] known invalid IDs cached: {len(known_invalid)}")

    # ---- Step 1: re-verify all known valid IDs --------------------- #
    print(f"[directorycc] re-verifying {len(known_valid)} known faculty...")
    updated: Dict[str, Any] = {}
    removed: List[str] = []
    modified: Dict[str, Any] = {}
    requests = 0
    errors: List[str] = []

    def reverify(emp_id: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[int], bool]:
        eid, data, status = fetch_one(http, emp_id)
        return eid, data, status, data is not None

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = [ex.submit(reverify, eid) for eid in sorted(known_valid)]
        done = 0
        for fut in as_completed(futs):
            eid, data, status, is_valid = fut.result()
            requests += 1
            done += 1
            if done % 100 == 0 or done == len(known_valid):
                print(f"   reverify {done}/{len(known_valid)}")
            if not is_valid:
                # gone -> removed
                removed.append(eid)
                old = prev_faculty.get(eid, {})
                modified[eid] = {"old": old, "new": None}
            else:
                new_rec = {"emp_id": eid, "api_response": data, "fetched_at": utc_now_iso()}
                old = prev_faculty.get(eid)
                if old is None or old != new_rec:
                    modified[eid] = {"old": old, "new": new_rec}
                updated[eid] = new_rec

    print(f"[directorycc] reverify done: {len(updated)} valid, {len(removed)} removed")

    # ---- Step 2: probe window for new additions -------------------- #
    all_known = known_valid | set(known_invalid)
    probe_ids: List[str] = []

    if known_valid:
        nums = [int(i) for i in known_valid if i.isdigit()]
        lo = max(1, min(nums) - PROBE_BUFFER)
        hi = max(nums) + PROBE_BUFFER
    else:
        lo, hi = 50000, 59999

    for n in range(lo, hi + 1):
        sid = str(n)
        if sid not in all_known:
            probe_ids.append(sid)

    # also probe a bit below 50000 and above max known in case range shifted
    extra_low = [str(n) for n in range(max(1, lo - PROBE_BUFFER), lo) if str(n) not in all_known][:PROBE_BUFFER]
    extra_high = [str(n) for n in range(hi + 1, hi + 1 + PROBE_BUFFER) if str(n) not in all_known][:PROBE_BUFFER]
    probe_ids = extra_low + probe_ids + extra_high

    print(f"[directorycc] probing {len(probe_ids)} candidate IDs for new faculty ({lo}-{hi} + buffers)...")

    newly_added: Dict[str, Any] = []
    newly_valid_ids: List[str] = []
    probe_errors = 0
    probed = 0

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_one, http, pid): pid for pid in probe_ids}
        for fut in as_completed(futs):
            pid = futs[fut]
            eid, data, status = fut.result()
            probed += 1
            if probed % 200 == 0 or probed == len(probe_ids):
                print(f"   probe {probed}/{len(probe_ids)}")
            if data is not None:
                newly_valid_ids.append(eid)
                newly_added.append({"emp_id": eid, "api_response": data, "fetched_at": utc_now_iso()})
            elif status == 404:
                known_invalid.append(eid)  # cache as invalid
            else:
                probe_errors += 1
                errors.append(f"probe {eid}: status={status}")

    print(f"[directorycc] probe done: {len(newly_valid_ids)} new valid, {probe_errors} errors")

    # ---- Step 3: re-check a sample of previously-invalid IDs ------- #
    invalid_to_check = known_invalid[:RECHECK_INVALID_SAMPLE]
    invalid_revalidated = 0
    if invalid_to_check:
        print(f"[directorycc] re-checking {len(invalid_to_check)} previously-invalid IDs...")
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futs = {ex.submit(fetch_one, http, iid): iid for iid in invalid_to_check}
            for fut in as_completed(futs):
                iid = futs[fut]
                eid, data, status = fut.result()
                if data is not None:
                    newly_valid_ids.append(eid)
                    newly_added.append({"emp_id": eid, "api_response": data, "fetched_at": utc_now_iso()})
                    invalid_revalidated += 1
                    # remove from invalid list
                    if eid in known_invalid:
                        known_invalid.remove(eid)

    # ---- Step 4: assemble new faculty map -------------------------- #
    new_faculty: Dict[str, Any] = {}
    for eid in updated:
        new_faculty[eid] = updated[eid]
    for rec in newly_added:
        new_faculty[rec["emp_id"]] = rec

    # removed IDs are simply not copied over

    # ---- Step 5: compute increment --------------------------------- #
    inc = diff_faculty(prev_faculty, new_faculty)
    inc["scanned_ids"] = len(known_valid) + probed
    inc["requests_made"] = http.requests_made
    inc["invalid_revalidated"] = invalid_revalidated
    inc["errors"] = errors[:50]
    inc["probe_range"] = {"lo": lo, "hi": hi}

    changed = inc["added_count"] > 0 or inc["modified_count"] > 0 or inc["removed_count"] > 0

    print(f"[directorycc] increment: +{inc['added_count']} ~{inc['modified_count']} -{inc['removed_count']}")

    # ---- Step 6: persist ------------------------------------------ #
    if not dry_run:
        new_version = utc_now_version()
        if changed:
            # save increment
            inc_version = new_version
            store.save_increment(inc_version, inc)
            # save new full snapshot
            snapshot_data = {
                "count": len(new_faculty),
                "faculty": new_faculty,
                "derived_from": prev_version,
                "increment": inc_version,
            }
            store.save_snapshot(new_version, snapshot_data)
            print(f"[directorycc] saved snapshot v{new_version} ({len(new_faculty)} faculty) + increment v{inc_version}")
        else:
            new_version = prev_version or new_version
            print(f"[directorycc] no changes; keeping snapshot {new_version}")

        # update checkpoint
        cp = checkpoint
        cp["last_run"] = utc_now_iso()
        cp["last_version"] = new_version
        cp["count"] = len(new_faculty)
        cp["valid_ids"] = sorted(new_faculty.keys(), key=lambda x: int(x) if x.isdigit() else x)
        cp["invalid_ids"] = sorted(set(known_invalid), key=lambda x: int(x) if x.isdigit() else x)
        cp["runs"].append({
            "version": new_version,
            "timestamp": utc_now_iso(),
            "added": inc["added_count"],
            "modified": inc["modified_count"],
            "removed": inc["removed_count"],
            "requests": http.requests_made,
            "changed": changed,
        })
        # keep runs list bounded
        if len(cp["runs"]) > 200:
            cp["runs"] = cp["runs"][-200:]
        store.save_checkpoint(cp)
    else:
        print("[directorycc] dry-run: nothing written")

    return {
        "source": "directorycc",
        "changed": changed,
        "added": inc["added"],
        "modified": inc["modified"],
        "removed": inc["removed"],
        "count_before": len(prev_faculty),
        "count_after": len(new_faculty),
        "version": new_version if not dry_run else None,
        "requests": http.requests_made,
    }


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    result = run_directorycc(dry_run=dry)
    print(json.dumps({
        "changed": result["changed"],
        "count_before": result["count_before"],
        "count_after": result["count_after"],
        "added_count": len(result["added"]),
        "modified_count": len(result["modified"]),
        "removed_count": len(result["removed"]),
        "requests": result["requests"],
        "version": result["version"],
    }, indent=2))
