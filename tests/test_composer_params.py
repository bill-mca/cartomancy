"""Unit tests for the composer->pixels engine (ROADMAP M1).

Pure stdlib (no QGIS, no pytest). The headline test reproduces the worked-example
table in docs/PLAN.md §5.1 exactly.

Run:  python -m unittest tests.test_composer_params -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.layout.composer_params import (  # noqa: E402
    compute_pixel_dimensions,
    credits_for_area,
    get_composer_export_params,
    select_resolution_tier,
)

# Paper sizes in mm: A4 = 210x297, A3 = 297x420.
# (name, width_mm, height_mm, dpi, expected_w_px, expected_h_px, expected_tier)
# Values are the docs/PLAN.md §5.1 table — the authoritative expected output.
PLAN_TABLE = [
    ("A4 portrait @ 96",   210.0, 297.0,  96.0,  794, 1123, "2K"),
    ("A4 portrait @ 300",  210.0, 297.0, 300.0, 2480, 3508, "4K"),
    ("A3 landscape @ 150", 420.0, 297.0, 150.0, 2480, 1754, "4K"),
    ("A3 landscape @ 300", 420.0, 297.0, 300.0, 4961, 3508, "4K"),
    ("Custom 200x200 @ 96", 200.0, 200.0,  96.0,  756,  756, "1K"),
]


# --- minimal duck-typed QgsPrintLayout stubs (no QGIS needed) --------------- #
class _Size:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class _Page:
    def __init__(self, w, h):
        self._size = _Size(w, h)

    def pageSize(self):
        return self._size


class _PageCollection:
    def __init__(self, w, h):
        self._page = _Page(w, h)

    def page(self, _i):
        return self._page


class _RenderContext:
    def __init__(self, dpi):
        self._dpi = dpi

    def dpi(self):
        return self._dpi


class _Rect:
    def __init__(self, xmin, ymin, xmax, ymax):
        self._v = (xmin, ymin, xmax, ymax)

    def xMinimum(self):
        return self._v[0]

    def yMinimum(self):
        return self._v[1]

    def xMaximum(self):
        return self._v[2]

    def yMaximum(self):
        return self._v[3]


class _Crs:
    def __init__(self, authid, wkt):
        self._authid, self._wkt = authid, wkt

    def authid(self):
        return self._authid

    def toWkt(self):
        return self._wkt


class _MapItem:
    def __init__(self, extent, crs):
        self._extent, self._crs = extent, crs

    def extent(self):
        return self._extent

    def crs(self):
        return self._crs


class _Layout:
    def __init__(self, w_mm, h_mm, dpi, map_item):
        self._pc = _PageCollection(w_mm, h_mm)
        self._rc = _RenderContext(dpi)
        self._map = map_item

    def pageCollection(self):
        return self._pc

    def renderContext(self):
        return self._rc

    def referenceMap(self):
        return self._map


def _make_layout(w_mm, h_mm, dpi, *, extent=(0, 0, 100, 100),
                 authid="EPSG:3857", wkt="PROJCS[\"WebMercator\"]"):
    return _Layout(w_mm, h_mm, dpi, _MapItem(_Rect(*extent), _Crs(authid, wkt)))


class ComputePixelDimensionsTest(unittest.TestCase):
    def test_reproduces_plan_table(self):
        for name, w_mm, h_mm, dpi, exp_w, exp_h, _tier in PLAN_TABLE:
            with self.subTest(name):
                self.assertEqual(compute_pixel_dimensions(w_mm, h_mm, dpi), (exp_w, exp_h))

    def test_rejects_nonpositive(self):
        for bad in [(0, 100, 96), (100, -1, 96), (100, 100, 0), (100, 100, -5)]:
            with self.subTest(bad), self.assertRaises(ValueError):
                compute_pixel_dimensions(*bad)


class SelectResolutionTierTest(unittest.TestCase):
    def test_reproduces_plan_table(self):
        for name, w_mm, h_mm, dpi, exp_w, exp_h, exp_tier in PLAN_TABLE:
            with self.subTest(name):
                self.assertEqual(select_resolution_tier(exp_w, exp_h), exp_tier)

    def test_boundaries(self):
        self.assertEqual(select_resolution_tier(1024, 1024), "1K")  # inclusive 1K
        self.assertEqual(select_resolution_tier(1025, 10), "2K")    # just over 1K
        self.assertEqual(select_resolution_tier(2048, 2048), "2K")  # inclusive 2K
        self.assertEqual(select_resolution_tier(2049, 10), "4K")    # just over 2K
        self.assertEqual(select_resolution_tier(10, 5000), "4K")    # height drives it


class CreditsForAreaTest(unittest.TestCase):
    def test_one_megapixel_is_base_cost(self):
        self.assertEqual(credits_for_area(1024, 1024), 20)

    def test_scales_with_area(self):
        self.assertEqual(credits_for_area(2048, 2048), 80)  # 4 MP * 20

    def test_rejects_nonpositive(self):
        with self.assertRaises(ValueError):
            credits_for_area(0, 100)


class GetComposerExportParamsTest(unittest.TestCase):
    def test_assembles_full_params_from_layout(self):
        for name, w_mm, h_mm, dpi, exp_w, exp_h, exp_tier in PLAN_TABLE:
            with self.subTest(name):
                layout = _make_layout(w_mm, h_mm, dpi, extent=(10, 20, 30, 40))
                params = get_composer_export_params(layout)
                self.assertEqual(params["width_px"], exp_w)
                self.assertEqual(params["height_px"], exp_h)
                self.assertEqual(params["resolution_tier"], exp_tier)
                self.assertEqual(params["dpi"], dpi)
                self.assertEqual(params["paper_w_mm"], w_mm)
                self.assertEqual(params["paper_h_mm"], h_mm)

    def test_extent_and_crs_passthrough_matches_pipeline_shape(self):
        layout = _make_layout(210, 297, 300, extent=(-1.5, -2.5, 3.5, 4.5),
                              authid="EPSG:4326", wkt="GEOGCS[\"WGS84\"]")
        params = get_composer_export_params(layout)
        # The keys raster_writer.write_geotiff / the bbox path consume.
        self.assertEqual(
            params["extent"], {"xmin": -1.5, "ymin": -2.5, "xmax": 3.5, "ymax": 4.5}
        )
        self.assertEqual(params["crs_authid"], "EPSG:4326")
        self.assertEqual(params["crs_wkt"], "GEOGCS[\"WGS84\"]")

    def test_missing_reference_map_raises_actionable_error(self):
        layout = _Layout(210, 297, 300, map_item=None)
        with self.assertRaises(ValueError) as cm:
            get_composer_export_params(layout)
        self.assertIn("reference map", str(cm.exception).lower())

    def test_blank_authid_becomes_none(self):
        layout = _make_layout(200, 200, 96, authid="")
        self.assertIsNone(get_composer_export_params(layout)["crs_authid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
