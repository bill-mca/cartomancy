"""Composer-to-pixels engine (ROADMAP M1).

Turn an open QGIS Print Layout into the exact output pixel dimensions and the
geo-context needed for a resolution-aware generation call. This is the heart of
the Cartomancy differentiator: the layout's **paper size x export DPI** defines
the precise output resolution, so generation happens at exactly the size the
user intends to print (docs/PLAN.md §2, §5.1).

The pure-math helpers (`compute_pixel_dimensions`, `select_resolution_tier`,
`credits_for_area`) have **no QGIS dependency** and are unit-tested directly.
`get_composer_export_params` is a thin adapter over a `QgsPrintLayout`; it is
duck-typed (no `qgis` import) so it can be tested with a stub layout too.

Note on rounding: docs/PLAN.md §5.1 shows a code snippet using ``int(...)`` but
its own worked-example table is produced by **rounding** (e.g. A4 @ 96 DPI =>
794 x 1123, not 793 x 1122). We round (round-half-up) so we reproduce that
table and never under-resolve by a pixel.
"""
from __future__ import annotations

MM_PER_INCH = 25.4

# Gemini resolution tiers, keyed by the longest output side (px). Picks the
# cheapest tier that still covers the composer's output (docs/PLAN.md §5.1, §6).
TIER_1K_MAX = 1024
TIER_2K_MAX = 2048

# Default credit cost basis for the optional DPI-aware pricing helper
# (docs/PLAN.md §6): 20 credits per 1024x1024 megapixel.
DEFAULT_CREDITS_PER_MEGAPIXEL = 20
_MEGAPIXEL = 1024 * 1024


def _round_px(value: float) -> int:
    """Round a positive pixel dimension half-up (deterministic, never 0)."""
    return max(1, int(value + 0.5))


def compute_pixel_dimensions(
    width_mm: float, height_mm: float, dpi: float
) -> tuple[int, int]:
    """Convert a physical page size (mm) at a given DPI to output pixels.

    Raises ValueError on non-positive inputs so the UI can surface a clear
    message instead of producing a degenerate 0-pixel request.
    """
    if width_mm <= 0 or height_mm <= 0:
        raise ValueError(f"Page size must be positive (got {width_mm} x {height_mm} mm)")
    if dpi <= 0:
        raise ValueError(f"DPI must be positive (got {dpi})")
    px_per_mm = dpi / MM_PER_INCH
    return _round_px(width_mm * px_per_mm), _round_px(height_mm * px_per_mm)


def select_resolution_tier(width_px: int, height_px: int) -> str:
    """Pick the cheapest tier ('1K'|'2K'|'4K') that covers the output size."""
    max_dim = max(width_px, height_px)
    if max_dim <= TIER_1K_MAX:
        return "1K"
    if max_dim <= TIER_2K_MAX:
        return "2K"
    return "4K"


def credits_for_area(
    width_px: int,
    height_px: int,
    credits_per_megapixel: int = DEFAULT_CREDITS_PER_MEGAPIXEL,
) -> int:
    """Optional DPI-aware pricing: credits proportional to pixel area, not tier
    (docs/PLAN.md §6). Not wired into billing yet — kept here so the engine owns
    all resolution<->cost math in one place.
    """
    if width_px <= 0 or height_px <= 0:
        raise ValueError("Pixel dimensions must be positive")
    return max(1, round((width_px * height_px) / _MEGAPIXEL * credits_per_megapixel))


def get_composer_export_params(layout) -> dict:
    """Read a `QgsPrintLayout` and return everything needed for a
    resolution-aware generation call.

    Duck-typed against the QGIS API so it stays unit-testable without QGIS:
      - ``layout.pageCollection().page(0).pageSize()`` -> ``.width()`` / ``.height()`` (mm)
      - ``layout.renderContext().dpi()`` -> export DPI
      - ``layout.referenceMap()`` -> the map frame item, with ``.extent()`` and ``.crs()``

    Returns a dict whose ``extent`` / ``crs_wkt`` / ``crs_authid`` feed straight
    into the existing generation pipeline (``raster_writer.write_geotiff`` and
    ``GenerationService`` both consume xmin/ymin/xmax/ymax + WKT/authid).

    Raises ValueError when the layout can't yield a valid request (no reference
    map set, non-positive paper size or DPI) so the UI shows an actionable hint.
    """
    page = layout.pageCollection().page(0)
    if page is None:
        raise ValueError("Layout has no pages")
    size = page.pageSize()
    width_mm = float(size.width())
    height_mm = float(size.height())

    dpi = float(layout.renderContext().dpi())

    width_px, height_px = compute_pixel_dimensions(width_mm, height_mm, dpi)
    tier = select_resolution_tier(width_px, height_px)

    map_item = layout.referenceMap()
    if map_item is None:
        raise ValueError(
            "This layout has no reference map. Add a map item (and set it as the "
            "reference map in its Item Properties) before generating."
        )

    extent = map_item.extent()
    crs = map_item.crs()
    authid = crs.authid() if crs is not None else ""

    return {
        "width_px": width_px,
        "height_px": height_px,
        "dpi": dpi,
        "paper_w_mm": width_mm,
        "paper_h_mm": height_mm,
        "resolution_tier": tier,
        # xmin/ymin/xmax/ymax: the shape raster_writer.write_geotiff and the
        # generation pipeline's bbox already expect.
        "extent": {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
        },
        "crs_authid": authid or None,
        "crs_wkt": crs.toWkt() if crs is not None else "",
    }
