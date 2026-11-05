# amazecc-cdn

High-performance Cloudflare Worker CDN for AmazeCC assets, Vitfac faculty photos, and VIT Chennai media, served from `cdn.amazecc.com`.

## Purpose & Architecture

AmazeCC displays faculty profile photos and media across several upstream sources:
1. **Cloudflare KV (`FACULTY_IMAGES`)**: Permanent, zero-origin storage tier for all faculty photos.
2. **Vitfac Supabase Storage**: Curated WebP faculty photos (`/storage/v1/object/public/...`).
3. **VIT Chennai WordPress Media**: On-premise WordPress media repository (`chennai.vit.ac.in`).
4. **directorycc**: Campus directory profiles and base64 avatars.

Direct browser requests to origin servers fail during course registration rushes due to egress bandwidth limits (Supabase HTTP 402), campus firewall rate limits, and incomplete TLS certificate chains on on-premise servers.

`cdn.amazecc.com` acts as the unified chokepoint and shield:
- **Zero-Origin KV Store**: Over 1,080 faculty photos stored permanently in Cloudflare KV (`fac_{empId}`).
- **Two-Tier Caching**:
  - **Edge Cache (`caches.default`)**: Cloudflare RAM/SSD edge caching across worldwide data centers.
  - **Persistent KV (`FACULTY_IMAGES`)**: Resilient KV store that primes the edge cache on cold starts.
- **Header Normalization & CORS**: Returns permissive CORS (`Access-Control-Allow-Origin: *`) and 1-year immutable caching (`Cache-Control: public, max-age=31536000, s-maxage=31536000, immutable`).
- **Negative Caching Guard**: Temporary 15-second TTL on misses to protect origins without causing sticky 404s.

---

## Endpoints

### 1. Unified Faculty Photos (Recommended)
- `GET /faculty/:empId.webp` or `GET /faculty/:empId`
  - Canonical chokepoint for all faculty images.
  - Automatically resolves from: Edge Cache &rarr; Cloudflare KV &rarr; Supabase &rarr; VIT Chennai WP Media &rarr; directorycc.
  - Resolved images are permanently saved to KV, ensuring origin servers are never contacted again for that faculty member.

### 2. Legacy / Passthrough Routes
- `GET /images/:empId.webp` — Vitfac Supabase image proxy
- `GET /storage/v1/object/public/:bucket/:path...` — Supabase Storage path
- `GET /chennai/wp-content/uploads/:path...` — VIT Chennai WordPress uploads proxy
- `GET /wp-content/uploads/:path...` — Direct upload path
- `GET /wp-media/:id` — WordPress media metadata lookup

### 3. Health & Diagnostic
- `GET /` or `GET /health` — Service status, bound origins, and mapped roster statistics

---

## Response Headers

| Header | Description |
| --- | --- |
| `X-AmazeCC-CDN` | `HIT` (Edge cache hit), `KV-HIT` (Served from KV), `MISS-RESOLVED-*`, `MISS-404` |
| `X-AmazeCC-Origin` | Upstream origin (`kv`, `chennai-wp`, `supabase`, `directorycc`) |
| `Cache-Control` | `public, max-age=31536000, s-maxage=31536000, immutable` |
| `Access-Control-Allow-Origin` | `*` |

---

## Scripts & Maintenance

### Faculty Photo Sync Script
To sync all faculty photos from the VIT Chennai roster into Cloudflare KV:
```bash
python3 scripts/sync_faculty_photos.py
```
This script downloads photos concurrently (bypassing missing intermediate SSL certs) and batch-uploads them directly to Cloudflare KV.

---

## Development & Deployment

### Prerequisites
- Node.js 18+
- Wrangler CLI authenticated (`npx wrangler login`)

### Typecheck
```bash
npm run typecheck
```

### Local Dev
```bash
npm run dev
```

### Deploy to Cloudflare
```bash
npm run deploy
```
