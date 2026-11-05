/**
 * Cloudflare Worker CDN for AmazeCC.
 *
 * Serves as the unified chokepoint for all faculty images:
 *   GET /faculty/:facId.webp  (or /faculty/:facId)
 *
 * Features:
 *   - Unified Employee ID routing: /faculty/{facId}.webp
 *   - Quality resolution cascade:
 *       1. Vitfac Supabase Storage (clean WebP)
 *       2. VIT Chennai WordPress Media (via roster mediaMap & wp-media)
 *       3. Directorycc base64 profile photo (decoded to binary JPEG)
 *   - Permanent Storage:
 *       - Stored permanently in Cloudflare KV (FACULTY_IMAGES)
 *       - Cached across Cloudflare Edge (caches.default) with 1-year immutable headers
 *       - Once resolved, origin servers are NEVER touched again for that faculty member!
 *   - Permissive CORS (*) so dossiers, canvases, and apps load images seamlessly
 *   - Backward compatible routes for /images/*, /storage/*, and /chennai/*
 */

import { MEDIA_MAP } from "./mediaMap";

interface Env {
  FACULTY_IMAGES: KVNamespace;
  SUPABASE_URL?: string;
  SUPABASE_STORAGE_PATH?: string;
  DEFAULT_BUCKET?: string;
  WP_BASE?: string;
  CACHE_WORKER_BASE?: string;
  EDGE_CACHE_TTL?: string;
  BROWSER_CACHE_TTL?: string;
  NOT_FOUND_CACHE_TTL?: string;
}

interface OriginTarget {
  url: string;
  originName: "supabase" | "chennai";
}

interface ImageMetadata {
  contentType: string;
  source: string;
  resolvedAt?: string;
}

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, Range, If-None-Match, If-Modified-Since",
  "Access-Control-Expose-Headers": "Content-Length, Content-Type, ETag, Last-Modified, X-AmazeCC-CDN, X-AmazeCC-Origin",
  "Access-Control-Max-Age": "86400",
};

const USER_AGENT = "AmazeCC-CDN/1.0 (+https://amazecc.com)";
const DEFAULT_SUPABASE_URL = "https://blfxhlmpvdhcpdwgrffr.supabase.co";
const DEFAULT_STORAGE_PATH = "/storage/v1/object/public";
const DEFAULT_BUCKET_NAME = "images";
const DEFAULT_WP_BASE = "https://chennai.vit.ac.in";
const DEFAULT_CACHE_WORKER_BASE = "https://cache.amazecc.com";

// 1 year permanent cache for resolved faculty photos (31536000 seconds)
const PERMANENT_CACHE_TTL = 365 * 24 * 60 * 60;
const DEFAULT_404_TTL = 15;

function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...CORS_HEADERS,
      ...headers,
    },
  });
}

/** Base64 to Uint8Array helper for decoding directorycc images */
function base64ToUint8Array(base64: string): Uint8Array {
  // Strip data URL prefix if present
  const raw = base64.replace(/^data:image\/[a-zA-Z]+;base64,/, "").trim();
  const binary = atob(raw);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/**
 * Resolves legacy/direct pass-through origin paths.
 */
function resolveOriginTarget(pathname: string, env: Env): OriginTarget | null {
  if (pathname.includes("..") || /%(?:2e|2f|5c)/i.test(pathname)) {
    return null;
  }

  const clean = pathname.replace(/\/+/g, "/").replace(/\/$/, "");
  const wpBase = (env.WP_BASE || DEFAULT_WP_BASE).replace(/\/+$/, "");
  const supabaseUrl = (env.SUPABASE_URL || DEFAULT_SUPABASE_URL).replace(/\/+$/, "");
  const storagePrefix = (env.SUPABASE_STORAGE_PATH || DEFAULT_STORAGE_PATH).replace(/\/+$/, "");
  const defaultBucket = env.DEFAULT_BUCKET || DEFAULT_BUCKET_NAME;

  // VIT Chennai paths
  if (clean.startsWith("/chennai/")) {
    const sub = clean.slice("/chennai".length);
    return { url: `${wpBase}${sub}`, originName: "chennai" };
  }
  if (clean.startsWith("/wp-content/")) {
    return { url: `${wpBase}${clean}`, originName: "chennai" };
  }
  if (clean.startsWith("/wp-json/")) {
    return { url: `${wpBase}${clean}`, originName: "chennai" };
  }
  const wpMediaMatch = clean.match(/^\/wp-media\/(\d+)$/);
  if (wpMediaMatch) {
    return { url: `${wpBase}/wp-json/wp/v2/media/${wpMediaMatch[1]}`, originName: "chennai" };
  }

  // Supabase Storage paths
  if (clean.startsWith("/storage/v1/object/public/")) {
    return { url: `${supabaseUrl}${clean}`, originName: "supabase" };
  }
  if (clean.startsWith("/storage/")) {
    return { url: `${supabaseUrl}${clean}`, originName: "supabase" };
  }

  const parts = clean.split("/").filter(Boolean);
  if (parts.length === 0) return null;

  if (parts.length === 1) {
    const filename = parts[0];
    if (/\.(?:webp|jpg|jpeg|png|svg|avif|gif|ico|pdf|json)$/i.test(filename)) {
      return { url: `${supabaseUrl}${storagePrefix}/${defaultBucket}/${filename}`, originName: "supabase" };
    }
    return null;
  }

  const bucket = parts[0];
  const rest = parts.slice(1).join("/");
  return { url: `${supabaseUrl}${storagePrefix}/${bucket}/${rest}`, originName: "supabase" };
}

/**
 * Cascade to fetch the highest quality image available for a faculty ID.
 * Hierarchy:
 *   1. Supabase Storage (clean curated WebP)
 *   2. VIT Chennai WordPress Media (WP photo via mediaMap)
 *   3. Directorycc base64 photo (via cache worker profile)
 */
async function resolveFacultyImage(
  empId: string,
  env: Env
): Promise<{ buffer: ArrayBuffer; contentType: string; source: string } | null> {
  const supabaseUrl = (env.SUPABASE_URL || DEFAULT_SUPABASE_URL).replace(/\/+$/, "");
  const cacheBase = (env.CACHE_WORKER_BASE || DEFAULT_CACHE_WORKER_BASE).replace(/\/+$/, "");

  // 1. Try Vitfac Supabase Storage
  try {
    const res = await fetch(`${supabaseUrl}/storage/v1/object/public/images/${empId}.webp`, {
      headers: { "User-Agent": USER_AGENT },
      signal: AbortSignal.timeout(3000),
    });
    if (res.ok) {
      const buffer = await res.arrayBuffer();
      if (buffer.byteLength > 100) {
        return { buffer, contentType: "image/webp", source: "supabase" };
      }
    }
  } catch {}

  // 2. Try VIT Chennai WordPress media via mediaMap
  const featuredMedia = MEDIA_MAP[empId];
  if (featuredMedia) {
    let src: string | null = null;
    try {
      // Query cached media metadata from cache.amazecc.com
      const metaRes = await fetch(`${cacheBase}/wp-media/${featuredMedia}`, {
        headers: { "User-Agent": USER_AGENT },
        signal: AbortSignal.timeout(3000),
      });
      if (metaRes.ok) {
        const meta: any = await metaRes.json();
        src = meta?.source_url || meta?.guid?.rendered || null;
      }
    } catch {}

    // Fall back to direct VIT Chennai WP endpoint if cache worker failed or timed out
    if (!src) {
      try {
        const wpBase = (env.WP_BASE || DEFAULT_WP_BASE).replace(/\/+$/, "");
        const metaRes = await fetch(`${wpBase}/wp-json/wp/v2/media/${featuredMedia}`, {
          headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
          signal: AbortSignal.timeout(6000),
        });
        if (metaRes.ok) {
          const meta: any = await metaRes.json();
          src = meta?.source_url || meta?.guid?.rendered || null;
        }
      } catch {}
    }

    if (src && typeof src === "string") {
      try {
        const imgRes = await fetch(src, {
          headers: { "User-Agent": USER_AGENT },
          signal: AbortSignal.timeout(6000),
        });
        if (imgRes.ok) {
          const buffer = await imgRes.arrayBuffer();
          if (buffer.byteLength > 100) {
            const ct = imgRes.headers.get("Content-Type") || "image/jpeg";
            return { buffer, contentType: ct, source: "chennai-wp" };
          }
        }
      } catch {}
    }
  }

  // 3. Try directorycc Base64 Profile Image via cache.amazecc.com
  try {
    const profRes = await fetch(`${cacheBase}/faculty/profile/${empId}`, {
      headers: { "User-Agent": USER_AGENT },
      signal: AbortSignal.timeout(3000),
    });
    if (profRes.ok) {
      const profJson: any = await profRes.json();
      const b64 = profJson?.data?.prof?.image || profJson?.data?.directory?.image;
      if (b64 && typeof b64 === "string" && b64.length > 100) {
        const uint8 = base64ToUint8Array(b64);
        return { buffer: uint8.buffer as ArrayBuffer, contentType: "image/jpeg", source: "directorycc" };
      }
    }
  } catch {}

  return null;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    // 1. CORS Preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: CORS_HEADERS,
      });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ error: "Method not allowed" }, 405, { Allow: "GET, HEAD, OPTIONS" });
    }

    const url = new URL(request.url);

    // 2. Health & Info Endpoints
    if (url.pathname === "/" || url.pathname === "/health") {
      return json({
        service: "amazecc-cdn",
        status: "healthy",
        domain: "cdn.amazecc.com",
        chokepoint: "/faculty/:empId.webp",
        origins: {
          supabase: env.SUPABASE_URL || DEFAULT_SUPABASE_URL,
          chennai: env.WP_BASE || DEFAULT_WP_BASE,
          cache: env.CACHE_WORKER_BASE || DEFAULT_CACHE_WORKER_BASE,
        },
        rosterEntriesMapped: Object.keys(MEDIA_MAP).length,
        timestamp: new Date().toISOString(),
      });
    }

    // 3. UNIFIED FACULTY IMAGE CHOKEPOINT: /faculty/:empId
    const facultyMatch = url.pathname.match(/^\/faculty\/([0-9a-zA-Z_-]+?)(?:\.(?:webp|jpg|jpeg|png))?$/i);
    if (facultyMatch) {
      const empId = facultyMatch[1];
      const cache = caches.default;
      // Canonical cache key: always /faculty/:empId.webp
      const canonicalUrl = new URL(`/faculty/${empId}.webp`, url.origin);
      const cacheKey = new Request(canonicalUrl.toString(), { method: "GET" });

      // Check Edge Cache (Fastest RAM/SSD tier) - only honor 200 OK
      const cached = await cache.match(cacheKey);
      if (cached && cached.status === 200) {
        const hitHeaders = new Headers(cached.headers);
        hitHeaders.set("X-AmazeCC-CDN", "HIT");
        for (const [k, v] of Object.entries(CORS_HEADERS)) {
          if (!hitHeaders.has(k)) hitHeaders.set(k, v);
        }
        if (request.method === "HEAD") {
          return new Response(null, { status: 200, headers: hitHeaders });
        }
        return new Response(cached.body, { status: 200, headers: hitHeaders });
      }

      // Check Persistent KV Store (FACULTY_IMAGES)
      const kvKey = `fac_${empId}`;
      if (env.FACULTY_IMAGES) {
        const { value: kvBuffer, metadata } = await env.FACULTY_IMAGES.getWithMetadata<ImageMetadata>(
          kvKey,
          "arrayBuffer"
        );
        if (kvBuffer) {
          const ct = metadata?.contentType || "image/webp";
          const src = metadata?.source || "kv";
          const resHeaders = new Headers({
            ...CORS_HEADERS,
            "Content-Type": ct,
            "Content-Length": String(kvBuffer.byteLength),
            "Cache-Control": `public, max-age=${PERMANENT_CACHE_TTL}, s-maxage=${PERMANENT_CACHE_TTL}, immutable`,
            "X-AmazeCC-CDN": "KV-HIT",
            "X-AmazeCC-Origin": src,
          });
          const kvResponse = new Response(kvBuffer, { status: 200, headers: resHeaders });
          // Prime Edge Cache so subsequent requests hit edge cache instantly
          ctx.waitUntil(cache.put(cacheKey, kvResponse.clone()));
          if (request.method === "HEAD") {
            return new Response(null, { status: 200, headers: resHeaders });
          }
          return kvResponse;
        }
      }

      // Not in KV -> Cascade through origins to find highest quality image
      const resolved = await resolveFacultyImage(empId, env);
      if (!resolved) {
        // Missing from all origins -> 60s negative cache
        const notFoundHeaders = {
          ...CORS_HEADERS,
          "Cache-Control": `public, max-age=${DEFAULT_404_TTL}, s-maxage=${DEFAULT_404_TTL}`,
          "X-AmazeCC-CDN": "MISS-404",
        };
        const notFoundRes = json(
          { error: "Faculty image not found in any origin", employeeId: empId },
          404,
          notFoundHeaders
        );
        ctx.waitUntil(cache.put(cacheKey, notFoundRes.clone()));
        if (request.method === "HEAD") {
          return new Response(null, { status: 404, headers: notFoundHeaders });
        }
        return notFoundRes;
      }

      // Found image! Permanently store in KV & Edge Cache
      const resHeaders = new Headers({
        ...CORS_HEADERS,
        "Content-Type": resolved.contentType,
        "Content-Length": String(resolved.buffer.byteLength),
        "Cache-Control": `public, max-age=${PERMANENT_CACHE_TTL}, s-maxage=${PERMANENT_CACHE_TTL}, immutable`,
        "X-AmazeCC-CDN": `MISS-RESOLVED-${resolved.source.toUpperCase()}`,
        "X-AmazeCC-Origin": resolved.source,
      });

      // Save permanently in KV
      if (env.FACULTY_IMAGES) {
        try {
          await env.FACULTY_IMAGES.put(kvKey, resolved.buffer.slice(0), {
            metadata: {
              contentType: resolved.contentType,
              source: resolved.source,
              resolvedAt: new Date().toISOString(),
            },
          });
        } catch (kvErr) {
          console.error("KV put failed:", kvErr);
        }
      }

      const freshResponse = new Response(resolved.buffer, { status: 200, headers: resHeaders });
      // Save permanently in Edge Cache
      ctx.waitUntil(cache.put(cacheKey, freshResponse.clone()));

      if (request.method === "HEAD") {
        return new Response(null, { status: 200, headers: resHeaders });
      }
      return freshResponse;
    }

    // 4. Backward-compatible Passthrough Routes (/images/*, /storage/*, /chennai/*)
    const target = resolveOriginTarget(url.pathname, env);
    if (!target) {
      return json({ error: "Invalid path or file format" }, 400);
    }

    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: "GET" });

    let cachedResponse = await cache.match(cacheKey);
    if (cachedResponse && cachedResponse.status === 200) {
      const hitHeaders = new Headers(cachedResponse.headers);
      hitHeaders.set("X-AmazeCC-CDN", "HIT");
      hitHeaders.set("X-AmazeCC-Origin", target.originName);
      for (const [k, v] of Object.entries(CORS_HEADERS)) {
        if (!hitHeaders.has(k)) hitHeaders.set(k, v);
      }
      if (request.method === "HEAD") {
        return new Response(null, { status: 200, headers: hitHeaders });
      }
      return new Response(cachedResponse.body, {
        status: 200,
        headers: hitHeaders,
      });
    }

    // Origin Fetch
    let originResponse: Response;
    try {
      originResponse = await fetch(target.url, {
        method: "GET",
        headers: {
          "User-Agent": USER_AGENT,
          "Accept": request.headers.get("Accept") || "image/*,*/*;q=0.8",
        },
        cf: {
          cacheEverything: true,
          cacheTtl: PERMANENT_CACHE_TTL,
        },
        signal: AbortSignal.timeout(5000),
      });
    } catch (err) {
      return json(
        { error: "Failed to connect to origin", origin: target.originName, details: String(err) },
        502
      );
    }

    if (originResponse.status === 404 || originResponse.status === 400) {
      const notFoundHeaders = {
        ...CORS_HEADERS,
        "Cache-Control": `public, max-age=${DEFAULT_404_TTL}, s-maxage=${DEFAULT_404_TTL}`,
        "X-AmazeCC-CDN": "MISS-404",
        "X-AmazeCC-Origin": target.originName,
      };
      const notFoundRes = json(
        { error: "Asset not found", path: url.pathname, origin: target.originName },
        404,
        notFoundHeaders
      );
      ctx.waitUntil(cache.put(cacheKey, notFoundRes.clone()));
      return notFoundRes;
    }

    if (!originResponse.ok) {
      return json(
        { error: `Origin returned HTTP ${originResponse.status}`, path: url.pathname, origin: target.originName },
        originResponse.status,
        CORS_HEADERS
      );
    }

    const responseHeaders = new Headers();
    for (const [k, v] of Object.entries(CORS_HEADERS)) {
      responseHeaders.set(k, v);
    }

    const contentType = originResponse.headers.get("Content-Type") || "image/jpeg";
    responseHeaders.set("Content-Type", contentType);
    responseHeaders.set(
      "Cache-Control",
      `public, max-age=${PERMANENT_CACHE_TTL}, s-maxage=${PERMANENT_CACHE_TTL}, immutable`
    );
    responseHeaders.set("X-AmazeCC-CDN", "MISS");
    responseHeaders.set("X-AmazeCC-Origin", target.originName);

    const contentLength = originResponse.headers.get("Content-Length");
    if (contentLength) responseHeaders.set("Content-Length", contentLength);
    const etag = originResponse.headers.get("ETag");
    if (etag) responseHeaders.set("ETag", etag);

    const finalResponse = new Response(originResponse.body, {
      status: 200,
      headers: responseHeaders,
    });

    ctx.waitUntil(cache.put(cacheKey, finalResponse.clone()));
    return finalResponse;
  },
};
