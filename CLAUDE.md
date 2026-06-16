# CLAUDE.md — orientation for AI coding sessions & contributors

## What this project is

**Cartomancy** (working codename) is a QGIS plugin for AI image editing that
lives in the **Print Layout / Print Composer** window. Output resolution is
derived from the layout's **paper size × export DPI**, so generation happens at
exactly the intended print resolution.

It is a **fork of "AI Edit by TerraLab" (GPL-2.0)** being taken in a new,
independent direction. Read these first:

- **[`docs/PLAN.md`](docs/PLAN.md)** — the canonical forward plan / strategy
  (market, Cloudflare architecture, D1 schema, pricing).
- **[`docs/ROADMAP.md`](docs/ROADMAP.md)** — the engineering sequence (milestones
  M0–M7, what to build next). **Start here for "what do I do now".**
- **[`docs/TESTING.md`](docs/TESTING.md)** — how to run locally without the
  proprietary backend (the dev auth/backend workaround).

## The pivot, in one paragraph

Upstream integrates with the **main map panel** and a rubber-band selection box,
talking to TerraLab's **proprietary** API. Cartomancy integrates with the
**Print Layout window**, derives resolution from the layout, and targets **our
own Cloudflare Workers backend** (D1 + R2 + KV + Gemini 2.5 Flash Image). We
keep the GPL and credit upstream; we are not affiliated with TerraLab.

## Repository map

```
__init__.py                  classFactory → src.ui.plugin.AIEditPlugin
metadata.txt                 QGIS plugin manifest (rebranded to Cartomancy)
src/
  api/terralab_client.py     HTTP client to the backend. base_url is OVERRIDABLE
                             via TERRALAB_BASE_URL env var or .env.local.
  core/
    auth/                    activation-key auth, pairing, key storage
      auth_manager.py        Bearer-key headers + credit/quota preflight
      activation_manager.py  key format (^tl_[0-9a-f]{32}$), /usage validation
      auth_helper.py         key persistence (QgsAuthManager, encrypted)
    generation/              generation + vectorization services
    config_store.py          cached server config
    prompts/                 prompt presets, history, loading messages
    telemetry*.py            opt-in event tracking
  ui/
    plugin.py                plugin entry / lifecycle (large)
    dock_widget.py           main dock UI (large)
    dialogs/, panels/, tools/  account settings, templates, markup, swipe, etc.
  workers/                   QgsTask background jobs
    generation_worker.py     submit + poll a generation
    pairing_poll_task.py     poll /api/plugin/pair/poll during browser sign-in
mockserver/                  zero-dep local mock backend (dev-only stand-in for
                             the API; seed of the future Cloudflare Worker)
tests/                       stdlib unittest tests (no QGIS needed)
.env.local.example           dev config template → copy to .env.local (git-ignored)
resources/icons/             icons (still upstream-branded; rebrand later)
i18n/                        Qt .ts translations (still named ai_edit_*)
docs/                        PLAN.md, ROADMAP.md, TESTING.md
```

## Key concepts to know before editing

- **Auth = an activation key** `tl_<32 hex>` sent as `Authorization: Bearer …`
  plus `X-Product-ID: ai-edit`. Validated by `GET /api/plugin/usage`. There is
  **no username/password**; sign-in is a browser **pairing** handoff that mints a
  key, or manual key entry.
- **Backend is swappable** without code changes via `TERRALAB_BASE_URL` (read
  from `.env.local` at `plugin.py:_create_client`). This is the seam we use for
  local testing now and for our Cloudflare backend later. `.env.local` also
  honors `SKIP_TRIAL_CHECK=true` (skip the credit pre-flight) and `DEBUG=true`
  (plugin dev mode). See `docs/TESTING.md`.
- **Credits/quota** are server-enforced; the client preflights with `/usage`
  (cached ~60s) in `AuthManager.check_can_generate()`.
- **Background work uses `QgsTask`** (see `src/workers/`), not raw threads, so
  signals marshal safely back to the UI thread.
- Many identifiers still say **`terralab` / `ai-edit` / `AIEdit`** (settings
  prefix, cache keys, i18n filenames, the `AIEditPlugin` class). A full namespace
  rename is a **separate, deliberate task** — don't do it piecemeal.

## Current direction / next steps

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full milestone plan (M0–M7).

1. ✅ **M0** — Docs reorientation + local mock backend (`mockserver/`) so the
   plugin runs end to end locally without the proprietary backend.
2. ⏭️ **M1** — Composer→pixels engine: `src/core/layout/composer_params.py`
   (`PLAN.md` §5.1) with unit tests. **This is the immediate next step.**
3. ⏭️ **M2/M3** — Capture the layout map frame at print resolution, then add the
   Layout Designer entry point and wire it to the existing generation pipeline.
4. ⏭️ **M4** — Stand up the Cloudflare Worker backend (`PLAN.md` §4) and repoint
   `TERRALAB_BASE_URL` at it (the mock already pins the contract).

## Conventions

- **Branch:** develop on `claude/sweet-rubin-7apl2n`; never push elsewhere
  without explicit permission.
- **License:** GPL-2.0. **Preserve** the upstream `LICENSE` and copyright notices;
  keep the "fork of TerraLab AI Edit, not affiliated" attribution in README and
  `metadata.txt`.
- **Naming:** "Cartomancy" is a working codename; the final public name is an
  open Phase 0 decision (`PLAN.md` §3.1). A few `metadata.txt` fields
  (`author`, `email`, `homepage`) are placeholders pending name/domain decisions.
- **Dev backend:** never call `terra-lab.ai` in tests; point at a local mock via
  `TERRALAB_BASE_URL` (see `docs/TESTING.md`).
