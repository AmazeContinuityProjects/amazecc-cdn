# Faculty Sync (directorycc + Chennai VIT)

Robust, incremental faculty synchronization with **versioned snapshots**, **append-only change increments**, and **point-in-time restore**.

## What it does

| Source | How | What's captured |
|--------|-----|-----------------|
| **directorycc.vit.ac.in** | Scans `/api/faculty/<id>` over an expanding ID window; re-verifies known IDs each run | Full API record per faculty (name, designation, email, education, publications, researchArea, awards, phd, patents, base64 image, …) |
| **chennai.vit.ac.in** | Discovers school post types via `/wp-json/wp/v2/types`, paginates each school's faculty posts, resolves `featured_media` → image URL | Post id, slug, name, emp_id (from `_links_to`), featured media id, image URL, per school |

## State layout

```
scripts/faculty_sync/state/
  directorycc/
    snapshots/   <utc_version>.json     # full point-in-time export
    increments/  <utc_version>.inc.json # added / modified / removed since parent
    checkpoint.json                     # last-run cursor + valid/invalid id lists
  chennai/
    schools/<school_type>/
      snapshots/   <utc_version>.json
      increments/  <utc_version>.inc.json
      checkpoint.json
    snapshots/   <utc_version>.json     # aggregate across all schools
    increments/  <utc_version>.inc.json
    checkpoint.json
```

## Versioning

Every run writes a UTC timestamp version `YYYYMMDD_HHMMSS`. Increments are append-only and never overwritten, so you always have full history.

## Point-in-time restore

Reconstruct the faculty map as of any historical version:

```bash
# List versions for directorycc
python scripts/faculty_sync/restore_cli.py list directorycc

# List versions for a specific chennai school
python scripts/faculty_sync/restore_cli.py list chennai:sbst

# Restore directorycc to a version and export JSON
python scripts/faculty_sync/restore_cli.py restore directorycc 20261116_103000 --out restored_dircc.json

# Restore chennai aggregate to a version
python scripts/faculty_sync/restore_cli.py restore chennai 20261116_103000 --out restored_chennai.json
```

Restore algorithm: load the latest snapshot at-or-before the target, then replay every increment with version <= target in order (apply added, apply modified, drop removed).

## Running a sync

```bash
# Dry run first (compute changes, write nothing)
python scripts/faculty_sync/run_all.py --dry-run

# Full sync (both sources)
python scripts/faculty_sync/run_all.py

# Only directorycc
python scripts/faculty_sync/run_all.py --source directorycc

# Only chennai, skip media image resolution (faster)
python scripts/faculty_sync/run_all.py --source chennai --no-images
```

## Robustness features

- **Exponential backoff + jitter** on HTTP retries (429/5xx retried; 4xx fail fast)
- **Atomic writes** (temp file + `os.replace`) so state is never half-written
- **Checkpointing** tracks last run, valid/invalid ID lists, and run history (bounded to 200 entries)
- **Re-verification**: every run re-fetches all previously-valid faculty to catch modifications and removals, not just additions
- **Probe window**: scans `[min_id - buffer, max_id + buffer]` to detect newly-added IDs
- **Invalid-ID re-check**: a sample of previously-404 IDs are re-checked each run in case they became valid again
- **Concurrency caps** to avoid overwhelming origins

## Config

`scripts/faculty_sync/config.json` — tune probe buffer, concurrency, timeouts, retry policy, and which chennai post types to skip.

## Output

Run summary is printed to stdout. The source of truth is the versioned JSON under `state/`. Increment JSON files are small (only the diff), snapshots contain the full faculty map.
