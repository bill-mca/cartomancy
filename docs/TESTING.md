# Local testing & the auth workaround

## The problem

The plugin (inherited from upstream) authenticates against a **proprietary
backend** at `https://terra-lab.ai`:

- A user signs in via a browser **pairing** flow (the plugin mints a code, opens
  `/connect?code=…`, and polls `/api/plugin/pair/poll` until an activation key is
  bound), **or** pastes an **activation key** of the form `tl_<32 hex>`.
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

## The hook that makes this easy

`TerraLabClient` already supports a **base-URL override**, so no code change is
needed to redirect every API call away from `terra-lab.ai`:

`src/api/terralab_client.py`
```python
def __init__(self, base_url=None, env_vars=None):
    if base_url is None:
        if env_vars and env_vars.get("TERRALAB_BASE_URL"):
            base_url = env_vars["TERRALAB_BASE_URL"]      # 1) env var
        else:
            base_url = self._read_base_url()              # 2) .env.local
    self.base_url = base_url.rstrip("/")                  # 3) default terra-lab.ai
```

Set it one of two ways:

- **Environment variable:** `TERRALAB_BASE_URL=http://127.0.0.1:8787`
- **`.env.local`** in the plugin root (git-ignored):
  ```
  TERRALAB_BASE_URL=http://127.0.0.1:8787
  ```

Point that at a local mock and the whole plugin talks to it instead.

## Two-tier workaround

### Tier 1 — Get past auth with a dev key + a one-endpoint mock

The activation-key format check is `^tl_[0-9a-f]{32}$`
(`src/core/auth/activation_manager.py`). A dev key such as
`tl_00000000000000000000000000000000` passes the format gate, so you can use the
**manual key entry** path and skip the browser pairing dance entirely.

Auth then hinges on a **single endpoint**, `GET /api/plugin/usage`. Return a
non-error quota payload and the plugin treats you as signed in with credits:

```json
{
  "product_id": "ai-edit",
  "images_used": 0,
  "images_limit": 9999,
  "is_free_tier": false
}
```

`AuthManager.check_can_generate()` allows generation whenever `images_used <
images_limit` and there is no `"error"` key, so this payload unlocks the UI.

### Tier 2 — A minimal mock backend (`mockserver/`, to be added)

A tiny local server (stdlib `http.server` or FastAPI) that implements the
contract below with canned responses, and returns a small valid GeoTIFF for
generation. This is intentionally the seed of the future Cloudflare Worker, so
keep the routes and JSON shapes identical to what the Worker will serve.

**Endpoints the plugin calls** (paths confirmed from
`src/api/terralab_client.py`):

| Method & path | Purpose | Minimum mock behaviour |
|---|---|---|
| `GET /api/plugin/usage` | **auth + quota** | return the JSON above (no `error`) |
| `GET /api/plugin/config?product=ai-edit` | server config | `{}` or canned config (plugin has a fallback) |
| `GET /api/plugin/bootstrap` | startup config | `{}` (has a fallback) |
| `GET /api/plugin/account` | account info | canned `{}` |
| `GET /api/plugin/pair/poll?code=…` | browser sign-in poll | `{"status":"ready","activation_key":"tl_00…00"}` to auto-pair, or skip and use manual key |
| `POST /api/plugin/pair/cancel` | cancel pairing | `204` |
| `POST /api/plugin/track` | telemetry | `204` / `{}` |
| `GET /api/plugin/history`, `GET/POST /api/plugin/favorites`, `…/favorites/delete` | history & favourites | `[]` / `{}` |
| `POST /api/ai-edit/generate` | **submit generation** | return a `request_id` |
| `GET /api/ai-edit/generate/status?request_id=…` | poll generation | return `succeeded` + a result URL to a local GeoTIFF |
| `POST /api/ai-edit/upload-url` | large-image upload | return a local upload URL + token |
| `POST /api/ai-edit/generate/cancel`, `…/generate/refund` | cancel / refund | `{}` |
| `GET /api/ai-edit/history`, `POST /api/ai-edit/history/favorite` | gen history | `[]` / `{}` |
| `GET /api/ai-edit/export-config` | export config | canned `{}` |

> The exact response shapes for the **generate** flow should be read back from
> the parsing code in `terralab_client.py` / `src/workers/generation_worker.py`
> before finalising the mock, so the plugin's result handling is satisfied. The
> **auth** path (above) is fully specified here.

## Recommended dev recipe (once `mockserver/` lands)

1. `python mockserver/app.py` (serves on `127.0.0.1:8787`).
2. Create `.env.local` with `TERRALAB_BASE_URL=http://127.0.0.1:8787`.
3. Launch QGIS, enable the plugin, paste dev key `tl_00000000000000000000000000000000`.
4. The plugin validates against the mock `/usage`, shows credits, and is ready.

## Optional future: an explicit offline/dev flag

If we want to skip even the mock for pure UI work, we can add a guarded
`CARTOMANCY_DEV_MODE` flag that makes `AuthManager` report "authenticated, ample
credits" and short-circuits validation. Keep it **off by default** and clearly
fenced so it can never ship enabled. The mock-backend route above is preferred
because it also exercises the real network/result-handling code paths.
