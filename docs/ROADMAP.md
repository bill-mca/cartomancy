# Development Roadmap

The **engineering** companion to [`PLAN.md`](PLAN.md). `PLAN.md` is the *what and
why* (market, architecture, pricing). This is the *how*: the concrete, sequenced
build path from "upstream fork that runs against a local mock" to "Print
Layout-native product on our own Cloudflare backend."

Each milestone is **independently testable against the mock backend**
([`mockserver/`](../mockserver/)), so we make visible progress long before the
real Worker exists.

---

## Where we are now (snapshot)

**Inherited from upstream (works today):** a full *main-map-panel* AI editor —
rubber-band selection (`src/ui/tools/selection_map_tool.py`), a large dock UI
(`src/ui/dock_widget.py`), the generation pipeline
(`src/core/generation/generation_service.py` + `src/workers/generation_worker.py`),
plus vectorization, markup, before/after swipe, prompt templates, history,
favorites, and telemetry.

**Auth:** activation-key Bearer token + browser pairing + server-enforced
credits; the backend base URL is swappable via `TERRALAB_BASE_URL`.

**✅ M0 — Local dev loop (DONE).** `mockserver/` + `.env.local` let the plugin
run end to end locally with **no proprietary backend and no plugin code change**.
See [`TESTING.md`](TESTING.md).

---

## Guiding principles

1. **Test against the mock first.** Every milestone has an acceptance check that
   runs against `mockserver/` before the real Worker is involved.
2. **Keep the backend swappable.** Never hardcode a new base URL; the
   `TERRALAB_BASE_URL` seam is how mock → Worker → prod all work unchanged.
3. **Build the pivot additively.** New Print Layout code lives in new modules
   (`src/core/layout/…`, a new panel) so we don't destabilize the inherited
   pipeline while we turn the ship.
4. **One deliberate rename, not piecemeal.** Identifiers still say
   `AIEdit`/`terralab`/`ai-edit`; rename once in M6.
5. **Preserve the GPL and upstream attribution** in every step.

---

## Milestones

### M1 — Composer→pixels engine  ·  *the differentiator's core*  ·  ✅ DONE
**Goal:** turn an open Print Layout into exact output pixel dimensions + the
geo-context needed for a resolution-aware API call. Pure logic, no UI.

**Delivered**
- `src/core/layout/composer_params.py`:
  - `compute_pixel_dimensions(width_mm, height_mm, dpi)` and
    `select_resolution_tier(...)` — pure math, no QGIS dependency.
  - `get_composer_export_params(layout)` — duck-typed adapter over a
    `QgsPrintLayout`; returns `width_px`/`height_px`, DPI, paper size, tier, and
    `extent` (xmin/ymin/xmax/ymax) + `crs_authid`/`crs_wkt` **already shaped for
    `raster_writer.write_geotiff` and the generation pipeline**.
  - `credits_for_area(...)` — optional DPI-aware pricing helper (`PLAN.md` §6).
- `tests/test_composer_params.py` — 11 stdlib tests; the headline test
  reproduces the `PLAN.md` §5.1 table exactly.

> Caught + resolved: the `PLAN.md` §5.1 snippet uses `int()` truncation, which
> does **not** reproduce its own example table (off by a pixel, e.g. 793 vs 794).
> We round half-up to match the table and avoid under-resolving.

**Acceptance:** ✅ `python -m unittest tests.test_composer_params` reproduces the
`PLAN.md` §5.1 table exactly.

---

### M2 — Capture the layout map frame at print resolution
**Goal:** render the layout's reference map item to an image at exactly
`width_px × height_px`, plus capture its extent/CRS for georeferencing the result.

**Tasks**
- New `src/core/layout/composer_capture.py`: render the `QgsLayoutItemMap`
  (via `QgsLayoutExporter` / a custom-painter render job) to a `QImage` at the
  derived pixel size, encode to PNG/base64 (the generation input).
- Reuse encoding/format patterns from `src/ui/canvas_exporter.py`.
- Produce the `extent_dict` + `crs_wkt` that `raster_writer.write_geotiff()`
  already consumes, so the result georeferences correctly.

**Acceptance:** for a sample layout, output a PNG of the exact expected pixel
size with correct extent/CRS; round-trips through `write_geotiff` to a valid
GeoTIFF. **Depends on:** M1.

---

### M3 — Layout Designer entry point + minimal generate flow  ·  *first visible pivot*
**Goal:** the user opens a Print Layout, types a prompt, clicks generate, and an
AI-edited georeferenced layer appears — driven entirely from the layout.

**Tasks**
- Hook the Layout Designer: in `AIEditPlugin.initGui`, subscribe to
  `iface.layoutDesignerOpened` and add `Layout → AI Edit → Generate from current
  layout…` (`PLAN.md` §5.5). Use `src/ui/terralab_menu.py` as the existing
  menu-wiring pattern; confirm exact `QgsLayoutDesignerInterface` calls against
  the QGIS API.
- A minimal generate panel/dialog (prompt input + progress bar). Reuse pieces of
  `dock_widget.py` rather than rebuild.
- Wire the pipeline: **M2 capture → M1 params → existing `GenerationService` /
  `GenerationTask` → `raster_writer` → add layer to project** (the worker and
  result-handling code are reused as-is).

**Acceptance:** end-to-end generation from a Print Layout against the **mock**;
the result layer is added and correctly georeferenced; runs with
`SKIP_TRIAL_CHECK=true`. **Depends on:** M1, M2.

---

### M4 — Cloudflare Worker backend  ·  *retire the mock*
**Goal:** replace the mock with the real backend; keep the identical contract so
the plugin changes only its `TERRALAB_BASE_URL`.

**Tasks** (`PLAN.md` §4)
- `workers/` scaffold; routes mirroring the mock: `/api/plugin/usage`,
  `/api/ai-edit/generate`, `/generate/status`, `/upload-url`, pairing, config.
- D1 schema + migrations (`PLAN.md` §4.2); R2 upload + signed URLs; KV for
  sessions/rate-limit; Gemini 2.5 Flash Image client (base64 in, GeoTIFF out).
- `/generate` credit flow: check → deduct → call Gemini → R2 → refund on failure
  (`PLAN.md` §4.4). Activation-key auth + pairing mint. Stripe webhook later.

**Acceptance:** the same plugin flow works against the deployed Worker; a D1
credit row is debited and an R2 object is created. **Depends on:** the frozen
contract (already pinned by the mock); can start **in parallel** with M1–M3.

> **Note:** the Cloudflare MCP tools (D1/R2/KV) are available in this session and
> can scaffold the infra when we reach M4.

---

### M5 — EU AI Act metadata tagging
**Goal:** every output GeoTIFF carries machine-readable AI-generated markers
(`PLAN.md` §5.3) — required from Aug 2026.

**Tasks:** the Worker embeds `AI_GENERATED`, `AI_NOTE`, `AI_PROMPT`, `AI_DATE`,
`AI_MODEL` tags before storing in R2; verify they appear in QGIS Layer
Properties → Metadata. **Depends on:** M4.

---

### M6 — Name, namespace rename, rebrand
**Goal:** ship under the final product name with a clean namespace.

**Tasks:** decide the name (`PLAN.md` §3.1) — **blocks this milestone and domain
registration**. Then one deliberate rename pass: `AIEditPlugin` class, `AIEdit/`
settings prefix, `terralab/...` cache keys, i18n `ai_edit_*.ts` filenames, the
upstream-branded icons, and the remaining `metadata.txt` placeholders
(`author`, `email`, `homepage`). Run `pb_tool` validation.

**Depends on:** the name decision (human).

---

### M7 — Polish & launch
Print/cartography-focused prompt templates, error-message UX, an excellent README
+ 3-min demo video (`PLAN.md` §8.4), and QGIS plugin repository submission
(`PLAN.md` §7–8).

---

## Suggested sequencing

```
M0 ✅ ──▶ M1 ──▶ M2 ──▶ M3 ─────────────▶ M5 ──▶ M6 ──▶ M7
                          │                ▲
                          └──▶ M4 (Worker)─┘   (M4 can run in parallel from now —
                                                the contract is already frozen by the mock)
```

- **Immediate next step:** M2 — capture the layout map frame at print resolution
  (M1's `get_composer_export_params` gives the exact target size + geo-context).
- M2 + M3 deliver the first user-visible Print Layout generation (against the mock).
- M4 (the Worker) can begin in parallel the moment we want it; it doesn't block M1–M3.

---

## Open decisions (need a human call — defaults proposed)

| # | Decision | Default recommendation |
|---|---|---|
| 1 | **Keep or drop the legacy main-panel mode** during/after the pivot? | Keep it working behind the scenes for now; lead with the Layout flow; drop before launch to cut scope. |
| 2 | **Worker language** — Python/FastAPI (per `PLAN.md` §4.3) vs TypeScript? | Reassess at M4: TypeScript has the more mature Workers runtime (image handling, Stripe SDK). The plugin stays Python regardless; only the backend language is in question. |
| 3 | **Final product name** (blocks M6 + domain) | Run the agent name-longlist task from `PLAN.md` §3.1; human picks. "Cartomancy" is the working default. |
| 4 | **MVP feature surface** in the Layout flow v1 | Prompt → generate → result layer only for v1; re-introduce vectorize / markup / swipe / templates afterwards. |

---

## Status checklist

- [x] **M0** — Local mock backend + `.env.local` dev loop
- [x] **M1** — Composer→pixels engine (+ unit tests)
- [ ] **M2** — Layout map-frame capture at print resolution
- [ ] **M3** — Layout Designer entry point + minimal generate flow
- [ ] **M4** — Cloudflare Worker backend (D1 / R2 / KV / Gemini)
- [ ] **M5** — EU AI Act GeoTIFF metadata tagging
- [ ] **M6** — Name decision + namespace rename + rebrand
- [ ] **M7** — Polish + QGIS plugin repository submission
