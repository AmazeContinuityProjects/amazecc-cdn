#!/usr/bin/env python3
"""
Sync all faculty photos from VIT Chennai WordPress to Cloudflare KV.
Uses httpx.AsyncClient with SSL verification disabled due to VIT Chennai's
missing intermediate CA certificate.
"""

import asyncio
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import httpx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CDN_DIR = os.path.dirname(SCRIPT_DIR)
MEDIA_MAP_PATH = os.path.join(CDN_DIR, "src", "mediaMap.ts")
CONCURRENCY = 12
BATCH_SIZE = 80

def get_namespace_id():
    if "KV_NAMESPACE_ID" in os.environ:
        return os.environ["KV_NAMESPACE_ID"]
    toml_path = os.path.join(CDN_DIR, "wrangler.toml")
    if os.path.exists(toml_path):
        with open(toml_path, "r", encoding="utf-8") as f:
            m = re.search(r'binding\s*=\s*"FACULTY_IMAGES"\s*[\r\n]+\s*id\s*=\s*"([a-f0-9]{32})"', f.read())
            if m:
                return m.group(1)
    raise RuntimeError("KV_NAMESPACE_ID not found. Set KV_NAMESPACE_ID in environment or configure wrangler.toml.")

NAMESPACE_ID = get_namespace_id()

def get_existing_keys():
    print("[1/4] Querying existing keys from Cloudflare KV...")
    cmd = ["npx", "wrangler", "kv", "key", "list", f"--namespace-id={NAMESPACE_ID}"]
    result = subprocess.run(cmd, cwd=CDN_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Warning: Failed to list KV keys: {result.stderr}")
        return set()
    
    stdout = result.stdout
    json_start = stdout.find("[")
    if json_start == -1:
        print("Warning: No JSON list found in output.")
        return set()
    
    try:
        keys_data = json.loads(stdout[json_start:])
        existing = {item["name"] for item in keys_data if "name" in item}
        print(f"Found {len(existing)} existing keys in KV.")
        return existing
    except Exception as e:
        print(f"Failed to parse keys JSON: {e}")
        return set()

def load_media_map():
    print("[2/4] Loading MEDIA_MAP from source...")
    with open(MEDIA_MAP_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    
    entries = re.findall(r'\"([0-9a-zA-Z_-]+)\":\s*(\d+)', content)
    media_map = {emp_id: int(mid) for emp_id, mid in entries}
    print(f"Loaded {len(media_map)} faculty entries from MEDIA_MAP.")
    return media_map

async def fetch_faculty_image(client, sem, emp_id, media_id):
    async with sem:
        meta_url = f"https://chennai.vit.ac.in/wp-json/wp/v2/media/{media_id}"
        src_url = None
        for attempt in range(2):
            try:
                res = await client.get(meta_url, timeout=7.0)
                if res.status_code == 200:
                    data = res.json()
                    src_url = data.get("source_url") or (data.get("guid") or {}).get("rendered")
                    break
                elif res.status_code == 404:
                    return None
            except Exception:
                await asyncio.sleep(0.5)
        
        if not src_url:
            return None
        
        for attempt in range(2):
            try:
                img_res = await client.get(src_url, timeout=8.0)
                if img_res.status_code == 200 and len(img_res.content) > 100:
                    ct = img_res.headers.get("content-type", "image/jpeg")
                    if "webp" in src_url.lower():
                        ct = "image/webp"
                    elif "png" in src_url.lower():
                        ct = "image/png"
                    elif "jpg" in src_url.lower() or "jpeg" in src_url.lower():
                        ct = "image/jpeg"
                    
                    b64_val = base64.b64encode(img_res.content).decode("ascii")
                    return {
                        "key": f"fac_{emp_id}",
                        "value": b64_val,
                        "base64": True,
                        "metadata": {
                            "contentType": ct,
                            "source": "chennai-wp",
                            "resolvedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        }
                    }
            except Exception:
                await asyncio.sleep(0.5)
        
        return None

def upload_batch(batch, batch_num):
    tmp_path = f"/tmp/kv_batch_{batch_num}.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(batch, f)
    
    cmd = [
        "npx", "wrangler", "kv:bulk", "put",
        f"--namespace-id={NAMESPACE_ID}",
        tmp_path
    ]
    res = subprocess.run(cmd, cwd=CDN_DIR, capture_output=True, text=True)
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    
    if res.returncode == 0:
        return True
    else:
        print(f"Error uploading batch {batch_num}: {res.stderr}")
        return False

async def main():
    existing_keys = get_existing_keys()
    media_map = load_media_map()
    
    to_fetch = [(emp_id, mid) for emp_id, mid in media_map.items() if f"fac_{emp_id}" not in existing_keys]
    print(f"[3/4] Remaining faculty photos to sync: {len(to_fetch)} (out of {len(media_map)})")
    
    if not to_fetch:
        print("All faculty photos are already in KV! Nothing to do.")
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=30)
    
    current_batch = []
    batch_counter = 0
    total_synced = 0
    total_failed = 0
    processed = 0

    async with httpx.AsyncClient(verify=False, limits=limits, follow_redirects=True) as client:
        tasks = [fetch_faculty_image(client, sem, emp_id, mid) for emp_id, mid in to_fetch]
        
        for future in asyncio.as_completed(tasks):
            result = await future
            processed += 1
            if result:
                current_batch.append(result)
                total_synced += 1
            else:
                total_failed += 1
            
            if len(current_batch) >= BATCH_SIZE:
                batch_counter += 1
                upload_batch(current_batch, batch_counter)
                print(f"  --> Uploaded batch #{batch_counter} ({len(current_batch)} photos). Total processed: {processed}/{len(to_fetch)} (synced: {total_synced}, failed/missing: {total_failed})")
                current_batch = []
            elif processed % 25 == 0:
                print(f"  Progress: {processed}/{len(to_fetch)} processed ({total_synced} downloaded so far)...")
        
        if current_batch:
            batch_counter += 1
            upload_batch(current_batch, batch_counter)
            print(f"  --> Uploaded final batch #{batch_counter} ({len(current_batch)} photos).")

    print("\n[4/4] Sync complete!")
    print(f"Total faculty processed: {processed}")
    print(f"Total synced to KV: {total_synced}")
    print(f"Missing from origin: {total_failed}")

if __name__ == "__main__":
    asyncio.run(main())
