# Local testing & the auth workaround

## The problem

The plugin (inherited from upstream) authenticates against a **proprietary
backend** at `https://terra-lab.ai`:

- A user signs in via a browser **pairing** flow (the plugin mints a code
  client-side, opens `/connect?code=…`, and polls `/api/plugin/pair/poll` until
  an activation key is bound), **or** pastes an **activation key** of the form
  `tl_<32 hex>`.
- Every request carries `Authorization: Bearer <key>` and `X-Product-ID: ai-edit`.
- Before each generation the plugin calls `GET /api/plugin/usage`; the server
  returns a quota payload and enforces credits.

We are building **our own** backend (Cloudflare Workers — see
[`PLAN.md` §4](PLAN.md#4-phase-1--cloudflare-infrastructure)). Until it exists,
we need to exercise the plugin **without** the proprietary backend.

> **Ground rule:** we do **not** hit TerraLab's servers and we do **not** try to
> obtain or reuse their activation keys. The workaround stands up *our own* mock
> that mirrors the request/response contract — the very contract our Cloudflare
> Worker will implement. This keeps dev self-contained and is the seed of the
> real backend.

## The best part: no plugin code changes needed

Two seams already exist in the plugin, both driven by a git-ignored `.env.local`
in the plugin root (read at startup by `src/ui/plugin.py:_create_client`):

| Key | Effect | Where it's read |
|---|---|---|
| `TERRALAB_BASE_URL` | redirect **every** API call to another base URL | `TerraLabClient.__init__` |
| `SKIP_TRIAL_CHECK` | skip the credit/quota pre-flight before generating | `plugin.py` → `GenerationTask(skip_trial_check=…)` |
| `DEBUG` | plugin dev mode (extra logging, debug artifacts) | `plugin.py._create_client` |

So the whole workaround is **config + a local mock** — we never patch the client.

## What we built: `mockserver/`

[`mockserver/app.py`](../mockserver/app.py) is a **zero-dependency** (stdlib-only)
HTTP server that answers the endpoints the plugin calls with canned responses,
and returns a real, GDAL-readable PNG for generations. It is intentionally the
seed of the future Cloudflare Worker — same routes, same JSON shapes.

```
python mockserver/app.py            # serves http://127.0.0.1:8787
python mockserver/app.py --port 9000 --max-dim 2048
```

Smoke-tested by [`tests/test_mockserver.py`](../tests/test_mockserver.py)
(stdlib `unittest`, no QGIS needed):

```
python -m unittest tests.test_mockserver -v
```

### Endpoint contract (implemented)

| Method & path | Purpose | Mock behaviour |
|---|---|---|
| `GET /api/plugin/usage` | **auth + quota** | `{product_id, images_used:0, images_limit:9999, …}` (401 `NO_AUTH` if no `Authorization`) |
| `POST /api/ai-edit/generate` | **submit generation** | returns `{request_id, status:"completed", image_url}` → plugin's sync path skips polling |
| `GET /generated/<id>.png` | the result image | a real RGB PNG (passes `download_image`'s image-magic check) |
| `GET /api/ai-edit/generate/status` | poll (if ever used) | `{status:"completed", image_url}` |
| `POST /api/ai-edit/upload-url` + `PUT /mock-upload/<token>` | large-input upload path | signed-URL handshake + accept the PUT |
| `GET /api/plugin/pair/poll` | browser sign-in poll | `{status:"ready", activation_key: DEV_KEY}` (Connect works with no real browser) |
| `GET /connect` | the page the browser opens | friendly "pairing complete" HTML |
| `GET /api/plugin/config`, `/api/ai-edit/export-config` | server config | sensible canned config |
| `GET /api/plugin/bootstrap` | startup bundle | **404** (plugin falls back to the individual endpoints — documented behaviour) |
| `GET /api/plugin/account`, `/history`, `/favorites` | account & lists | minimal canned payloads |
| `POST …/generate/cancel`, `…/refund`, `…/track`, `…/favorites…`, `…/pair/cancel` | side effects | `{"status":"ok"}` |

## Recommended dev recipe

1. **Run the mock:**
   ```
   python mockserver/app.py
   ```
   It prints the dev activation key (`tl_000…000`).

2. **Point the plugin at it.** Copy the template and edit if needed:
   ```
   cp .env.local.example .env.local
   ```
   `.env.local` (in the installed plugin directory) should contain:
   ```
   TERRALAB_BASE_URL=http://127.0.0.1:8787
   SKIP_TRIAL_CHECK=true
   DEBUG=true
   ```

3. **Launch QGIS**, enable the plugin, and either:
   - paste the dev key `tl_00000000000000000000000000000000` (manual entry), or
   - click **Connect** (the mock auto-pairs and returns the dev key).

4. The plugin validates against the mock `/usage`, shows credits, and a
   generation runs through submit → download → GeoTIFF → layer on the map.

> **Tip:** `.env.local` must live in the directory QGIS loads the plugin from
> (your QGIS profile's `python/plugins/<plugin>/`), not only in this repo — unless
> you've symlinked the repo there for development.

## How this becomes the real backend

The mock's routes and JSON shapes are deliberately the contract the Cloudflare
Worker will serve (`PLAN.md` §4). When the Worker is ready, you only change
`TERRALAB_BASE_URL` to its URL — no plugin changes. The mock then remains useful
for offline/CI testing.

## Optional future: an explicit offline/dev flag

If we ever want to skip even the mock for pure UI work, we can add a guarded
`CARTOMANCY_DEV_MODE` flag that makes `AuthManager` report "authenticated, ample
credits" and short-circuits validation. Keep it **off by default** and clearly
fenced. The mock route above is preferred because it also exercises the real
network/result-handling code paths.
