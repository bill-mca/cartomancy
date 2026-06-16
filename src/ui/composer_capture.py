"""Capture a Print Layout's map frame to an image at print resolution (ROADMAP M2).

The companion to `core/layout/composer_params.py` (M1): M1 says *how big* the
output should be (from paper size x DPI), this renders the layout's reference
map item to the image we actually send for generation.

Design: heavy reuse of the proven canvas export path (`canvas_exporter.py`).
The only new part is building a `QgsMapSettings` from the layout's reference
`QgsLayoutItemMap` instead of the main canvas; everything downstream
(sizing to the resolution tier, rendering, encoding, geo-context) is shared.

Important: we render the INPUT at the **resolution tier budget** (<= 4K), not at
the raw paper pixel count. An A0 page at 300 DPI is ~140 megapixels — far beyond
any model tier and enough to exhaust memory. The full paper pixel size only
drives *tier selection* (done in M1); the model then generates at the tier and
the result is georeferenced back onto the layout's extent.

This module imports QGIS, so it can't run in the headless test container; it is
verified inside QGIS (see docs/ROADMAP.md M2/M3).
"""
from __future__ import annotations

from dataclasses import dataclass

from qgis.core import QgsMapSettings, QgsProject
from qgis.PyQt.QtGui import QColor

from ..core.layout.composer_params import get_composer_export_params
from ..core.logger import log_debug, log_warning
from .canvas_exporter import apply_export_context, prepare_export, render_export

# Mirror canvas_exporter's stance: a rotated map can't be georeferenced to an
# axis-aligned GeoTIFF without distortion, so refuse rather than mis-place output.
_MAX_ROTATION_DEG = 0.01


@dataclass
class LayoutCapture:
    """Everything M3 needs to launch a generation from a captured layout."""

    image_b64: str
    input_format: str           # 'webp' | 'jpeg' | 'png' (actual encoded format)
    extent_dict: dict           # xmin/ymin/xmax/ymax of the ACTUAL rendered extent
    crs_wkt: str
    crs_authid: str | None
    width_px: int               # rendered input width (tier-budget sized)
    height_px: int              # rendered input height
    paper_width_px: int         # full print pixels (paper_mm x DPI) — for display
    paper_height_px: int
    resolution_tier: str        # '1K' | '2K' | '4K'
    dpi: float


def _effective_layers(map_item):
    """The layers the layout map actually renders (resolves follow-project,
    locked layers, theme presets). Falls back to the project's visible layer
    tree, then to all project layers."""
    try:
        layers = [lyr for lyr in map_item.layersToRender() if lyr is not None]
        if layers:
            return layers
    except Exception as err:  # noqa: BLE001
        log_warning(f"layersToRender() failed, falling back to project layers: {err}")
    try:
        root = QgsProject.instance().layerTreeRoot()
        visible = [n.layer() for n in root.findLayers() if n.isVisible() and n.layer()]
        if visible:
            return visible
    except Exception as err:  # noqa: BLE001
        log_warning(f"layer-tree fallback failed: {err}")
    return list(QgsProject.instance().mapLayers().values())


def _map_settings_from_layout(layout):
    """Build a `QgsMapSettings` from the layout's reference map item.

    Returns (settings, extent, map_item). Raises ValueError when the layout
    can't be captured (no reference map, or a rotated map).
    """
    map_item = layout.referenceMap()
    if map_item is None:
        raise ValueError(
            "This layout has no reference map. Add a map item (and set it as the "
            "reference map in its Item Properties) before generating."
        )

    try:
        rotation = float(map_item.mapRotation())
    except Exception:  # noqa: BLE001
        rotation = 0.0
    if abs(rotation) > _MAX_ROTATION_DEG:
        raise ValueError(
            "Rotated layout maps are not supported yet. Set the map rotation to "
            "0 in the map item's properties and try again."
        )

    extent = map_item.extent()
    crs = map_item.crs()

    settings = QgsMapSettings()
    settings.setLayers(_effective_layers(map_item))
    settings.setDestinationCrs(crs)
    settings.setExtent(extent)
    try:
        settings.setBackgroundColor(map_item.backgroundColor())
    except Exception:  # noqa: BLE001
        settings.setBackgroundColor(QColor(255, 255, 255))
    try:
        settings.setRotation(rotation)
    except Exception:  # noqa: BLE001
        pass
    return settings, extent, map_item


def capture_layout_map(layout, ctx=None, progress_cb=None) -> LayoutCapture:
    """Render the layout's reference map frame to a base64 image for generation.

    `ctx` (a PipelineContext) is populated with geo-context (extent, CRS,
    centroid, ground resolution, export size) so the backend can georeference
    and the debug artifacts line up — exactly as the canvas path does.

    Raises ValueError for user-fixable problems (no reference map, rotated map),
    RuntimeError if the server export config hasn't loaded yet (sizing needs it).
    """
    params = get_composer_export_params(layout)  # validates paper size, DPI, ref map
    settings, extent, _map_item = _map_settings_from_layout(layout)
    tier = params["resolution_tier"]

    log_debug(
        f"Layout capture: paper={params['paper_w_mm']}x{params['paper_h_mm']}mm "
        f"@ {params['dpi']}dpi -> {params['width_px']}x{params['height_px']}px "
        f"(print) -> tier {tier}"
    )

    # Size the input to the tier budget (aspect-matched, aligned), render, encode.
    # prepare_export reads max_dimension/align from the server export config; it
    # raises a clear RuntimeError if that hasn't loaded yet.
    prep = prepare_export(settings, extent, target_resolution=tier)
    image_b64, raw_len, actual_extent, fmt_token = render_export(prep, progress_cb)
    apply_export_context(ctx, prep, actual_extent, raw_len, fmt_token)

    return LayoutCapture(
        image_b64=image_b64,
        input_format=fmt_token,
        extent_dict={
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        },
        crs_wkt=params["crs_wkt"],
        crs_authid=params["crs_authid"],
        width_px=prep.out_w,
        height_px=prep.out_h,
        paper_width_px=params["width_px"],
        paper_height_px=params["height_px"],
        resolution_tier=tier,
        dpi=params["dpi"],
    )
