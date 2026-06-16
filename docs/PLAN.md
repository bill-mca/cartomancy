# QGIS AI Print Composer Plugin — Competitor Launch Plan

> **This is the canonical forward plan for the fork.** It was authored from a
> planning conversation and uploaded as the source of truth for the project's
> new direction. The working codename for the product is **Cartomancy** (final
> public name is still an open Phase 0 decision — see §3.1).
>
> A Cloudflare-first, Print Layout-native AI image editing plugin for QGIS,
> competing directly with TerraLab's AI Edit.

---

## Table of Contents

1. [Market Context](#1-market-context)
2. [Core Concept & Differentiator](#2-core-concept--differentiator)
3. [Phase 0 — Decisions (Blocking Everything)](#3-phase-0--decisions-blocking-everything)
4. [Phase 1 — Cloudflare Infrastructure](#4-phase-1--cloudflare-infrastructure)
5. [Phase 2 — QGIS Plugin](#5-phase-2--qgis-plugin)
6. [Phase 3 — Pricing Structure](#6-phase-3--pricing-structure)
7. [Phase 4 — Build Sprint](#7-phase-4--build-sprint)
8. [Phase 5 — Launch Sequence](#8-phase-5--launch-sequence)
9. [Margin Analysis](#9-margin-analysis)
10. [Immediate Next Steps](#10-immediate-next-steps)

---

## 1. Market Context

### The Competitor: TerraLab AI Edit

TerraLab is a two-founder startup (Yvann and Lilien) building AI tools for QGIS. Their
**AI Edit** plugin (version 0.7.1 as of mid-2026) is the current market leader in
AI-powered raster image editing for QGIS.

**How they work:**
- Plugin code is **GPL-2.0 open source** on GitHub; the backend API server is proprietary
- Uses **Google Gemini 2.5 Flash Image** (marketed as "Nano Banana 2") as the AI model
- Users draw a rectangle on a raster layer in the main QGIS project panel
- The selected area is sent to TerraLab's cloud backend, which proxies to Google's API
- The georeferenced result is returned as a GeoTIFF and added to the project

**Their pricing (in EUR):**

| Tier | Price | Credits/month | Generations @ 1K |
|---|---|---|---|
| Free | €0 | 100 (lifetime) | 5 |
| Pro | €29/seat/mo | 3,000 | ~150 |
| Enterprise | Custom | Custom | Custom |

Credit costs: 20 credits per 1K generation, 30 per 2K, 40 per 4K.

**Underlying API cost (Gemini 2.5 Flash Image):**
- AUD ~$0.06 per 1K image
- AUD ~$0.10–$0.15 per 2K image
- AUD ~$0.23 per 4K image

At Pro tier, TerraLab charges roughly **5× the API cost** at 1K resolution — standard
SaaS wrapper margins.

---

## 2. Core Concept & Differentiator

### Our Differentiator: Print Layout Native

TerraLab integrates with the **main QGIS project panel**. We integrate with the
**Print Composer (Print Layout)** window instead.

This is a meaningful advantage because:

- The Print Layout already knows the **output paper size** (A3, A1, custom, etc.)
- The Print Layout already has a user-set **export DPI** (e.g. 300 DPI for print)
- Together these define the **exact pixel dimensions** of the intended output
- The AI generation can be called at precisely the right resolution — no guessing,
  no upscaling, no wasted credits on oversized generations

**No rubber-band selection tool is needed.** The entire map frame in the Print Layout
*is* the selection region. The user opens the layout, types a prompt, clicks generate.

### The Core User Promise

> *"Edit your map at exactly the resolution you're going to print it.
> No guessing, no upscaling."*

### Data Flow

```
User opens a Print Layout in QGIS
  → Plugin reads: paper size (mm) + export DPI + map frame pixel dimensions
  → User types a prompt in the plugin panel
  → Plugin renders the map frame to a base64 image at print resolution
  → Sends image + prompt to our Cloudflare Worker API
  → Worker checks credits, proxies to Gemini API
  → AI-generated GeoTIFF returned, stored in R2
  → Signed URL sent back to plugin
  → Plugin downloads GeoTIFF, saves to project working directory
  → Layer added to the QGIS project panel automatically
```

---

## 3. Phase 0 — Decisions (Blocking Everything)

### 3.1 Name the Product

The product name is required before domain registration, QGIS plugin namespace,
branding, and marketing can begin. It is the single hardest blocker.

**Naming directions:**

| Angle | Examples |
|---|---|
| Print/layout focused | *LayoutAI*, *PrintEdit*, *ComposerAI* |
| GIS-generic, memorable | *GeoForge*, *MapInk*, *RasterEdit*, *GeoCanvas* |
| Playful (competing with "Nano Banana" energy) | *MapBrush*, *LayerDream*, *GeoInk* |

> **Working codename:** the repository is named **Cartomancy** (cartography +
> the "magic" of generative AI). It is used throughout the docs and code as a
> placeholder until the final public name is chosen.

**Human task:** Make the final call on the name.

**Agent task:** Generate a longlist of 30+ name candidates, check each against the
[QGIS plugin repository](https://plugins.qgis.org) for namespace conflicts, and run
domain availability checks.

### 3.2 Domain Registration

Register via **Cloudflare Registrar** — it sells domains at cost with no markup,
and DNS is already on Cloudflare for free. Ideal for a Cloudflare-first stack.

### 3.3 AI Backend Choice

We will use **Google Gemini 2.5 Flash Image** (same as TerraLab) for the MVP.
Reasons:
- Lowest per-image API cost (~AUD $0.06 at 1K)
- Proven on geospatial imagery
- No GPU required; fully managed

Future option: evaluate **Gemini 3.1 Flash Image** (now available, higher quality
but higher cost) or self-hosted open-weight models (FLUX, Stable Diffusion) to
reduce per-image cost at scale.

---

## 4. Phase 1 — Cloudflare Infrastructure

Everything lives on Cloudflare. No traditional VPS or container hosting required.

### 4.1 Services Map

| Cloudflare Service | Role |
|---|---|
| **Workers** (Python + FastAPI) | API backend — auth, generation, credits, Stripe webhook |
| **D1** (SQLite) | User accounts, credit ledger, generation audit log |
| **R2** (object storage) | Stores generated GeoTIFF files; zero egress fees |
| **KV** (key-value) | Session token cache, rate limiting state |
| **Pages** | Marketing website + user dashboard |
| **Registrar** | Domain at cost |
| **Secrets** | Gemini API key, Stripe secret key |

### 4.2 D1 Database Schema

```sql
-- Users
CREATE TABLE users (
    id          TEXT PRIMARY KEY,       -- UUID v4
    email       TEXT UNIQUE NOT NULL,
    created_at  INTEGER NOT NULL,       -- Unix epoch
    plan        TEXT DEFAULT 'free'     -- 'free' | 'pro' | 'studio'
);

-- Credit ledger
CREATE TABLE credits (
    user_id     TEXT PRIMARY KEY REFERENCES users(id),
    balance     INTEGER DEFAULT 75,     -- starts at 75 (3-4 free generations)
    reset_at    INTEGER                 -- epoch for monthly reset; NULL on free tier
);

-- Generation audit log
CREATE TABLE generations (
    id              TEXT PRIMARY KEY,   -- UUID v4
    user_id         TEXT REFERENCES users(id),
    created_at      INTEGER NOT NULL,
    resolution      TEXT NOT NULL,      -- '1K' | '2K' | '4K'
    credits_used    INTEGER NOT NULL,
    dpi             INTEGER,            -- from print layout
    paper_width_mm  REAL,
    paper_height_mm REAL,
    prompt          TEXT,
    r2_key          TEXT,               -- path in R2 bucket
    status          TEXT DEFAULT 'pending'  -- 'pending' | 'success' | 'failed'
);
```

### 4.3 Worker Directory Structure

```
workers/
  src/
    entry.py              # FastAPI app + Cloudflare WorkerEntrypoint
    routes/
      auth.py             # POST /auth/signup, /auth/login, /auth/verify
      generate.py         # POST /generate  ← the money route
      credits.py          # GET /credits, POST /credits/check
      stripe.py           # POST /stripe/webhook
    services/
      gemini.py           # Gemini API client (base64 image in, GeoTIFF out)
      r2.py               # R2 upload / signed URL helpers
      credits.py          # deduct / check / reset logic
  wrangler.toml
  pyproject.toml
```

### 4.4 The `/generate` Endpoint Flow

```
POST /generate
  {
    "prompt": "Remove all clouds from the image",
    "image_b64": "<base64 encoded PNG/JPEG>",
    "resolution": "2K",
    "dpi": 300,
    "paper_w_mm": 420.0,
    "paper_h_mm": 297.0
  }

  1. Authenticate JWT from Authorization header
  2. Look up credit balance in D1
  3. If balance < cost → return 402 Payment Required
  4. Deduct credits immediately (prevents abuse on failure)
  5. Send image + prompt to Gemini 2.5 Flash Image API
  6. On success:
       - Upload result GeoTIFF to R2 (key: generations/{user_id}/{gen_id}.tif)
       - Generate signed download URL (15-minute expiry)
       - Write generation record to D1 (status = 'success')
       - Return { url, generation_id, credits_remaining }
  7. On Gemini failure:
       - Refund credits to D1
       - Write generation record (status = 'failed')
       - Return 502 with error message
```

### 4.5 R2 Lifecycle Policy

Generated GeoTIFFs are stored temporarily. Set an R2 lifecycle rule to
auto-delete objects in `generations/` after **30 days**. Users download
the file into their project directory at generation time; R2 is just the
delivery mechanism, not permanent storage.

---

## 5. Phase 2 — QGIS Plugin

### 5.1 The Composer-to-Pixels Pipeline

The core technical differentiator. All resolution logic derives from the
Print Layout, not from user selection.

```python
from qgis.core import (
    QgsProject,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutExporter,
    QgsRectangle,
)

def get_composer_export_params(layout: QgsPrintLayout) -> dict:
    """
    Reads the active print layout and returns everything
    needed to make a resolution-aware API call.
    """
    # Map frame item
    map_item = layout.referenceMap()  # QgsLayoutItemMap

    # Paper size in mm
    page = layout.pageCollection().page(0)
    width_mm  = page.pageSize().width()    # e.g. 420.0 for A3 landscape
    height_mm = page.pageSize().height()   # e.g. 297.0

    # Export DPI from Layout > Export Settings
    dpi = layout.renderContext().dpi()     # e.g. 300.0

    # Output pixel dimensions
    px_per_mm = dpi / 25.4
    width_px  = int(width_mm  * px_per_mm)  # e.g. 4961 for A3 @ 300 DPI
    height_px = int(height_mm * px_per_mm)  # e.g. 3508

    # Geographic extent and CRS of the map frame
    extent = map_item.extent()
    crs    = map_item.crs()

    return {
        "width_px":     width_px,
        "height_px":    height_px,
        "dpi":          dpi,
        "paper_w_mm":   width_mm,
        "paper_h_mm":   height_mm,
        "extent_xmin":  extent.xMinimum(),
        "extent_xmax":  extent.xMaximum(),
        "extent_ymin":  extent.yMinimum(),
        "extent_ymax":  extent.yMaximum(),
        "crs_wkt":      crs.toWkt(),
    }


def select_resolution_tier(width_px: int, height_px: int) -> str:
    """
    Picks the cheapest Gemini resolution tier that covers
    the composer's output pixel dimensions.
    """
    max_dim = max(width_px, height_px)
    if max_dim <= 1024:
        return "1K"    # 20 credits — AUD ~$0.06 API cost
    elif max_dim <= 2048:
        return "2K"    # 30 credits — AUD ~$0.15 API cost
    else:
        return "4K"    # 40 credits — AUD ~$0.23 API cost
```

**Example resolution calculations:**

| Paper Size | DPI | Output pixels | Tier selected |
|---|---|---|---|
| A4 portrait | 96 dpi (screen) | 794 × 1123 | 2K |
| A4 portrait | 300 dpi (print) | 2480 × 3508 | 4K |
| A3 landscape | 150 dpi | 2480 × 1754 | 4K |
| A3 landscape | 300 dpi | 4961 × 3508 | 4K |
| Custom 200 × 200 mm | 96 dpi | 756 × 756 | 1K |

### 5.2 Saving the Result to the Project Working Directory

The generated GeoTIFF is saved into the QGIS project's working directory and
added as a layer — matching TerraLab's behaviour.

```python
import os
import time
import requests
from qgis.core import QgsRasterLayer, QgsProject

def save_and_load_result(download_url: str, prompt: str) -> QgsRasterLayer:
    """
    Downloads the GeoTIFF from the signed R2 URL, saves it to the
    project working directory, and adds it as a QGIS layer.
    """
    project     = QgsProject.instance()
    project_dir = os.path.dirname(project.fileName())

    # Sanitise prompt for use in filename
    safe_prompt = "".join(
        c for c in prompt[:30] if c.isalnum() or c in " _-"
    ).strip().replace(" ", "_")

    filename    = f"ai_edit_{safe_prompt}_{int(time.time())}.tif"
    output_path = os.path.join(project_dir, filename)

    # Download from R2 signed URL
    response = requests.get(download_url, timeout=120)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    # Add to project
    layer = QgsRasterLayer(output_path, f"AI Edit — {safe_prompt}")
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
    else:
        raise RuntimeError(f"Generated layer is not valid: {output_path}")

    return layer
```

### 5.3 AI-Generated Metadata Tagging

Every output GeoTIFF must carry AI-generated markers in its metadata. This is
required by the **EU AI Act (Article 50)**, which mandates machine-readable
marking of AI-generated content from August 2026.

Tags to embed (written by the Worker before storing in R2):

```
AI_GENERATED = TRUE
AI_NOTE      = Synthetic imagery, not survey data
AI_PROMPT    = <the user's prompt>
AI_DATE      = <ISO 8601 timestamp>
AI_MODEL     = gemini-2.5-flash-image
```

These are visible in QGIS (Layer Properties → Metadata) and in any tool that
reads GeoTIFF tags.

### 5.4 Plugin Directory Structure

```
plugin_name/
  __init__.py
  metadata.txt
  main.py                  # Entry point; hooks into Print Layout window menu
  composer_tool.py         # Layout param reading + resolution calculation
  api_client.py            # Calls Cloudflare Worker API
  auth_dialog.py           # Login / signup UI (Qt)
  generate_dialog.py       # Prompt input + progress bar
  layer_writer.py          # Downloads GeoTIFF, saves to project dir, adds layer
  resources/
    icons/
      plugin_icon.svg
      generate_button.svg
  i18n/
    en.ts
    fr.ts
  metadata.txt
```

### 5.5 Plugin Integration Point

The plugin adds a menu item to the **Print Layout window** (not the main QGIS
toolbar). Specifically it hooks into `QgsLayoutDesignerInterface`, adding an
item under the **Layout** menu:

```
Layout → AI Edit → Generate from current layout...
```

This keeps it contextually correct — the plugin is only meaningful when a
Print Layout is open — and clearly distinguishes the product from TerraLab's
main-panel approach.

### 5.6 metadata.txt (QGIS Plugin Repository Required Fields)

```ini
[general]
name=<Plugin Name>
qgisMinimumVersion=3.0
description=AI-powered image editing from the QGIS Print Layout. Edit your
    map at exactly the resolution you intend to print it.
version=0.1.0
author=<Your Name>
email=<your@email.com>
about=Integrates AI image generation into the QGIS Print Layout / Print
    Composer window. Set your paper size and DPI in the layout, type a prompt,
    and receive a georeferenced GeoTIFF at the exact output resolution.
    No selection drawing required.
tracker=https://github.com/<org>/<repo>/issues
repository=https://github.com/<org>/<repo>
homepage=https://<your-domain>
tags=ai,print,composer,layout,raster,image editing,georeferenced,generative ai
category=Raster
icon=resources/icons/plugin_icon.svg
experimental=False
deprecated=False
```

---

## 6. Phase 3 — Pricing Structure

Priced in AUD to distinguish from TerraLab's EUR pricing. Competitive but
slightly cheaper at the Pro tier.

| Tier | Price | Credits/month | Generations @ 1K | Generations @ 4K |
|---|---|---|---|---|
| Free | AUD$0 | 75 (lifetime) | 3–4 | 1 |
| Pro | AUD$25/seat/mo | 3,000 | ~150 | ~75 |
| Studio | AUD$65/seat/mo | 10,000 | ~500 | ~250 |

**Credit costs per generation:**

| Resolution | Credits | Approx. API cost (AUD) | Charge to user (AUD) | Markup |
|---|---|---|---|---|
| 1K | 20 | ~$0.06 | ~$0.17 | ~2.8× |
| 2K | 30 | ~$0.15 | ~$0.25 | ~1.7× |
| 4K | 40 | ~$0.23 | ~$0.33 | ~1.4× |

> Note: markup figures above are at the Pro tier. Margins improve significantly
> if users stay on free tier credits (which are sunk cost) or if volume grows.

### Optional: DPI-Aware Credit Pricing

A potential future differentiator — charge credits proportional to actual pixel
area rather than discrete tiers:

```
credits = round((width_px * height_px) / (1024 * 1024) * 20)
```

This makes pricing feel fairer to power users working at unusual sizes and
aligns cost exactly with underlying API spend.

---

## 7. Phase 4 — Build Sprint

### Agent-Led Tasks

These can begin as soon as the product name and Cloudflare account are set up:

1. Generate the full plugin scaffold with all stub files and docstrings
2. Write and unit-test the composer DPI calculation engine (`composer_tool.py`)
3. Write the Cloudflare Worker FastAPI backend (`entry.py` + all routes)
4. Write the D1 schema and `wrangler.toml` migration files
5. Write the Gemini API client with base64 image handling (`services/gemini.py`)
6. Write the R2 upload/download helper with signed URL generation (`services/r2.py`)
7. Write the Stripe webhook handler for subscription lifecycle events
8. Write `metadata.txt` and verify against QGIS plugin checker (`pb_tool`)
9. Generate 30+ geospatial prompt templates for the in-plugin template library
10. Write GeoTIFF metadata tagging (EU AI Act compliance)

### Human-Led Tasks

1. **Decide product name** ← this blocks everything
2. Register domain via Cloudflare Registrar
3. Set up Cloudflare account — create D1 database, R2 bucket, KV namespace
4. Set up Stripe account — create subscription products matching the tier table
5. Obtain Gemini API key from Google AI Studio; store as a Cloudflare Worker secret
6. Install plugin in QGIS on Linux and test against real print layouts
7. Validate georeferencing accuracy of output at multiple paper sizes and DPIs
8. Record demo video (3 minutes, showing composer workflow end to end)

### Suggested Weekly Sequence

**Week 1–2:** Agent scaffolds plugin + Worker code; human sets up accounts and services

**Week 3:** Human installs and tests plugin locally on Linux against real layouts;
validates DPI maths, credit deduction, and layer output

**Week 4:** Agent wires up live Gemini API calls (replacing mocks); human tests
Stripe payment flow in test mode end to end

**Week 5:** Polish pass — prompt templates, error messages, progress bar UX,
EU AI Act metadata tagging, `pb_tool` compliance check

**Week 6:** Submit to QGIS plugin repository; prepare launch content

---

## 8. Phase 5 — Launch Sequence

### 8.1 QGIS Plugin Repository Submission

- Plugin must pass automated QGIS checks (valid `metadata.txt`, GPL-compatible
  licence, no bundled non-Python binaries)
- Allow **1–2 weeks** for volunteer reviewer turnaround
- Respond promptly to review feedback — reviewers appreciate responsive authors

**Agent task:** Run `pb_tool` validation before submission; fix any issues.

**Human task:** Create QGIS.org account, submit plugin, monitor review thread.

### 8.2 Launch Marketing Channels (Low-cost, High-signal)

| Channel | Approach |
|---|---|
| **QGIS community forum** | Genuine "here's what we built and why" post with before/after imagery |
| **r/QGIS, r/gis** | Authentic demo post — community is hostile to obvious marketing |
| **LinkedIn** | Target GIS professionals, urban planners, surveyors, environmental scientists |
| **YouTube** | 3-minute demo showing the Print Layout workflow; this is the highest-value asset |
| **GitHub README** | Make it excellent — many QGIS users browse GitHub before installing |

### 8.3 Key Differentiator Messaging

Lead with the print resolution angle:

> *"Edit your map at exactly the resolution you're going to print it.
> No guessing, no upscaling, no wasted credits."*

Secondary messages:
- "Integrated into the Print Layout — not bolted onto the main panel"
- "Priced in AUD — no currency conversion surprises"
- "Georeferenced output saved directly to your project folder"

### 8.4 Demo Video Script (Outline)

1. Open QGIS with a real aerial/satellite dataset (0:00–0:15)
2. Open a Print Layout, show the paper size and DPI settings (0:15–0:40)
3. Show the plugin panel in the Layout window (0:40–1:00)
4. Type a prompt — e.g. "Remove all cloud cover and fill with clear sky" (1:00–1:15)
5. Click Generate — show the progress bar (1:15–1:45)
6. Result appears as a new layer in the project panel (1:45–2:15)
7. Show the layer properties → metadata → AI_GENERATED tag (2:15–2:30)
8. Export the layout to PDF at 300 DPI — the AI-edited layer is included (2:30–3:00)

---

## 9. Margin Analysis

### Unit Economics at Pro Tier (AUD$25/seat/mo)

Assuming a typical Pro user does 80 generations/month at mixed resolutions
(60% × 1K, 30% × 2K, 10% × 4K):

| | Volume | Cost/gen (AUD) | Total cost |
|---|---|---|---|
| 1K generations | 48 | $0.06 | $2.88 |
| 2K generations | 24 | $0.15 | $3.60 |
| 4K generations | 8 | $0.23 | $1.84 |
| Cloudflare infra | — | ~$0.50/user/mo | $0.50 |
| **Total cost** | | | **~$8.82** |
| **Revenue** | | | **$25.00** |
| **Gross margin** | | | **~65%** |

Gross margin improves as users generate less (free tier users on paid plan),
and compresses as 4K usage increases. Studio tier margin is higher due to
volume rarely being fully consumed.

### Cloudflare Cost Estimate

At early scale (< 1,000 users):

| Service | Estimated monthly cost |
|---|---|
| Workers | Free tier (100,000 req/day free) |
| D1 | Free tier (5M row reads/day free) |
| R2 | ~AUD$0.02/GB/mo storage + ~AUD$0.04 per 10K operations |
| KV | Free tier (100,000 reads/day free) |
| Pages | Free |
| **Total infra** | **< AUD$5/mo at early scale** |

---

## 10. Immediate Next Steps

In priority order — nothing else can start until item 1 is resolved:

| # | Task | Owner | Blocks |
|---|---|---|---|
| 1 | Decide product name | Human | Everything |
| 2 | Register domain (Cloudflare Registrar) | Human | Marketing, Pages |
| 3 | Set up Cloudflare account, create D1 / R2 / KV | Human | Backend dev |
| 4 | Set up Stripe account, create subscription products | Human | Payments |
| 5 | Obtain Gemini API key; store as Worker secret | Human | Generation endpoint |
| 6 | Agent generates plugin scaffold + Worker code | Agent | All dev work |
| 7 | Agent writes DPI calculation engine with unit tests | Agent | Core feature |
| 8 | Human installs plugin on Linux, tests against real layouts | Human | QA |

---

*Document generated: June 2026*
*Stack: QGIS 3.x · Cloudflare Workers (Python/FastAPI) · D1 · R2 · KV · Stripe · Gemini 2.5 Flash Image*
