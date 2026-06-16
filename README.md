# Cartomancy — AI map editing, native to the QGIS Print Layout

[![QGIS](https://img.shields.io/badge/QGIS-3.0+-93b023?style=flat-square&logo=qgis&logoColor=white)](https://qgis.org)
[![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/status-early%20fork%20%C2%B7%20WIP-orange?style=flat-square)]()

> **Working codename: _Cartomancy_** (cartography + the "magic" of generative AI).
> The final public name is still an open decision — see
> [`docs/PLAN.md` §3.1](docs/PLAN.md#31-name-the-product).

## What this is

Cartomancy is an AI image-editing plugin for QGIS that lives in the **Print
Layout (Print Composer)** window instead of the main map panel. You set your
paper size and export DPI in the layout, type a prompt, and get a georeferenced
GeoTIFF back **at exactly the resolution you intend to print** — added straight
to your project.

> *"Edit your map at exactly the resolution you're going to print it.
> No guessing, no upscaling, no wasted credits."*

## Why the Print Layout?

The Print Layout already knows the two things that pin down output resolution:

- **Paper size** (A3, A1, custom — in mm)
- **Export DPI** (e.g. 300 DPI for print)

Together these give the **exact pixel dimensions** of the intended output, so
the AI generation is called at precisely the right size. No rubber-band
selection tool is needed — the map frame in the layout *is* the region. This is
the core differentiator and the reason the plugin hooks into the Layout
Designer rather than the main toolbar.

## Project status

🚧 **Early-stage fork. Work in progress — not yet usable end to end.**

This repository began as a fork of **["AI Edit by TerraLab"](https://github.com/TerraLabAI/QGIS_AI-Edit)**
(GPL-2.0) and is being taken in a **new and independent direction**:

| | Upstream (TerraLab AI Edit) | Cartomancy (this fork) |
|---|---|---|
| Integration point | Main QGIS map panel + rubber-band selection | **Print Layout / Composer window** |
| Resolution | User-drawn box, inferred | **Derived from paper size × DPI** |
| Backend | TerraLab's proprietary API | **Our own Cloudflare Workers stack** |
| Pricing | EUR | AUD |

The full rationale, architecture, schema, pricing, and build sprint are in the
canonical plan:

- 📋 **[`docs/PLAN.md`](docs/PLAN.md)** — the forward plan (market, architecture, roadmap)
- 🧪 **[`docs/TESTING.md`](docs/TESTING.md)** — how to run the plugin locally without the
  proprietary backend (the dev auth/backend workaround)
- 🤖 **[`CLAUDE.md`](CLAUDE.md)** — orientation for contributors and AI coding sessions

## Architecture at a glance

```
QGIS Print Layout ──reads──▶ paper size + DPI ──▶ exact pixel dimensions
        │
        ├─ render map frame to image (at print resolution)
        ▼
  Cartomancy plugin (this repo, Python/Qt)
        │  HTTPS + activation-key auth
        ▼
  Cloudflare Worker API (to be built — see docs/PLAN.md §4)
        ├─ D1   : accounts, credits, audit log
        ├─ R2   : generated GeoTIFFs (signed URLs, 30-day lifecycle)
        ├─ KV   : sessions / rate limiting
        └─ Gemini 2.5 Flash Image  ◀── the generation model
        │
        ▼
  georeferenced GeoTIFF ──▶ saved to project dir ──▶ added as a QGIS layer
```

## Repository layout

```
__init__.py            classFactory entry point (QGIS loads this)
metadata.txt           QGIS plugin manifest
src/
  api/                 HTTP client to the backend (base URL is overridable)
  core/                auth, config, generation services, prompts, telemetry
  ui/                  dock widget, dialogs, panels, map tools
  workers/             QgsTask background jobs (generation, pairing poll, …)
resources/             icons
i18n/                  Qt translation files
docs/                  PLAN.md, TESTING.md  ← start here
```

See [`CLAUDE.md`](CLAUDE.md) for a deeper map of the codebase and key concepts.

## Development

This is a QGIS plugin; it runs inside QGIS (3.0+). For local testing **without**
the proprietary backend, point the client at your own/mock backend via the
`TERRALAB_BASE_URL` override and follow [`docs/TESTING.md`](docs/TESTING.md).

## Attribution & license

Cartomancy is **free software** licensed under the **GNU GPL v2** (see
[`LICENSE`](LICENSE)) — the same license as its upstream.

It is a fork of **AI Edit by TerraLab** (© Yvann and Lilien / TerraLab),
originally at <https://github.com/TerraLabAI/QGIS_AI-Edit>. We are grateful for
their open-source work. **Cartomancy is an independent project and is not
affiliated with, endorsed by, or supported by TerraLab.** It targets an
independent backend and does not use TerraLab's services. Upstream copyright and
license notices are retained as required by the GPL.
