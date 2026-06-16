# Cartomancy backend (Cloudflare Worker) — M4

The real backend that replaces `mockserver/`. It serves the **same contract the
mock already pins**, so going live is just repointing the plugin's
`TERRALAB_BASE_URL` at the deployed Worker — no plugin changes.

> This is `docs/PLAN.md` §4's `workers/`, named **`backend/`** here to avoid
> confusion with `src/workers/` (the plugin's QgsTask jobs).

## Status

- ✅ **D1 schema** — [`migrations/0001_init.sql`](migrations/0001_init.sql),
  reconciled with the plugin's activation-key auth + pairing.
- ⏳ **Route handlers / services** — blocked on the **language decision** below.
- ⏳ **Cloudflare resources** — D1 / R2 / KV / secrets (human-provisioned; the
  Cloudflare MCP tools in this workspace can create them when we're ready).

## Open decision: implementation language

`PLAN.md` §4.3 specifies **Python + FastAPI**. Worth reconsidering at M4:
**TypeScript** has the more mature Cloudflare Workers runtime (first-class R2/D1
bindings, the Stripe SDK, image/streaming handling), whereas Python Workers are
newer and more constrained. The plugin stays Python regardless — this only
affects the Worker. **Recommendation: TypeScript.** Pending your call.

## Cloudflare bindings (wrangler)

| Binding | Service | Use |
|---|---|---|
| `DB` | D1 | users, activation keys, credits, pairing, generation log |
| `BUCKET` | R2 | generated GeoTIFFs; signed download URLs; 30-day lifecycle |
| `KV` | KV | usage cache / rate-limit state |
| `GEMINI_API_KEY` | secret | Google Gemini 2.5 Flash Image |
| `STRIPE_SECRET_KEY` | secret | subscription webhooks (later) |

## Auth model (how the plugin authenticates)

Every authenticated request carries `Authorization: Bearer tl_<32 hex>` and
`X-Product-ID: ai-edit`. The Worker: `sha256(key)` → look up `activation_keys`
→ resolve `user_id` → read `credits`. No username/password anywhere.

## Endpoint contract (must match `mockserver/app.py`)

The mock is the executable spec; the Worker must answer the same routes/shapes.
Source of truth for shapes: `mockserver/app.py` + `src/api/terralab_client.py`.

| Method & path | Worker responsibility |
|---|---|
| `GET /api/plugin/usage` | auth → return `{product_id, images_used, images_limit, is_free_tier}` |
| `GET /api/plugin/config?product=` | static-ish plugin config |
| `GET /api/ai-edit/export-config` | `{max_dimension, align, input_format, submit_timeouts_ms}` (drives input sizing) |
| `GET /api/plugin/pair/poll?code=` | pairing handoff → `{status, activation_key}` |
| `POST /api/plugin/pair/cancel` | retire a pairing code |
| `POST /api/ai-edit/generate` | **the money route** — see flow below |
| `GET /api/ai-edit/generate/status?request_id=` | poll job → `{status, image_url}` |
| `POST /api/ai-edit/upload-url` | presigned R2 PUT for large inputs |
| `POST /api/ai-edit/generate/cancel` · `/refund` | cancel / refund credits |
| `GET /api/plugin/account` · `/history` · `/api/ai-edit/history` | account + history |
| `POST /api/plugin/track` | telemetry sink |

## The `/generate` flow (PLAN §4.4, with credit safety)

```
POST /api/ai-edit/generate
  body: prompt, image (b64) | upload_token, resolution (1K|2K|4K),
        aspect_ratio, + geo context (bbox, crs, export_width/height,
        dpi, paper_w/h_mm from the print layout)

  1. Auth: Bearer key → user_id (401 if unknown/revoked)
  2. cost = credits for resolution (PLAN §6); 402 if balance < cost
  3. Deduct credits + insert generations row (status='pending')  ← deduct up front
  4. Call Gemini 2.5 Flash Image (image + prompt)
  5. Success: embed EU AI Act GeoTIFF tags (M5) → put to R2
     (generations/{user_id}/{id}.tif) → signed URL → row status='completed'
     → return { request_id, status:'completed', image_url, credits_remaining }
  6. Failure: refund credits → row status='failed' → 502
```

The plugin's `GenerationService` takes the `status:'completed' + image_url`
shortcut and skips polling, so a synchronous Worker response is fine; the
`/status` route exists for an async path if generation is moved to a queue.

## Going live

1. Provision D1 / R2 / KV; set secrets; apply `migrations/0001_init.sql`.
2. Deploy the Worker (`wrangler deploy`).
3. In the plugin's `.env.local`: `TERRALAB_BASE_URL=https://<worker-url>`.
   Nothing else changes. Keep `mockserver/` for offline/CI testing.
