# Faculty Master Constructor

Builds the master faculty catalogs that power `cache.amazecc.com` endpoints:

- `/faculty/combined` (aka `/faculty/all`) — compact combined catalog
- `/faculty/all-data` — complete catalog with ALL shapes merged
- `/faculty/profile/{id}` — combined vitfac + directorycc profile bundle

This directory contains:

1. **`sync/`** — a complete copy of `faculty_sync` that incrementally syncs
   faculty from `directorycc.vit.ac.in` (per-ID API scan) and
   `chennai.vit.ac.in` (WordPress REST across all 23 school post types).
   Every run produces versioned snapshots + append-only increments with
   point-in-time restore.

2. **`build/`** — build scripts that construct the combined JSON catalogs
   by merging:
   - **chennai roster** (school, wpId, slug, profileUrl, featuredMedia)
   - **directorycc profiles** (name, designation, email, education,
     publications, researchArea, awards, phd, patents, links, metrics, …)
   - **VITFAC review catalog** (designation, dept, courses, wPct, reviews,
     metrics, lore) from `cache.amazecc.com/faculty`
   - **Master registry** with active/inactive tracking and date stamps

   Images are NOT stored as base64 — they point to
   `https://cdn.amazecc.com/faculty/<empId>.webp` (already in the CDN KV).

3. **`kv/`** — Cloudflare KV upload script.

4. **`artifacts/`** — all generated JSONs copied here for manual download
   and upload if the automated KV push fails.

## Directory layout

```
scripts/faculty_master_constructor/
├── run.py                          # master entry point
├── config.json                     # tunable parameters
├── README.md                       # this file
├── .github/workflows/faculty-sync.yml   # GitHub Actions workflow
├── sync/                           # copy of faculty_sync
│   ├── run_all.py                  # sync orchestrator
│   ├── sync_directorycc.py         # directorycc per-ID scanner
│   ├── sync_chennai.py             # chennai WordPress school syncer
│   ├── restore_cli.py              # point-in-time restore CLI
│   ├── store.py                    # versioned snapshot/increment store
│   ├── http_client.py              # robust HTTP client
│   ├── config.json
│   ├── __init__.py
│   └── state/                      # synced state (snapshots + increments)
│       ├── directorycc/
│       │   ├── snapshots/
│       │   ├── increments/
│       │   └── checkpoint.json
│       └── chennai/
│           ├── snapshots/
│           ├── increments/
│           └── schools/<type>/
├── build/                          # catalog construction
│   ├── build_all.py                # orchestrates the full build (run_pipeline)
│   ├── build_vitfac_catalog.py     # fetches vitfac catalog from cache.amazecc.com
│   ├── build_master_registry.py    # active/inactive tracking + date stamps
│   ├── build_combined.py           # builds faculty_combined_v1.json
│   └── build_all_data.py           # builds faculty_all_data_v1.json
├── kv/                             # KV upload
│   └── upload_to_kv.py             # uploads JSONs to Cloudflare KV
├── state/                          # built catalog outputs
│   ├── vitfac_catalog_v1.json
│   ├── faculty_combined_v1.json
│   ├── faculty_all_data_v1.json
│   ├── master_registry.json        # active/inactive tracking registry
│   └── incremental_diff.json       # change log since last build
└── artifacts/                      # downloadable JSONs (copied from state/)
    ├── vitfac_catalog_v1.json
    ├── faculty_combined_v1.json
    ├── faculty_all_data_v1.json
    ├── master_registry.json
    └── incremental_diff.json
```

## Active / Inactive tracking

The master registry (`state/master_registry.json`) tracks every faculty
member ever seen, with these fields:

| Field | Meaning |
|-------|---------|
| `active` | `true` if found in the latest sync run, `false` otherwise |
| `added_at` | ISO timestamp when the faculty was first seen |
| `inactive_at` | ISO timestamp when the faculty was last marked inactive (`null` if active) |
| `last_seen_at` | ISO timestamp of the most recent sync run |
| `sources` | list of sources: `directorycc`, `chennai`, `vitfac` |

Logic per sync run:
- Faculty in the current directorycc/chennai/vitfac lists → `active=true`,
  `inactive_at=null` (reactivated if previously inactive)
- Faculty NOT in the current lists but previously known → `active=false`,
  `inactive_at=now` (only set the first time they go inactive)
- Brand new faculty → `active=true`, `added_at=now`

## KV keys produced (cache.amazecc.com, namespace `CACHE` = `3cf003b8421b4de0a8bc54776f1df337`)

| Key | Contents |
|-----|----------|
| `faculty_list_v1` | VITFAC review catalog |
| `faculty_combined_v1` | Combined compact catalog |
| `faculty_all_data_v1` | Complete all-data catalog |
| `master_registry_v1` | Master faculty registry (active/inactive) |
| `incremental_diff_v1` | Incremental change log |
| `directory_profile_<empId>_v1` | Individual directorycc profile bundles (optional) |

## GitHub Actions

A workflow is at `.github/workflows/faculty-sync.yml`. It runs on a daily
schedule (02:00 UTC) and can be triggered manually via `workflow_dispatch`.

Required repository secrets:
- `CLOUDFLARE_API_TOKEN` — Cloudflare API token with Workers KV write access
- `CLOUDFLARE_ACCOUNT_ID` — your Cloudflare account ID

The workflow:
1. Syncs directorycc + chennai
2. Builds the master registry + combined + all-data catalogs
3. Stages all JSONs to `artifacts/`
4. Optionally uploads to KV (only if `upload_to_kv=true` input is set)
5. Uploads `artifacts/` and `sync/state/` as downloadable workflow artifacts
   (so you can manually upload to KV if the automated push fails)

## Usage

```bash
# Full pipeline: sync + build (no KV upload)
python scripts/faculty_master_constructor/run.py run

# Full pipeline with KV upload
python scripts/faculty_master_constructor/run.py run --upload-to-kv

# Just build from existing synced state (no re-sync)
python scripts/faculty_master_constructor/run.py build

# Dry-run sync (see what would change, no writes)
python scripts/faculty_master_constructor/run.py --dry-run-sync

# Upload built JSONs to KV (after a successful build)
python scripts/faculty_master_constructor/run.py run --upload-to-kv

# Point-in-time restore
python scripts/faculty_master_constructor/run.py restore directorycc 20261116_122348 --out restored.json
python scripts/faculty_master_constructor/run.py restore chennai 20261116_122446 --out restored_chennai.json

# List available versions
python scripts/faculty_master_constructor/run.py list directorycc
python scripts/faculty_master_constructor/run.py list chennai
```

## Manual KV upload (if automated upload fails)

All generated JSONs are copied to `scripts/faculty_master_constructor/artifacts/`.
Download them from the GitHub Actions workflow artifacts, then upload manually:

```bash
npx wrangler kv key put faculty_list_v1 --path artifacts/vitfac_catalog_v1.json --namespace-id 3cf003b8421b4de0a8bc54776f1df337 --remote
npx wrangler kv key put faculty_combined_v1 --path artifacts/faculty_combined_v1.json --namespace-id 3cf003b8421b4de0a8bc54776f1df337 --remote
npx wrangler kv key put faculty_all_data_v1 --path artifacts/faculty_all_data_v1.json --namespace-id 3cf003b8421b4de0a8bc54776f1df337 --remote
npx wrangler kv key put master_registry_v1 --path artifacts/master_registry.json --namespace-id 3cf003b8421b4de0a8bc54776f1df337 --remote
npx wrangler kv key put incremental_diff_v1 --path artifacts/incremental_diff.json --namespace-id 3cf003b8421b4de0a8bc54776f1df337 --remote
```

## Merge precedence (combined / all-data)

For each faculty member, fields are resolved in this order:

| Field | Source priority |
|-------|----------------|
| name | directorycc → chennai → vitfac |
| designation | directorycc → vitfac |
| dept | vitfac → chennai school |
| email / intercom / phd | directorycc |
| researchArea / publications / awards / patents / … | directorycc (full dossier) |
| wPct / reviews / metrics / lore / courses | vitfac |
| active / added_at / inactive_at | master registry |
| imageUrl | `https://cdn.amazecc.com/faculty/<empId>.webp` |
