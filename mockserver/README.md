# Cartomancy mock backend

A **zero-dependency, stdlib-only** stand-in for the proprietary TerraLab API, so
the QGIS plugin runs end to end on your machine without touching `terra-lab.ai`.

It is the **seed of the future Cloudflare Worker** (`docs/PLAN.md` §4): the routes
and JSON shapes here are the contract the Worker will serve, so swapping backends
later is just a URL change.

## Run

```
python mockserver/app.py                  # http://127.0.0.1:8787
python mockserver/app.py --port 9000      # custom port
python mockserver/app.py --max-dim 2048   # larger result images (slower)
```

On startup it prints the dev activation key (`tl_000…000`).

## Wire the plugin to it

Create `.env.local` in the plugin root (see `../.env.local.example`):

```
TERRALAB_BASE_URL=http://127.0.0.1:8787
SKIP_TRIAL_CHECK=true
DEBUG=true
```

Then sign in with the dev key, or click **Connect** (the mock auto-pairs).
Full walkthrough: [`../docs/TESTING.md`](../docs/TESTING.md).

## Test

```
python -m unittest tests.test_mockserver -v
```

## Scope & caveats

- **Dev only.** No real auth (any well-formed key works), no persistence
  (in-memory, reset on restart), canned responses.
- Generated results are synthetic gradient PNGs, capped at `--max-dim` per side
  to keep pure-Python encoding fast. They are valid georeferenced rasters once
  the plugin writes them (extent/CRS come from the plugin side), but the pixels
  are not a real AI edit.
- Implements the endpoints the plugin actually calls; see the table in
  `docs/TESTING.md`. Add routes here as new plugin features need them, keeping
  shapes aligned with the planned Worker.
